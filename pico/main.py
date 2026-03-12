# pico/main.py
# Full patched file: integrates non-blocking NeoPixel behaviors driven by API color codes.
# - Non-blocking LEDController that animates indefinitely until a button press requests change.
# - Robust API fetch for color codes (handles SERVER with/without scheme).
# - Button IRQ sets a simple request flag; main loop does heavy work and switches LED behavior.
#
# NOTE: Save pio_neopixel.py alongside this file on the Pico (the PIO driver used here).
#       Adjust NP_PIN, NP_ORDER or LED pin if your board differs.

from machine import Pin, SPI, PWM
import framebuf, time, gc, sys
import urequests
from display import show_image
from secrets import SERVER

# New import: PIO NeoPixel driver (place pio_neopixel.py next to this file)
try:
    from pio_neopixel import NeoPixelPIO
except Exception:
    NeoPixelPIO = None

# --------------------------
# LCD driver (unchanged)
# --------------------------
class LCD_3inch5(framebuf.FrameBuffer):
    """
    Existing display class (keeps all previous methods) with touch init + touch_read
    added so your main loop can call lcd.touch_get() exactly like the working demo.
    """

    TP_CS = 16
    TP_IRQ = 17

    def __init__(self):
        # display colours
        self.RED   =   0x07E0
        self.GREEN =   0x001f
        self.BLUE  =   0xf800
        self.WHITE =   0xffff
        self.BLACK =   0x0000

        # framebuffer dimensions for one strip
        self.width  = 320
        self.height = 160

        # LCD control pins
        self.cs  = Pin(9,  Pin.OUT)
        self.rst = Pin(15, Pin.OUT)
        self.dc  = Pin(8,  Pin.OUT)

        # Touch controller pins
        self.tp_cs = Pin(self.TP_CS, Pin.OUT)
        self.irq = Pin(self.TP_IRQ, Pin.IN, Pin.PULL_UP)

        # bring lines to idle states
        self.cs(1); self.dc(1); self.rst(1)
        self.tp_cs(1)

        # high-speed SPI for display
        self.spi = SPI(1, 60_000_000, sck=Pin(10), mosi=Pin(11), miso=Pin(12))

        # framebuffer for one strip (RGB565)
        self.buffer = bytearray(self.width * self.height * 2)
        super().__init__(self.buffer, self.width, self.height, framebuf.RGB565)
        self._buf1 = bytearray(1)
        self._init()
        PWM(Pin(13), freq=1000).duty_u16(65535)

    def _cmd(self, c):
        self._buf1[0] = c
        self.cs(1); self.dc(0); self.cs(0)
        self.spi.write(self._buf1)
        self.cs(1)

    def _dat(self, d):
        self._buf1[0] = d
        self.cs(1); self.dc(1); self.cs(0)
        self.spi.write(self._buf1)
        self.cs(1)

    def _init(self):
        import time as _t
        self.rst(1); _t.sleep_ms(5)
        self.rst(0); _t.sleep_ms(10)
        self.rst(1); _t.sleep_ms(5)
        for c, d in [
            (0x21, []), (0xC2, [0x33]), (0xC5, [0x00, 0x1e, 0x80]),
            (0xB1, [0xB0]), (0x36, [0x28]),
            (0xE0, [0x00,0x13,0x18,0x04,0x0F,0x06,0x3a,0x56,0x4d,0x03,0x0a,0x06,0x30,0x3e,0x0f]),
            (0xE1, [0x00,0x13,0x18,0x01,0x11,0x06,0x38,0x34,0x4d,0x06,0x0d,0x0b,0x31,0x37,0x0f]),
            (0x3A, [0x55]), (0x11, []), (0x29, []), (0xB6, [0x00, 0x62]),
            (0x36, [0x88]),
        ]:
            self._cmd(c)
            for b in d:
                self._dat(b)
        _t.sleep_ms(120)

    def _show(self, y_start, y_end):
        self._cmd(0x2A)
        self._dat(0x00); self._dat(0x00)
        self._dat(0x01); self._dat(0x3F)
        self._cmd(0x2B)
        self._dat(y_start >> 8); self._dat(y_start & 0xFF)
        self._dat(y_end   >> 8); self._dat(y_end   & 0xFF)
        self._cmd(0x2C)
        self.cs(1); self.dc(1); self.cs(0)
        self.spi.write(self.buffer)
        self.cs(1)

    def show_up(self):   self._show(0x000, 0x09F)
    def show_mid(self):  self._show(0x0A0, 0x13F)
    def show_down(self): self._show(0x140, 0x1DF)

    def bl_ctrl(self, duty):
        pwm = PWM(Pin(13))
        pwm.freq(1000)
        if duty >= 100:
            pwm.duty_u16(65535)
        else:
            pwm.duty_u16(655 * duty)

    def draw_point(self, x, y, color):
        self._cmd(0x2A)
        self._dat((x-2) >> 8); self._dat((x-2) & 0xff)
        self._dat(x >> 8); self._dat(x & 0xff)

        self._cmd(0x2B)
        self._dat((y-2) >> 8); self._dat((y-2) & 0xff)
        self._dat(y >> 8); self._dat(y & 0xff)

        self._cmd(0x2C)

        self.cs(1)
        self.dc(1)
        self.cs(0)
        for i in range(0, 9):
            # write hi, lo bytes
            self.spi.write(bytearray([(color >> 8) & 0xFF]))
            self.spi.write(bytearray([color & 0xFF]))
        self.cs(1)

    # ---------------------
    # Touch helpers
    # ---------------------
    def touch_present(self):
        """Return True if touch controller reports a touch (IRQ low)."""
        try:
            return self.irq() == 0
        except Exception:
            try:
                return self.irq.value() == 0
            except Exception:
                return False

    def touch_get(self):
        """
        Read the resistive touch controller and return averaged [X_point, Y_point],
        or None if no touch is present.
        """
        if not self.touch_present():
            return None

        # switch to lower SPI speed for touch controller
        self.spi = SPI(1, 5_000_000, sck=Pin(10), mosi=Pin(11), miso=Pin(12))
        # select touch controller
        self.tp_cs(0)

        X_Point = 0
        Y_Point = 0
        try:
            for i in range(3):
                # read X (command 0xD0)
                self.spi.write(bytearray([0xD0]))
                rd = self.spi.read(2)
                time.sleep_us(10)
                X_Point += (((rd[0] << 8) + rd[1]) >> 3)

                # read Y (command 0x90)
                self.spi.write(bytearray([0x90]))
                rd = self.spi.read(2)
                time.sleep_us(10)
                Y_Point += (((rd[0] << 8) + rd[1]) >> 3)

            X_Point = X_Point / 3.0
            Y_Point = Y_Point / 3.0
        finally:
            # deselect touch controller and restore high-speed SPI for display
            self.tp_cs(1)
            self.spi = SPI(1, 60_000_000, sck=Pin(10), mosi=Pin(11), miso=Pin(12))

        return [X_Point, Y_Point]


