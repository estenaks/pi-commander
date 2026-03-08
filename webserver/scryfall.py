from __future__ import annotations
import sys
import json
import time
import random
import threading
import urllib.request
import urllib.parse
import urllib.error

from cache import (
    CACHE_EXPIRY_DAYS,
    _cache_lock,
    _get_cached_data,
    _set_cached_data,
)
from images import CARD_BACK_WEB_URL, _any_to_bmp, _any_to_strips

SCRYFALL_REQUEST_DELAY = 0.050  # 50ms between requests

PLAYERS = [1, 2, 3, 4]

_state_lock = threading.Lock()
_state_by_player = {
    p: {
        "last_query": None,
        "card_id": None,
        "faces_meta": [],
        "border_crop_url": None,
        "premium": None,
    }
    for p in PLAYERS
}

_bmp_cache:   dict[tuple[int, str], bytes]       = {}
_strip_cache: dict[tuple[int, str], list[bytes]] = {}

_render_executors: dict[int, threading.Thread] = {}
_render_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Scryfall HTTP
# ---------------------------------------------------------------------------

def _scryfall_get(url: str, use_cache: bool = False, cache_key: str = None, cache_hours: int = 24) -> dict:
    if use_cache and cache_key:
        with _cache_lock:
            cached_data = _get_cached_data(cache_key, cache_hours)
            if cached_data is not None:
                print(f"[cache] Using cached data for: {cache_key}")
                return cached_data

    time.sleep(SCRYFALL_REQUEST_DELAY)

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "raspberrypi-webserver-poc/0.1",
            "Accept": "application/json",
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read().decode("utf-8")
            result = json.loads(data)

        if use_cache and cache_key:
            with _cache_lock:
                _set_cached_data(cache_key, result)

        return result

    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")
        except Exception:
            body = ""
        raise RuntimeError(f"Scryfall HTTP {e.code}: {body or e.reason}") from e
    except Exception as e:
        raise RuntimeError(f"Scryfall request failed: {type(e).__name__}: {e}") from e


# ---------------------------------------------------------------------------
# Card image helpers
# ---------------------------------------------------------------------------

def _pick_image_border_crop_only(iu: dict) -> str:
    """For Pico display: prefers border_crop (480×680)."""
    if not isinstance(iu, dict):
        return ""
    return (
        iu.get("border_crop")
        or iu.get("normal")
        or iu.get("large")
        or iu.get("png")
        or iu.get("art_crop")
        or ""
    )


def _pick_image_normal(iu: dict) -> str:
    """For e-paper display: prefers normal (488×680) for higher dither quality."""
    if not isinstance(iu, dict):
        return ""
    return (
        iu.get("normal")
        or iu.get("large")
        or iu.get("border_crop")
        or iu.get("png")
        or iu.get("art_crop")
        or ""
    )


def _extract_faces_meta_always_two(card: dict) -> list[dict]:
    faces = card.get("card_faces") or []
    if isinstance(faces, list) and len(faces) >= 2:
        f0 = faces[0] or {}
        f1 = faces[1] or {}
        iu0 = f0.get("image_uris") or {}
        iu1 = f1.get("image_uris") or {}
        u0  = _pick_image_border_crop_only(iu0)
        u1  = _pick_image_border_crop_only(iu1)
        n0  = _pick_image_normal(iu0)
        n1  = _pick_image_normal(iu1)
        tl0 = f0.get("type_line") if isinstance(f0.get("type_line"), str) else ""
        tl1 = f1.get("type_line") if isinstance(f1.get("type_line"), str) else ""
        if u0:
            if not u1:
                u1 = u0
                n1 = n0
            return [
                {"image_url": u0, "normal_image_url": n0, "type_line": tl0},
                {"image_url": u1, "normal_image_url": n1, "type_line": tl1},
            ]

    iu    = card.get("image_uris") or {}
    front = _pick_image_border_crop_only(iu)
    if not front:
        return []

    normal = _pick_image_normal(iu)
    tl = card.get("type_line") if isinstance(card.get("type_line"), str) else ""
    return [
        {"image_url": front, "normal_image_url": normal, "type_line": tl},
        {"image_url": CARD_BACK_WEB_URL, "normal_image_url": CARD_BACK_WEB_URL, "type_line": "Card Back"},
    ]


def _extract_border_crop(card: dict) -> str:
    border_crop = (card.get("image_uris") or {}).get("border_crop")
    if border_crop:
        return border_crop
    faces = card.get("card_faces") or []
    for face in faces:
        bc = (face.get("image_uris") or {}).get("border_crop")
        if bc:
            return bc
    return ""


