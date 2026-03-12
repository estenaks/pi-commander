from __future__ import annotations
import io
import os
import sys
import socket
import threading
import urllib.parse
from datetime import date

from flask import Flask, render_template, request, jsonify, send_file, after_this_request

import cache as _cache_module
import images as _images_module
from cache import _cache_lock, _cleanup_old_cache, _get_cache_size_gb
from images import _any_to_bmp, init_fallback_bmps
from scryfall import (
    PLAYERS,
    _bmp_cache,
    _strip_cache, 
    _state_lock,
    _state_by_player,
    _require_player,
    _scryfall_get,
    _set_player_state,
    _get_all_sets,
    _get_full_set_data,
    _get_mb2_main_pool,
    _get_mb2_physical_pool,
    _get_cards_by_rarity_from_set,
    _get_cu_from_pool,
    _get_cards_by_color_from_set,
    _get_cards_by_cn_range,
    _select_random_cards_from_pool,
    _get_rare_or_mythic_card_from_set,
    _get_common_land_from_set,
)
import random
import time

_env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.isfile(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip())

DEV_PORT: str = os.environ.get("DEV_PORT", ":8000")
HOST: str = os.environ.get("HOST", "0.0.0.0")
PORT: int = int(os.environ.get("PORT", 8000))

app = Flask(__name__)

CARD_BACK_PATH = os.path.join(os.path.dirname(__file__), "cardback.jpg")
CARD_BACK_WEB_URL = "/cardback.jpg"

EXCLUDED_SET_CODES = {"pred", "h17", "phtr", "punk", "klr", "h2r"}

EXCLUDED_SET_KEYWORDS = {
    "commander",
    "jumpstart",
    "eternal",
    "timeshifts",
    "big score",
    "planechase",
    "clue",
    "jurassic",
    "heroes of the realm",
}

# ── Bonus sheet mapping ──────────────────────────────────────────────────────
#
# Maps main set code → Scryfall set code of its bonus sheet pool.
# Frontend rolls for a hit using BONUS_SHEET_RATES; on a hit, sends bonus=true
# and the backend draws from the bonus pool instead of the main set.
#
# Confidence annotations:
#   ✓ confirmed  – verified against Scryfall / mtg.wiki
#   ?            – code sourced from unreliable search summaries, needs verification
#                  against https://scryfall.com/sets or mtg.wiki/page/Bonus_sheet
#
BONUS_SHEET_MAP: dict[str, str] = {
    # ── Pre-play-booster era ────────────────────────────────────────────────
    # Time Spiral (2006) — purple-border timeshifted, extra slot
    "tsp": "tsb",    # ✓
    # Time Spiral Remastered (2021) — timeshifted old-border, replaces common
    # (user confirmed: bonus sheet is within the same set)
    "tsr": "tsr",    # ✓ user-confirmed

    # ── Play-booster / set-booster era ─────────────────────────────────────
    # Strixhaven (2021) — Mystical Archive, replaces common
    "stx": "sta",    # ✓
    # Dominaria United (2022) — Legends Retold, extra slot (set/collector only)
    "dmu": "dmc",    # ✓
    # The Brothers' War (2022) — Retro Artifacts, replaces common
    "bro": "brr",    # ✓
    # March of the Machine (2023) — Multiverse Legends, replaces common
    "mom": "mul",    # ✓
    # Wilds of Eldraine (2023) — Enchanting Tales, replaces common
    "woe": "wot",    # ✓
    # Murders at Karlov Manor (2024) — bonus dossier cards, extra slot
    "mkm": "mkc",    # ? verify: previously thought rvr, search now says mkc
    # Outlaws of Thunder Junction (2024) — Breaking News, extra slot
    "otj": "otp",    # ? verify: search returned otp and otb inconsistently
    # Modern Horizons 3 (2024) — New-to-Modern, replaces common
    "mh3": "m3c",    # ✓
    # Bloomburrow (2024) — Big Harvest, replaces common
    "blb": "blb",    # ? verify: no separate code confirmed; may be same set
    # Duskmourn: House of Horror (2024) — Featuring, extra slot
    "dsk": "spg",    # ? verify: search says spg (Special Guests umbrella code)
    # Innistrad Remastered (2025) — retro old-border reprints, replaces common
    "inr": "inr",    # ? verify: may be within same set like tsr
}
_images_module.CARD_BACK_PATH = CARD_BACK_PATH
_images_module.CARD_BACK_WEB_URL = CARD_BACK_WEB_URL
_images_module.CONFIG_PORT = DEV_PORT