lcd = LCD_3inch5()
gc.collect()

# --------------------------
# NeoPixel setup + non-blocking LED controller
# --------------------------
# PIO NeoPixel: data pin GP4, single pixel. Adjust if needed.
NP_PIN = 4
NP_ORDER = "GRB"      # confirmed
NP_SM_ID = 0

# Prototype target brightness (0.0..1.0). Change as needed.
TARGET_BRIGHTNESS = 0.5

# Fade timing parameters
FADE_STEPS = 36
FADE_STEP_DELAY = 0.02  # seconds

# Initialize NeoPixel PIO driver (if available)
if NeoPixelPIO is not None:
    try:
        npixel = NeoPixelPIO(pin=NP_PIN, n=1, order=NP_ORDER, brightness=1.0, sm_id=NP_SM_ID)
    except Exception as e:
        print("NeoPixelPIO init failed:", e)
        npixel = None
else:
    npixel = None

# Provide a safe 'led' for legacy blinking (onboard LED GP25)
try:
    led = Pin(25, Pin.OUT)
except Exception:
    led = None

# Palette for W U B R G (use the 'second' variants)
PALETTE_WUBRG = {
    "W": (249, 250, 244),   # white (second)
    "U": (14, 104, 171),    # blue (second)
    "B": (21, 11, 0),       # black (second)
    "R": (211, 32, 42),     # red (second)
    "G": (0, 115, 62),      # green (second)
}
# Golds used for 3+ behavior
GOLD1 = (255, 191, 0)
GOLD2 = (255, 220, 115)


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


