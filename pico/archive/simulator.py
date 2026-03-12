"""
pi-commander Pico simulator — runs on Mac/Linux with CPython.

Emulates the Waveshare Pico-Eval-Board touch display, physically rotated
90° clockwise — window is 320×480 (portrait).

    pip install pygame numpy
    source pico/.venv/bin/activate
    python3 pico/simulator.py

Keyboard:
    ENTER       cycle players 1→2→3→4→1
    S           toggle BMP mode / Strip mode (raw RGB565 from /img endpoint)
    R           re-fetch current image
    0 / DELETE  reset counter
    Q / Escape  quit
"""

import os
import sys
import json
import urllib.request

import pygame
pygame.init()
import pygame.freetype
import pygame.surfarray

try:
    import numpy as np
except ImportError:
    sys.exit("numpy not found — run: pip install pygame numpy")

# ---- Config ----
SERVER   = "http://127.0.0.1:8000"
CW, CH   = 480, 320          # logical canvas: landscape
WIN_W, WIN_H = CH, CW        # 320×480 portrait window
PLAYERS  = [1, 2, 3, 4]
SD_DIR   = os.path.join(os.path.dirname(__file__), "sd")

STRIP_W  = 320
STRIP_H  = 160
STRIPS   = 3

ZONE_LEFT_MAX  = CW // 4
ZONE_RIGHT_MIN = CW * 3 // 4

# ---- Colours ----
BLACK     = (0,   0,   0)
WHITE     = (255, 255, 255)
DARK_BLUE = (20,  40,  120)
DARK_RED  = (120, 15,  15)
ZONE_GRAY = (128, 128, 128)
DIM       = (160, 160, 160)
ZONE_ALPHA = 191


# ---- Font ----

def make_font(name, size, bold=False):
    return pygame.freetype.SysFont(name, size, bold=bold)

def render_text(font, text, color):
    surf, _ = font.render(text, color)
    return surf


# ---- Coordinate mapping ----

def window_to_logical(wx, wy):
    return wy, WIN_W - 1 - wx


# ---- BMP helpers ----

def _fetch_bytes(path):
    with urllib.request.urlopen(f"{SERVER}{path}", timeout=15) as r:
        return r.read()

def bmp_filename(player, face):
    return os.path.join(SD_DIR, f"player{player}_{face}.bmp")

def download_all(status_cb=None):
    os.makedirs(SD_DIR, exist_ok=True)
    if status_cb:
        status_cb("Fetching manifest...")
    manifest = json.loads(_fetch_bytes("/bmp/all"))
    for entry in manifest["files"]:
        p, face, url = entry["player"], entry["face"], entry["url"]
        msg = f"GET /bmp/{p}/{face}"
        print(f"  {msg}")
        if status_cb:
            status_cb(msg)
        try:
            data = _fetch_bytes(url)
            with open(bmp_filename(p, face), "wb") as f:
                f.write(data)
        except Exception as exc:
            print(f"  WARNING: {url}: {exc}")
    if status_cb:
        status_cb("Done.")

def load_bmp_surface(player, face):
    path = bmp_filename(player, face)
    if not os.path.exists(path):
        return None
    try:
        return pygame.image.load(path)
    except Exception as exc:
        print(f"WARNING: could not load {path}: {exc}")
        return None


# ---- Strip helpers ----

def fetch_strip(player, face, strip_idx):
    url = f"{SERVER}/img/{player}/{face}/raw?strip={strip_idx}"
    print(f"  GET {url}")
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            data = r.read()
        print(f"  got {len(data)} bytes")
        return data
    except Exception as exc:
        print(f"  ERROR: {exc}")
        return None

def strip_to_surface(data):
    """Convert raw RGB565 byte-swapped data (320×160) to a pygame Surface."""
    # data is 320*160*2 bytes, little-endian RGB565 (byte-swapped for display)
    arr = np.frombuffer(data, dtype=np.uint16)
    # undo byte-swap: data[i]=lo, data[i+1]=hi → value = (hi<<8)|lo
    arr = arr.byteswap()   # now each uint16 is the original RGB565 value
    r = ((arr >> 11) & 0x1F).astype(np.uint8) * 8
    g = ((arr >>  5) & 0x3F).astype(np.uint8) * 4
    b = ( arr        & 0x1F).astype(np.uint8) * 8
    # shape: (320*160,) → (160, 320, 3) → transpose to (320, 160, 3) for surfarray
    rgb = np.stack([r, g, b], axis=1).reshape(STRIP_H, STRIP_W, 3).transpose(1, 0, 2)
    return pygame.surfarray.make_surface(rgb)

def fetch_image_as_surface(player, face, status_cb=None):
    """Fetch all 3 strips and assemble into a single 480×320 landscape surface."""
    full = pygame.Surface((CW, CH))
    full.fill(BLACK)
    for strip_idx in range(STRIPS):
        if status_cb:
            status_cb(f"Fetching strip {strip_idx+1}/3...")
        data = fetch_strip(player, face, strip_idx)
        expected = STRIP_W * STRIP_H * 2
        if data is None or len(data) != expected:
            print(f"  strip {strip_idx}: bad size {len(data) if data else 0} (expected {expected})")
            continue
        strip_surf = strip_to_surface(data)
        # strips are in portrait space (320 wide × 160 tall)
        # landscape canvas x = strip_idx * 160
        full.blit(strip_surf, (strip_idx * STRIP_H, 0))
    return full


