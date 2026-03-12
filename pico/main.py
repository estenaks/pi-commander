from machine import Pin, SPI, PWM
import framebuf, time, gc
from display import show_image
from secrets import SERVER

# PIO NeoPixel driver (optional)
try:
    from pio_neopixel import NeoPixelPIO
except Exception:
    NeoPixelPIO = None

# Free up some memory
try:
    gc.collect()
    print("main: free memory before LCD alloc:", gc.mem_free())
except Exception:
    pass

# --------------------------
# LCD driver (kept minimal for used methods)
# --------------------------
class LCD_3inch5(framebuf.FrameBuffer):
    TP_CS = 16
    TP_IRQ = 17

    def __init__(self):
        self.width = 320
        self.height = 160
        self.cs  = Pin(9,  Pin.OUT)
        self.rst = Pin(15, Pin.OUT)
        self.dc  = Pin(8,  Pin.OUT)
        self.tp_cs = Pin(self.TP_CS, Pin.OUT)
        self.irq = Pin(self.TP_IRQ, Pin.IN, Pin.PULL_UP)
        self.cs(1); self.dc(1); self.rst(1)
        self.tp_cs(1)
        self.spi = SPI(1, 60_000_000, sck=Pin(10), mosi=Pin(11), miso=Pin(12))
        try:
            self.buffer = bytearray(self.width * self.height * 2)
        except Exception as e:
            print("LCD alloc failed:", e)
            try:
                gc.collect()
                print("free after GC:", gc.mem_free())
            except Exception:
                pass
            raise
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
        self._dat(y_end   >> 8); self._dat(y_end & 0xFF)
        self._cmd(0x2C)
        self.cs(1); self.dc(1); self.cs(0)
        self.spi.write(self.buffer)
        self.cs(1)

    def show_up(self):   self._show(0x000, 0x09F)
    def show_mid(self):  self._show(0x0A0, 0x13F)
    def show_down(self): self._show(0x140, 0x1DF)

    def touch_present(self):
        try:
            return self.irq() == 0
        except Exception:
            try:
                return self.irq.value() == 0
            except Exception:
                return False

    def touch_get(self):
        if not self.touch_present():
            return None
        self.spi = SPI(1, 5_000_000, sck=Pin(10), mosi=Pin(11), miso=Pin(12))
        self.tp_cs(0)
        X_Point = 0
        Y_Point = 0
        try:
            for i in range(3):
                self.spi.write(bytearray([0xD0]))
                rd = self.spi.read(2)
                time.sleep_us(10)
                X_Point += (((rd[0] << 8) + rd[1]) >> 3)
                self.spi.write(bytearray([0x90]))
                rd = self.spi.read(2)
                time.sleep_us(10)
                Y_Point += (((rd[0] << 8) + rd[1]) >> 3)
            X_Point = X_Point / 3.0
            Y_Point = Y_Point / 3.0
        finally:
            self.tp_cs(1)
            self.spi = SPI(1, 60_000_000, sck=Pin(10), mosi=Pin(11), miso=Pin(12))
        return [X_Point, Y_Point]


lcd = LCD_3inch5()

try:
    gc.collect()
    print("main: free memory after LCD alloc:", gc.mem_free())
except Exception:
    pass

# --------------------------
# NeoPixel + palette
# --------------------------
NP_PIN = 4
NP_ORDER = "GRB"
NP_SM_ID = 0

TARGET_BRIGHTNESS = 0.5
FADE_STEPS = 36
FADE_STEP_DELAY = 0.02

if NeoPixelPIO is not None:
    try:
        npixel = NeoPixelPIO(pin=NP_PIN, n=1, order=NP_ORDER, brightness=1.0, sm_id=NP_SM_ID)
    except Exception as e:
        print("NeoPixelPIO init failed:", e)
        npixel = None
else:
    npixel = None
# Temp while testing: 
# npixel = None

PALETTE_WUBRG = {
    "W": (210, 210, 150),
    "U": (0, 40, 200),
    "B": (20, 10, 0),
    "R": (220, 0, 0),
    "G": (0, 115, 20),
    "C": (180, 184, 192),
    "L": (120, 78, 40),
}
GOLD1 = (255, 191, 0)
GOLD2 = (255, 220, 115)
_last_colors_cache = {}
current_face = "front"


# --------------------------
# helpers: clamp, lerp, scale
# --------------------------
def clamp255(v):
    if v < 0: return 0
    if v > 255: return 255
    return int(v)

def lerp_tuple(a, b, t):
    return (int(a[0] + (b[0] - a[0]) * t),
            int(a[1] + (b[1] - a[1]) * t),
            int(a[2] + (b[2] - a[2]) * t))

