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
    _get_cards_by_rarity_from_set,
    _get_cards_by_color_from_set,
    _get_cards_by_frame_from_set,
    _get_cards_by_border_from_set,
    _select_random_cards_from_pool,
    _get_rare_or_mythic_card_from_set,
    _get_common_land_from_set,
)
import random
import time

# ---- Load .env (if present) ----------------------------------------
_env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.isfile(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip())

# ---- Config from environment ---------------------------------------
DEV_PORT: str = os.environ.get("DEV_PORT", ":8000")
HOST: str = os.environ.get("HOST", "0.0.0.0")
PORT: int = int(os.environ.get("PORT", 8000))
# --------------------------------------------------------------------

app = Flask(__name__)

CARD_BACK_PATH = os.path.join(os.path.dirname(__file__), "cardback.jpg")
CARD_BACK_WEB_URL = "/cardback.jpg"

EXCLUDED_SET_CODES = {
    # "pred",
    # "h17",
    # "phtr",
    # "punk",
    # "klr",
    # "h2r"
}

# Wire path/URL constants into submodules
_images_module.CARD_BACK_PATH = CARD_BACK_PATH
_images_module.CARD_BACK_WEB_URL = CARD_BACK_WEB_URL
_images_module.CONFIG_PORT = DEV_PORT

# Pre-generate fallback BMPs at startup
_CONFIG_PROMPT_BMP, _CARD_BACK_BMP = init_fallback_bmps(CARD_BACK_PATH)


def _get_local_ip() -> str:
    """Return the LAN IP of this machine, e.g. 192.168.1.42"""
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


# ---- Routes ----

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


# ---- Booster API endpoints ----

