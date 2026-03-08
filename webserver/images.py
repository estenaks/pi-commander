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
# These are the ONLY colours the Pico will ever receive, so we know exactly
# how each one must be encoded.
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

def _bs(c: int) -> int:
    """Byte-swap a 16-bit value — matches bs() in test_display.py."""
    return ((c & 0xFF) << 8) | (c >> 8)

def _rgb565(r: int, g: int, b: int) -> int:
    """Pack RGB888 → RGB565 word, then byte-swap — matches rgb() in test_display.py."""
    return _bs(((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3))

# Pre-compute the 2-byte little-endian framebuffer word for each palette entry.
# Index i → bytes to write into lcd.buffer for that colour.
_PALETTE_WORDS: list[bytes] = []
for _r, _g, _b in _PALETTE_RGB:
    _w = _rgb565(_r, _g, _b)
    _PALETTE_WORDS.append(bytes([_w & 0xFF, (_w >> 8) & 0xFF]))

# Build a PIL palette image (needed for quantize())
_PIL_PALETTE_IMG = Image.new("P", (1, 1))
_flat = []
for _r, _g, _b in _PALETTE_RGB:
    _flat += [_r, _g, _b]
# PIL palette must be 768 bytes (256 × 3); pad with zeros
_flat += [0] * (768 - len(_flat))
_PIL_PALETTE_IMG.putpalette(_flat)


def config_url() -> str:
    """Single source of truth for the /config URL used in all QR codes."""
    return f"http://{LOCAL_IP}{CONFIG_PORT}/config"


def _quantize_to_palette(img: Image.Image) -> Image.Image:
    """Floyd-Steinberg dither img down to _PALETTE_RGB using PIL's quantize()."""
    img = img.convert("RGB")
    # quantize() with a palette image uses the supplied palette and dithers with F-S
    return img.quantize(palette=_PIL_PALETTE_IMG, dither=Image.Dither.FLOYDSTEINBERG)


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
    """Dither image to the 16-colour Pico palette, then encode as BGR565 strips.

    Each strip is DISPLAY_W * STRIP_H * 2 bytes.
    Every pixel is one of the 16 known-good palette entries, encoded with the
    exact same bs(rgb(r,g,b)) formula used in test_display.py — so colours are
    guaranteed to match what the test demo produces.
    """
    img = _fit_image(data)
    quantized = _quantize_to_palette(img)  # palette-indexed, Floyd-Steinberg dithered

    # Convert palette indices to the pre-computed 2-byte framebuffer words using numpy
    indices = np.frombuffer(quantized.tobytes(), dtype=np.uint8)  # DISPLAY_W * DISPLAY_H values

    # Build lookup: index → [lo, hi]
    lut = np.zeros((256, 2), dtype=np.uint8)
    for i, word_bytes in enumerate(_PALETTE_WORDS):
        lut[i, 0] = word_bytes[0]
        lut[i, 1] = word_bytes[1]

    # Map every pixel index to its 2-byte word
    pixels_2b = lut[indices]  # shape: (DISPLAY_W * DISPLAY_H, 2)

    strips = []
    num_strips = DISPLAY_H // STRIP_H
    strip_px = DISPLAY_W * STRIP_H
    for s in range(num_strips):
        strip_data = pixels_2b[s * strip_px:(s + 1) * strip_px]
        strips.append(bytes(strip_data.flatten()))

    return strips


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


def _any_to_strips(url_or_path: str) -> list[bytes]:
    """Convert either a remote URL or local path to dithered palette strips."""
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


def _make_config_prompt_bmp() -> bytes:
    """Generate a 320x480 placeholder BMP with a QR code and text
    telling the user to visit /config."""
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

    img = img.rotate(90, expand=True)
    buf = io.BytesIO()
    img.save(buf, format="BMP")
    return buf.getvalue()


def _make_config_prompt_strips() -> list[bytes]:
    """Generate config prompt as dithered palette strips."""
    bmp = _make_config_prompt_bmp()
    return _image_to_strips(bmp)


def init_fallback_bmps(card_back_path: str) -> tuple[bytes, bytes | None]:
    """Pre-generate fallback BMPs at startup. Returns (config_prompt_bmp, card_back_bmp)."""
    config_prompt_bmp = _make_config_prompt_bmp()

    card_back_bmp = None
    try:
        card_back_bmp = _file_to_bmp(card_back_path)
    except Exception as exc:
        print(f"[bmp] Warning: could not load cardback.jpg: {exc}", file=sys.stderr)

    return config_prompt_bmp, card_back_bmp


def init_fallback_strips(card_back_path: str) -> tuple[list[bytes], list[bytes] | None]:
    """Pre-generate fallback strip lists at startup."""
    config_prompt_strips = _make_config_prompt_strips()

    card_back_strips = None
    try:
        card_back_strips = _any_to_strips(card_back_path)
    except Exception as exc:
        print(f"[strips] Warning: could not load cardback.jpg: {exc}", file=sys.stderr)

    return config_prompt_strips, card_back_strips