def scale_color(rgb, scale):
    if scale <= 0:
        return (0, 0, 0)
    if scale >= 1:
        return (clamp255(rgb[0]), clamp255(rgb[1]), clamp255(rgb[2]))
    return (clamp255(int(rgb[0] * scale)),
            clamp255(int(rgb[1] * scale)),
            clamp255(int(rgb[2] * scale)))


# --------------------------
# HSV conversions + variants (recommended)
# --------------------------
def rgb_to_hsv(rgb):
    # returns (h in 0..360, s in 0..1, v in 0..1)
    r = rgb[0] / 255.0
    g = rgb[1] / 255.0
    b = rgb[2] / 255.0
    mx = max(r, g, b)
    mn = min(r, g, b)
    d = mx - mn
    if d == 0:
        h = 0.0
    elif mx == r:
        h = (60 * ((g - b) / d) + 360) % 360
    elif mx == g:
        h = (60 * ((b - r) / d) + 120) % 360
    else:
        h = (60 * ((r - g) / d) + 240) % 360
    s = 0.0 if mx == 0 else d / mx
    v = mx
    return h, s, v

def hsv_to_rgb(h, s, v):
    # expects h in 0..360, s and v in 0..1
    if s == 0.0:
        val = clamp255(int(v * 255))
        return (val, val, val)
    h = h % 360
    hi = int(h // 60)  # 0..5
    f = (h / 60.0) - hi
    p = v * (1.0 - s)
    q = v * (1.0 - s * f)
    t = v * (1.0 - s * (1.0 - f))
    if hi == 0:
        r, g, b = v, t, p
    elif hi == 1:
        r, g, b = q, v, p
    elif hi == 2:
        r, g, b = p, v, t
    elif hi == 3:
        r, g, b = p, q, v
    elif hi == 4:
        r, g, b = t, p, v
    else:
        r, g, b = v, p, q
    return (clamp255(int(r * 255)), clamp255(int(g * 255)), clamp255(int(b * 255)))

def variants_hsv(rgb, lighter_delta=0.30, darker_delta=0.45):
    """
    Return (lighter_rgb, darker_rgb).
    - darker: reduce V by factor (v * (1 - darker_delta))
    - lighter: push V toward 1 by fraction lighter_delta and slightly desaturate
    """
    h, s, v = rgb_to_hsv(rgb)
    v_dark = max(0.0, v * (1.0 - darker_delta))
    dark_rgb = hsv_to_rgb(h, s, v_dark)
    v_light = min(1.0, v + (1.0 - v) * lighter_delta)
    s_light = max(0.0, s * (1.0 - 0.15 * lighter_delta))
    light_rgb = hsv_to_rgb(h, s_light, v_light)
    return light_rgb, dark_rgb

def second_variant(rgb, lighter_delta=0.30, darker_delta=0.45):
    """Return (lighter, darker) using HSV variants (full-intensity values)."""
    return variants_hsv(rgb, lighter_delta=lighter_delta, darker_delta=darker_delta)


# --------------------------
# Non-blocking LED controller (shimmer + mono)
# --------------------------
class LEDController:
    def __init__(self, npixel):
        self.np = npixel
        self.mode = None
        self.phase = None
        self.start_ms = 0
        self.phase_ms = 0
        self.cur_from = (0,0,0)
        self.cur_to = (0,0,0)
        self.fade_ms = int(FADE_STEP_DELAY * 1000 * FADE_STEPS)

    def _now(self):
        return time.ticks_ms()

    def _start_phase(self, from_rgb, to_rgb, duration_ms, phase_name):
        self.cur_from = from_rgb
        self.cur_to = to_rgb
        self.start_ms = self._now()
        self.phase_ms = max(int(duration_ms), 1)
        self.phase = phase_name
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
        elapsed = time.ticks_diff(now, self.start_ms)
        if elapsed >= self.phase_ms:
            if self.mode == 'mono':
                self.phase = 'hold'
                self._step(1.0)
            elif self.mode == 'shimmer':
                self._advance_shimmer_phase()
        else:
            t = elapsed / self.phase_ms if self.phase_ms > 0 else 1.0
            self._step(t)

    def start_mono_variants(self, rgb, lighter_delta=0.30, darker_delta=0.45, target_brightness=TARGET_BRIGHTNESS):
        """
        For mono: compute lighter and darker variants and shimmer between them (ping-pong).
        This gives a more visible, hue-preserving mono animation than tiny multiplicative changes.
        """
        if not self.np:
            return
        lighter, darker = second_variant(rgb, lighter_delta=lighter_delta, darker_delta=darker_delta)
        # start shimmer between lighter and darker; controller will apply target_brightness scaling
        self.start_shimmer(lighter, darker, target_brightness=target_brightness)

    def start_shimmer(self, color_a_rgb, color_b_rgb, target_brightness=TARGET_BRIGHTNESS):
        if not self.np:
            return
        self.mode = 'shimmer'
        # scale to requested target brightness (driver brightness kept at 1.0)
        self.g1 = scale_color(color_a_rgb, target_brightness)
        self.g2 = scale_color(color_b_rgb, target_brightness)
        self.shimmer_phase = 'g1_to_g2'
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

led_ctrl = LEDController(npixel)


# --------------------------
# Robust fetch for color code
# --------------------------
def fetch_player_color_code(player):
    resp = None
    ureq = None
    try:
        # import urequests only when we actually fetch
        import urequests as ureq
        base = SERVER.strip()
        if base.startswith("http://") or base.startswith("https://"):
            base = base.rstrip("/")
            url = "{}/api/current/{}".format(base, player)
        else:
            url = "http://{}/api/current/{}".format(base, player)

        resp = ureq.get(url)
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
    finally:
        # ensure we drop urequests reference and collect after the fetch
        try:
            del ureq
        except Exception:
            pass
        try:
            gc.collect()
        except Exception:
            pass


# --------------------------
# display logic: always shimmer (mono uses lighter/darker variants)
# --------------------------
def display_colors_from_code(code):
    if not code or npixel is None:
        return
    code = code.upper()
    colors_seq = []
    for ch in code:
        if ch in PALETTE_WUBRG:
            colors_seq.append((ch, PALETTE_WUBRG[ch]))
    if not colors_seq:
        return

    if len(colors_seq) == 1:
        _, base = colors_seq[0]
        # use HSV-derived lighter/darker pair for a clear mono shimmer
        print("LED: mono -> compute lighter/darker and shimmer")
        led_ctrl.start_mono_variants(base, lighter_delta=0.30, darker_delta=0.45, target_brightness=TARGET_BRIGHTNESS)

    elif len(colors_seq) == 2:
        _, a = colors_seq[0]
        _, b = colors_seq[1]
        print("LED: two-color shimmer:", a, "<->", b)
        led_ctrl.start_shimmer(a, b, target_brightness=TARGET_BRIGHTNESS)

    else:
        print("LED: multi (3+) detected; using gold shimmer")
        led_ctrl.start_shimmer(GOLD1, GOLD2, target_brightness=TARGET_BRIGHTNESS)


# --------------------------
# Loading screen (kept)
# --------------------------
def loading_screen(lcd, player):
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
# Button + main loop (minimal)
# --------------------------
DEFAULT_BUTTON_PIN = 2
current_player = 1
counter = 0

_pending_player_change = False
_requested_player = None

def _on_button_pressed_irq(pin=None):
    global _pending_player_change, _requested_player
    try:
        _requested_player = (current_player % 4) + 1
        _pending_player_change = True
    except Exception:
        _pending_player_change = True
        _requested_player = 1

_use_poll_fallback = False
try:
    import button
    button.init(pin_no=DEFAULT_BUTTON_PIN, callback=_on_button_pressed_irq, debounce_ms=200)
    if hasattr(button, "poll_pressed") and getattr(button, "_HAS_SCHEDULE", True) is False:
        _use_poll_fallback = True
except Exception as e:
    print("button module not available or failed to init:", e)
    _use_poll_fallback = True

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
# Start: initial display + LED start
# --------------------------
print("Showing initial player", current_player)
try:
    show_image(lcd, SERVER, player=current_player, face="front", counter=counter)
except Exception as e:
    print("Initial display error:", e)

try:
    code = fetch_player_color_code(current_player)
    print("Fetched colors:", code)
    display_colors_from_code(code)
except Exception as e:
    print("Error fetching/starting initial LED behavior:", e)


# Main loop
while True:
    if _use_poll_fallback:
        try:
            import button as _btn
            if _btn.poll_pressed():
                _on_button_pressed_irq()
        except Exception:
            pass

    # Update LED animation frequently (non-blocking)
    try:
        led_ctrl.update()
    except Exception as e:
        print("LED update error:", e)

    # Handle pending player changes (do heavy work in main loop)
    if _pending_player_change:
        req = _requested_player
        _pending_player_change = False
        current_player = req
        print("Processing requested player change ->", current_player)

        try:
            loading_screen(lcd, current_player)
        except Exception as e:
            print("Loading screen failed:", e)

        try:
            show_image(lcd, SERVER, player=current_player, face="front", counter=counter)
        except Exception as e:
            print("Error fetching/displaying player {}: {}".format(current_player, e))

        try:
            code = fetch_player_color_code(current_player)
            print("Fetched colors:", code)
            # cache result (None is a valid cache entry meaning "no color data")
            _last_colors_cache[current_player] = code
            display_colors_from_code(code)
        except Exception as e:
            print("Error fetching/displaying player LED colors:", e)

    # Touch handling (kept)
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

        time.sleep_ms(40)
    else:
        time.sleep_ms(40)