from flask import Flask, render_template, request, jsonify
import urllib.request
import urllib.parse
import json
import threading
import urllib.error

app = Flask(__name__)

CARD_BACK_URL = "https://files.mtg.wiki/Magic_card_back.jpg"

# Simple in-memory shared state (POC)
_state_lock = threading.Lock()
_state = {
    "last_query": None,
    "card_id": None,
    "faces": [],  # always length 2 when a card is loaded
    # Backward compat for older frontends:
    "border_crop_url": None,
}

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
    """
    Always use border_crop (never art_crop). Fallbacks are only used if border_crop is missing.
    """
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

def _extract_faces_always_two(card: dict) -> list[str]:
    """
    Returns exactly two URLs:
      - DFC: [face0_border_crop, face1_border_crop]
      - single-faced: [front_border_crop, CARD_BACK_URL]
    """
    faces = card.get("card_faces") or []
    if isinstance(faces, list) and len(faces) >= 2:
        iu0 = (faces[0] or {}).get("image_uris") or {}
        iu1 = (faces[1] or {}).get("image_uris") or {}

        u0 = _pick_image_border_crop_only(iu0)
        u1 = _pick_image_border_crop_only(iu1)

        if u0:
            if not u1:
                u1 = u0
            return [u0, u1]

    # single-faced
    iu = card.get("image_uris") or {}
    front = _pick_image_border_crop_only(iu)
    if not front:
        return []
    return [front, CARD_BACK_URL]

def _extract_border_crop(card: dict) -> str:
    # Old behavior retained (border_crop of first available face)
    border_crop = (card.get("image_uris") or {}).get("border_crop")
    if border_crop:
        return border_crop

    faces = card.get("card_faces") or []
    for face in faces:
        bc = (face.get("image_uris") or {}).get("border_crop")
        if bc:
            return bc

    return ""

@app.get("/")
def index():
    return render_template("index.html")

@app.get("/config")
def config():
    return render_template("config.html")

@app.get("/face")
def face():
    return render_template("face.html")

@app.get("/api/current")
def api_current():
    with _state_lock:
        return jsonify({
            "last_query": _state["last_query"],
            "card_id": _state["card_id"],
            "faces": _state["faces"],  # ALWAYS two when present
            # backward compat:
            "border_crop_url": _state["border_crop_url"],
        })

@app.post("/api/search")
def api_search():
    payload = request.get_json(silent=True) or {}
    query = (payload.get("q") or "").strip()
    if not query:
        return jsonify({"error": "Missing JSON field 'q'"}), 400

    try:
        params = urllib.parse.urlencode({"fuzzy": query})
        url = f"https://api.scryfall.com/cards/named?{params}"
        card = _scryfall_get(url)

        faces = _extract_faces_always_two(card)
        if not faces:
            return jsonify({"error": "No suitable image found for this card"}), 422

        border_crop = _extract_border_crop(card) or faces[0]

        with _state_lock:
            _state["last_query"] = query
            _state["card_id"] = card.get("id")
            _state["faces"] = faces
            _state["border_crop_url"] = border_crop

        return jsonify({
            "ok": True,
            "mode": "search",
            "name": card.get("name"),
            "card_id": card.get("id"),
            "faces": faces,
            "border_crop_url": border_crop,
            "scryfall_uri": card.get("scryfall_uri"),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 502

@app.post("/api/random")
def api_random():
    payload = request.get_json(silent=True) or {}
    colors = payload.get("colors") or []
    identity_match = (payload.get("identity_match") or "exact").strip().lower()

    allowed = {"w", "u", "b", "r", "g"}
    colors = [c.lower() for c in colors if isinstance(c, str)]
    colors = [c for c in colors if c in allowed]

    try:
        q_parts = ["t:legendary", "t:creature"]

        if colors:
            colors_str = "".join(colors)
            if identity_match == "exact":
                q_parts.append(f"id={colors_str}")
            else:
                q_parts.append(f"id>={colors_str}")

        q = " ".join(q_parts)
        url = "https://api.scryfall.com/cards/random?" + urllib.parse.urlencode({"q": q})
        card = _scryfall_get(url)

        faces = _extract_faces_always_two(card)
        if not faces:
            return jsonify({"error": "No suitable image found for this random card"}), 422

        border_crop = _extract_border_crop(card) or faces[0]

        with _state_lock:
            _state["last_query"] = q
            _state["card_id"] = card.get("id")
            _state["faces"] = faces
            _state["border_crop_url"] = border_crop

        return jsonify({
            "ok": True,
            "mode": "random",
            "query": q,
            "name": card.get("name"),
            "card_id": card.get("id"),
            "faces": faces,
            "border_crop_url": border_crop,
            "scryfall_uri": card.get("scryfall_uri"),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 502

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)