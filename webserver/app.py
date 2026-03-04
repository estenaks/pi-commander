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
import time
import random
from datetime import datetime, timedelta

from PIL import Image, ImageDraw, ImageFont

app = Flask(__name__)

CARD_BACK_PATH = os.path.join(os.path.dirname(__file__), "cardback.jpg")
CARD_BACK_WEB_URL = "/cardback.jpg"   # served to browser / used in faces_meta
# Set CONFIG_PORT to ":8000" if not using nginx to forward port 80, otherwise leave blank
CONFIG_PORT = ""

PLAYERS = [1, 2, 3, 4]

# Cache configuration
CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
MAX_CACHE_SIZE_GB = 20
CACHE_EXPIRY_DAYS = 365
SCRYFALL_REQUEST_DELAY = 0.050  # 50ms delay between requests
# Set exclusion list - sets to exclude from booster generation
EXCLUDED_SET_CODES = {
    # Manually excluded sets (add more as needed)
    "pred",
    "h17",
    "phtr"
    # Commander sets are excluded by name filter below
}

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

# Scryfall cache: keyed by cache filename
_cache_lock = threading.Lock()

def _ensure_cache_dir():
    """Ensure the cache directory exists."""
    os.makedirs(CACHE_DIR, exist_ok=True)

def _get_cache_path(cache_key: str) -> str:
    """Get the full path for a cache file."""
    return os.path.join(CACHE_DIR, f"{cache_key}.json")

def _get_cache_size_gb() -> float:
    """Get current cache size in GB."""
    if not os.path.exists(CACHE_DIR):
        return 0.0
    
    total_size = 0
    for filename in os.listdir(CACHE_DIR):
        file_path = os.path.join(CACHE_DIR, filename)
        if os.path.isfile(file_path):
            total_size += os.path.getsize(file_path)
    
    return total_size / (1024 ** 3)  # Convert to GB

def _cleanup_old_cache():
    """Remove cache files older than CACHE_EXPIRY_DAYS or if cache is too large."""
    if not os.path.exists(CACHE_DIR):
        return
    
    now = datetime.now()
    files_info = []
    
    # Get info about all cache files
    for filename in os.listdir(CACHE_DIR):
        if not filename.endswith('.json'):
            continue
            
        file_path = os.path.join(CACHE_DIR, filename)
        if os.path.isfile(file_path):
            mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
            size = os.path.getsize(file_path)
            files_info.append((file_path, mtime, size))
    
    # Remove files older than expiry
    for file_path, mtime, _ in files_info:
        if now - mtime > timedelta(days=CACHE_EXPIRY_DAYS):
            try:
                os.remove(file_path)
                print(f"[cache] Removed expired cache file: {os.path.basename(file_path)}")
            except Exception as e:
                print(f"[cache] Failed to remove expired file {file_path}: {e}")
    
    # If still too large, remove oldest files
    current_size = _get_cache_size_gb()
    if current_size > MAX_CACHE_SIZE_GB:
        # Sort by modification time (oldest first)
        files_info.sort(key=lambda x: x[1])
        
        for file_path, _, size in files_info:
            if current_size <= MAX_CACHE_SIZE_GB:
                break
                
            try:
                os.remove(file_path)
                current_size -= size / (1024 ** 3)
                print(f"[cache] Removed old cache file to free space: {os.path.basename(file_path)}")
            except Exception as e:
                print(f"[cache] Failed to remove file {file_path}: {e}")

