"""
pio_combo_behavior.py

Behavioral test for card-color combinations using the PIO NeoPixel driver.

Rules implemented (test-mode):
- Uses a selected palette (the "second" colors plus two golds).
- TARGET_BRIGHTNESS (0.0..1.0) is the prototype brightness percent to fade to.
- Mono-color combos (size == 1):
    OFF -> fade to TARGET_BRIGHTNESS*color -> hold for MONO_HOLD_SECONDS -> program continues
- Two-color combos (size == 2):
    For each pair: repeat TWO_PAIR_REPEATS times:
      fade OFF -> colorA@TARGET -> hold TWO_HOLD_SECONDS -> fade to OFF ->
      fade OFF -> colorB@TARGET -> hold TWO_HOLD_SECONDS -> fade to OFF
- Three-or-more combos (size >= 3):
    Fade OFF -> first gold color @ TARGET, then cycle slowly between gold1 and gold2
    for MULTI_GOLD_TEST_SECONDS (test duration), then turn off and end the whole program.

Notes:
- Requires pio_neopixel.py (NeoPixelPIO) present on device and importable.
- The NeoPixelPIO instance is created with driver brightness = 1.0 and we scale the
  color values by TARGET_BRIGHTNESS when performing fades/holds.
- MicroPython-friendly (no itertools). Uses combinations_indices generator.
"""

import time
from pio_neopixel import NeoPixelPIO

# ----------------- Configuration ----------------------------------------------
PIN = 4
ORDER = "GRB"
SM_ID = 0
NUM_PIXELS = 1

PALETTE = [
    (248, 231, 185),  # second white
    (14,  104, 100),  # second blue
    (21,   11,   0),  # second black
    (211,  10,  10),  # second red
    (0,   115,  10),  # second green
    # Added golds (user requested)
    (255, 191,   0),  # gold1
    (255, 220, 115),  # gold2
]

# Prototype brightness target (0.0 .. 1.0). Change for experimentation.
TARGET_BRIGHTNESS = 0.5  # 50%

# Timing for tests
MONO_HOLD_SECONDS = 10            # hold mono color for inspection
MULTI_GOLD_TEST_SECONDS = 10      # total time to run gold cycling test in multi-color combos

# Fade detail
STEPS = 36
STEP_DELAY = 0.02  # seconds per step

# ----------------- Palette ----------------------------------------------------
# The 'second' colors previously chosen (white2, blue2, black2, red2, green2)


# Identify gold indices in the local PALETTE
GOLD1_INDEX = len(PALETTE) - 2
GOLD2_INDEX = len(PALETTE) - 1

# Combo sizes to consider (we'll only act on size==1 or size>=3)
MIN_COMBO_SIZE = 1
MAX_COMBO_SIZE = min(5, len(PALETTE))  # cap to palette size

# Instantiate PIO NeoPixel driver (driver brightness = 1.0; we scale colors manually)
np = NeoPixelPIO(pin=PIN, n=NUM_PIXELS, order=ORDER, brightness=1.0, sm_id=SM_ID)


# ----------------- Helpers ---------------------------------------------------
def clamp255(v):
    if v < 0:
        return 0
    if v > 255:
        return 255
    return int(v)


def scale_color(rgb, scale):
    """Return tuple of rgb scaled by scale (0..1)."""
    if scale <= 0:
        return (0, 0, 0)
    if scale >= 1:
        return (clamp255(rgb[0]), clamp255(rgb[1]), clamp255(rgb[2]))
    return (clamp255(int(rgb[0] * scale)),
            clamp255(int(rgb[1] * scale)),
            clamp255(int(rgb[2] * scale)))


def lerp_tuple(a, b, t):
    return (int(a[0] + (b[0] - a[0]) * t),
            int(a[1] + (b[1] - a[1]) * t),
            int(a[2] + (b[2] - a[2]) * t))


def fade_between(start_rgb, end_rgb, steps=STEPS, delay=STEP_DELAY):
    for i in range(steps + 1):
        t = i / steps
        cur = lerp_tuple(start_rgb, end_rgb, t)
        np.show_color(cur)
        time.sleep(delay)