# Non-blocking LED controller
class LEDController:
    """
    Stateful non-blocking LED animator.

    Modes:
      - mono: fade in to target and hold (implemented via shimmer with same color)
      - two: alternate A and B forever (fade_in -> hold -> fade_out -> next)  [kept for backward compat]
      - shimmer: crossfade between two arbitrary colors indefinitely (this replaces 'multi' gold shimmer)
    """
    def __init__(self, npixel):
        self.np = npixel
        self.mode = None
        self.phase = None
        self.start_ms = 0
        self.phase_ms = 0
        self.cur_from = (0,0,0)
        self.cur_to = (0,0,0)
        self.a_target = (0,0,0)
        self.b_target = (0,0,0)
        self.g1 = (0,0,0)
        self.g2 = (0,0,0)
        self.fade_ms = int(FADE_STEP_DELAY * 1000 * FADE_STEPS)
        self.two_hold_ms = 3000

    def _now(self):
        return time.ticks_ms()

    def _start_phase(self, from_rgb, to_rgb, duration_ms, phase_name):
        self.cur_from = from_rgb
        self.cur_to = to_rgb
        self.start_ms = self._now()
        self.phase_ms = max(int(duration_ms), 1)
        self.phase = phase_name
        # immediately draw initial color for the phase
        self._step(0.0)

    def _step(self, t):
        color = lerp_tuple(self.cur_from, self.cur_to, t)
        if self.np:
            try:
                self.np.show_color(color)
            except Exception:
                pass

    def update(self):
        if not self.mode or not self.np:
            return
        now = self._now()

        if self.mode == 'mono':
            if self.phase == 'fade_in':
                elapsed = time.ticks_diff(now, self.start_ms)
                if elapsed >= self.phase_ms:
                    self._step(1.0)
                    self.phase = 'hold'
                else:
                    t = elapsed / self.phase_ms
                    self._step(t)
            # hold: nothing to do

        elif self.mode == 'two':
            elapsed = time.ticks_diff(now, self.start_ms)
            if elapsed >= self.phase_ms:
                self._advance_two_phase()
            else:
                if self.phase_ms > 0:
                    t = elapsed / self.phase_ms
                    self._step(t)

        elif self.mode == 'shimmer':
            # shimmer crossfades between g1 and g2: phases 'g1_to_g2' and 'g2_to_g1'
            elapsed = time.ticks_diff(now, self.start_ms)
            if elapsed >= self.phase_ms:
                self._advance_shimmer_phase()
            else:
                t = elapsed / self.phase_ms
                self._step(t)

    # MONO helper: fades in and holds by using shimmer with identical colors
    def start_mono(self, rgb, target_brightness=TARGET_BRIGHTNESS):
        if not self.np:
            return
        tg = scale_color(rgb, target_brightness)
        self.mode = 'mono'
        self._start_phase((0,0,0), tg, self.fade_ms, 'fade_in')

    # TWO color (left as-is, indefinite)
    def start_two(self, a_rgb, b_rgb, target_brightness=TARGET_BRIGHTNESS, hold_ms=3000):
        if not self.np:
            return
        self.mode = 'two'
        self.two_hold_ms = hold_ms
        self.a_target = scale_color(a_rgb, target_brightness)
        self.b_target = scale_color(b_rgb, target_brightness)
        self.two_phase = 'a_fade_in'
        self._start_phase((0,0,0), self.a_target, self.fade_ms, self.two_phase)

    def _advance_two_phase(self):
        if self.two_phase == 'a_fade_in':
            self.two_phase = 'a_hold'
            self._start_phase(self.a_target, self.a_target, self.two_hold_ms, self.two_phase)
        elif self.two_phase == 'a_hold':
            self.two_phase = 'a_fade_out'
            self._start_phase(self.a_target, (0,0,0), self.fade_ms, self.two_phase)
        elif self.two_phase == 'a_fade_out':
            self.two_phase = 'b_fade_in'
            self._start_phase((0,0,0), self.b_target, self.fade_ms, self.two_phase)
        elif self.two_phase == 'b_fade_in':
            self.two_phase = 'b_hold'
            self._start_phase(self.b_target, self.b_target, self.two_hold_ms, self.two_phase)
        elif self.two_phase == 'b_hold':
            self.two_phase = 'b_fade_out'
            self._start_phase(self.b_target, (0,0,0), self.fade_ms, self.two_phase)
        elif self.two_phase == 'b_fade_out':
            self.two_phase = 'a_fade_in'
            self._start_phase((0,0,0), self.a_target, self.fade_ms, self.two_phase)
        else:
            self.two_phase = 'a_fade_in'
            self._start_phase((0,0,0), self.a_target, self.fade_ms, self.two_phase)

    # SHIMMER (generalized crossfade between any two colors)
    def start_shimmer(self, color_a_rgb, color_b_rgb, target_brightness=TARGET_BRIGHTNESS):
        """
        Begin crossfading between color_a and color_b indefinitely.
        If color_a == color_b this results in a steady color (useful for mono).
        """
        if not self.np:
            return
        self.mode = 'shimmer'
        self.g1 = scale_color(color_a_rgb, target_brightness)
        self.g2 = scale_color(color_b_rgb, target_brightness)
        self.shimmer_phase = 'g1_to_g2'
        # use a slightly slower crossfade for a pleasant shimmer
        self._start_phase(self.g1, self.g2, max(self.fade_ms * 2, 1), self.shimmer_phase)

    def _advance_shimmer_phase(self):
        if self.shimmer_phase == 'g1_to_g2':
            self.shimmer_phase = 'g2_to_g1'
            self._start_phase(self.g2, self.g1, max(self.fade_ms * 2, 1), self.shimmer_phase)
        else:
            self.shimmer_phase = 'g1_to_g2'
            self._start_phase(self.g1, self.g2, max(self.fade_ms * 2, 1), self.shimmer_phase)

    def stop(self):
        self.mode = None
        self.phase = None
        if self.np:
            try:
                self.np.off()
            except Exception:
                pass