# ---- Drawing ----

def draw_zone_hints(canvas, font_small):
    left_bg = pygame.Surface((ZONE_LEFT_MAX, CH), pygame.SRCALPHA)
    left_bg.fill((*ZONE_GRAY, ZONE_ALPHA))
    canvas.blit(left_bg, (0, 0))
    t = render_text(font_small, "-1", DARK_RED)
    canvas.blit(t, (ZONE_LEFT_MAX // 2 - t.get_width() // 2, CH // 2 - 8))

    right_bg = pygame.Surface((CW - ZONE_RIGHT_MIN, CH), pygame.SRCALPHA)
    right_bg.fill((*ZONE_GRAY, ZONE_ALPHA))
    canvas.blit(right_bg, (ZONE_RIGHT_MIN, 0))
    t = render_text(font_small, "+1", DARK_BLUE)
    canvas.blit(t, (ZONE_RIGHT_MIN + (CW - ZONE_RIGHT_MIN) // 2 - t.get_width() // 2, CH // 2 - 8))

    t = render_text(font_small, "tap to flip", DIM)
    canvas.blit(t, (CW // 2 - t.get_width() // 2, CH // 2 - 8))

def draw_overlay(canvas, counter, player, face, mode_label, font_large, font_small):
    bar = pygame.Surface((CW, 22), pygame.SRCALPHA)
    bar.fill((0, 0, 0, 170))
    canvas.blit(bar, (0, 0))
    hud = render_text(
        font_small,
        f"P{player} [{face}] [{mode_label}]  ENTER=next  S=mode  R=reload  Q=quit",
        WHITE,
    )
    canvas.blit(hud, (6, 3))

    if counter != 0:
        color = DARK_BLUE if counter > 0 else DARK_RED
        sign  = "+" if counter > 0 else ""
        bg = pygame.Surface((240, 72), pygame.SRCALPHA)
        bg.fill((0, 0, 0, 150))
        canvas.blit(bg, (CW // 2 - 120, CH // 2 - 36))
        text = render_text(font_large, f"{sign}{counter}/{sign}{counter}", color)
        rect = text.get_rect(center=(CW // 2, CH // 2))
        canvas.blit(text, rect)


# ---- Main ----

def main():
    pygame.init()
    screen = pygame.display.set_mode((WIN_W, WIN_H))
    pygame.display.set_caption("pi-commander simulator")
    clock      = pygame.time.Clock()
    font_large = make_font("monospace", 52, bold=True)
    font_small = make_font("monospace", 14)

    canvas = pygame.Surface((CW, CH), pygame.SRCALPHA)

    player      = 1
    face        = "front"
    counter     = 0
    use_strips  = True
    cached_surf = None

    def show_status(msg):
        canvas.fill(BLACK)
        t = render_text(font_small, msg, WHITE)
        canvas.blit(t, (20, CH // 2 - 10))
        screen.blit(pygame.transform.rotate(canvas, -90), (0, 0))
        pygame.display.flip()

    def reload():
        nonlocal cached_surf
        if use_strips:
            show_status(f"Fetching strips P{player} {face}...")
            cached_surf = fetch_image_as_surface(player, face, status_cb=show_status)
        else:
            show_status("Downloading BMPs...")
            try:
                download_all(status_cb=show_status)
            except Exception as exc:
                show_status(f"ERROR: {exc}")
                pygame.time.wait(2000)
            cached_surf = load_bmp_surface(player, face)

    def draw():
        canvas.fill(BLACK)
        mode_label = "STRIPS" if use_strips else "BMP"
        if cached_surf:
            canvas.blit(cached_surf, (0, 0))
        else:
            err = render_text(font_small, "No image — press R to fetch", DARK_RED)
            canvas.blit(err, (20, CH // 2))
        draw_zone_hints(canvas, font_small)
        draw_overlay(canvas, counter, player, face, mode_label, font_large, font_small)
        screen.blit(pygame.transform.rotate(canvas, -90), (0, 0))
        pygame.display.flip()

    reload()
    draw()

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            elif event.type == pygame.MOUSEBUTTONDOWN:
                lx, _ = window_to_logical(*event.pos)
                if lx < ZONE_LEFT_MAX:
                    counter -= 1
                elif lx >= ZONE_RIGHT_MIN:
                    counter += 1
                else:
                    face    = "back" if face == "front" else "front"
                    counter = 0
                    reload()
                draw()

            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_q, pygame.K_ESCAPE):
                    running = False

                elif event.key == pygame.K_RETURN:
                    player  = (player % len(PLAYERS)) + 1
                    face    = "front"
                    counter = 0
                    reload()
                    draw()

                elif event.key in (pygame.K_0, pygame.K_DELETE, pygame.K_KP0):
                    counter = 0
                    draw()

                elif event.key == pygame.K_s:
                    use_strips = not use_strips
                    reload()
                    draw()

                elif event.key == pygame.K_r:
                    reload()
                    draw()

        clock.tick(30)

    pygame.quit()


if __name__ == "__main__":
    main()