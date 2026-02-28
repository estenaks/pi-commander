from flask import Flask, render_template, request, jsonify
import urllib.request
import urllib.parse
import json
import threading
import urllib.error

app = Flask(__name__)

# Simple in-memory shared state (POC)
_state_lock = threading.Lock()
_state = {
    "last_query": None,
    "card_id": None,
    "faces": [],  # list of image urls (len 1 for single-faced, len 2 for DFC)
    # Backward-compat / existing consumer:
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

def _extract_faces(card: dict) -> list[str]:
    """
    Returns 1 or 2 image URLs.
    - Single-faced: prefer art_crop (per your request), fall back to border_crop.
    - Multi-faced: use each face's border_crop (fall back to art_crop/normal if needed).
    """
    faces = card.get("card_faces") or []
    out: list[str] = []

    # Multi-faced cards (transform/MDFC/etc.)
    if isinstance(faces, list) and len(faces) >= 2:
        for face in faces[:2]:
            iu = face.get("image_uris") or {}
            url = (
                iu.get("border_crop")
                or iu.get("art_crop")
                or iu.get("normal")
                or iu.get("large")
                or iu.get("png")
            )
            if url:
                out.append(url)

        # Only accept if we actually found at least one
        if out:
            # Ensure length 2 for flipping convenience (duplicate if only one found)
            if len(out) == 1:
                out.append(out[0])
            return out

    # Single-faced cards
    iu = card.get("image_uris") or {}
    url = (
        iu.get("art_crop")
        or iu.get("border_crop")
        or iu.get("normal")
        or iu.get("large")
        or iu.get("png")
    )
    if url:
        return [url]

    return []

def _extract_border_crop(card: dict) -> str:
    """
    Old behavior retained (border_crop of first available face).
    """
    iu = card.get("image_uris") or {}
    border_crop = iu.get("border_crop")
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
            "faces": _state["faces"],
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

        faces = _extract_faces(card)
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

        faces = _extract_faces(card)
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