# ---------------------------------------------------------------------------
# Player state
# ---------------------------------------------------------------------------

def _require_player(player: int) -> int:
    if player not in _state_by_player:
        raise ValueError("Invalid player. Must be 1..4.")
    return player


def _generate_bmps(player: int) -> None:
    """Blocking render — always called from a background thread."""
    with _state_lock:
        faces_meta = list(_state_by_player[player]["faces_meta"])
        card_id    = _state_by_player[player]["card_id"]

    if not faces_meta:
        return

    front_url = faces_meta[0]["image_url"]
    back_url  = faces_meta[1]["image_url"] if len(faces_meta) > 1 else CARD_BACK_WEB_URL

    for face, url in (("front", front_url), ("back", back_url)):
        try:
            print(f"[render] player={player} face={face} — starting dither…")
            bmp_bytes = _any_to_bmp(url)
            strips    = _any_to_strips(url)
            with _state_lock:
                if _state_by_player[player]["card_id"] == card_id:
                    _bmp_cache[(player, face)]   = bmp_bytes
                    _strip_cache[(player, face)] = strips
                    print(f"[render] player={player} face={face} — done, cache updated.")
                else:
                    print(f"[render] player={player} face={face} — card changed, discarding.")
        except Exception as exc:
            print(f"[render] Error for player={player} face={face}: {exc}", file=sys.stderr)


def _schedule_render(player: int) -> None:
    """Fire _generate_bmps in a background daemon thread."""
    t = threading.Thread(target=_generate_bmps, args=(player,), daemon=True)
    t.start()
    with _render_lock:
        _render_executors[player] = t


def _set_player_state(player: int, *, last_query: str, card: dict, premium: str | None = None) -> dict:
    faces_meta = _extract_faces_meta_always_two(card)
    if not faces_meta:
        raise RuntimeError("No suitable image found for this card")

    border_crop = _extract_border_crop(card) or faces_meta[0]["image_url"]

    with _state_lock:
        st = _state_by_player[player]
        st["last_query"]      = last_query
        st["card_id"]         = card.get("id")
        st["faces_meta"]      = faces_meta
        st["border_crop_url"] = border_crop
        st["premium"]         = premium

        result = {
            "last_query":      st["last_query"],
            "card_id":         st["card_id"],
            "faces":           st["faces_meta"],
            "border_crop_url": st["border_crop_url"],
            "premium":         st["premium"],
        }

    _schedule_render(player)
    return result


# ---------------------------------------------------------------------------
# Generic set helpers
# ---------------------------------------------------------------------------

def _get_all_sets() -> list[dict]:
    """Return all Magic sets from Scryfall (1-day cache)."""
    return _scryfall_get(
        "https://api.scryfall.com/sets",
        use_cache=True,
        cache_key="all_sets",
        cache_hours=24,
    ).get("data", [])


def _classify_color(colors: list[str], type_line: str) -> str:
    """Map a card to its Mystery Booster colour bucket."""
    if "Land" in type_line:
        return "land"
    if len(colors) > 1:
        return "multi"
    if len(colors) == 1:
        return colors[0].upper()
    return "colorless"


def _build_card_record(card: dict, set_code: str) -> dict | None:
    """Convert a raw Scryfall card object into our cached dict format."""
    rarity = card.get("rarity", "")
    if rarity not in ["common", "uncommon", "rare", "mythic", "special"]:
        return None

    type_line = card.get("type_line", "")
    colors = card.get("colors") or []

    if card.get("image_uris"):
        front_url = _pick_image_border_crop_only(card["image_uris"])
        back_url  = None
    elif card.get("card_faces") and len(card["card_faces"]) >= 2:
        f0 = card["card_faces"][0]
        f1 = card["card_faces"][1]
        front_url = _pick_image_border_crop_only(f0.get("image_uris") or {})
        back_url  = _pick_image_border_crop_only(f1.get("image_uris") or {})
        if not colors:
            colors = f0.get("colors") or []
    else:
        return None

    if not front_url:
        return None

    cn_str = card.get("collector_number", "")
    try:
        cn_int = int("".join(c for c in cn_str if c.isdigit()) or "0")
    except ValueError:
        cn_int = 0

    return {
        "id":               card.get("id"),
        "name":             card.get("name", "Unknown"),
        "image_url":        front_url,
        "back_image_url":   back_url,
        "rarity":           rarity,
        "set":              set_code,
        "mana_cost":        card.get("mana_cost", ""),
        "type_line":        type_line,
        "is_common_land":   (rarity == "common" and "Land" in type_line),
        "color_bucket":     _classify_color(colors, type_line),
        "frame":            card.get("frame", ""),
        "border_color":     card.get("border_color", ""),
        "collector_number": cn_int,
    }


