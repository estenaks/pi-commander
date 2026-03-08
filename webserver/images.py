from __future__ import annotations
import io
import sys
import struct
import urllib.request

import numpy as np
import qrcode
from PIL import Image, ImageDraw, ImageFont

# These are set by app.py after import so images.py doesn't need to know the path itself
CARD_BACK_PATH: str = ""
CARD_BACK_WEB_URL: str = "/cardback.jpg"
CONFIG_PORT: str = ""
LOCAL_IP: str = "127.0.0.1"

# Strip height must match the Pico framebuffer height
STRIP_H = 160
DISPLAY_W = 320
DISPLAY_H = 480

# ---------------------------------------------------------------------------
# Palette — the 16 named colours from test_display.py, in RGB888.
# ---------------------------------------------------------------------------
_PALETTE_RGB = [
    (  0,   0,   0),   # BLACK
    (255, 255, 255),   # WHITE
    (255,   0,   0),   # RED
    (  0, 255,   0),   # GREEN
    (  0,   0, 255),   # BLUE
    (255, 255,   0),   # YELLOW
    (  0, 255, 255),   # CYAN
    (255,   0, 255),   # MAGENTA
    (255, 165,   0),   # ORANGE
    (128, 128, 128),   # GREY
    ( 64,  64,  64),   # DGREY
    (192, 192, 192),   # LGREY
    (  0,   0, 128),   # NAVY
    (  0, 128, 128),   # TEAL
    (128,   0, 128),   # PURPLE
    (  0, 128,   0),   # LIME
]

_PALETTE_NP = np.array(_PALETTE_RGB, dtype=np.int32)  # (16, 3)

def _bs(c: int) -> int:
    return ((c & 0xFF) << 8) | (c >> 8)

