#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import time
import logging
import urllib.request
import io
import json
import signal

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageOps
import qrcode

# ── Waveshare lib path ────────────────────────────────────────────────────────
WAVESHARE_LIB = os.path.join(
    os.path.expanduser("~"),
    "e-Paper", "RaspberryPi_JetsonNano", "python", "lib"
)
if os.path.exists(WAVESHARE_LIB):
    sys.path.insert(0, WAVESHARE_LIB)

from waveshare_epd import epd4in01f

# ── Config ────────────────────────────────────────────────────────────────────
API_URL        = "http://127.0.0.1/api/current/1"
CONFIG_URL_API = "http://127.0.0.1/api/config-url"
POLL_SECS      = 1


# CONTRAST:
FAST_CONTRAST_FACTOR = 1.0   # 1.0 = no change. Increase to boost contrast for fast dither.
FAST_AUTOCONTRAST    = False # set True to run ImageOps.autocontrast before enhancement

#^might have to remove
# BLACK BORDER:
BORDER_TARGET_RGB = np.array([22, 20, 15], dtype=np.int32)
# Euclidean distance threshold — pixels within this distance will be forced to black.
# Tune this value while testing. 10..25 is a reasonable range to try.
BORDER_DISTANCE_THRESHOLD = 18

# BLUE
TARGET_PALE_BLUE_RGB = np.array([175, 220, 230], dtype=np.int32)
BLUE_DISTANCE_THRESHOLD = 40   # how close a pixel must be to be considered the pale-blue target
BLUE_RED_SCALE   = 0.45        # scale applied to R for matched pixels (reduce red)
BLUE_GREEN_SCALE = 0.6         # scale applied to G for matched pixels (reduce green)
BLUE_BLUE_SCALE  = 2.4         # scale applied to B for matched pixels (increase blue)
# If you only want this for the fast path, set to True and the code below will skip when precise=True
APPLY_BLUE_AMPLIFY_ONLY_FAST = True

EPD_W = 640
EPD_H = 400

PALETTE = np.array([
    [  0,   0,   0],
    [255, 255, 255],
    [  0, 255,   0],
    [  0,   0, 255],
    [255,   0,   0],
    [255, 255,   0],
    [255, 128,   0],
], dtype=np.int32)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
)
log = logging.getLogger(__name__)

# ── Shutdown flag ─────────────────────────────────────────────────────────────

_shutdown = False

def _handle_sigterm(signum, frame):
    global _shutdown
    log.info("SIGTERM received — will clear display and exit.")
    _shutdown = True

signal.signal(signal.SIGTERM, _handle_sigterm)

# ── Image processing ──────────────────────────────────────────────────────────

def fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "epd-commander/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read()


def _nearest_palette_vectorized(arr: np.ndarray) -> np.ndarray:
    diff = arr[:, :, np.newaxis, :] - PALETTE[np.newaxis, np.newaxis, :, :]
    dist = (diff ** 2).sum(axis=3)
    idx  = dist.argmin(axis=2)
    return PALETTE[idx]


def floyd_steinberg_fast(img: Image.Image) -> Image.Image:
    arr = np.array(img.convert("RGB"), dtype=np.int32)
    h, w = arr.shape[:2]
    for y in range(h):
        row     = arr[y]
        new_row = _nearest_palette_vectorized(row[np.newaxis])[0]
        err     = row - new_row
        arr[y]  = new_row
        if y + 1 < h:
            arr[y + 1,  1:  ] += err[:-1] * 3 // 16
            arr[y + 1,   :  ] += err       * 5 // 16
            arr[y + 1, :-1  ] += err[ 1:] * 1 // 16
            arr[y + 1]         = np.clip(arr[y + 1], 0, 255)
    return Image.fromarray(arr.astype(np.uint8), "RGB")


def floyd_steinberg_precise(img: Image.Image) -> Image.Image:
    arr = np.array(img.convert("RGB"), dtype=np.int32)
    h, w = arr.shape[:2]
    for y in range(h):
        for x in range(w):
            old = arr[y, x].copy()
            diff = PALETTE - old
            new  = PALETTE[(diff ** 2).sum(axis=1).argmin()]
            arr[y, x] = new
            err = old - new
            if x + 1 < w:
                arr[y,     x + 1] += err * 7 // 16
            if y + 1 < h:
                if x > 0:
                    arr[y + 1, x - 1] += err * 3 // 16
                arr[y + 1, x    ] += err * 5 // 16
                if x + 1 < w:
                    arr[y + 1, x + 1] += err * 1 // 16
        arr[y] = np.clip(arr[y], 0, 255)
    return Image.fromarray(arr.astype(np.uint8), "RGB")


