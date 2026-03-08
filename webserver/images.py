from __future__ import annotations
import io
import sys
import struct
import urllib.request

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


def config_url() -> str:
    """Single source of truth for the /config URL used in all QR codes."""
    return f"http://{LOCAL_IP}{CONFIG_PORT}/config"


def _image_to_bmp(data: bytes) -> bytes:
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
    img = img.convert("RGB")
    img = img.rotate(90, expand=True)
    buf = io.BytesIO()
    img.save(buf, format="BMP")
    return buf.getvalue()


def _image_to_strips(data: bytes) -> list[bytes]:
    """Convert image bytes to a list of raw RGB565 strips (byte-swapped for display).

    Returns a list of 3 strips, each DISPLAY_W * STRIP_H * 2 bytes.
    Pixels are in RGB565 little-endian (byte-swapped) matching our bs() helper on the Pico.
    """
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
    img = img.convert("RGB")
    img = img.rotate(90, expand=True)   # now 320 wide, 480 tall

    strips = []
    num_strips = DISPLAY_H // STRIP_H
    for s in range(num_strips):
        y0 = s * STRIP_H
        y1 = y0 + STRIP_H
        strip_img = img.crop((0, y0, DISPLAY_W, y1))
        pixels = strip_img.tobytes()   # RGB888, row-major

        # Convert RGB888 → RGB565 byte-swapped
        out = bytearray(DISPLAY_W * STRIP_H * 2)
        j = 0
        for i in range(0, len(pixels), 3):
            r = pixels[i]
            g = pixels[i + 1]
            b = pixels[i + 2]
            rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            # byte-swap so Pico framebuf little-endian matches display
            out[j]     = rgb565 & 0xFF
            out[j + 1] = (rgb565 >> 8) & 0xFF
            j += 2

        strips.append(bytes(out))

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
    """Convert either a remote URL or local path to RGB565 strips."""
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
    """Generate a 320×480 placeholder BMP with a QR code and text
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
    """Generate config prompt as RGB565 strips."""
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