@app.get("/api/booster/sets")
def api_booster_sets():
    """Get all available Magic sets for booster generation."""
    try:
        with _cache_lock:
            _cleanup_old_cache()

        sets_data = _get_all_sets()

        today = date.today()
        eligible_sets = []
        for set_data in sets_data:
            set_type = set_data.get("set_type", "")
            set_code = set_data.get("code", "").lower()
            set_name = set_data.get("name", "").lower()
            released_at = set_data.get("released_at", "")

            # Skip sets not yet released
            if released_at:
                try:
                    if date.fromisoformat(released_at) > today:
                        continue
                except ValueError:
                    pass  # malformed date — allow through

            if set_code in EXCLUDED_SET_CODES:
                continue

            if (set_name.endswith("commander") or
                (set_name.startswith("commander") and any(char.isdigit() for char in set_name)) or
                " commander" in set_name or
                "jumpstart" in set_name or
                "eternal" in set_name or
                "timeshifts" in set_name or
                "big score" in set_name or
                "planechase" in set_name or
                "clue" in set_name or
                "jurassic" in set_name or
                "heroes of the realm" in set_name):
                continue

            if set_type in ["expansion", "core", "masters", "draft_innovation", "commander", "funny", "starter", "eternal"]:
                eligible_sets.append({
                    "code": set_data.get("code"),
                    "name": set_data.get("name"),
                    "released_at": set_data.get("released_at"),
                    "card_count": set_data.get("card_count", 0),
                    "set_type": set_type,
                    "icon_svg_uri": set_data.get("icon_svg_uri", ""),
                })

        return jsonify({
            "sets": eligible_sets,
            "cache_size_gb": round(_get_cache_size_gb(), 2),
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.post("/api/booster/single")
def api_booster_single_card():
    """Get a single random card for progressive pack generation.

    Request body (JSON):
      set_code    str   required  — Scryfall set code, e.g. "mb1", "mb2", "mkm"
      rarity      str   required  — "common" | "uncommon" | "rare" | "mythic"
                                    | "any" | "land"
      exclude_ids list  optional  — card IDs already used in this pack
      color       str   optional  — colour-bucket filter for Mystery Booster
                                    colour slots: "W"|"U"|"B"|"R"|"G"|
                                    "multi"|"colorless"|"land"
      frame       str   optional  — Scryfall frame filter, e.g. "future"
                                    (used for MB2 Future Sight slot)
      border      str   optional  — Scryfall border_color filter, e.g. "white"
                                    (used for MB2 white-border slot)
    """
    try:
        payload     = request.get_json(silent=True) or {}
        set_code    = payload.get("set_code",    "").strip().lower()
        rarity      = payload.get("rarity",      "").strip().lower()
        exclude_ids = set(payload.get("exclude_ids", []))
        color       = (payload.get("color")  or "").strip().upper()   # "W","U","B","R","G","multi","colorless","land"
        frame       = (payload.get("frame")  or "").strip().lower()   # "future", ""
        border      = (payload.get("border") or "").strip().lower()   # "white", ""

        if not set_code:
            return jsonify({"error": "Missing set_code parameter"}), 400
        if not rarity:
            return jsonify({"error": "Missing rarity parameter"}), 400

        print(f"[booster] Fetching {rarity} card for {set_code}"
              f"{f' color={color}' if color else ''}"
              f"{f' frame={frame}' if frame else ''}"
              f"{f' border={border}' if border else ''}"
              f" (excluding {len(exclude_ids)} cards)")
        start_time = time.time()

        set_cards = _get_full_set_data(set_code)
        if not set_cards:
            return jsonify({"error": f"No cards found for set {set_code}"}), 404

        # ── Apply optional filters before rarity dispatch ────────────────────
        # Each filter narrows the pool; filters stack (AND semantics).

        working_pool = set_cards

        if color:
            # "land" colour bucket keeps all rarities (MB1 land slot has no
            # rarity constraint), so we skip the later rarity filter entirely.
            working_pool = _get_cards_by_color_from_set(working_pool, color.lower())
            if not working_pool:
                return jsonify({"error": f"No {color} cards found for set {set_code}"}), 404

        if frame:
            working_pool = _get_cards_by_frame_from_set(working_pool, frame)
            if not working_pool:
                return jsonify({"error": f"No frame={frame} cards found for set {set_code}"}), 404

        if border:
            working_pool = _get_cards_by_border_from_set(working_pool, border)
            if not working_pool:
                return jsonify({"error": f"No border={border} cards found for set {set_code}"}), 404

        # ── Rarity dispatch (operates on whatever pool filters left us) ──────

        if rarity == "rare":
            card = _get_rare_or_mythic_card_from_set(working_pool, exclude_ids)
            if not card:
                return jsonify({"error": f"No available rare/mythic cards for set {set_code}"}), 404

        elif rarity == "land":
            card = _get_common_land_from_set(working_pool, exclude_ids)
            if not card:
                return jsonify({"error": f"No available land cards for set {set_code}"}), 404

        elif rarity == "any":
            # "any" — pick from the full (optionally-filtered) working pool
            available = [c for c in working_pool if c["id"] not in exclude_ids]
            if not available:
                return jsonify({"error": f"No available cards for set {set_code}"}), 404
            card = random.choice(available)

        else:
            # Specific rarity: common / uncommon / mythic
            rarity_pool = _get_cards_by_rarity_from_set(working_pool, rarity)
            if not rarity_pool:
                return jsonify({"error": f"No {rarity} cards found for set {set_code}"}), 404

            if rarity == "common" and not color and not frame and not border:
                # Standard common slot — exclude basic lands (they have their
                # own slot). Skip this filter when a colour bucket was requested
                # because the MB land slot intentionally wants lands.
                rarity_pool = [c for c in rarity_pool if not c.get("is_common_land")]
                if not rarity_pool:
                    return jsonify({"error": f"No non-land common cards found for set {set_code}"}), 404

            selected_cards = _select_random_cards_from_pool(rarity_pool, 1, exclude_ids)
            if not selected_cards:
                return jsonify({"error": f"No available {rarity} cards for set {set_code}"}), 404
            card = selected_cards[0]

        fetch_time = time.time() - start_time
        print(f"[booster] Selected {card['rarity']} card in {fetch_time:.3f}s: {card['name']}")
        return jsonify({"card": card, "from_cache": True, "fetch_time": round(fetch_time, 3)})

    except Exception as e:
        print(f"[booster] Error fetching single card: {e}", file=sys.stderr)
        return jsonify({"error": str(e)}), 500


# ---- API (per-player) ----

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
    """Send a card to a player, with optional premium tag."""
    _require_player(player)
    payload = request.get_json(silent=True) or {}
    query = (payload.get("q") or "").strip()
    premium = payload.get("premium") or None
    if not query:
        return jsonify({"error": "Missing JSON field 'q'"}), 400

    try:
        params = urllib.parse.urlencode({"fuzzy": query})
        url = f"https://api.scryfall.com/cards/named?{params}"
        card = _scryfall_get(url)
        data = _set_player_state(player, last_query=query, card=card, premium=premium)
        return jsonify({"ok": True, "player": player, "name": card.get("name"), "premium": premium, **data})
    except Exception as e:
        return jsonify({"error": str(e)}), 502

@app.post("/api/premium/<int:player>")
def api_premium_player(player: int):
    """Set or clear the premium (foil) tag for a player's current card."""
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
    query = (payload.get("q") or "").strip()
    if not query:
        return jsonify({"error": "Missing JSON field 'q'"}), 400

    try:
        params = urllib.parse.urlencode({"fuzzy": query})
        url = f"https://api.scryfall.com/cards/named?{params}"
        card = _scryfall_get(url)
        data = _set_player_state(player, last_query=query, card=card)
        return jsonify({"ok": True, "mode": "search", "player": player, "name": card.get("name"), **data})
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.post("/api/random/<int:player>")
def api_random_player(player: int):
    _require_player(player)
    payload = request.get_json(silent=True) or {}
    colors = payload.get("colors") or []
    identity_match = (payload.get("identity_match") or "exact").strip().lower()
    mode = (payload.get("mode") or "commander").strip().lower()

    allowed = {"w", "u", "b", "r", "g"}
    colors = [c.lower() for c in colors if isinstance(c, str)]
    colors = [c for c in colors if c in allowed]

    try:
        q_parts = []

        if mode == "commander":
            q_parts.append("is:commander")
        else:
            q_parts.append("t:legendary")
            q_parts.append("t:creature")

        if colors:
            colors_str = "".join(colors)
            if identity_match == "exact":
                q_parts.append(f"id={colors_str}")
            else:
                q_parts.append(f"id>={colors_str}")

        q = " ".join(q_parts)
        url = "https://api.scryfall.com/cards/random?" + urllib.parse.urlencode({"q": q})
        card = _scryfall_get(url)
        data = _set_player_state(player, last_query=q, card=card)
        return jsonify({"ok": True, "mode": "random", "player": player, "query": q, "name": card.get("name"), **data})
    except Exception as e:
        return jsonify({"error": str(e)}), 502


# Backward-compatible endpoints (player 1)
@app.get("/api/current")
def api_current_compat():
    return api_current_player(1)


@app.post("/api/search")
def api_search_compat():
    return api_search_player(1)


@app.post("/api/random")
def api_random_compat():
    return api_random_player(1)


# ---- BMP endpoints ----

def _get_bmp_for_player(player: int, face: str) -> bytes:
    """Return cached BMP for *player*/*face*, or config-prompt placeholder if not set."""
    with _state_lock:
        bmp = _bmp_cache.get((player, face))
    if bmp is not None:
        return bmp
    return _CONFIG_PROMPT_BMP


@app.get("/bmp/<int:player>/front")
def bmp_player_front(player: int):
    _require_player(player)
    data = _get_bmp_for_player(player, "front")
    return send_file(
        io.BytesIO(data),
        mimetype="image/bmp",
        as_attachment=True,
        download_name=f"player{player}_front.bmp",
    )


@app.get("/bmp/<int:player>/back")
def bmp_player_back(player: int):
    _require_player(player)
    data = _get_bmp_for_player(player, "back")
    return send_file(
        io.BytesIO(data),
        mimetype="image/bmp",
        as_attachment=True,
        download_name=f"player{player}_back.bmp",
    )


@app.get("/bmp/all")
def bmp_all():
    files = [
        {"player": p, "face": face, "url": f"/bmp/{p}/{face}"}
        for p in PLAYERS
        for face in ("front", "back")
    ]
    return jsonify({"files": files})

# ---- OTA routes ----

import subprocess
import hashlib

@app.get("/ota/version")
def ota_version():
    """Return the current git SHA of pico/main.py so the Pico can detect updates."""
    try:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sha = subprocess.check_output(
            ["git", "log", "-1", "--format=%H", "pico/main.py"],
            cwd=repo_root,
            text=True,
        ).strip()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"sha": sha})


