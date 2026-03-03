"""
pi-commander Pico simulator — runs on Mac/Linux with CPython.

Emulates the Waveshare Pico-Eval-Board touch display, physically rotated
90° clockwise — window is 320×480 (portrait).

    pip install pygame   (requires Python 3.13)
    python3 pico/simulator.py

Touch zones (on the rotated physical display):
    Bottom 25%  → -1 counter   (was left  25% in landscape)
    Top    25%  → +1 counter   (was right 25% in landscape)
    Centre 50%  → flip front/back, reset counter

Keyboard:
    ENTER       KEY button — cycle players 1→2→3→4→1
    0 / DELETE  Reset counter
    R           Re-download all BMPs
    Q / Escape  Quit
"""

import os
import sys
import json
import urllib.request

try:
    import pygame
except ImportError:
    sys.exit("pygame not found — run: pip install pygame")

try:
    import pygame.freetype
    _freetype_ok = True
except Exception:
    _freetype_ok = False

# ---- Config ----
SERVER  = "http://127.0.0.1:8000"
CW, CH  = 480, 320
WIN_W, WIN_H = CH, CW   # 320 × 480 portrait window
PLAYERS = [1, 2, 3, 4]
SD_DIR  = os.path.join(os.path.dirname(__file__), "sd")

ZONE_LEFT_MAX  = CW // 4
ZONE_RIGHT_MIN = CW * 3 // 4

# ---- Colours ----
BLACK      = (0,   0,   0)
WHITE      = (255, 255, 255)
DARK_BLUE  = (20,  40,  120)   # +1 counter
DARK_RED   = (120, 15,  15)    # -1 counter
ZONE_GRAY  = (128, 128, 128)   # button fill (semi-transparent below)
DIM        = (160, 160, 160)   # button label text

# Button background: gray at 75% opacity = alpha 191
ZONE_ALPHA = 191


# ---- Font ----

def make_font(name: str, size: int, bold: bool = False):
    if _freetype_ok:
        return pygame.freetype.SysFont(name, size, bold=bold)
    raise RuntimeError("pygame.freetype unavailable — use Python 3.13")


def render_text(font, text: str, color) -> "pygame.Surface":
    if _freetype_ok and isinstance(font, pygame.freetype.Font):
        surf, _ = font.render(text, color)
        return surf
    return font.render(text, True, color)


# ---- Coordinate mapping ----

def window_to_logical(wx: int, wy: int) -> tuple[int, int]:
    lx = wy
    ly = WIN_W - 1 - wx
    return lx, ly


# ---- File helpers ----

def _fetch_bytes(path: str) -> bytes:
    with urllib.request.urlopen(f"{SERVER}{path}", timeout=10) as r:
        return r.read()


def bmp_filename(player: int, face: str) -> str:
    return os.path.join(SD_DIR, f"player{player}_{face}.bmp")


def download_all(status_cb=None) -> None:
    os.makedirs(SD_DIR, exist_ok=True)
    if status_cb:
        status_cb("Fetching manifest…")
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
    print("All BMPs saved to pico/sd/")


def load_surface(player: int, face: str) -> "pygame.Surface | None":
    path = bmp_filename(player, face)
    if not os.path.exists(path):
        return None
    try:
        return pygame.image.load(path)
    except Exception as exc:
        print(f"WARNING: could not load {path}: {exc}")
        return None


# ---- Drawing ----

