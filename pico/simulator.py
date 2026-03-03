"""
pi-commander Pico simulator — runs on Mac/Linux with CPython.

Emulates the Waveshare Pico-Eval-Board touch display (480×320).
BMPs are downloaded to pico/sd/ (gitignored).

    pip install pygame
    python3 pico/simulator.py

Touch zones (mirrored by mouse click regions):
    Left  25% of screen   → -1 counter
    Right 25% of screen   → +1 counter
    Centre 50%            → flip front/back, reset counter

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

# Import pygame core and freetype separately so a freetype failure
# doesn't get swallowed by the ImportError catch on pygame itself.
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
W, H    = 480, 320
PLAYERS = [1, 2, 3, 4]
SD_DIR  = os.path.join(os.path.dirname(__file__), "sd")

# Touch zone x boundaries
ZONE_LEFT_MAX  = W // 4       # 0–119   → dec
ZONE_RIGHT_MIN = W * 3 // 4  # 360–479 → inc

# Colours
BLACK  = (0,   0,   0)
WHITE  = (255, 255, 255)
YELLOW = (255, 220,  50)
RED    = (220,  50,  50)
DIM    = (80,   80,  80)


# ---- Font helpers ----
# freetype.render(text, color) returns (Surface, Rect).
# We wrap it so call sites get back a plain Surface with a working .get_rect().

def make_font(name: str, size: int, bold: bool = False):
    if _freetype_ok:
        return pygame.freetype.SysFont(name, size, bold=bold)
    # Fallback to pygame.font if freetype somehow unavailable
    pygame.font.init()
    return pygame.font.SysFont(name, size, bold=bold)


def render_text(font, text: str, color) -> "pygame.Surface":
    """Render text to a Surface regardless of font type."""
    if _freetype_ok and isinstance(font, pygame.freetype.Font):
        surf, _ = font.render(text, color)
        return surf
    # pygame.font.Font path
    return font.render(text, True, color)


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

def draw_zone_hints(screen, font_small) -> None:
    """Dim touch-zone borders so the user can see tap areas."""
    pygame.draw.rect(screen, DIM, (0, 0, ZONE_LEFT_MAX, H), 1)
    t = render_text(font_small, "-1", DIM)
    screen.blit(t, (ZONE_LEFT_MAX // 2 - t.get_width() // 2, H // 2 - 8))

    pygame.draw.rect(screen, DIM, (ZONE_RIGHT_MIN, 0, W - ZONE_RIGHT_MIN, H), 1)
    t = render_text(font_small, "+1", DIM)
    screen.blit(t, (ZONE_RIGHT_MIN + (W - ZONE_RIGHT_MIN) // 2 - t.get_width() // 2, H // 2 - 8))

    t = render_text(font_small, "flip", DIM)
    screen.blit(t, (W // 2 - t.get_width() // 2, H // 2 - 8))


def draw_overlay(screen, counter: int, player: int, face: str,
                 font_large, font_small) -> None:
    # Status bar
    bar = pygame.Surface((W, 22), pygame.SRCALPHA)
    bar.fill((0, 0, 0, 170))
    screen.blit(bar, (0, 0))
    hud = render_text(
        font_small,
        f"Player {player}  [{face}]   ENTER=next player   R=reload   Q=quit",
        WHITE,
    )
    screen.blit(hud, (6, 3))

    # Counter
    if counter != 0:
        bg = pygame.Surface((240, 72), pygame.SRCALPHA)
        bg.fill((0, 0, 0, 150))
        screen.blit(bg, (W // 2 - 120, H // 2 - 36))
        sign  = "+" if counter > 0 else ""
        color = YELLOW if counter > 0 else RED
        text  = render_text(font_large, f"{sign}{counter}/{sign}{counter}", color)
        rect  = text.get_rect(center=(W // 2, H // 2))
        screen.blit(text, rect)


# ---- Main ----

def main() -> None:
    pygame.init()
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption("pi-commander simulator")
    clock      = pygame.time.Clock()
    font_large = make_font("monospace", 52, bold=True)
    font_small = make_font("monospace", 14)

    player  = 1
    face    = "front"
    counter = 0

    def show_status(msg: str) -> None:
        screen.fill(BLACK)
        t = render_text(font_small, msg, WHITE)
        screen.blit(t, (20, H // 2 - 10))
        pygame.display.flip()

    # Boot: download if SD empty, else use cache
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
        screen.fill(BLACK)
        surf = load_surface(player, face)
        if surf:
            screen.blit(surf, (0, 0))
        else:
            err = render_text(
                font_small,
                f"No BMP: player {player} {face} — press R",
                RED,
            )
            screen.blit(err, (20, H // 2))
        draw_zone_hints(screen, font_small)
        draw_overlay(screen, counter, player, face, font_large, font_small)
        pygame.display.flip()

    draw()

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            elif event.type == pygame.MOUSEBUTTONDOWN:
                mx, _ = event.pos
                if mx < ZONE_LEFT_MAX:
                    counter -= 1
                elif mx >= ZONE_RIGHT_MIN:
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