# --- display_colors_from_code: always uses shimmer with two colors ----------------
def display_colors_from_code(code: str):
    """
    Always drives the LED using the shimmer function (two colors input).
    - For len == 1: call shimmer(color, color) -> steady
    - For len == 2: shimmer(color0, color1)
    - For len >= 3: pick two 'cold' colors present (priority U, G, W). If fewer than 2 cold colors,
      fall back to the first two distinct colors in the code list.
    """
    if not code or npixel is None:
        return
    code = code.upper()
    colors_seq = []
    for ch in code:
        if ch in PALETTE_WUBRG:
            colors_seq.append((ch, PALETTE_WUBRG[ch]))

    if not colors_seq:
        return

    # Helper to pick two "cold" colors from the ordered sequence.
    def pick_two_cold(seq):
        # Preference order for "cold" colors (user-chosen): U (blue), G (green), W (white), B (black), R (red)
        cold_priority = ['U', 'G', 'W', 'B', 'R']
        found = []
        # Keep original order relative to the code string, but filter by cold priority presence
        for key, rgb in seq:
            if key in cold_priority and key not in [k for k, _ in found]:
                found.append((key, rgb))
            if len(found) >= 2:
                break
        # If still fewer than 2, choose additional distinct colors from seq in order
        if len(found) < 2:
            for key, rgb in seq:
                if key not in [k for k, _ in found]:
                    found.append((key, rgb))
                if len(found) >= 2:
                    break
        # If only one distinct color overall, duplicate it
        if len(found) == 1:
            found.append(found[0])
        return found[0][1], found[1][1]

    if len(colors_seq) == 1:
        # mono -> steady color using shimmer with identical colors
        _, rgb = colors_seq[0]
        led_ctrl.start_shimmer(rgb, rgb, target_brightness=TARGET_BRIGHTNESS)
    elif len(colors_seq) == 2:
        _, a = colors_seq[0]
        _, b = colors_seq[1]
        led_ctrl.start_shimmer(a, b, target_brightness=TARGET_BRIGHTNESS)
    else:
        # 3+ -> pick two cold colors (fall back to first two)
        a_col, b_col = pick_two_cold(colors_seq)
        led_ctrl.start_shimmer(a_col, b_col, target_brightness=TARGET_BRIGHTNESS)


