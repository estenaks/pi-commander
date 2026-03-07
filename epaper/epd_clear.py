#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
epd_clear.py
One-shot helper: init the display, clear it to white, then sleep.
Run this at the end of a programming/testing session to prevent burn-in.

Usage:
    python3 epaper/epd_clear.py
"""

import sys
import os
import logging

SCRIPT_DIR   = os.path.dirname(os.path.realpath(__file__))
REPO_ROOT    = os.path.dirname(SCRIPT_DIR)
WAVESHARE_LIB = os.path.join(
    os.path.expanduser("~"),
    "e-Paper", "RaspberryPi_JetsonNano", "python", "lib"
)
if os.path.exists(WAVESHARE_LIB):
    sys.path.insert(0, WAVESHARE_LIB)

from waveshare_epd import epd4in01f

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger(__name__)

if __name__ == "__main__":
    log.info("Initialising display…")
    epd = epd4in01f.EPD()
    epd.init()
    log.info("Clearing…")
    epd.Clear()
    log.info("Done. Sleeping display.")
    epd.sleep()