def _fetch_all_pages(scryfall_query: str, cache_key: str) -> list[dict]:
    """Download every page of a Scryfall search query and cache the result."""
    with _cache_lock:
        cached = _get_cached_data(cache_key, cache_hours=24 * CACHE_EXPIRY_DAYS)
        if cached is not None:
            return cached

    print(f"[booster] 📦 DOWNLOADING: query={scryfall_query!r} ...")
    all_cards: list[dict] = []
    page = 1

    while True:
        try:
            params = urllib.parse.urlencode({"q": scryfall_query, "page": page})
            url = f"https://api.scryfall.com/cards/search?{params}"
            print(f"[booster]   page {page} ...")
            time.sleep(SCRYFALL_REQUEST_DELAY)
            response = _scryfall_get(url, use_cache=False)

            for raw in response.get("data", []):
                rec = _build_card_record(raw, raw.get("set", ""))
                if rec:
                    all_cards.append(rec)

            if not response.get("has_more", False):
                break
            page += 1

        except Exception as e:
            print(f"[booster] Error on page {page}: {e}")
            if page == 1:
                return []
            break

    print(f"[booster] ✅ Done ({len(all_cards)} cards)")

    with _cache_lock:
        _set_cached_data(cache_key, all_cards)

    return all_cards


def _get_full_set_data(set_code: str) -> list[dict]:
    return _fetch_all_pages(
        scryfall_query=f"set:{set_code}",
        cache_key=f"set_{set_code}_full",
    )


def _get_mb2_main_pool() -> list[dict]:
    return _fetch_all_pages(
        scryfall_query="e:plst date=2024-08-02",
        cache_key="mb2_main_pool",
    )


def _get_mb2_physical_pool() -> list[dict]:
    return _fetch_all_pages(
        scryfall_query="e:mb2",
        cache_key="mb2_physical_pool",
    )


def _get_cards_by_rarity_from_set(pool: list[dict], rarity: str) -> list[dict]:
    return [c for c in pool if c.get("rarity") == rarity]


def _get_cu_from_pool(pool: list[dict]) -> list[dict]:
    return [c for c in pool if c.get("rarity") in ("common", "uncommon")]


def _get_cards_by_color_from_set(pool: list[dict], color_bucket: str) -> list[dict]:
    color_bucket = color_bucket.upper() if len(color_bucket) == 1 else color_bucket.lower()
    return [c for c in pool if c.get("color_bucket") == color_bucket]


def _get_cards_by_cn_range(pool: list[dict], cn_min: int, cn_max: int) -> list[dict]:
    return [c for c in pool if cn_min <= c.get("collector_number", 0) <= cn_max]


def _select_random_cards_from_pool(pool: list[dict], count: int, exclude_ids: set = None) -> list[dict]:
    if exclude_ids is None:
        exclude_ids = set()
    available = [c for c in pool if c["id"] not in exclude_ids]
    if len(available) < count:
        print(f"[booster] Warning: only {len(available)} available, requested {count}")
        return available
    selected = random.sample(available, count)
    for c in selected:
        exclude_ids.add(c["id"])
    return selected


def _has_mythic_rares(pool: list[dict]) -> bool:
    return any(c.get("rarity") == "mythic" for c in pool)


def _get_rare_or_mythic_card_from_set(pool: list[dict], exclude_ids: set = None) -> dict | None:
    if exclude_ids is None:
        exclude_ids = set()

    if _has_mythic_rares(pool) and random.randint(1, 8) == 1:
        mythics = [c for c in pool if c.get("rarity") == "mythic" and c["id"] not in exclude_ids]
        if mythics:
            card = random.choice(mythics)
            exclude_ids.add(card["id"])
            return card

    rares = [c for c in pool if c.get("rarity") == "rare" and c["id"] not in exclude_ids]
    if rares:
        card = random.choice(rares)
        exclude_ids.add(card["id"])
        return card

    return None


def _get_common_land_from_set(pool: list[dict], exclude_ids: set = None) -> dict | None:
    if exclude_ids is None:
        exclude_ids = set()
    lands = [c for c in pool if c.get("rarity") == "common" and "Land" in c.get("type_line", "")]
    available = [c for c in lands if c["id"] not in exclude_ids]
    if not available:
        return None
    card = random.choice(available)
    exclude_ids.add(card["id"])
    return card