def prepare_image(raw_bytes: bytes, precise: bool = False) -> Image.Image:
    img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    img = img.rotate(-90, expand=True)
    src_w, src_h = img.size
    scale = EPD_W / src_w
    new_h = round(src_h * scale)
    img   = img.resize((EPD_W, new_h), Image.LANCZOS)

    if new_h > EPD_H:
        top = (new_h - EPD_H) // 2
        img = img.crop((0, top, EPD_W, top + EPD_H))
    elif new_h < EPD_H:
        canvas = Image.new("RGB", (EPD_W, EPD_H), (0, 0, 0))
        canvas.paste(img, (0, (EPD_H - new_h) // 2))
        img = canvas

    # --- Step 1: force near-border colour to true black -------------------
    try:
        arr = np.array(img, dtype=np.int32)
        # Euclidean distance from target border colour per pixel
        diff = arr - BORDER_TARGET_RGB[np.newaxis, np.newaxis, :]
        dist = np.sqrt((diff ** 2).sum(axis=2))
        mask = dist <= BORDER_DISTANCE_THRESHOLD
        if mask.any():
            log.info(f"Forcing {mask.sum()} pixels near {BORDER_TARGET_RGB.tolist()} -> black (threshold={BORDER_DISTANCE_THRESHOLD})")
            arr[mask] = [0, 0, 0]
            img = Image.fromarray(arr.astype(np.uint8), "RGB")
    except Exception as e:
        log.warning(f"Border-black adjustment failed: {e}")
    # ---------------------------------------------------------------------

    # QUICK CONTRAST ADJUSTMENT FOR FAST PATH (optional)
    if not precise:
        if FAST_AUTOCONTRAST:
            log.info("Applying autocontrast for fast path")
            img = ImageOps.autocontrast(img)
        if FAST_CONTRAST_FACTOR != 1.0:
            log.info(f"Applying contrast x{FAST_CONTRAST_FACTOR} for fast dither")
            img = ImageEnhance.Contrast(img).enhance(FAST_CONTRAST_FACTOR)

    if precise:
        log.info("Running Floyd-Steinberg precise dither (foil — this takes a while)…")
        img = floyd_steinberg_precise(img)
    else:
        log.info("Running Floyd-Steinberg fast dither…")
        img = floyd_steinberg_fast(img)
    return img


def make_qr_splash(config_url: str) -> Image.Image:
    """Generate a 640×400 black splash screen with a QR code and instructions."""
    img  = Image.new("RGB", (EPD_W, EPD_H), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    qr = qrcode.QRCode(border=2)
    qr.add_data(config_url)
    qr.make(fit=True)
    qr_img  = qr.make_image(fill_color="white", back_color="black").convert("RGB")
    qr_size = 280
    qr_img  = qr_img.resize((qr_size, qr_size), Image.NEAREST)
    qr_x    = (EPD_W - qr_size) // 2
    qr_y    = (EPD_H - qr_size) // 2 - 20
    img.paste(qr_img, (qr_x, qr_y))

    font_large = ImageFont.load_default(size=50)
    font_small = ImageFont.load_default(size=30)

    lines_above = [("No card configured.", font_large)]
    lines_below = [
        ("Scan or visit:", font_small),
        (config_url,       font_small),
    ]

    y = qr_y - 36
    for text, font in reversed(lines_above):
        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        draw.text(((EPD_W - w) // 2, y), text, fill=(255, 255, 255), font=font)
        y -= bbox[3] - bbox[1] + 6

    y = qr_y + qr_size + 10
    for text, font in lines_below:
        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        draw.text(((EPD_W - w) // 2, y), text, fill=(200, 200, 200), font=font)
        y += bbox[3] - bbox[1] + 6

    return img


# ── Display helpers ───────────────────────────────────────────────────────────

def show_image(epd, img: Image.Image) -> None:
    log.info("Initialising display…")
    epd.init()
    log.info("Sending image to display…")
    epd.display(epd.getbuffer(img))
    log.info("Display updated — going to sleep.")
    epd.sleep()


def clear_and_exit(epd) -> None:
    log.info("Clearing display before exit…")
    try:
        epd.init()
        epd.Clear()
        epd.sleep()
        log.info("Display cleared.")
    except Exception as e:
        log.warning(f"Clear failed: {e}")
    sys.exit(0)


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    log.info("epd_commander starting up")
    epd = epd4in01f.EPD()

    current_card_id = None
    current_premium = None
    showing_qr      = False

    while True:
        if _shutdown:
            clear_and_exit(epd)

        try:
            raw  = fetch_bytes(API_URL)
            data = json.loads(raw)

            card_id   = data.get("card_id")
            premium   = data.get("premium")
            faces     = data.get("faces", [])

            # Prefer normal (488×680) over border_crop for better dither quality.
            # Fall back to image_url if normal_image_url is absent or empty.
            image_url = None
            if faces:
                image_url = (
                    faces[0].get("normal_image_url")
                    or faces[0].get("image_url")
                )

            if not card_id or not image_url:
                if not showing_qr:
                    log.info("No card set — showing QR splash.")
                    try:
                        config_url = json.loads(
                            fetch_bytes(CONFIG_URL_API)
                        ).get("url", "http://raspberrypi.local/config")
                    except Exception:
                        config_url = "http://raspberrypi.local/config"
                    splash = make_qr_splash(config_url)
                    show_image(epd, splash)
                    showing_qr = True
                else:
                    log.debug("No card set, QR already showing.")

            elif card_id != current_card_id or premium != current_premium:
                log.info(f"Card or premium changed → {card_id}  premium={premium}")
                is_foil = premium == "foil"
                log.info(f"Fetching normal image: {image_url}")
                log.info(f"Rendering: {'precise (foil)' if is_foil else 'fast'}")
                img_bytes = fetch_bytes(image_url)
                img       = prepare_image(img_bytes, precise=is_foil)
                show_image(epd, img)
                current_card_id = card_id
                current_premium = premium
                showing_qr      = False
            else:
                log.debug("No change.")

        except KeyboardInterrupt:
            log.info("Interrupted — exiting without clearing display.")
            break
        except Exception as e:
            log.warning(f"Error: {e} — will retry in {POLL_SECS}s")

        time.sleep(POLL_SECS)


if __name__ == "__main__":
    main()