# create controller
led_ctrl = LEDController(npixel)

# --------------------------
# Robust fetch for color code (handles SERVER with/without scheme)
# --------------------------
def fetch_player_color_code(player):
    resp = None
    try:
        base = SERVER.strip()
        if base.startswith("http://") or base.startswith("https://"):
            base = base.rstrip("/")
            url = "{}/api/current/{}".format(base, player)
        else:
            url = "http://{}/api/current/{}".format(base, player)

        resp = urequests.get(url)
        # parse JSON safely
        data = None
        try:
            data = resp.json()
        finally:
            try:
                resp.close()
            except Exception:
                pass
            resp = None

        if not data:
            return None

        colors = data.get("colors")
        if colors is None:
            return None

        # Normalize possible types
        if isinstance(colors, (list, tuple)):
            parts = []
            for c in colors:
                if c is None:
                    continue
                parts.append(str(c).strip())
            s = "".join(parts)
            return s.upper() if s else None

        if isinstance(colors, str):
            s = colors.strip()
            return s.upper() if s else None

        # fallback for numbers / other types
        try:
            s = str(colors).strip()
            return s.upper() if s else None
        except Exception:
            return None

    except Exception as e:
        print("Failed to fetch color code:", e)
        try:
            if resp is not None:
                resp.close()
        except Exception:
            pass
        return None



