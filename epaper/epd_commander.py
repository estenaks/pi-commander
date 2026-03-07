#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
epd_commander.py
Polls the pi-commander webserver for the current card and displays it
on the Waveshare 4.01inch e-Paper HAT (F) 7-color display.

Assumes this file lives at ~/pi-commander/epaper/epd_commander.py
and the Waveshare lib is at ~/pi-commander/RaspberryPi_JetsonNano/python/lib/
"""

import sys
import os
import time
import logging
import urllib.request
import io
import json

import numpy as np
from PIL import Image

# ── Waveshare lib path ────────────────────────���───────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
REPO_ROOT  = os.path.dirname(SCRIPT_DIR)
WAVESHARE_LIB = os.path.join(
    os.path.expanduser("~"),
    "e-Paper", "RaspberryPi_JetsonNano", "python", "lib"
)
if os.path.exists(WAVESHARE_LIB):
    sys.path.insert(0, WAVESHARE_LIB)

from waveshare_epd import epd4in01f

# ── Config ────────────────────────────────────────────────────────────────────
API_URL    = "http://127.0.0.1/api/current/1"
POLL_SECS  = 5

EPD_W = 640   # physical pixels (landscape)
EPD_H = 400

# Exact 7 colors the hardware can render (sRGB)
PALETTE = np.array([
    [  0,   0,   0],   # black
    [255, 255, 255],   # white
    [  0, 255,   0],   # green
    [  0,   0, 255],   # blue
    [255,   0,   0],   # red
    [255, 255,   0],   # yellow
    [255, 128,   0],   # orange
], dtype=np.int32)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
)
log = logging.getLogger(__name__)


# ── Image processing ──────────────────────────────────────────────────────────

def fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "epd-commander/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read()


def nearest_palette_color(pixel: np.ndarray) -> np.ndarray:
    """Return the closest palette entry to pixel (int32 RGB)."""
    diffs = PALETTE - pixel          # (7, 3)
    dists = (diffs ** 2).sum(axis=1) # (7,)
    return PALETTE[np.argmin(dists)]


def floyd_steinberg(img: Image.Image) -> Image.Image:
    """
    Apply Floyd-Steinberg dithering, snapping every pixel to the 7-color
    hardware palette.  Operates entirely in int32 to avoid uint8 clipping
    during error diffusion.
    """
    arr = np.array(img.convert("RGB"), dtype=np.int32)
    h, w = arr.shape[:2]

    for y in range(h):
        for x in range(w):
            old = arr[y, x].copy()
            new = nearest_palette_color(old)
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

        # clamp after each row to stop error accumulation blowing out
        arr[y] = np.clip(arr[y], 0, 255)

    return Image.fromarray(arr.astype(np.uint8), "RGB")


def prepare_image(raw_bytes: bytes) -> Image.Image:
    """
    raw_bytes  → open → rotate 90° CW → scale to height 400 →
    center-crop width to 640 → Floyd-Steinberg dither → return RGB PIL image
    """
    img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")

    # Rotate 90° clockwise (portrait card → landscape panel)
    img = img.rotate(-90, expand=True)

    # Scale so height == EPD_H, preserve aspect ratio
    src_w, src_h = img.size
    scale  = EPD_H / src_h
    new_w  = round(src_w * scale)
    img    = img.resize((new_w, EPD_H), Image.LANCZOS)

    # Center-crop width to EPD_W
    if new_w > EPD_W:
        left = (new_w - EPD_W) // 2
        img  = img.crop((left, 0, left + EPD_W, EPD_H))
    elif new_w < EPD_W:
        # Narrower than panel (shouldn't normally happen) — pad with black
        canvas = Image.new("RGB", (EPD_W, EPD_H), (0, 0, 0))
        canvas.paste(img, ((EPD_W - new_w) // 2, 0))
        img = canvas

    log.info("Running Floyd-Steinberg dither (this takes a few seconds)…")
    img = floyd_steinberg(img)
    return img


# ── Display helpers ───────────────────────────────────────────────────────────

def show_image(epd, img: Image.Image) -> None:
    """init → display → sleep"""
    log.info("Initialising display…")
    epd.init()
    log.info("Sending image to display…")
    epd.display(epd.getbuffer(img))
    log.info("Display updated — going to sleep.")
    epd.sleep()


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    log.info("epd_commander starting up")
    epd = epd4in01f.EPD()

    current_card_id = None

    while True:
        try:
            raw = fetch_bytes(API_URL)
            data = json.loads(raw)

            card_id   = data.get("card_id")
            faces     = data.get("faces", [])
            image_url = faces[0].get("image_url") if faces else None

            if not card_id or not image_url:
                log.debug("No card set yet, waiting…")
            elif card_id != current_card_id:
                log.info(f"Card changed → {card_id}  url={image_url}")
                img_bytes = fetch_bytes(image_url)
                img       = prepare_image(img_bytes)
                show_image(epd, img)
                current_card_id = card_id
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