@app.get("/ota/main.py")
def ota_main():
    """Serve the latest pico/main.py for the Pico to download."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(repo_root, "pico", "main.py")
    return send_file(path, mimetype="text/plain")


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
        f"    GET  {base}/api/booster/sets",
        f"    POST {base}/api/booster/single",
        f"         optional filters: color, frame, border",
        f"    POST {base}/api/send/<player>",
        "",
        "  BMP",
        f"    GET  {base}/bmp/all",
        f"    GET  {base}/bmp/1/front   {base}/bmp/1/back",
        f"    GET  {base}/bmp/2/front   {base}/bmp/2/back",
        f"    GET  {base}/bmp/3/front   {base}/bmp/3/back",
        f"    GET  {base}/bmp/4/front   {base}/bmp/4/back",
        "",
    ]
    print("\n".join(lines))

    try:
        import qrcode
        face_url = f"{base}/face"
        qr = qrcode.QRCode(border=1)
        qr.add_data(face_url)
        qr.make(fit=True)
        print(f"  Scan to open /face on your phone ({face_url}):")
        qr.print_ascii(invert=True)
        print()
    except ImportError:
        print(f"  /face → {base}/face  (install 'qrcode' for ASCII QR in console)\n")


if __name__ == "__main__":
    with _cache_lock:
        _cleanup_old_cache()

    _print_endpoints(HOST, PORT)
    app.run(host=HOST, port=PORT, debug=False)