# --------------------------
# UI helpers: loading screen (unchanged)
# --------------------------
def loading_screen(lcd, player, repeat_each_strip=True):
    BLACK = 0x0000
    WHITE = 0xFFFF
    msg = "Loading: P{}".format(player)
    for show_fn in (lcd.show_up, lcd.show_mid, lcd.show_down):
        lcd.fill(BLACK)
        char_w = 8
        x = max(0, (lcd.width - len(msg) * char_w) // 2)
        y = max(0, lcd.height // 2 - 8)
        try:
            lcd.text(msg, x, y, WHITE)
        except Exception:
            lcd.fill(0)
            lcd.buffer[0] = 0xFF
        show_fn()
        gc.collect()
        time.sleep_ms(50)


# --------------------------
# Button + main loop integration (non-blocking)
# --------------------------
DEFAULT_BUTTON_PIN = 2
current_player = 1
current_face = "front"
_busy = False  # legacy busy flag (not used for button IRQ)
counter = 0

# Pending-request flags set by IRQ-safe handler
_pending_player_change = False
_requested_player = None

# Lightweight IRQ-safe handler: only set request flag
def _on_button_pressed_irq(pin=None):
    global _pending_player_change, _requested_player
    try:
        # compute next player number quickly
        _requested_player = (current_player % 4) + 1
        _pending_player_change = True
    except Exception:
        # swallow errors in IRQ context
        _pending_player_change = True
        _requested_player = 1

# Initialize button module and wire IRQ callback.
_use_poll_fallback = False
try:
    import button
    # use the tiny IRQ handler
    button.init(pin_no=DEFAULT_BUTTON_PIN, callback=_on_button_pressed_irq, debounce_ms=200)
    if hasattr(button, "poll_pressed") and getattr(button, "_HAS_SCHEDULE", True) is False:
        _use_poll_fallback = True
except Exception as e:
    print("button module not available or failed to init:", e)
    _use_poll_fallback = True

# Config toggles
DEBUG_TOUCH = True
ORIENT_SWAP = False

DEBOUNCE_MS = 300
last_change_ts = 0

def map_touch_to_screen(raw_x, raw_y, lcd):
    if ORIENT_SWAP:
        mapped_x = int((raw_x - 430) * lcd.width / 3270)
        mapped_y = 320 - int((raw_y - 430) * 320 / 3270)
    else:
        mapped_x = int((raw_y - 430) * lcd.width / 3270)
        mapped_y = 320 - int((raw_x - 430) * 320 / 3270)
    if mapped_x < 0: mapped_x = 0
    if mapped_x > lcd.width: mapped_x = lcd.width
    return mapped_x, mapped_y


# --------------------------
# Start: show initial player + initial LED behavior
# --------------------------
print("Showing initial player", current_player)
try:
    show_image(lcd, SERVER, player=current_player, face="front", counter=counter)
except Exception as e:
    print("Initial display error:", e)

# Kick off initial LED behavior based on API (non-blocking)
try:
    code = fetch_player_color_code(current_player)
    print("Fetched colors:", code)
    display_colors_from_code(code)
except Exception as e:
    print("Error fetching/starting initial LED behavior:", e)


# --------------------------
# Main keep-alive loop (updates LED controller frequently)
# --------------------------
while True:
    # Poll fallback for button module if needed
    if _use_poll_fallback:
        try:
            import button as _btn
            if _btn.poll_pressed():
                _on_button_pressed_irq()
        except Exception:
            pass

    # Update LED animation (non-blocking)
    try:
        led_ctrl.update()
    except Exception as e:
        print("LED update error:", e)

    # Handle pending player change request (do heavy work in main loop)
    if _pending_player_change:
        # capture and clear flag quickly
        req = _requested_player
        _pending_player_change = False

        # switch player
        current_player = req
        print("Processing requested player change ->", current_player)

        # quick blink (legacy)
        try:
            if led:
                led.value(1)
        except Exception:
            pass

        # show loading screen and display image (blocking network work done here)
        try:
            loading_screen(lcd, current_player)
        except Exception as e:
            print("Loading screen failed:", e)

        try:
            show_image(lcd, SERVER, player=current_player, face="front", counter=counter)
        except Exception as e:
            print("Error fetching/displaying player {}: {}".format(current_player, e))

        # fetch color code and start LED behavior (non-blocking)
        try:
            code = fetch_player_color_code(current_player)
            print("Fetched colors:", code)
            display_colors_from_code(code)
        except Exception as e:
            print("Error fetching/displaying player LED colors:", e)

        # turn off legacy LED blink
        try:
            if led:
                led.value(0)
        except Exception:
            pass

    # Touch handling (unchanged)
    get = None
    try:
        get = lcd.touch_get()
    except Exception:
        get = None

    if get is not None:
        raw_x, raw_y = get[0], get[1]
        mapped_x, mapped_y = map_touch_to_screen(raw_x, raw_y, lcd)
        if DEBUG_TOUCH:
            print("raw:", raw_x, raw_y, "mapped:", mapped_x, mapped_y)

        MAP_H = 320
        bottom_threshold = MAP_H // 4
        top_threshold = (MAP_H * 3) // 4

        now = time.ticks_ms()
        if time.ticks_diff(now, last_change_ts) >= DEBOUNCE_MS:
            if mapped_y >= top_threshold:
                counter += 1
                last_change_ts = now
                print("TOUCH TOP -> counter", counter)
                try:
                    show_image(lcd, SERVER, player=current_player, face="front", counter=counter)
                except Exception as e:
                    print("display error:", e)
            elif mapped_y < bottom_threshold:
                counter -= 1
                last_change_ts = now
                print("TOUCH BOTTOM -> counter", counter)
                try:
                    show_image(lcd, SERVER, player=current_player, face="front", counter=counter)
                except Exception as e:
                    print("display error:", e)
            else:
                current_face = "back" if current_face == "front" else "front"
                last_change_ts = now
                print("TOUCH MIDDLE -> flip to", current_face)
                try:
                    show_image(lcd, SERVER, player=current_player, face=current_face, counter=counter)
                except Exception as e:
                    print("display error:", e)

        time.sleep_ms(40)  # keep loop responsive
    else:
        # no touch - still keep loop responsive so LED updates run smoothly
        time.sleep_ms(40)