_CONFIG_PROMPT_BMP, _CARD_BACK_BMP = init_fallback_bmps(CARD_BACK_PATH)


def _get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


LOCAL_IP = _get_local_ip()
_images_module.LOCAL_IP = LOCAL_IP 
app.jinja_env.globals["LOCAL_IP"] = LOCAL_IP
app.jinja_env.globals["DEV_PORT"] = DEV_PORT


# ── Static / page routes ────────────────────────────────────────────────────

@app.get("/cardback.jpg")
def serve_cardback():
    return send_file(CARD_BACK_PATH, mimetype="image/jpeg")

@app.get("/")
def root():
    return render_template("face.html", player=1)

@app.get("/face")
def face():
    return render_template("face.html", player=1)

@app.get("/player2")
def player2():
    return render_template("face.html", player=2)

@app.get("/player3")
def player3():
    return render_template("face.html", player=3)

@app.get("/player4")
def player4():
    return render_template("face.html", player=4)

@app.get("/config")
def config():
    return render_template("config.html")

@app.get("/booster")
def booster():
    return render_template("booster.html")


# ── Booster API ─────────────────────────────────────────────────────────────

@app.get("/api/booster/sets")
def api_booster_sets():
    try:
        with _cache_lock:
            _cleanup_old_cache()

        sets_data = _get_all_sets()
        today = date.today()
        eligible_sets = []

        for set_data in sets_data:
            set_type  = set_data.get("set_type", "")
            set_code  = set_data.get("code", "").lower()
            set_name  = set_data.get("name", "").lower()
            released_at = set_data.get("released_at", "")

            if released_at:
                try:
                    if date.fromisoformat(released_at) > today:
                        continue
                except ValueError:
                    pass

            if set_code in EXCLUDED_SET_CODES:
                continue

            if any(kw in set_name or kw in set_code for kw in EXCLUDED_SET_KEYWORDS):
                continue

            if (set_name.endswith("commander") or
                (set_name.startswith("commander") and any(char.isdigit() for char in set_name))):
                continue

            if set_type in ["expansion", "core", "masters", "draft_innovation",
                            "commander", "funny", "starter", "eternal"]:
                eligible_sets.append({
                    "code":        set_data.get("code"),
                    "name":        set_data.get("name"),
                    "released_at": set_data.get("released_at"),
                    "card_count":  set_data.get("card_count", 0),
                    "set_type":    set_type,
                    "icon_svg_uri": set_data.get("icon_svg_uri", ""),
                })

        return jsonify({
            "sets":          eligible_sets,
            "cache_size_gb": round(_get_cache_size_gb(), 2),
            # Expose the bonus sheet map so the frontend can build its rate table
            # against known set codes without hard-coding the backend mapping.
            "bonus_sheet_map": BONUS_SHEET_MAP,
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.post("/api/booster/single")
def api_booster_single_card():
    """Pick one card for progressive pack generation.

    Request body (JSON):
      set_code    str   required
      rarity      str   required  "common"|"uncommon"|"rare"|"mythic"|"any"|"land"|"cu"
      exclude_ids list  optional
      color       str   optional  color-bucket filter: "W"|"U"|"B"|"R"|"G"|"multi"|"colorless"|"land"
      cn_min      int   optional  collector-number lower bound (inclusive)
      cn_max      int   optional  collector-number upper bound (inclusive)
      subtype     str   optional  "mb2_main"|"mb2_frame"|"mb2_border"|"mb2_test"
                                  Controls which MB2 pool is queried.
      bonus       bool  optional  If true, draw from the bonus sheet for this set
                                  (set_code must appear in BONUS_SHEET_MAP).

    Mystery Booster 2 routing:
      subtype=mb2_main   → pool from e:plst date=2024-08-02
      subtype=mb2_frame  → pool from e:mb2, filtered by cn_min/cn_max
      subtype=mb2_border → pool from e:mb2 filtered to cn 1–121 (white-bordered)
      subtype=mb2_test   → pool from e:mb2 filtered to cn 265+
    """
    try:
        payload     = request.get_json(silent=True) or {}
        set_code    = payload.get("set_code", "").strip().lower()
        rarity      = payload.get("rarity",   "").strip().lower()
        exclude_ids = set(payload.get("exclude_ids", []))
        color       = (payload.get("color")   or "").strip()
        cn_min      = payload.get("cn_min")   # int or None
        cn_max      = payload.get("cn_max")   # int or None
        subtype     = (payload.get("subtype") or "").strip().lower()
        bonus       = bool(payload.get("bonus", False))

        if not set_code:
            return jsonify({"error": "Missing set_code"}), 400
        if not rarity:
            return jsonify({"error": "Missing rarity"}), 400

        # ── Bonus sheet redirect ───────────────────────────────────────────
        # If the caller rolled a bonus hit, swap the pool to the bonus sheet.
        # The rarity/color/cn filters still apply to the bonus pool.
        effective_set_code = set_code
        is_bonus = False
        if bonus and not subtype:
            bonus_code = BONUS_SHEET_MAP.get(set_code)
            if bonus_code:
                effective_set_code = bonus_code
                is_bonus = True
            else:
                # No bonus sheet registered — treat as normal draw
                pass

        print(f"[booster] {set_code}/{subtype or '-'} rarity={rarity}"
              f"{f' color={color}' if color else ''}"
              f"{f' cn={cn_min}-{cn_max}' if cn_min is not None else ''}"
              f"{' BONUS→' + effective_set_code if is_bonus else ''}"
              f" (excl {len(exclude_ids)})")
        t0 = time.time()

        # ── Resolve the card pool ──────────────────────────────────────────
        if subtype == "mb2_main":
            pool = _get_mb2_main_pool()
        elif subtype in ("mb2_frame", "mb2_border", "mb2_test"):
            pool = _get_mb2_physical_pool()
            if cn_min is not None and cn_max is not None:
                pool = _get_cards_by_cn_range(pool, int(cn_min), int(cn_max))
        else:
            # Standard set (or bonus sheet redirect) — use full-set cache
            pool = _get_full_set_data(effective_set_code)

        if not pool:
            return jsonify({"error": f"No cards found for set {effective_set_code} / subtype {subtype}"}), 404

        # ── Apply colour filter ────────────────────────────────────────────
        if color:
            pool = _get_cards_by_color_from_set(pool, color)
            if not pool:
                return jsonify({"error": f"No {color} cards in pool"}), 404

        # ── Rarity dispatch ────────────────────────────────────────────────
        if rarity == "rare":
            card = _get_rare_or_mythic_card_from_set(pool, exclude_ids)
            if not card:
                return jsonify({"error": "No rare/mythic cards available"}), 404

        elif rarity == "land":
            card = _get_common_land_from_set(pool, exclude_ids)
            if not card:
                return jsonify({"error": "No land cards available"}), 404

        elif rarity == "cu":
            # Commons + uncommons combined (used by MB2 colour slots)
            cu_pool = _get_cu_from_pool(pool)
            if not cu_pool:
                return jsonify({"error": "No C/U cards available"}), 404
            available = [c for c in cu_pool if c["id"] not in exclude_ids]
            if not available:
                return jsonify({"error": "No available C/U cards"}), 404
            card = random.choice(available)

        elif rarity == "any":
            available = [c for c in pool if c["id"] not in exclude_ids]
            if not available:
                return jsonify({"error": "No available cards"}), 404
            card = random.choice(available)

        else:
            # specific rarity: common / uncommon / mythic
            rarity_pool = _get_cards_by_rarity_from_set(pool, rarity)
            if not rarity_pool:
                return jsonify({"error": f"No {rarity} cards available"}), 404

            # Exclude basic lands from plain common slots (no colour filter active,
            # no subtype, and not drawing from a bonus sheet)
            if rarity == "common" and not color and not subtype and not is_bonus:
                rarity_pool = [c for c in rarity_pool if not c.get("is_common_land")]
                if not rarity_pool:
                    return jsonify({"error": "No non-land common cards available"}), 404

            sel = _select_random_cards_from_pool(rarity_pool, 1, exclude_ids)
            if not sel:
                return jsonify({"error": f"No available {rarity} cards"}), 404
            card = sel[0]

        dt = time.time() - t0
        print(f"[booster] → {card['rarity']} {card['name']} ({dt:.3f}s)")
        return jsonify({
            "card":       card,
            "from_cache": True,
            "fetch_time": round(dt, 3),
            "is_bonus":   is_bonus,
        })

    except Exception as e:
        print(f"[booster] Error: {e}", file=sys.stderr)
        return jsonify({"error": str(e)}), 500


# ── Player API ───────────────────────────────────────────────────────────────

@app.get("/api/current/<int:player>")
def api_current_player(player: int):
    _require_player(player)
    with _state_lock:
        st = _state_by_player[player]
        return jsonify({
            "player": player,
            "last_query": st["last_query"],
            "card_id": st["card_id"],
            "faces": st["faces_meta"],
            "border_crop_url": st["border_crop_url"],
            "premium": st["premium"],
            "colors": st.get("colors"),
        })

@app.post("/api/send/<int:player>")
def api_send_player(player: int):
    _require_player(player)
    payload = request.get_json(silent=True) or {}
    query   = (payload.get("q") or "").strip()
    premium = payload.get("premium") or None
    if not query:
        return jsonify({"error": "Missing JSON field 'q'"}), 400
    try:
        params = urllib.parse.urlencode({"fuzzy": query})
        card = _scryfall_get(f"https://api.scryfall.com/cards/named?{params}")
        data = _set_player_state(player, last_query=query, card=card, premium=premium)
        return jsonify({"ok": True, "player": player, "name": card.get("name"), "premium": premium, **data})
    except Exception as e:
        return jsonify({"error": str(e)}), 502

@app.post("/api/premium/<int:player>")
def api_premium_player(player: int):
    _require_player(player)
    payload = request.get_json(silent=True) or {}
    premium = payload.get("premium") or None
    with _state_lock:
        if not _state_by_player[player]["card_id"]:
            return jsonify({"error": "No card set for this player"}), 409
        _state_by_player[player]["premium"] = premium
    return jsonify({"ok": True, "player": player, "premium": premium})

@app.post("/api/search/<int:player>")
def api_search_player(player: int):
    _require_player(player)
    payload = request.get_json(silent=True) or {}
    query   = (payload.get("q") or "").strip()
    if not query:
        return jsonify({"error": "Missing JSON field 'q'"}), 400
    try:
        params = urllib.parse.urlencode({"fuzzy": query})
        card = _scryfall_get(f"https://api.scryfall.com/cards/named?{params}")
        data = _set_player_state(player, last_query=query, card=card)
        return jsonify({"ok": True, "mode": "search", "player": player, "name": card.get("name"), **data})
    except Exception as e:
        return jsonify({"error": str(e)}), 502

@app.post("/api/random/<int:player>")
def api_random_player(player: int):
    _require_player(player)
    payload = request.get_json(silent=True) or {}

    # Colors / identity (existing)
    colors = payload.get("colors") or []
    identity_match = (payload.get("identity_match") or "exact").strip().lower()

    # New generalized filters
    cmc = payload.get("cmc", None)                # int or string (e.g. 3, ">=3", "2..4")
    card_types = payload.get("card_types") or []  # list of strings, e.g. ["creature"]
    is_labels = payload.get("is") or []           # string or list; arbitrary is: labels

    # Backwards-compatibility: support the old 'mode' if present
    mode = (payload.get("mode") or "").strip().lower()

    allowed = {"W", "U", "B", "R", "G"}
    colors = [c.upper() for c in colors if isinstance(c, str) and c.upper() in allowed]

    try:
        q_parts = []

        # colors / identity -> id: or id>=
        if colors:
            colors_str = "".join(colors)
            op = "=" if identity_match == "exact" else ">="
            q_parts.append(f"id{op}{colors_str}")

        # card types -> t:token per entry (AND semantics)
        if isinstance(card_types, str) and card_types:
            card_types = [card_types]
        for ct in card_types:
            ct = (ct or "").strip()
            if not ct:
                continue
            # Allow callers to include operators/prefixes; otherwise prefix with t:
            if ct.startswith(("t:", "-t:")):
                q_parts.append(ct)
            else:
                q_parts.append(f"t:{ct}")

        # is: labels (accept string or list). If label already contains 'is:' or leading '-',
        # pass it through; otherwise prefix with 'is:'.
        if isinstance(is_labels, str) and is_labels:
            is_labels = [is_labels]
        for lb in is_labels:
            lb = (lb or "").strip()
            if not lb:
                continue
            if lb.startswith("is:") or lb.startswith("-is:") or lb.startswith("-"):
                q_parts.append(lb)
            else:
                q_parts.append(f"is:{lb}")

        # cmc handling — numeric equality preferred; strings passed through where appropriate
        if cmc is not None and cmc != "":
            # numeric (int/float) or numeric string -> cmc=X
            if isinstance(cmc, (int, float)) or (isinstance(cmc, str) and cmc.strip().lstrip("+-").replace(".", "", 1).isdigit()):
                cmc_val = int(float(cmc))
                q_parts.append(f"cmc={cmc_val}")
            else:
                cmc_str = str(cmc).strip()
                # If caller provided an operator prefix (>=, <=, >, <) or a range with '..', pass through
                if cmc_str.startswith((">", "<", "=", "!")) or ".." in cmc_str:
                    # ensure it has 'cmc' prefix if not already
                    if cmc_str.startswith("cmc"):
                        q_parts.append(cmc_str)
                    else:
                        q_parts.append(f"cmc{cmc_str}")
                else:
                    # default to equality if ambiguous
                    q_parts.append(f"cmc={cmc_str}")

        # Back-compat: if 'mode' provided, map to previous query fragments
        # (kept for existing clients; frontend should prefer the new fields)
        if mode:
            if mode == "commander":
                q_parts.append("is:commander")
            elif mode == "creature":
                q_parts.append("t:creature")
            else:
                # previous default: legendary creature for non-commander modes
                q_parts.append("t:legendary")
                q_parts.append("t:creature")

        # If the caller provided an explicit raw query (q) — append it verbatim.
        raw_q = (payload.get("q") or "").strip()
        if raw_q:
            q_parts.append(raw_q)

        if not q_parts:
            # If nothing is specified, fall back to the previous default behavior:
            # select a commander by default (same as before).
            q_parts.append("is:commander")

        # quote each q value but preserve '=' and ':' characters so they appear as in your working URL
        params = "+".join(urllib.parse.quote(p, safe="=:") for p in q_parts)
        url = "https://api.scryfall.com/cards/random?q=" + params

        card = _scryfall_get(url)
        data = _set_player_state(player, last_query=url, card=card)
        return jsonify({"ok": True, "mode": "random", "player": player, "query": url, "name": card.get("name"), **data})
    except Exception as e:
        return jsonify({"error": str(e)}), 502


# Backward-compatible player-1 aliases
@app.get("/api/current")
def api_current_compat():
    return api_current_player(1)

@app.post("/api/search")
def api_search_compat():
    return api_search_player(1)

@app.post("/api/random")
def api_random_compat():
    return api_random_player(1)


# ── BMP routes ───────────────────────────────────────────────────────────────

def _get_bmp_for_player(player: int, face: str) -> bytes:
    with _state_lock:
        bmp = _bmp_cache.get((player, face))
    return bmp if bmp is not None else _CONFIG_PROMPT_BMP

@app.get("/bmp/<int:player>/front")
def bmp_player_front(player: int):
    _require_player(player)
    return send_file(io.BytesIO(_get_bmp_for_player(player, "front")),
                     mimetype="image/bmp", as_attachment=True,
                     download_name=f"player{player}_front.bmp")

@app.get("/bmp/<int:player>/back")
def bmp_player_back(player: int):
    _require_player(player)
    return send_file(io.BytesIO(_get_bmp_for_player(player, "back")),
                     mimetype="image/bmp", as_attachment=True,
                     download_name=f"player{player}_back.bmp")

@app.get("/bmp/all")
def bmp_all():
    return jsonify({"files": [
        {"player": p, "face": f, "url": f"/bmp/{p}/{f}"}
        for p in PLAYERS for f in ("front", "back")
    ]})


# ── OTA routes ───────────────────────────────────────────────────────────────

import subprocess
import hashlib

# Files in pico/ that should NOT be sent to the device
_OTA_SKIP = {"secrets.py.example", "setup.sh", "requirements.txt"}

@app.get("/ota/manifest")
def ota_manifest():
    """Return {files: {filename: sha256, ...}} for every deployable pico/ file."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pico_dir  = os.path.join(repo_root, "pico")
    files = {}
    for name in os.listdir(pico_dir):
        if name in _OTA_SKIP or not name.endswith(".py"):
            continue
        path = os.path.join(pico_dir, name)
        if not os.path.isfile(path):
            continue
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                h.update(chunk)
        files[name] = h.hexdigest()
    return jsonify({"files": files})


@app.get("/ota/file/<filename>")
def ota_file(filename):
    """Serve a single file from pico/ by name."""
    if filename in _OTA_SKIP or "/" in filename or "\\" in filename:
        return jsonify({"error": "not allowed"}), 403
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(repo_root, "pico", filename)
    if not os.path.isfile(path):
        return jsonify({"error": "not found"}), 404
    return send_file(path, mimetype="text/plain")


# Keep the old single-file OTA routes for backwards compat (can delete later)
@app.get("/ota/version")
def ota_version():
    try:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sha = subprocess.check_output(
            ["git", "log", "-1", "--format=%H", "pico/main.py"],
            cwd=repo_root, text=True).strip()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"sha": sha})

@app.get("/ota/main.py")
def ota_main():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return send_file(os.path.join(repo_root, "pico", "main.py"), mimetype="text/plain")

# --- QR business

@app.get("/api/config-url")
def api_config_url():
    return jsonify({"url": _images_module.config_url()})

# --- Pico business

from images import _any_to_strips, init_fallback_strips

# in startup, alongside the existing BMP fallbacks:
_CONFIG_PROMPT_STRIPS, _CARD_BACK_STRIPS = init_fallback_strips(CARD_BACK_PATH)

def _get_strips_for_player(player: int, face: str) -> list[bytes]:
    with _state_lock:
        strips = _strip_cache.get((player, face))
    return strips if strips is not None else _CONFIG_PROMPT_STRIPS


@app.get("/img/<int:player>/<face>/raw")
def img_strip(player: int, face: str):
    """Serve one 320×160 RGB565 strip (byte-swapped).
    ?strip=0  → rows   0-159
    ?strip=1  → rows 160-319
    ?strip=2  → rows 320-479
    Content-Type: application/octet-stream
    Content-Length is always 320*160*2 = 102400 bytes.
    """
    _require_player(player)
    if face not in ("front", "back"):
        return jsonify({"error": "face must be front or back"}), 400
    try:
        strip_idx = int(request.args.get("strip", 0))
    except ValueError:
        return jsonify({"error": "strip must be 0, 1 or 2"}), 400
    if strip_idx not in (0, 1, 2):
        return jsonify({"error": "strip must be 0, 1 or 2"}), 400

    strips = _get_strips_for_player(player, face)
    return strips[strip_idx], 200, {
        "Content-Type": "application/octet-stream",
        "Content-Length": str(len(strips[strip_idx])),
    }


@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response

@app.get("/strip-viewer")
def strip_viewer():
    return send_file(os.path.join(os.path.dirname(os.path.dirname(__file__)), "pico", "strip_viewer.html"))

# ── Startup ───────────────────────────────────────────────────────────────────

def _print_endpoints(host: str, port: int) -> None:
    ip = LOCAL_IP
    base = f"http://{ip}{DEV_PORT}"
    lines = [
        "",
        "  pi-commander running — endpoints:",
        "",
        "  Browser",
        f"    {base}/",
        f"    {base}/face",
        f"    {base}/player2",
        f"    {base}/player3",
        f"    {base}/player4",
        f"    {base}/config",
        f"    {base}/booster",
        f"    {base}/cardback.jpg",
        "",
        "  API",
        f"    GET  {base}/api/current/<player>",
        f"    POST {base}/api/search/<player>",
        f"    POST {base}/api/random/<player>",
        f"    POST {base}/api/premium/<player>",
        f"    POST {base}/api/send/<player>",
        f"    GET  {base}/api/config-url",
        f"    POST {base}/api/shutdown",
        "",
        "  Booster",
        f"    GET  {base}/api/booster/sets",
        f"    POST {base}/api/booster/single",
        "",
        "  BMP",
        f"    GET  {base}/bmp/all",
        f"    GET  {base}/bmp/1/front   {base}/bmp/1/back",
        f"    GET  {base}/bmp/2/front   {base}/bmp/2/back",
        f"    GET  {base}/bmp/3/front   {base}/bmp/3/back",
        f"    GET  {base}/bmp/4/front   {base}/bmp/4/back",
        "",
        "  OTA",
        f"    GET  {base}/ota/version",
        f"    GET  {base}/ota/main.py",
        "",
    ]
    print("\n".join(lines))

    # QR code for /config in the console
    try:
        import qrcode
        config_url = _images_module.config_url()
        qr = qrcode.QRCode(border=1)
        qr.add_data(config_url)
        qr.make(fit=True)
        print(f"  Scan to open /config on your phone ({config_url}):")
        qr.print_ascii(invert=True)
        print()
    except ImportError:
        print(f"  /config → {base}/config  (install 'qrcode' for ASCII QR in console)\n")

if __name__ == "__main__":
    with _cache_lock:
        _cleanup_old_cache()
    _print_endpoints(HOST, PORT)
    app.run(host=HOST, port=PORT, debug=False)