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
# Logical canvas matches BMP size (landscape)
CW, CH  = 480, 320
# Physical window is portrait (rotated 90° CW)
WIN_W, WIN_H = CH, CW   # 320 × 480
PLAYERS = [1, 2, 3, 4]
SD_DIR  = os.path.join(os.path.dirname(__file__), "sd")

# Touch zones in LOGICAL (landscape) coordinates
ZONE_LEFT_MAX  = CW // 4       # x < 120  → dec
ZONE_RIGHT_MIN = CW * 3 // 4  # x >= 360 → inc

# Colours
BLACK  = (0,   0,   0)
WHITE  = (255, 255, 255)
YELLOW = (255, 220,  50)
RED    = (220,  50,  50)
DIM    = (80,   80,  80)


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
    """
    Map a click on the portrait window (320×480) back to logical
    landscape coordinates (480×320).

    Physical rotation is 90° CW:
        logical_x = wy
        logical_y = WIN_W - 1 - wx   (= 319 - wx)
    """
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


# ---- Drawing (all done on logical 480×320 canvas) ----

def draw_zone_hints(canvas, font_small) -> None:
    pygame.draw.rect(canvas, DIM, (0, 0, ZONE_LEFT_MAX, CH), 1)
    t = render_text(font_small, "-1", DIM)
    canvas.blit(t, (ZONE_LEFT_MAX // 2 - t.get_width() // 2, CH // 2 - 8))

    pygame.draw.rect(canvas, DIM, (ZONE_RIGHT_MIN, 0, CW - ZONE_RIGHT_MIN, CH), 1)
    t = render_text(font_small, "+1", DIM)
    canvas.blit(t, (ZONE_RIGHT_MIN + (CW - ZONE_RIGHT_MIN) // 2 - t.get_width() // 2, CH // 2 - 8))

    t = render_text(font_small, "flip", DIM)
    canvas.blit(t, (CW // 2 - t.get_width() // 2, CH // 2 - 8))


def draw_overlay(canvas, counter: int, player: int, face: str,
                 font_large, font_small) -> None:
    # Status bar along the logical top edge
    bar = pygame.Surface((CW, 22), pygame.SRCALPHA)
    bar.fill((0, 0, 0, 170))
    canvas.blit(bar, (0, 0))
    hud = render_text(
        font_small,
        f"P{player} [{face}]  ENTER=next  R=reload  Q=quit",
        WHITE,
    )
    canvas.blit(hud, (6, 3))

    # Counter centred on canvas
    if counter != 0:
        bg = pygame.Surface((240, 72), pygame.SRCALPHA)
        bg.fill((0, 0, 0, 150))
        canvas.blit(bg, (CW // 2 - 120, CH // 2 - 36))
        sign  = "+" if counter > 0 else ""
        color = YELLOW if counter > 0 else RED
        text  = render_text(font_large, f"{sign}{counter}/{sign}{counter}", color)
        rect  = text.get_rect(center=(CW // 2, CH // 2))
        canvas.blit(text, rect)


# ---- Main ----

def main() -> None:
    pygame.init()
    # Portrait window — matches physically rotated display
    screen = pygame.display.set_mode((WIN_W, WIN_H))
    pygame.display.set_caption("pi-commander simulator (rotated 90° CW)")
    clock      = pygame.time.Clock()
    font_large = make_font("monospace", 52, bold=True)
    font_small = make_font("monospace", 14)

    # Offscreen landscape canvas — everything is drawn here, then rotated
    canvas = pygame.Surface((CW, CH))

    player  = 1
    face    = "front"
    counter = 0

    def show_status(msg: str) -> None:
        canvas.fill(BLACK)
        t = render_text(font_small, msg, WHITE)
        canvas.blit(t, (20, CH // 2 - 10))
        screen.blit(pygame.transform.rotate(canvas, -90), (0, 0))
        pygame.display.flip()

    # Boot
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
                RED,
            )
            canvas.blit(err, (20, CH // 2))
        draw_zone_hints(canvas, font_small)
        draw_overlay(canvas, counter, player, face, font_large, font_small)
        # Rotate canvas 90° CW onto the portrait window
        screen.blit(pygame.transform.rotate(canvas, -90), (0, 0))
        pygame.display.flip()

    draw()

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            elif event.type == pygame.MOUSEBUTTONDOWN:
                # Map portrait window click → logical landscape coords
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