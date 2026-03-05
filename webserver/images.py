import io
import sys
import urllib.request

import qrcode
from PIL import Image, ImageDraw, ImageFont

# These are set by app.py after import so images.py doesn't need to know the path itself
CARD_BACK_PATH: str = ""
CARD_BACK_WEB_URL: str = "/cardback.jpg"
CONFIG_PORT: str = ""


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
    img = img.rotate(90, expand=True)
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

    img = img.rotate(90, expand=True)
    buf = io.BytesIO()
    img.save(buf, format="BMP")
    return buf.getvalue()


def init_fallback_bmps(card_back_path: str) -> tuple[bytes, bytes | None]:
    """Pre-generate fallback BMPs at startup. Returns (config_prompt_bmp, card_back_bmp)."""
    config_prompt_bmp = _make_config_prompt_bmp()

    card_back_bmp = None
    try:
        card_back_bmp = _file_to_bmp(card_back_path)
    except Exception as exc:
        print(f"[bmp] Warning: could not load cardback.jpg: {exc}", file=sys.stderr)

    return config_prompt_bmp, card_back_bmp