def _get_cached_data(cache_key: str, cache_hours: int = 24) -> dict | None:
    """Get data from cache if it exists and is not expired."""
    cache_path = _get_cache_path(cache_key)
    
    if not os.path.exists(cache_path):
        return None
    
    try:
        # Never expire set data (they're permanent)
        if cache_key.startswith("set_") and cache_key.endswith("_full"):
            with open(cache_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        
        # Check if file is too old based on cache_hours parameter
        mtime = datetime.fromtimestamp(os.path.getmtime(cache_path))
        if datetime.now() - mtime > timedelta(hours=cache_hours):
            os.remove(cache_path)
            print(f"[cache] Removed expired cache file: {cache_key} (older than {cache_hours} hours)")
            return None
        
        with open(cache_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"[cache] Error reading cache file {cache_key}: {e}")
        return None

def _set_cached_data(cache_key: str, data: dict) -> None:
    """Save data to cache."""
    _ensure_cache_dir()
    cache_path = _get_cache_path(cache_key)
    
    try:
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        print(f"[cache] Cached data for key: {cache_key}")
    except Exception as e:
        print(f"[cache] Error writing cache file {cache_key}: {e}")


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


def _scryfall_get(url: str, use_cache: bool = False, cache_key: str = None, cache_hours: int = 24) -> dict:
    """Make a GET request to Scryfall with optional caching."""
    
    # Check cache first if enabled
    if use_cache and cache_key:
        with _cache_lock:
            cached_data = _get_cached_data(cache_key, cache_hours)
            if cached_data is not None:
                print(f"[cache] Using cached data for: {cache_key}")
                return cached_data
    
    # Add rate limiting delay
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
            
        # Cache the result if caching is enabled
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


# ---- Booster pack helpers ----

def _get_all_sets() -> list[dict]:
    """Get all Magic sets from Scryfall with 1-day caching."""
    return _scryfall_get(
        "https://api.scryfall.com/sets",
        use_cache=True,
        cache_key="all_sets",
        cache_hours=24  # Cache sets list for only 1 day
    ).get("data", [])


def _get_full_set_data(set_code: str) -> list[dict]:
    """Get all cards from a set, with permanent caching."""
    cache_key = f"set_{set_code}_full"
    
    # Check cache first (never expires for sets)
    with _cache_lock:
        cached_set = _get_cached_data(cache_key, cache_hours=24 * CACHE_EXPIRY_DAYS)
        if cached_set is not None:
            # print(f"[booster] ✅ CACHE HIT: Using cached set data for {set_code} ({len(cached_set)} cards)")
            return cached_set
    
    print(f"[booster] 📦 DOWNLOADING: Fetching full set data for {set_code}...")
    all_cards = []
    page = 1
    
    while True:
        try:
            # Fetch cards page by page
            url = f"https://api.scryfall.com/cards/search?q=set:{set_code}&page={page}"
            print(f"[booster] Fetching page {page} for set {set_code}...")
            
            # Add delay for rate limiting
            time.sleep(SCRYFALL_REQUEST_DELAY)
            
            response = _scryfall_get(url, use_cache=False)  # Don't cache individual pages
            
            cards_data = response.get("data", [])
            if not cards_data:
                break
                
            # Process and add cards from this page
            for card in cards_data:
                rarity = card.get("rarity", "")
                if rarity not in ["common", "uncommon", "rare", "mythic"]:
                    continue

                type_line = card.get("type_line", "")

                # Single-faced card
                if card.get("image_uris"):
                    front_url = _pick_image_border_crop_only(card.get("image_uris", {}))
                    back_url = None  # use cardback.jpg on frontend
                # Double-faced card — images live on card_faces
                elif card.get("card_faces") and len(card["card_faces"]) >= 2:
                    f0 = card["card_faces"][0]
                    f1 = card["card_faces"][1]
                    front_url = _pick_image_border_crop_only(f0.get("image_uris") or {})
                    back_url = _pick_image_border_crop_only(f1.get("image_uris") or {})
                else:
                    continue  # no usable image

                if not front_url:
                    continue

                processed_card = {
                    "id": card.get("id"),
                    "name": card.get("name", "Unknown"),
                    "image_url": front_url,
                    "back_image_url": back_url,  # None for single-faced, URL for DFC
                    "rarity": rarity,
                    "set": set_code,
                    "mana_cost": card.get("mana_cost", ""),
                    "type_line": type_line,
                    "is_common_land": (rarity == "common" and "Land" in type_line)
                }
                all_cards.append(processed_card)
            
            # Check if there are more pages
            if not response.get("has_more", False):
                break
                
            page += 1
            
        except Exception as e:
            print(f"[booster] Error fetching page {page} for set {set_code}: {e}")
            if page == 1:
                # If we can't even get the first page, return empty
                return []
            break
    
    print(f"[booster] ✅ DOWNLOADED: Set {set_code} complete ({len(all_cards)} cards)")
    
    # Cache the complete set permanently
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
    
    # Filter out already used cards
    available_cards = [card for card in card_pool if card["id"] not in exclude_ids]
    
    if len(available_cards) < count:
        print(f"[booster] Warning: Only {len(available_cards)} cards available, requested {count}")
        return available_cards
    
    # Randomly select without replacement
    selected = random.sample(available_cards, count)
    
    # Mark as used
    for card in selected:
        exclude_ids.add(card["id"])
    
    return selected

def _has_mythic_rares(set_cards: list[dict]) -> bool:
    """Check if a set has any mythic rare cards."""
    return any(card.get("rarity") == "mythic" for card in set_cards)


def _get_rare_or_mythic_card_from_set(set_cards: list[dict], exclude_ids: set = None) -> dict | None:
    """Get a rare card with 1/8 chance of being mythic, from cached set data."""
    if exclude_ids is None:
        exclude_ids = set()
    
    # Check if set has mythics
    has_mythics = _has_mythic_rares(set_cards)
    
    # 1/8 chance for mythic (12.5%) - but only if set has mythics
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
    
    # Fall back to rare
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
    
    # Get all common lands (includes both basic and non-basic)
    common_lands = [card for card in set_cards 
                   if card.get("rarity") == "common" and 
                      "Land" in card.get("type_line", "")]
    
    # Filter out excluded cards
    available_lands = [card for card in common_lands if card["id"] not in exclude_ids]
    
    if not available_lands:
        print(f"[booster] No available common lands found")
        return None
    
    # Just pick a random common land
    selected = random.choice(available_lands)
    exclude_ids.add(selected["id"])
    print(f"[booster] Selected common land: {selected['name']}")
    return selected

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
            _cleanup_old_cache()  # Clean up old cache files periodically
            
        sets_data = _get_all_sets()
        
        # Filter to sets that are likely to have cards available for random generation
        # Focus on regular expansion sets and avoid things like promos, tokens, etc.
        eligible_sets = []
        for set_data in sets_data:
            set_type = set_data.get("set_type", "")
            set_code = set_data.get("code", "").lower()
            set_name = set_data.get("name", "").lower()
            
            # Skip excluded sets
            if set_code in EXCLUDED_SET_CODES:
                continue
                
            # Skip commander precon sets (but allow commander booster sets like "Commander Legends")
            # and skip jumpstart sets
            if (set_name.endswith("commander") or 
                (set_name.startswith("commander") and any(char.isdigit() for char in set_name)) or
                " commander" in set_name or
                "jumpstart" in set_name or
                "etarnal" in set_name):
                continue
            
            if set_type in ["expansion", "core", "masters", "draft_innovation", "commander", "funny", "starter", "eternal"]:
                eligible_sets.append({
                    "code": set_data.get("code"),
                    "name": set_data.get("name"),
                    "released_at": set_data.get("released_at"),
                    "card_count": set_data.get("card_count", 0),
                    "set_type": set_type,
                    "icon_svg_uri": set_data.get("icon_svg_uri", "")  # Add icon
                })
        
        return jsonify({
            "sets": eligible_sets,
            "cache_size_gb": round(_get_cache_size_gb(), 2)
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.post("/api/booster/single")
def api_booster_single_card():
    """Get a single random card for progressive pack generation."""
    try:
        payload = request.get_json(silent=True) or {}
        set_code = payload.get("set_code", "").strip().lower()
        rarity = payload.get("rarity", "").strip().lower()
        exclude_ids = set(payload.get("exclude_ids", []))  # Cards already in this pack
        
        if not set_code:
            return jsonify({"error": "Missing set_code parameter"}), 400
        if not rarity:
            return jsonify({"error": "Missing rarity parameter"}), 400
        
        print(f"[booster] Fetching single {rarity} card for {set_code} (excluding {len(exclude_ids)} cards)")
        start_time = time.time()
        
        # Get the complete cached set data
        set_cards = _get_full_set_data(set_code)
        
        if not set_cards:
            return jsonify({"error": f"No cards found for set {set_code}"}), 404
        
        # Handle rare/mythic logic
        if rarity == "rare":
            card = _get_rare_or_mythic_card_from_set(set_cards, exclude_ids)
            if not card:
                return jsonify({"error": f"No available rare or mythic cards for set {set_code}"}), 404
                
            fetch_time = time.time() - start_time
            print(f"[booster] Selected {card['rarity']} card in {fetch_time:.3f}s: {card['name']}")
            
            return jsonify({
                "card": card,
                "from_cache": True,  # Now everything comes from cache
                "fetch_time": round(fetch_time, 3)
            })
        
        # Handle basic land logic
        if rarity == "land":
            card = _get_common_land_from_set(set_cards, exclude_ids)
            if not card:
                return jsonify({"error": f"No available land cards for set {set_code}"}), 404
                
            fetch_time = time.time() - start_time
            print(f"[booster] Selected land card in {fetch_time:.3f}s: {card['name']}")
            
            return jsonify({
                "card": card,
                "from_cache": True,
                "fetch_time": round(fetch_time, 3)
            })

        # Handle "any" rarity for premium slot — no rarity restriction
        if rarity == "any":
            available = [card for card in set_cards if card["id"] not in exclude_ids]
            if not available:
                return jsonify({"error": f"No available cards for set {set_code}"}), 404

            card = random.choice(available)
            fetch_time = time.time() - start_time
            print(f"[booster] Selected premium card in {fetch_time:.3f}s: {card['name']} ({card['rarity']})")

            return jsonify({
                "card": card,
                "from_cache": True,
                "fetch_time": round(fetch_time, 3)
            })
        
        # Handle common/uncommon
        rarity_pool = _get_cards_by_rarity_from_set(set_cards, rarity)
        if not rarity_pool:
            return jsonify({"error": f"No {rarity} cards found for set {set_code}"}), 404

        # For common slots, exclude common lands — those belong in the land slot only
        if rarity == "common":
            rarity_pool = [card for card in rarity_pool if not card.get("is_common_land")]
            if not rarity_pool:
                return jsonify({"error": f"No non-land common cards found for set {set_code}"}), 404

        selected_cards = _select_random_cards_from_pool(rarity_pool, 1, exclude_ids)
        
        if not selected_cards:
            return jsonify({"error": f"No available {rarity} cards for set {set_code}"}), 404
        
        card = selected_cards[0]
        fetch_time = time.time() - start_time
        print(f"[booster] Selected {rarity} card in {fetch_time:.3f}s: {card['name']}")
        
        return jsonify({
            "card": card,
            "from_cache": True,  # Everything comes from cache now
            "fetch_time": round(fetch_time, 3)
        })
        
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
        f"    {base}/booster",
        f"    {base}/cardback.jpg",
        "",
        f"  API",
        f"    GET  {base}/api/current/<player>",
        f"    POST {base}/api/search/<player>",
        f"    POST {base}/api/random/<player>",
        f"    GET  {base}/api/booster/sets",
        f"    POST {base}/api/booster/single",
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
    # Clean up cache on startup
    with _cache_lock:
        _cleanup_old_cache()
    
    _print_endpoints("0.0.0.0", 8000)
    app.run(host="0.0.0.0", port=8000, debug=False)