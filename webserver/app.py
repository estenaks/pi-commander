from flask import Flask, render_template, request, jsonify, send_file
import urllib.request
import urllib.parse
import json
import threading
import urllib.error
import io
import os
import sys
import qrcode

from PIL import Image, ImageDraw, ImageFont

app = Flask(__name__)

CARD_BACK_PATH = os.path.join(os.path.dirname(__file__), "cardback.jpg")
CARD_BACK_WEB_URL = "/cardback.jpg"   # served to browser / used in faces_meta
# Set CONFIG_PORT to ":8000" if not using nginx to forward port 80, otherwise leave blank
CONFIG_PORT = ""

PLAYERS = [1, 2, 3, 4]

_state_lock = threading.Lock()
_state_by_player = {
    p: {
        "last_query": None,
        "card_id": None,
        # always length 2 when set:
        # [{"image_url": "...", "type_line": "..."}, {"image_url": "...", "type_line": "..."}]
        "faces_meta": [],
        # backward compat for older previews:
        "border_crop_url": None,
    }
    for p in PLAYERS
}

# BMP cache: keyed by (player, face) where face is "front" or "back"
# Protected by _state_lock
_bmp_cache: dict[tuple[int, str], bytes] = {}


# ---- Image helpers ----

def _image_to_bmp(data: bytes) -> bytes:
    img = Image.open(io.BytesIO(data))
    orig_w, orig_h = img.size
    new_h = 480
    new_w = max(1, round(orig_w * new_h / orig_h))
    img = img.resize((new_w, new_h), Image.LANCZOS)
    if new_w > 320:
        left = (new_w - 320) // 2
        img = img.crop((left, 0, left + 320, 480))
    elif new_w < 320:
        padded = Image.new("RGB", (320, 480), (0, 0, 0))
        padded.paste(img, ((320 - new_w) // 2, 0))
        img = padded
    img = img.convert("RGB")
    img = img.rotate(90, expand=True)  # → 480×320 landscape for the Pico display
    buf = io.BytesIO()
    img.save(buf, format="BMP")
    return buf.getvalue()


def _url_to_bmp(url: str) -> bytes:
    """Fetch a remote image and convert to BMP."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "raspberrypi-webserver-poc/0.1",
            "Accept": "image/*",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = resp.read()
    return _image_to_bmp(data)


def _file_to_bmp(path: str) -> bytes:
    """Read a local image file and convert to BMP."""
    with open(path, "rb") as f:
        return _image_to_bmp(f.read())


def _any_to_bmp(url_or_path: str) -> bytes:
    """Convert either a remote URL or the local card-back sentinel to BMP."""
    if url_or_path == CARD_BACK_WEB_URL:
        return _file_to_bmp(CARD_BACK_PATH)
    if url_or_path.startswith("http://") or url_or_path.startswith("https://"):
        return _url_to_bmp(url_or_path)
    return _file_to_bmp(url_or_path)


def _make_config_prompt_bmp() -> bytes:
    """Generate a 320×480 placeholder BMP with a QR code and text
    telling the user to visit /config."""
    import qrcode

    config_url = f"http://raspberrypi.local{CONFIG_PORT}/config"

    qr = qrcode.QRCode(border=2)
    qr.add_data(config_url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="white", back_color="black").convert("RGB")

    qr_size = 240
    qr_img = qr_img.resize((qr_size, qr_size), Image.NEAREST)

    img = Image.new("RGB", (320, 480), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    qr_x = (320 - qr_size) // 2
    qr_y = 60
    img.paste(qr_img, (qr_x, qr_y))

    font_large = ImageFont.load_default(size=20)
    font_small = ImageFont.load_default(size=16)
    lines = [
        ("No card configured.", font_large),
        ("", None),
        ("Scan or visit /config", font_small),
        ("then reboot the Pico.", font_small),
    ]
    y = qr_y + qr_size + 16
    for line, font in lines:
        if line and font:
            bbox = draw.textbbox((0, 0), line, font=font)
            text_w = bbox[2] - bbox[0]
            draw.text(((320 - text_w) // 2, y), line, fill=(255, 255, 255), font=font)
        y += 28

    img = img.rotate(90, expand=True)  # → 480×320 landscape, same as card BMPs
    buf = io.BytesIO()
    img.save(buf, format="BMP")
    return buf.getvalue()


# Pre-generate fallback BMPs at startup
_CONFIG_PROMPT_BMP: bytes = _make_config_prompt_bmp()

_CARD_BACK_BMP: bytes | None = None
try:
    _CARD_BACK_BMP = _file_to_bmp(CARD_BACK_PATH)
except Exception as _exc:
    print(f"[bmp] Warning: could not load cardback.jpg: {_exc}", file=sys.stderr)


# ---- State helpers ----

def _require_player(player: int) -> int:
    if player not in _state_by_player:
        raise ValueError("Invalid player. Must be 1..4.")
    return player


def _scryfall_get(url: str) -> dict:
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
            return json.loads(data)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")
        except Exception:
            body = ""
        raise RuntimeError(f"Scryfall HTTP {e.code}: {body or e.reason}") from e
    except Exception as e:
        raise RuntimeError(f"Scryfall request failed: {type(e).__name__}: {e}") from e


def _pick_image_border_crop_only(iu: dict) -> str:
    if not isinstance(iu, dict):
        return ""
    return (
        iu.get("border_crop")
        or iu.get("normal")
        or iu.get("large")
        or iu.get("png")
        or iu.get("art_crop")  # last resort
        or ""
    )


def _extract_faces_meta_always_two(card: dict) -> list[dict]:
    # DFC / modal etc
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

    # single-faced — use local card back as back face
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


def _generate_bmps(player: int) -> None:
    """Generate and cache BMP images for both faces of *player*'s current card.

    Called outside _state_lock — reads state under lock, does network I/O,
    then writes cache under lock. All errors are caught so they never crash
    the server.
    """
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
                # Only cache if the player's card hasn't changed during conversion
                if _state_by_player[player]["card_id"] == card_id:
                    _bmp_cache[(player, face)] = bmp_bytes
        except Exception as exc:
            print(f"[bmp] Error generating {face} BMP for player {player}: {exc}", file=sys.stderr)


def _set_player_state(player: int, *, last_query: str, card: dict) -> dict:
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

        result = {
            "last_query": st["last_query"],
            "card_id": st["card_id"],
            "faces": st["faces_meta"],
            "border_crop_url": st["border_crop_url"],
        }

    # BMP generation after state is set (outside lock) so /face etc. are unaffected
    _generate_bmps(player)
    return result


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
        })


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
    return _CONFIG_PROMPT_BMP  # no card set for this player — always safe, never raises




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

def _print_endpoints(host: str, port: int) -> None:
    base = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}"
    lines = [
        "",
        "  pi-commander running — endpoints:",
        "",
        f"  Browser",
        f"    {base}/",
        f"    {base}/face",
        f"    {base}/player2",
        f"    {base}/player3",
        f"    {base}/player4",
        f"    {base}/config",
        f"    {base}/cardback.jpg",
        "",
        f"  API",
        f"    GET  {base}/api/current/<player>",
        f"    POST {base}/api/search/<player>",
        f"    POST {base}/api/random/<player>",
        "",
        f"  BMP",
        f"    GET  {base}/bmp/all",
        f"    GET  {base}/bmp/1/front   {base}/bmp/1/back",
        f"    GET  {base}/bmp/2/front   {base}/bmp/2/back",
        f"    GET  {base}/bmp/3/front   {base}/bmp/3/back",
        f"    GET  {base}/bmp/4/front   {base}/bmp/4/back",
        "",
    ]
    print("\n".join(lines))


if __name__ == "__main__":
    _print_endpoints("0.0.0.0", 8000)
    app.run(host="0.0.0.0", port=8000, debug=False)