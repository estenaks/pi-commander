from __future__ import annotations
import io
import os
import sys
import socket
import threading
import urllib.parse
from datetime import date

from flask import Flask, render_template, request, jsonify, send_file

import cache as _cache_module
import images as _images_module
from cache import _cache_lock, _cleanup_old_cache, _get_cache_size_gb
from images import _any_to_bmp, init_fallback_bmps
from scryfall import (
    PLAYERS,
    _bmp_cache,
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

            if (set_name.endswith("commander") or
                (set_name.startswith("commander") and any(ch.isdigit() for ch in set_name)) or
                " commander" in set_name or
                "jumpstart" in set_name):
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
        color       = (payload.get("color")   or "").strip().lower()
        cn_min      = payload.get("cn_min")   # int or None
        cn_max      = payload.get("cn_max")   # int or None
        subtype     = (payload.get("subtype") or "").strip().lower()

        if not set_code:
            return jsonify({"error": "Missing set_code"}), 400
        if not rarity:
            return jsonify({"error": "Missing rarity"}), 400

        print(f"[booster] {set_code}/{subtype or '-'} rarity={rarity}"
              f"{f' color={color}' if color else ''}"
              f"{f' cn={cn_min}-{cn_max}' if cn_min is not None else ''}"
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
            # Standard set — use existing full-set cache
            pool = _get_full_set_data(set_code)

        if not pool:
            return jsonify({"error": f"No cards found for set {set_code} / subtype {subtype}"}), 404

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

            # Exclude basic lands from plain common slots (no colour filter active)
            if rarity == "common" and not color and not subtype:
                rarity_pool = [c for c in rarity_pool if not c.get("is_common_land")]
                if not rarity_pool:
                    return jsonify({"error": "No non-land common cards available"}), 404

            sel = _select_random_cards_from_pool(rarity_pool, 1, exclude_ids)
            if not sel:
                return jsonify({"error": f"No available {rarity} cards"}), 404
            card = sel[0]

        dt = time.time() - t0
        print(f"[booster] → {card['rarity']} {card['name']} ({dt:.3f}s)")
        return jsonify({"card": card, "from_cache": True, "fetch_time": round(dt, 3)})

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
    colors  = payload.get("colors") or []
    identity_match = (payload.get("identity_match") or "exact").strip().lower()
    mode    = (payload.get("mode") or "commander").strip().lower()

    allowed = {"w", "u", "b", "r", "g"}
    colors = [c.lower() for c in colors if isinstance(c, str) and c.lower() in allowed]

    try:
        q_parts = []
        if mode == "commander":
            q_parts.append("is:commander")
        else:
            q_parts.append("t:legendary t:creature")

        if colors:
            colors_str = "".join(colors)
            q_parts.append(f"id={'=' if identity_match == 'exact' else '>='}{colors_str}")

        q = " ".join(q_parts)
        url = "https://api.scryfall.com/cards/random?" + urllib.parse.urlencode({"q": q})
        card = _scryfall_get(url)
        data = _set_player_state(player, last_query=q, card=card)
        return jsonify({"ok": True, "mode": "random", "player": player, "query": q, "name": card.get("name"), **data})
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


# ── Startup ───────────────────────────────────────────────────────────────────

def _print_endpoints(host: str, port: int) -> None:
    base = f"http://{LOCAL_IP}{DEV_PORT}"
    print(f"""
  pi-commander running
  {base}/booster   ← booster simulator
  {base}/          ← player 1
  POST {base}/api/booster/single
""")

if __name__ == "__main__":
    with _cache_lock:
        _cleanup_old_cache()
    _print_endpoints(HOST, PORT)
    app.run(host=HOST, port=PORT, debug=False)