def _rgb565(r: int, g: int, b: int) -> int:
    return _bs(((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3))

_PALETTE_WORDS: list[bytes] = []
for _r, _g, _b in _PALETTE_RGB:
    _w = _rgb565(_r, _g, _b)
    _PALETTE_WORDS.append(bytes([_w & 0xFF, (_w >> 8) & 0xFF]))

# numpy LUT: shape (16, 2) — palette index → [lo, hi] framebuffer bytes
_PALETTE_LUT = np.array([[w[0], w[1]] for w in _PALETTE_WORDS], dtype=np.uint8)


# ---------------------------------------------------------------------------
# Dithering — precise Floyd-Steinberg, pixel-by-pixel, 4-neighbour.
# Direct port of floyd_steinberg_precise() from epd_commander.py.
# Returns palette indices (H, W) uint8 instead of a PIL image.
# ---------------------------------------------------------------------------

def _floyd_steinberg_precise(img: Image.Image) -> np.ndarray:
    arr     = np.array(img.convert("RGB"), dtype=np.int32)
    h, w    = arr.shape[:2]
    indices = np.zeros((h, w), dtype=np.uint8)

    for y in range(h):
        for x in range(w):
            old  = arr[y, x].copy()
            diff = _PALETTE_NP - old
            idx  = (diff ** 2).sum(axis=1).argmin()
            new  = _PALETTE_NP[idx]
            arr[y, x]     = new
            indices[y, x] = idx
            err = old - new
            if x + 1 < w:
                arr[y,     x + 1] += err * 7 // 16
            if y + 1 < h:
                if x > 0:
                    arr[y + 1, x - 1] += err * 3 // 16
                arr[y + 1, x    ]     += err * 5 // 16
                if x + 1 < w:
                    arr[y + 1, x + 1] += err * 1 // 16
        arr[y] = np.clip(arr[y], 0, 255)

    return indices


def _indices_to_strips(indices: np.ndarray) -> list[bytes]:
    """Convert a (DISPLAY_H, DISPLAY_W) palette indices array to encoded strips."""
    pixels_2b = _PALETTE_LUT[indices]
    strips = []
    for s in range(DISPLAY_H // STRIP_H):
        strip = pixels_2b[s * STRIP_H:(s + 1) * STRIP_H]
        strips.append(bytes(strip.reshape(-1)))
    return strips


def config_url() -> str:
    """Single source of truth for the /config URL used in all QR codes."""
    return f"http://{LOCAL_IP}{CONFIG_PORT}/config"


def _fit_image(data: bytes) -> Image.Image:
    """Open, resize and centre-crop/pad to DISPLAY_W × DISPLAY_H RGB."""
    img = Image.open(io.BytesIO(data))
    orig_w, orig_h = img.size
    new_h = DISPLAY_H
    new_w = max(1, round(orig_w * new_h / orig_h))
    img = img.resize((new_w, new_h), Image.LANCZOS)
    if new_w > DISPLAY_W:
        left = (new_w - DISPLAY_W) // 2
        img = img.crop((left, 0, left + DISPLAY_W, DISPLAY_H))
    elif new_w < DISPLAY_W:
        padded = Image.new("RGB", (DISPLAY_W, DISPLAY_H), (0, 0, 0))
        padded.paste(img, ((DISPLAY_W - new_w) // 2, 0))
        img = padded
    return img.convert("RGB")


def _image_to_bmp(data: bytes) -> bytes:
    img = _fit_image(data)
    buf = io.BytesIO()
    img.save(buf, format="BMP")
    return buf.getvalue()


def _image_to_strips(data: bytes) -> list[bytes]:
    """Precise Floyd-Steinberg dither to 16-colour Pico palette, encode as strips."""
    img = _fit_image(data)
    return _indices_to_strips(_floyd_steinberg_precise(img))


def _url_to_bmp(url: str) -> bytes:
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
    with open(path, "rb") as f:
        return _image_to_bmp(f.read())


def _any_to_bmp(url_or_path: str) -> bytes:
    if url_or_path == CARD_BACK_WEB_URL:
        return _file_to_bmp(CARD_BACK_PATH)
    if url_or_path.startswith("http://") or url_or_path.startswith("https://"):
        return _url_to_bmp(url_or_path)
    return _file_to_bmp(url_or_path)


def _any_to_strips(url_or_path: str) -> list[bytes]:
    if url_or_path == CARD_BACK_WEB_URL:
        with open(CARD_BACK_PATH, "rb") as f:
            data = f.read()
    elif url_or_path.startswith("http://") or url_or_path.startswith("https://"):
        req = urllib.request.Request(
            url_or_path,
            headers={"User-Agent": "raspberrypi-webserver-poc/0.1", "Accept": "image/*"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
    else:
        with open(url_or_path, "rb") as f:
            data = f.read()
    return _image_to_strips(data)


def _make_config_prompt_image() -> Image.Image:
    """Generate a 320×480 config prompt, correctly oriented for the Pico display."""
    url = config_url()

    qr = qrcode.QRCode(border=2)
    qr.add_data(url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="white", back_color="black").convert("RGB")

    qr_size = 240
    qr_img = qr_img.resize((qr_size, qr_size), Image.NEAREST)

    img = Image.new("RGB", (DISPLAY_W, DISPLAY_H), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    qr_x = (DISPLAY_W - qr_size) // 2
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
            draw.text(((DISPLAY_W - text_w) // 2, y), line, fill=(255, 255, 255), font=font)
        y += 28

    return img  # 320×480, no rotation — correct for Pico strips


def _make_config_prompt_bmp() -> bytes:
    """BMP path rotates 90° for the legacy BMP pipeline."""
    img = _make_config_prompt_image()
    img = img.rotate(90, expand=True)
    buf = io.BytesIO()
    img.save(buf, format="BMP")
    return buf.getvalue()


def _make_config_prompt_strips() -> list[bytes]:
    """Generate config prompt as dithered palette strips — no rotation, direct render."""
    img = _make_config_prompt_image()
    return _indices_to_strips(_floyd_steinberg_precise(img))


def init_fallback_bmps(card_back_path: str) -> tuple[bytes, bytes | None]:
    config_prompt_bmp = _make_config_prompt_bmp()

    card_back_bmp = None
    try:
        card_back_bmp = _file_to_bmp(card_back_path)
    except Exception as exc:
        print(f"[bmp] Warning: could not load cardback.jpg: {exc}", file=sys.stderr)

    return config_prompt_bmp, card_back_bmp


def init_fallback_strips(card_back_path: str) -> tuple[list[bytes], list[bytes] | None]:
    config_prompt_strips = _make_config_prompt_strips()

    card_back_strips = None
    try:
        card_back_strips = _any_to_strips(card_back_path)
    except Exception as exc:
        print(f"[strips] Warning: could not load cardback.jpg: {exc}", file=sys.stderr)

    return config_prompt_strips, card_back_strips