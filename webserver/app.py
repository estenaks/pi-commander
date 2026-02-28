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
    "border_crop_url": None,
}

def _scryfall_get(url: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            # Scryfall asks for a descriptive UA
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
        # Scryfall often returns JSON error bodies; surface them
        body = ""
        try:
            body = e.read().decode("utf-8")
        except Exception:
            body = ""
        raise RuntimeError(f"Scryfall HTTP {e.code}: {body or e.reason}") from e
    except Exception as e:
        raise RuntimeError(f"Scryfall request failed: {type(e).__name__}: {e}") from e

def _extract_border_crop(card: dict) -> str:
    # Normal single-faced cards
    border_crop = (card.get("image_uris") or {}).get("border_crop")
    if border_crop:
        return border_crop

    # Double-faced / modal, etc.
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

        border_crop = _extract_border_crop(card)
        if not border_crop:
            return jsonify({"error": "No border_crop image found for this card"}), 422

        with _state_lock:
            _state["last_query"] = query
            _state["border_crop_url"] = border_crop

        return jsonify({
            "ok": True,
            "mode": "search",
            "name": card.get("name"),
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
            # "Exactly these colors" in Scryfall search syntax is expressed with:
            #   id=<colors> and NOT id> <colors>
            # However, the most reliable way in practice is using `id=<colors>` with an explicit comparator.
            #
            # Scryfall supports color identity operators; we use `id=` for exact identity match.
            # Example: id=ub means exactly UB (not UBR, not U).
            colors_str = "".join(colors)

            if identity_match == "exact":
                q_parts.append(f"id={colors_str}")
            else:
                # fallback: at least includes those colors (superset)
                q_parts.append(f"id>={colors_str}")

        q = " ".join(q_parts)
        url = "https://api.scryfall.com/cards/random?" + urllib.parse.urlencode({"q": q})
        card = _scryfall_get(url)

        border_crop = _extract_border_crop(card)
        if not border_crop:
            return jsonify({"error": "No border_crop image found for this random card"}), 422

        with _state_lock:
            _state["last_query"] = q
            _state["border_crop_url"] = border_crop

        return jsonify({
            "ok": True,
            "mode": "random",
            "query": q,
            "name": card.get("name"),
            "border_crop_url": border_crop,
            "scryfall_uri": card.get("scryfall_uri"),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 502

if __name__ == "__main__":
    # For lan access on port 80 (as you configured via systemd reverse proxy / port 8000 etc.)
    app.run(host="0.0.0.0", port=8000, debug=False)