def draw_zone_hints(canvas, font_small) -> None:
    """Gray semi-transparent button zones with labels."""
    # Left zone — dec (-1), dark red label
    left_bg = pygame.Surface((ZONE_LEFT_MAX, CH), pygame.SRCALPHA)
    left_bg.fill((*ZONE_GRAY, ZONE_ALPHA))
    canvas.blit(left_bg, (0, 0))
    t = render_text(font_small, "-1", DARK_RED)
    canvas.blit(t, (ZONE_LEFT_MAX // 2 - t.get_width() // 2, CH // 2 - 8))

    # Right zone — inc (+1), dark blue label
    right_bg = pygame.Surface((CW - ZONE_RIGHT_MIN, CH), pygame.SRCALPHA)
    right_bg.fill((*ZONE_GRAY, ZONE_ALPHA))
    canvas.blit(right_bg, (ZONE_RIGHT_MIN, 0))
    t = render_text(font_small, "+1", DARK_BLUE)
    canvas.blit(t, (ZONE_RIGHT_MIN + (CW - ZONE_RIGHT_MIN) // 2 - t.get_width() // 2, CH // 2 - 8))

    # Centre zone — flip, neutral label
    t = render_text(font_small, "tap to flip", DIM)
    canvas.blit(t, (CW // 2 - t.get_width() // 2, CH // 2 - 8))


def draw_overlay(canvas, counter: int, player: int, face: str,
                 font_large, font_small) -> None:
    # Status bar
    bar = pygame.Surface((CW, 22), pygame.SRCALPHA)
    bar.fill((0, 0, 0, 170))
    canvas.blit(bar, (0, 0))
    hud = render_text(
        font_small,
        f"P{player} [{face}]  ENTER=next  R=reload  Q=quit",
        WHITE,
    )
    canvas.blit(hud, (6, 3))

    # Counter
    if counter != 0:
        color = DARK_BLUE if counter > 0 else DARK_RED
        sign  = "+" if counter > 0 else ""
        # Semi-transparent bg sized to counter text
        bg = pygame.Surface((240, 72), pygame.SRCALPHA)
        bg.fill((0, 0, 0, 150))
        canvas.blit(bg, (CW // 2 - 120, CH // 2 - 36))
        text = render_text(font_large, f"{sign}{counter}/{sign}{counter}", color)
        rect = text.get_rect(center=(CW // 2, CH // 2))
        canvas.blit(text, rect)


# ---- Main ----

def main() -> None:
    pygame.init()
    screen = pygame.display.set_mode((WIN_W, WIN_H))
    pygame.display.set_caption("pi-commander simulator (rotated 90° CW)")
    clock      = pygame.time.Clock()
    font_large = make_font("monospace", 52, bold=True)
    font_small = make_font("monospace", 14)

    canvas = pygame.Surface((CW, CH), pygame.SRCALPHA)

    player  = 1
    face    = "front"
    counter = 0

    def show_status(msg: str) -> None:
        canvas.fill(BLACK)
        t = render_text(font_small, msg, WHITE)
        canvas.blit(t, (20, CH // 2 - 10))
        screen.blit(pygame.transform.rotate(canvas, -90), (0, 0))
        pygame.display.flip()

    if not os.path.exists(bmp_filename(1, "front")):
        show_status("Downloading BMPs from server…")
        try:
            download_all(status_cb=show_status)
        except Exception as exc:
            show_status(f"ERROR: {exc}  (is the server running?)")
            pygame.time.wait(3000)
    else:
        print("Using cached BMPs — press R to re-download.")

    def draw() -> None:
        canvas.fill(BLACK)
        surf = load_surface(player, face)
        if surf:
            canvas.blit(surf, (0, 0))
        else:
            err = render_text(
                font_small,
                f"No BMP: player {player} {face} — press R",
                DARK_RED,
            )
            canvas.blit(err, (20, CH // 2))
        draw_zone_hints(canvas, font_small)
        draw_overlay(canvas, counter, player, face, font_large, font_small)
        screen.blit(pygame.transform.rotate(canvas, -90), (0, 0))
        pygame.display.flip()

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
                draw()

            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_q, pygame.K_ESCAPE):
                    running = False

                elif event.key == pygame.K_RETURN:
                    player  = (player % len(PLAYERS)) + 1
                    face    = "front"
                    counter = 0
                    draw()

                elif event.key in (pygame.K_0, pygame.K_DELETE, pygame.K_KP0):
                    counter = 0
                    draw()

                elif event.key == pygame.K_r:
                    show_status("Re-downloading BMPs…")
                    try:
                        download_all(status_cb=show_status)
                    except Exception as exc:
                        show_status(f"ERROR: {exc}")
                        pygame.time.wait(2000)
                    draw()

        clock.tick(30)

    pygame.quit()


if __name__ == "__main__":
    main()