# combinations generator (micro-python friendly)
def combinations_indices(n, r):
    if r < 0 or r > n:
        return
    if r == 0:
        yield ()
        return
    indices = [i for i in range(r)]
    while True:
        yield tuple(indices)
        for i in range(r - 1, -1, -1):
            if indices[i] != i + n - r:
                break
        else:
            return
        indices[i] += 1
        for j in range(i + 1, r):
            indices[j] = indices[j - 1] + 1


# ----------------- Behaviors -------------------------------------------------
def do_mono_color(color_rgb):
    """OFF -> target -> hold for MONO_HOLD_SECONDS -> fade OFF"""
    target = scale_color(color_rgb, TARGET_BRIGHTNESS)
    fade_between((0, 0, 0), target)
    # hold for inspection
    hold_until = time.ticks_add(time.ticks_ms(), int(MONO_HOLD_SECONDS * 1000))
    while time.ticks_diff(hold_until, time.ticks_ms()) > 0:
        np.show_color(target)
        time.sleep(0.25)
    fade_between(target, (0, 0, 0))
    np.off()


def do_multi_gold_cycle(crossfade=True):
    """
    Multi-color behavior:
    - Fade OFF -> gold1@TARGET
    - If crossfade==True: crossfade gold1<->gold2 (no zero) for MULTI_GOLD_TEST_SECONDS
      (gold1->gold2->gold1...).
    - If crossfade==False: original behavior (gold1->gold2 with fades that may include zero).
    - Fade out from the last shown gold and finish.
    """
    gold1 = scale_color(PALETTE[GOLD1_INDEX], TARGET_BRIGHTNESS)
    gold2 = scale_color(PALETTE[GOLD2_INDEX], TARGET_BRIGHTNESS)

    # Fade into gold1
    fade_between((0, 0, 0), gold1)

    # Cycle duration
    end_time = time.ticks_add(time.ticks_ms(), int(MULTI_GOLD_TEST_SECONDS * 1000))
    last_shown = gold1

    if crossfade:
        # Crossfade continuously between the two golds (no zero)
        while time.ticks_diff(end_time, time.ticks_ms()) > 0:
            # gold1 -> gold2
            fade_between(gold1, gold2, steps=STEPS * 2, delay=STEP_DELAY)
            last_shown = gold2
            if time.ticks_diff(end_time, time.ticks_ms()) <= 0:
                break
            # gold2 -> gold1
            fade_between(gold2, gold1, steps=STEPS * 2, delay=STEP_DELAY)
            last_shown = gold1
    else:
        # Fallback / original behavior: alternate fades but may include intermediate off periods
        while time.ticks_diff(end_time, time.ticks_ms()) > 0:
            fade_between(gold1, gold2, steps=STEPS * 2, delay=STEP_DELAY)
            last_shown = gold2
            if time.ticks_diff(end_time, time.ticks_ms()) <= 0:
                break
            fade_between(gold2, gold1, steps=STEPS * 2, delay=STEP_DELAY)
            last_shown = gold1

    # Fade out smoothly from the last shown gold to off
    fade_between(last_shown, (0, 0, 0))
    np.off()


# ----------------- Main loop -------------------------------------------------
def run():
    n_palette = len(PALETTE)
    max_k = min(MAX_COMBO_SIZE, n_palette)
    print("Running only mono (size==1) and multi (size>=3) combos.")
    try:
        for k in range(MIN_COMBO_SIZE, max_k + 1):
            # skip pair size == 2 entirely
            if k == 2:
                continue
            print("=== combos of size", k, "===")
            for combo in combinations_indices(n_palette, k):
                print("Combo indices:", combo)
                combo_colors = [PALETTE[i] for i in combo]

                # if len(combo_colors) == 1:
                #     do_mono_color(combo_colors[0])
                #     # continue to next combo

                # else:
                # len >= 3 -> run gold cycle and then end program
                print("Multi-color combo encountered -> running gold-cycle test and exiting.")
                do_multi_gold_cycle()
                print("Multi-color test finished; exiting.")
                np.deinit()
                return

        print("Completed configured combos (mono only).")
    except KeyboardInterrupt:
        print("Interrupted by user — turning off.")
    finally:
        np.off()
        np.deinit()


if __name__ == "__main__":
    run()