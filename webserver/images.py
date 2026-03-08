from __future__ import annotations
import io
import sys
import urllib.request

import numpy as np
import qrcode
from PIL import Image, ImageDraw, ImageFont

# These are set by app.py after import so images.py doesn't need to know the path itself
CARD_BACK_PATH: str = ""
CARD_BACK_WEB_URL: str = "/cardback.jpg"
CONFIG_PORT: str = ""
LOCAL_IP: str = "127.0.0.1"

STRIP_H   = 160
DISPLAY_W = 320
DISPLAY_H = 480

# ---------------------------------------------------------------------------
# Palette — 128 colours sampled uniformly from the RGB565 gamut.
#
# RGB565: 5-bit R (0-31), 6-bit G (0-63), 5-bit B (0-31).
# We take every other representable value on each axis:
#   R: 4 levels  →  r5 in {0, 10, 21, 31}  →  r8 in {0, 82, 173, 255}
#   G: 8 levels  →  g6 in {0, 9, 18, 27, 36, 45, 54, 63}  →  g8 proportional
#   B: 4 levels  →  same as R
# 4 × 8 × 4 = 128 entries, evenly distributed, green-biased to match RGB565.
#
# Each entry is stored in two forms:
#   _PALETTE_NP   — (128, 3) int32 RGB888, for nearest-colour distance maths
#   _PALETTE_LUT  — (128, 2) uint8 [lo, hi] framebuffer bytes ready to send
# ---------------------------------------------------------------------------

def _bs(c: int) -> int:
    return ((c & 0xFF) << 8) | (c >> 8)

def _rgb565_word(r: int, g: int, b: int) -> int:
    """RGB888 → byte-swapped RGB565 word, matching bs(rgb()) in test_display.py."""
    return _bs(((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3))

# Build the 128-colour palette
_r_steps = [round(v * 255 / 31) for v in (0, 10, 21, 31)]          # 4 R
_g_steps = [round(v * 255 / 63) for v in (0, 9, 18, 27, 36, 45, 54, 63)]  # 8 G
_b_steps = [round(v * 255 / 31) for v in (0, 10, 21, 31)]          # 4 B

_palette_rgb: list[tuple[int, int, int]] = [
    (r, g, b)
    for r in _r_steps
    for g in _g_steps
    for b in _b_steps
]  # 4 × 8 × 4 = 128 entries

_PALETTE_NP  = np.array(_palette_rgb, dtype=np.int32)               # (128, 3)
_PALETTE_LUT = np.array(
    [(_rgb565_word(r, g, b) & 0xFF, (_rgb565_word(r, g, b) >> 8) & 0xFF)
     for r, g, b in _palette_rgb],
    dtype=np.uint8
)  # (128, 2)


# ---------------------------------------------------------------------------
# Dithering — fast Floyd-Steinberg, row-vectorized nearest-colour snap.
# Ported from epd_commander.py floyd_steinberg_fast, adapted for 128 colours.
# One nearest-colour search per row (vectorized) instead of per pixel.
# Returns palette indices array (H, W) uint8.
# ---------------------------------------------------------------------------

def _nearest_row(row: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Snap one row (W, 3) to nearest palette colour.
    Returns (snapped (W,3), indices (W,)) — both in one vectorized pass.
    """
    # (W, 1, 3) - (1, 128, 3) → (W, 128, 3)
    diff = row[:, np.newaxis, :] - _PALETTE_NP[np.newaxis, :, :]
    dist = (diff ** 2).sum(axis=2)   # (W, 128)
    idx  = dist.argmin(axis=1)       # (W,)
    return _PALETTE_NP[idx], idx


def _floyd_steinberg_fast(img: Image.Image) -> np.ndarray:
    """Fast Floyd-Steinberg dither to the 128-colour RGB565 palette.

    Nearest-colour snap is vectorized per row — same approach as
    epd_commander.py floyd_steinberg_fast but with a much richer palette,
    so quantisation error is small and quality is good without full
    pixel-by-pixel 4-neighbour diffusion.

    Returns palette indices (DISPLAY_H, DISPLAY_W), dtype uint8.
    """
    arr     = np.array(img.convert("RGB"), dtype=np.int32)
    h, w    = arr.shape[:2]
    indices = np.zeros((h, w), dtype=np.uint8)

    for y in range(h):
        new_row, ix = _nearest_row(arr[y])
        err          = arr[y] - new_row          # (W, 3)
        arr[y]       = new_row
        indices[y]   = ix

        if y + 1 < h:
            arr[y + 1,  1:  ] += err[:-1] * 7 // 16   # right
            arr[y + 1,   :  ] += err       * 5 // 16   # below
            arr[y + 1, :-1  ] += err[ 1:] * 3 // 16   # below-left
            arr[y + 1]         = np.clip(arr[y + 1], 0, 255)

    return indices


def _indices_to_strips(indices: np.ndarray) -> list[bytes]:
    pixels_2b = _PALETTE_LUT[indices]                   # (H, W, 2)
    strips = []
    for s in range(DISPLAY_H // STRIP_H):
        strip = pixels_2b[s * STRIP_H:(s + 1) * STRIP_H]
        strips.append(bytes(strip.reshape(-1)))
    return strips


def config_url() -> str:
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
    """Fast F-S dither to 128-colour RGB565 palette, encode as strips."""
    img = _fit_image(data)
    return _indices_to_strips(_floyd_steinberg_fast(img))


def _url_to_bmp(url: str) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": "raspberrypi-webserver-poc/0.1", "Accept": "image/*"})
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
            headers={"User-Agent": "raspberrypi-webserver-poc/0.1", "Accept": "image/*"})
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

    img  = Image.new("RGB", (DISPLAY_W, DISPLAY_H), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    qr_x, qr_y = (DISPLAY_W - qr_size) // 2, 60
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
            bbox   = draw.textbbox((0, 0), line, font=font)
            text_w = bbox[2] - bbox[0]
            draw.text(((DISPLAY_W - text_w) // 2, y), line, fill=(255, 255, 255), font=font)
        y += 28

    return img  # 320×480, no rotation


def _make_config_prompt_bmp() -> bytes:
    img = _make_config_prompt_image()
    img = img.rotate(90, expand=True)
    buf = io.BytesIO()
    img.save(buf, format="BMP")
    return buf.getvalue()


def _make_config_prompt_strips() -> list[bytes]:
    img = _make_config_prompt_image()
    return _indices_to_strips(_floyd_steinberg_fast(img))


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