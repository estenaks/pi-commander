#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
epd_commander.py
Polls the pi-commander webserver for the current card and displays it
on the Waveshare 4.01inch e-Paper HAT (F) 7-color display.
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

# ── Waveshare lib path ────────────────────────────────────────────────────────
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

EPD_W = 640
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


def _nearest_palette_vectorized(arr: np.ndarray) -> np.ndarray:
    """
    For every pixel in arr (H, W, 3) int32, return the closest palette color.
    Fully vectorized — no Python loops over pixels.
    """
    # arr: (H, W, 3)  PALETTE: (7, 3)
    # expand dims so subtraction broadcasts to (H, W, 7, 3)
    diff = arr[:, :, np.newaxis, :] - PALETTE[np.newaxis, np.newaxis, :, :]
    dist = (diff ** 2).sum(axis=3)          # (H, W, 7)
    idx  = dist.argmin(axis=2)              # (H, W)
    return PALETTE[idx]                     # (H, W, 3)


def floyd_steinberg_fast(img: Image.Image) -> Image.Image:
    """
    Floyd-Steinberg dithering using vectorized nearest-color lookup.
    Loops over rows only (not individual pixels), so much faster than the
    pure per-pixel version — at the cost of slight accuracy because error
    diffusion within a row is still sequential but the palette snap is batched.
    This is fine for testing; quality is very close in practice.
    """
    arr = np.array(img.convert("RGB"), dtype=np.int32)
    h, w = arr.shape[:2]

    for y in range(h):
        row     = arr[y]                                    # (W, 3)
        new_row = _nearest_palette_vectorized(row[np.newaxis])[0]  # (W, 3)
        err     = row - new_row                             # (W, 3)
        arr[y]  = new_row

        # Distribute error — same coefficients as classic F-S
        if y + 1 < h:
            arr[y + 1,  1:  ] += err[:-1] * 3 // 16   # down-left  (3/16)
            arr[y + 1,   :  ] += err       * 5 // 16   # down       (5/16)
            arr[y + 1, :-1  ] += err[ 1:] * 1 // 16   # down-right (1/16)
            arr[y + 1]         = np.clip(arr[y + 1], 0, 255)

        # The 7/16 right-neighbour part stays sequential within the row —
        # skipped in fast mode (acceptable quality trade-off for testing)

    return Image.fromarray(arr.astype(np.uint8), "RGB")


def floyd_steinberg_precise(img: Image.Image) -> Image.Image:
    """
    Classic per-pixel Floyd-Steinberg with full error propagation
    (right, down-left, down, down-right).  Accurate but slow on a Pi
    (~30-90s for 640x400).  Switch to this for final/production quality.
    """
    arr = np.array(img.convert("RGB"), dtype=np.int32)
    h, w = arr.shape[:2]

    for y in range(h):
        for x in range(w):
            old = arr[y, x].copy()
            # nearest palette color (inline for speed)
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


def prepare_image(raw_bytes: bytes) -> Image.Image:
    """
    raw_bytes → open → rotate 90° CW → scale to width 640 →
    center-crop height to 400 → dither → return RGB PIL image
    """
    img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")

    # Rotate 90° clockwise (portrait card → landscape panel)
    img = img.rotate(-90, expand=True)

    # Scale so width == EPD_W, preserve aspect ratio
    src_w, src_h = img.size
    scale  = EPD_W / src_w
    new_h  = round(src_h * scale)
    img    = img.resize((EPD_W, new_h), Image.LANCZOS)

    # Center-crop height to EPD_H
    if new_h > EPD_H:
        top = (new_h - EPD_H) // 2
        img = img.crop((0, top, EPD_W, top + EPD_H))
    elif new_h < EPD_H:
        # Taller panel than image (unlikely) — pad with black
        canvas = Image.new("RGB", (EPD_W, EPD_H), (0, 0, 0))
        canvas.paste(img, (0, (EPD_H - new_h) // 2))
        img = canvas

    log.info("Running dither (fast mode)…")
    #img = floyd_steinberg_fast(img)   # ← swap to floyd_steinberg_precise for production
    img = floyd_steinberg_precise(img)
    return img


# ── Display helpers ──────────────���────────────────────────────────────────────

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
            raw  = fetch_bytes(API_URL)
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