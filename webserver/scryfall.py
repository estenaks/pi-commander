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
from images import CARD_BACK_WEB_URL, _any_to_bmp

SCRYFALL_REQUEST_DELAY = 0.050  # 50ms delay between requests

PLAYERS = [1, 2, 3, 4]

_state_lock = threading.Lock()
_state_by_player = {
    p: {
        "last_query": None,
        "card_id": None,
        "faces_meta": [],
        "border_crop_url": None,
        "premium": None,   # e.g. "foil" etc, or None
    }
    for p in PLAYERS
}

# BMP cache: keyed by (player, face) where face is "front" or "back"
# Protected by _state_lock
_bmp_cache: dict[tuple[int, str], bytes] = {}


# ---- Scryfall HTTP ----

def _scryfall_get(url: str, use_cache: bool = False, cache_key: str = None, cache_hours: int = 24) -> dict:
    """Make a GET request to Scryfall with optional caching."""
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


# ---- Card image helpers ----

def _pick_image_border_crop_only(iu: dict) -> str:
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


def _extract_faces_meta_always_two(card: dict) -> list[dict]:
    faces = card.get("card_faces") or []
    if isinstance(faces, list) and len(faces) >= 2:
        f0 = faces[0] or {}
        f1 = faces[1] or {}

        u0 = _pick_image_border_crop_only((f0.get("image_uris") or {}))
        u1 = _pick_image_border_crop_only((f1.get("image_uris") or {}))

        tl0 = f0.get("type_line") if isinstance(f0.get("type_line"), str) else ""
        tl1 = f1.get("type_line") if isinstance(f1.get("type_line"), str) else ""

        if u0:
            if not u1:
                u1 = u0
            return [
                {"image_url": u0, "type_line": tl0},
                {"image_url": u1, "type_line": tl1},
            ]

    iu = card.get("image_uris") or {}
    front = _pick_image_border_crop_only(iu)
    if not front:
        return []

    tl = card.get("type_line") if isinstance(card.get("type_line"), str) else ""
    return [
        {"image_url": front, "type_line": tl},
        {"image_url": CARD_BACK_WEB_URL, "type_line": "Card Back"},
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


# ---- Player state ----

def _require_player(player: int) -> int:
    if player not in _state_by_player:
        raise ValueError("Invalid player. Must be 1..4.")
    return player


def _generate_bmps(player: int) -> None:
    """Generate and cache BMP images for both faces of *player*'s current card."""
    with _state_lock:
        faces_meta = list(_state_by_player[player]["faces_meta"])
        card_id = _state_by_player[player]["card_id"]

    if not faces_meta:
        return

    front_url = faces_meta[0]["image_url"]
    back_url = faces_meta[1]["image_url"] if len(faces_meta) > 1 else CARD_BACK_WEB_URL

    for face, url in (("front", front_url), ("back", back_url)):
        try:
            bmp_bytes = _any_to_bmp(url)
            with _state_lock:
                if _state_by_player[player]["card_id"] == card_id:
                    _bmp_cache[(player, face)] = bmp_bytes
        except Exception as exc:
            print(f"[bmp] Error generating {face} BMP for player {player}: {exc}", file=sys.stderr)


def _set_player_state(player: int, *, last_query: str, card: dict, premium: str | None = None) -> dict:
    faces_meta = _extract_faces_meta_always_two(card)
    if not faces_meta:
        raise RuntimeError("No suitable image found for this card")

    border_crop = _extract_border_crop(card) or faces_meta[0]["image_url"]

    with _state_lock:
        st = _state_by_player[player]
        st["last_query"] = last_query
        st["card_id"] = card.get("id")
        st["faces_meta"] = faces_meta
        st["border_crop_url"] = border_crop
        st["premium"] = premium

        result = {
            "last_query": st["last_query"],
            "card_id": st["card_id"],
            "faces": st["faces_meta"],
            "border_crop_url": st["border_crop_url"],
            "premium": st["premium"],
        }

    _generate_bmps(player)
    return result


# ---- Booster helpers ----

def _get_all_sets() -> list[dict]:
    """Get all Magic sets from Scryfall with 1-day caching."""
    return _scryfall_get(
        "https://api.scryfall.com/sets",
        use_cache=True,
        cache_key="all_sets",
        cache_hours=24,
    ).get("data", [])


def _get_full_set_data(set_code: str) -> list[dict]:
    """Get all cards from a set, with permanent caching."""
    cache_key = f"set_{set_code}_full"

    with _cache_lock:
        cached_set = _get_cached_data(cache_key, cache_hours=24 * CACHE_EXPIRY_DAYS)
        if cached_set is not None:
            return cached_set

    print(f"[booster] 📦 DOWNLOADING: Fetching full set data for {set_code}...")
    all_cards = []
    page = 1

    while True:
        try:
            url = f"https://api.scryfall.com/cards/search?q=set:{set_code}&page={page}"
            print(f"[booster] Fetching page {page} for set {set_code}...")
            time.sleep(SCRYFALL_REQUEST_DELAY)
            response = _scryfall_get(url, use_cache=False)

            cards_data = response.get("data", [])
            if not cards_data:
                break

            for card in cards_data:
                rarity = card.get("rarity", "")
                if rarity not in ["common", "uncommon", "rare", "mythic"]:
                    continue

                type_line = card.get("type_line", "")

                if card.get("image_uris"):
                    front_url = _pick_image_border_crop_only(card.get("image_uris", {}))
                    back_url = None
                elif card.get("card_faces") and len(card["card_faces"]) >= 2:
                    f0 = card["card_faces"][0]
                    f1 = card["card_faces"][1]
                    front_url = _pick_image_border_crop_only(f0.get("image_uris") or {})
                    back_url = _pick_image_border_crop_only(f1.get("image_uris") or {})
                else:
                    continue

                if not front_url:
                    continue

                all_cards.append({
                    "id": card.get("id"),
                    "name": card.get("name", "Unknown"),
                    "image_url": front_url,
                    "back_image_url": back_url,
                    "rarity": rarity,
                    "set": set_code,
                    "mana_cost": card.get("mana_cost", ""),
                    "type_line": type_line,
                    "is_common_land": (rarity == "common" and "Land" in type_line),
                })

            if not response.get("has_more", False):
                break

            page += 1

        except Exception as e:
            print(f"[booster] Error fetching page {page} for set {set_code}: {e}")
            if page == 1:
                return []
            break

    print(f"[booster] ✅ DOWNLOADED: Set {set_code} complete ({len(all_cards)} cards)")

    with _cache_lock:
        _set_cached_data(cache_key, all_cards)

    return all_cards


def _get_cards_by_rarity_from_set(set_cards: list[dict], rarity: str) -> list[dict]:
    """Filter cards by rarity from a complete set."""
    return [card for card in set_cards if card.get("rarity") == rarity]


def _select_random_cards_from_pool(card_pool: list[dict], count: int, exclude_ids: set = None) -> list[dict]:
    """Select random cards from a pool, avoiding duplicates."""
    if exclude_ids is None:
        exclude_ids = set()

    available_cards = [card for card in card_pool if card["id"] not in exclude_ids]

    if len(available_cards) < count:
        print(f"[booster] Warning: Only {len(available_cards)} cards available, requested {count}")
        return available_cards

    selected = random.sample(available_cards, count)

    for card in selected:
        exclude_ids.add(card["id"])

    return selected


def _has_mythic_rares(set_cards: list[dict]) -> bool:
    """Check if a set has any mythic rare cards."""
    return any(card.get("rarity") == "mythic" for card in set_cards)


def _get_rare_or_mythic_card_from_set(set_cards: list[dict], exclude_ids: set = None) -> dict | None:
    """Get a rare card with 1/8 chance of being mythic."""
    if exclude_ids is None:
        exclude_ids = set()

    has_mythics = _has_mythic_rares(set_cards)
    try_mythic = has_mythics and (random.randint(1, 8) == 1)

    if try_mythic:
        mythic_pool = _get_cards_by_rarity_from_set(set_cards, "mythic")
        available_mythics = [card for card in mythic_pool if card["id"] not in exclude_ids]

        if available_mythics:
            selected = random.choice(available_mythics)
            exclude_ids.add(selected["id"])
            print(f"[booster] Selected mythic rare: {selected['name']}")
            return selected
        else:
            print(f"[booster] No available mythic rares (all in exclude list), falling back to rare")

    rare_pool = _get_cards_by_rarity_from_set(set_cards, "rare")
    available_rares = [card for card in rare_pool if card["id"] not in exclude_ids]

    if available_rares:
        selected = random.choice(available_rares)
        exclude_ids.add(selected["id"])
        print(f"[booster] Selected rare: {selected['name']}")
        return selected

    print(f"[booster] No available rare cards!")
    return None


def _get_common_land_from_set(set_cards: list[dict], exclude_ids: set = None) -> dict | None:
    """Get a random common land from the set."""
    if exclude_ids is None:
        exclude_ids = set()

    common_lands = [
        card for card in set_cards
        if card.get("rarity") == "common" and "Land" in card.get("type_line", "")
    ]
    available_lands = [card for card in common_lands if card["id"] not in exclude_ids]

    if not available_lands:
        print(f"[booster] No available common lands found")
        return None

    selected = random.choice(available_lands)
    exclude_ids.add(selected["id"])
    print(f"[booster] Selected common land: {selected['name']}")
    return selected


# ---------------------------------------------------------------------------
# MTGJSON booster config
# ---------------------------------------------------------------------------

# Sheet name fragments that map to known slot types understood by the frontend.
# Checked in order — first match wins.
_SHEET_TYPE_MAP = [
    ("timeshifted",  "timeshifted"),
    ("foil",         "foil"),
    ("land",         "land"),
    ("basicland",    "land"),
    ("basic",        "land"),
    ("mythic",       "rare"),    # mythic-only sheet → treat as rare slot
    ("rare",         "rare"),
    ("uncommon",     "uncommon"),
    ("common",       "common"),
]

_SLOT_LABELS = {
    "common":      "Common",
    "uncommon":    "Uncommon",
    "rare":        "Rare",
    "land":        "Land",
    "foil":        "✨",
    "timeshifted": "Timeshifted",
}

MTGJSON_SET_URL = "https://mtgjson.com/api/v5/{code}.json"
MTGJSON_CACHE_HOURS = 24 * 7  # one week — booster configs don't change

# Preferred booster types in order — "play" is the modern replacement for "draft"
_BOOSTER_TYPE_PREFERENCE = ["draft", "play", "default"]


def _classify_sheet(sheet_name: str) -> str:
    """Map a raw MTGJSON sheet name to one of our known slot type strings."""
    lower = sheet_name.lower()
    for fragment, slot_type in _SHEET_TYPE_MAP:
        if fragment in lower:
            return slot_type
    return "common"  # safe fallback


def _foil_probability_from_slot(slot: dict | list) -> float:
    """
    Given a raw MTGJSON slot entry (either a string/dict for a fixed sheet,
    or a list of [sheetName, weight] pairs for a variable slot), return the
    probability (0.0–1.0) that the slot produces a foil card.

    Examples
    --------
    "foil"                                 → 1.0
    [["foil", 1], ["common", 4]]           → 0.2
    [["foil", 1], ["uncommon", 1]]         → 0.5
    "common"                               → 0.0
    """
    if isinstance(slot, str):
        return 1.0 if "foil" in slot.lower() else 0.0

    if isinstance(slot, list):
        total = sum(w for _, w in slot)
        if total == 0:
            return 0.0
        foil_weight = sum(w for name, w in slot if "foil" in name.lower())
        return foil_weight / total

    return 0.0


def _pick_booster_data(booster_section: dict) -> dict | None:
    """Pick the best booster type from a MTGJSON booster section.
    Prefers draft → play → default, then any non-collector type, then anything."""
    for key in _BOOSTER_TYPE_PREFERENCE:
        if key in booster_section:
            print(f"[booster_config] Using booster type: '{key}'")
            return booster_section[key]
    # Avoid collector boosters if possible — they have weird slot distributions
    non_collector = {k: v for k, v in booster_section.items() if "collector" not in k.lower()}
    if non_collector:
        key = next(iter(non_collector))
        print(f"[booster_config] Falling back to booster type: '{key}'")
        return non_collector[key]
    key = next(iter(booster_section))
    print(f"[booster_config] Last-resort booster type: '{key}'")
    return booster_section[key]


def _parse_mtgjson_slots(booster_data: dict) -> list[dict]:
    """
    Parse a MTGJSON booster config dict (one booster type, e.g. 'play')
    into a flat list of slot descriptors understood by the frontend.

    Handles both schemas:
      - Modern: boosters[].contents (dict of sheetName→count) + sheets{}
      - Legacy: slots[] with deck key + sheets{}

    Each descriptor:
    {
        "type":             str,    # "common" | "uncommon" | "rare" | "land" | "foil" | "timeshifted"
        "count":            int,
        "foil":             bool,
        "foil_probability": float,  # only present when foil_probability > 0
        "label":            str,
    }
    """
    sheets = booster_data.get("sheets", {})

    # --- Modern schema: boosters[].contents + sheets ---
    boosters_list = booster_data.get("boosters", [])
    if boosters_list and sheets:
        # Pick the highest-weight booster variant (most common pack makeup)
        best = max(boosters_list, key=lambda b: b.get("weight", 0))
        contents = best.get("contents", {})
        if not contents:
            print(f"[booster_config] Modern schema: best booster variant has empty contents")
            return []

        print(f"[booster_config] Modern schema: contents = {dict(contents)}")

        # Aggregate counts per (slot_type, is_foil) key, then emit descriptors.
        # Multiple sheet names can map to the same slot type — merge their counts.
        slot_totals: dict[tuple[str, bool], dict] = {}

        for sheet_name, count in contents.items():
            sheet_meta = sheets.get(sheet_name, {})
            is_foil = bool(sheet_meta.get("foil", False))
            slot_type = "foil" if is_foil else _classify_sheet(sheet_name)

            key = (slot_type, is_foil)
            if key not in slot_totals:
                slot_totals[key] = {
                    "type":  slot_type,
                    "count": 0,
                    "foil":  is_foil,
                    "label": _SLOT_LABELS.get(slot_type, slot_type.capitalize()),
                }
            slot_totals[key]["count"] += count

        result = list(slot_totals.values())
        print(f"[booster_config] Modern schema: parsed {len(result)} slot types")
        return result

    # --- Legacy schema: slots[] list ---
    raw_slots = booster_data.get("slots", [])
    if raw_slots:
        result = []
        for raw in raw_slots:
            deck = raw.get("deck")
            count = raw.get("count", 1)

            if deck is None:
                continue

            # Variable slot: deck is a list of [sheetName, weight] pairs
            if isinstance(deck, list):
                foil_prob = _foil_probability_from_slot(deck)
                # Classify by the highest-weight non-foil sheet name for label/type
                non_foil = [(name, w) for name, w in deck if "foil" not in name.lower()]
                primary = max(non_foil, key=lambda x: x[1])[0] if non_foil else deck[0][0]
                slot_type = "foil" if foil_prob >= 1.0 else _classify_sheet(primary)
                is_foil = foil_prob >= 1.0
            else:
                # Fixed slot: deck is a sheet name string
                sheet_meta = sheets.get(deck, {})
                is_foil = bool(sheet_meta.get("foil", False)) or "foil" in deck.lower()
                foil_prob = 1.0 if is_foil else 0.0
                slot_type = _classify_sheet(deck)

            descriptor: dict = {
                "type":  slot_type,
                "count": count,
                "foil":  is_foil,
                "label": _SLOT_LABELS.get(slot_type, slot_type.capitalize()),
            }
            if foil_prob > 0.0:
                descriptor["foil_probability"] = round(foil_prob, 4)

            result.append(descriptor)

        print(f"[booster_config] Legacy schema: parsed {len(result)} slot types")
        return result

    print(f"[booster_config] No usable slots found (neither modern nor legacy schema matched)")
    return []


def _get_booster_config(set_code: str) -> dict:
    """
    Fetch, parse, and cache the MTGJSON booster config for *set_code*.

    Returns:
        {
            "slots": [ { type, count, foil, foil_probability?, label }, ... ],
            "source": "mtgjson" | "fallback"
        }

    Falls back to the classic 10C/3U/1R/1L layout if MTGJSON has no data.
    """
    cache_key = f"booster_config_{set_code}"

    with _cache_lock:
        cached = _get_cached_data(cache_key, cache_hours=MTGJSON_CACHE_HOURS)
        if cached is not None:
            print(f"[booster_config] Cache hit for {set_code}")
            return cached

    print(f"[booster_config] Fetching MTGJSON booster config for {set_code}...")
    url = MTGJSON_SET_URL.format(code=set_code.upper())

    try:
        import urllib.request
        import json as _json

        req = urllib.request.Request(url, headers={"User-Agent": "pi-commander/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = _json.loads(resp.read().decode())

        booster_section = raw.get("data", {}).get("booster", {})
        print(f"[booster_config] Available booster types for {set_code}: {list(booster_section.keys())}")

        if not booster_section:
            print(f"[booster_config] No booster section in MTGJSON for {set_code}, using fallback")
        else:
            booster_data = _pick_booster_data(booster_section)

            if booster_data:
                slots = _parse_mtgjson_slots(booster_data)
                if slots:
                    result = {"slots": slots, "source": "mtgjson"}
                    with _cache_lock:
                        _set_cached_data(cache_key, result)
                    print(f"[booster_config] Parsed {len(slots)} slot types for {set_code} from MTGJSON")
                    return result

            print(f"[booster_config] No usable booster data in MTGJSON for {set_code}, using fallback")

    except Exception as e:
        print(f"[booster_config] MTGJSON fetch failed for {set_code}: {e} — using fallback")

    # Fallback: classic draft booster layout (no foil slot)
    fallback = {
        "slots": [
            {"type": "common",   "count": 10, "foil": False, "label": "Common"},
            {"type": "uncommon", "count": 3,  "foil": False, "label": "Uncommon"},
            {"type": "rare",     "count": 1,  "foil": False, "label": "Rare"},
            {"type": "land",     "count": 1,  "foil": False, "label": "Land"},
        ],
        "source": "fallback",
    }
    with _cache_lock:
        _set_cached_data(cache_key, fallback)
    return fallback