"""
pio_neopixel.py - WS2812 / NeoPixel driver using RP2040 PIO for MicroPython (rp2)

Provides NeoPixelPIO class that you can import into main.py:

from pio_neopixel import NeoPixelPIO
np = NeoPixelPIO(pin=4, n=8, order="GRB", brightness=0.25)
np[0] = (255, 0, 0)   # logical RGB
np.write()

Features:
- Uses rp2.StateMachine to generate correct WS2812 timings (independent of Python timing)
- Supports configurable color order ("RGB","GRB", etc.)
- Optional brightness scaling (float 0.0-1.0 or int 0-255)
- Methods: write(), fill(color), __setitem__, __getitem__, deinit()
"""

import array
from machine import Pin
import rp2

# PIO program for WS2812 (24-bit per pixel)
# Uses sideset to toggle the data pin.
@rp2.asm_pio(sideset_init=rp2.PIO.OUT_LOW, out_shiftdir=rp2.PIO.SHIFT_LEFT, autopull=True, pull_thresh=24)
def _ws2812():
    T1 = 2
    T2 = 5
    T3 = 3
    label("bitloop")
    out(x, 1)               .side(0) [T3 - 1]
    jmp(not_x, "do_zero")  .side(1) [T1 - 1]
    jmp("bitloop")         .side(1) [T2 - 1]
    label("do_zero")
    nop()                  .side(0) [T2 - 1]


class NeoPixelPIO:
    """
    NeoPixel driver using the RP2040 PIO.

    Parameters:
    - pin: integer GP pin number (data out to DIN of first pixel)
    - n: number of pixels (int)
    - order: string mapping of channels, e.g. "GRB" or "RGB" (default "GRB")
    - brightness: float 0.0-1.0 or int 0-255 to scale brightness globally
    - sm_id: state machine index to use (0-7). Default 0. If occupied, use other id.
    - freq: PIO state machine clock frequency (Hz). 8_000_000 works well.

    Example:
      np = NeoPixelPIO(pin=4, n=1, order="GRB", brightness=0.3)
      np.fill((255,0,0))
      np.write()
    """
    ORDER_MAPS = {
        "RGB": (0, 1, 2),
        "RBG": (0, 2, 1),
        "GRB": (1, 0, 2),
        "GBR": (1, 2, 0),
        "BRG": (2, 0, 1),
        "BGR": (2, 1, 0),
    }

    def __init__(self, pin, n, order="GRB", brightness=1.0, sm_id=0, freq=8_000_000):
        self.pin = int(pin)
        self.n = int(n)
        self.order_name = order
        self.order = self.ORDER_MAPS.get(order, (1, 0, 2))
        # Normalize brightness to float 0.0..1.0
        if isinstance(brightness, int):
            self.brightness = max(0.0, min(1.0, brightness / 255.0))
        else:
            self.brightness = max(0.0, min(1.0, float(brightness)))
        self.sm_id = int(sm_id)
        # buffer of logical RGB tuples (0-255)
        self.buf = [(0, 0, 0)] * self.n
        # create state machine
        self.sm = rp2.StateMachine(self.sm_id, _ws2812, freq=freq, sideset_base=Pin(self.pin))
        self.sm.active(1)

    def _pack_color(self, rgb):
        """Return 24-bit integer for the state machine in the chosen order, applying brightness."""
        r, g, b = rgb
        # clamp inputs to 0..255
        r = max(0, min(255, int(r)))
        g = max(0, min(255, int(g)))
        b = max(0, min(255, int(b)))
        # apply brightness scaling
        if self.brightness != 1.0:
            r = int(r * self.brightness)
            g = int(g * self.brightness)
            b = int(b * self.brightness)
        # arrange into 24-bit per configured order (MSB first)
        vals = [r, g, b]
        packed = (vals[self.order[0]] << 16) | (vals[self.order[1]] << 8) | (vals[self.order[2]])
        return packed

    def write(self):
        """
        Send the current buffer to the LED chain. Blocking until all data pushed.
        """
        # Build an array of 24-bit values (unsigned int)
        arr = array.array("I", (self._pack_color(c) for c in self.buf))
        # Using sm.put with shift parameter of 8 allows sending the array of 24-bit values
        try:
            self.sm.put(arr, 8)
        except Exception:
            # Fallback: push values one by one
            for v in arr:
                self.sm.put(v)
        # A small delay to ensure latch (optional)
        # Note: For single pixel this is usually fine; user can insert more delay if needed.

    def fill(self, color):
        """Fill buffer with color tuple (r,g,b) logical order."""
        self.buf = [tuple(color)] * self.n

    def __setitem__(self, index, color):
        """Set pixel at index (supports slice)"""
        if isinstance(index, slice):
            rng = range(*index.indices(self.n))
            if isinstance(color, tuple):
                for i in rng:
                    self.buf[i] = tuple(color)
            else:
                raise TypeError("color must be a tuple for slice assignment")
        else:
            if index < 0:
                index = self.n + index
            if not (0 <= index < self.n):
                raise IndexError("pixel index out of range")
            self.buf[index] = tuple(color)

    def __getitem__(self, index):
        return self.buf[index]

    def deinit(self):
        """Deactivate the state machine and clear buffer."""
        try:
            self.sm.active(0)
        except Exception:
            pass
        self.fill((0, 0, 0))
        try:
            self.write()
        except Exception:
            pass

    # convenience helpers
    def off(self):
        self.fill((0, 0, 0))
        self.write()

    def show_color(self, color):
        self.fill(color)
        self.write()