from machine import Pin, SPI, PWM
import framebuf, time, gc
import urequests
from display import show_image
from secrets import SERVER

# --------------------------
# LCD driver (unchanged)
# --------------------------
class LCD_3inch5(framebuf.FrameBuffer):
    """
    Existing display class (keeps all previous methods) with touch init + touch_read
    added so your main loop can call lcd.touch_get() exactly like the working demo.
    Replace the existing class in pico/main.py with this class body (rest of file unchanged).
    """

    # Touch controller pins (kept local to this class so you don't need module-level constants)
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

        # LCD control pins (same as before)
        self.cs  = Pin(9,  Pin.OUT)
        self.rst = Pin(15, Pin.OUT)
        self.dc  = Pin(8,  Pin.OUT)

        # Touch controller pins
        # tp_cs is the touch controller chip select (output)
        self.tp_cs = Pin(self.TP_CS, Pin.OUT)
        # irq is the touch interrupt line; use internal pull-up so idle == 1
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
        # irq is pulled-up; when touched the controller pulls it low.
        try:
            return self.irq() == 0
        except Exception:
            # Some boards/micropython variants use value() - support both
            try:
                return self.irq.value() == 0
            except Exception:
                return False

    def touch_get(self):
        """
        Read the resistive touch controller and return averaged [X_point, Y_point],
        or None if no touch is present.

        Matches the demo: uses lower SPI clock, selects touch CS, sends 0xD0/0x90,
        reads 3 samples and returns their average.
        """
        # If no touch, return None quickly
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
# UI helpers: loading screen
# --------------------------
def loading_screen(lcd, player, repeat_each_strip=True):
    """
    Show a simple loading screen (text) for the given player.
    We draw to the LCD buffer and call the three show functions so the
    message appears across the whole physical display.
    """
    # Simple background + text color choices (RGB565 approximations)
    BLACK = 0x0000
    WHITE = 0xFFFF

    # Prepare message
    msg = "Loading: P{}".format(player)

    # For each strip, draw the same buffer and show it in place
    for show_fn in (lcd.show_up, lcd.show_mid, lcd.show_down):
        lcd.fill(BLACK)
        # estimate char width as 8 px; center horizontally, place vertically ~middle of strip
        char_w = 8
        x = max(0, (lcd.width - len(msg) * char_w) // 2)
        y = max(0, lcd.height // 2 - 8)
        try:
            lcd.text(msg, x, y, WHITE)
        except Exception:
            # Some ports may not have text; fall back to a single pixel indicator
            lcd.fill(0)
            lcd.buffer[0] = 0xFF
        show_fn()
        gc.collect()
        # small pause so the strip update is perceptible
        time.sleep_ms(50)

# --------------------------
# Button + LED setup
# --------------------------
DEFAULT_BUTTON_PIN = 2 
current_player = 1
current_face = "front"
_busy = False  # prevent re-entrancy

counter = 0

def _on_button_pressed():
    """
    Called when the button press is scheduled to run in VM context
    (either via micropython.schedule or polled fallback).
    """
    global current_player, _busy

    if _busy:
        # ignore presses while already fetching/displaying
        return
    _busy = True

    # cycle players 1..4
    current_player = (current_player % 4) + 1
    print("Button -> switching to player", current_player)

    # immediate quick blink to acknowledge press
    try:
        led.value(1)
    except Exception:
        pass

    # show loading screen (visual feedback)
    try:
        loading_screen(lcd, current_player)
    except Exception as e:
        print("Loading screen failed:", e)

    # keep LED on during network fetch/display for clearer feedback
    try:
        show_image(lcd, SERVER, player=current_player, face="front", counter=counter)
    except Exception as e:
        print("Error fetching/displaying player {}: {}".format(current_player, e))

    # turn LED off at end
    try:
        led.value(0)
    except Exception:
        pass

    # small settle
    time.sleep_ms(50)
    _busy = False
    gc.collect()

# Initialize button module and wire IRQ callback.
_use_poll_fallback = False
try:
    import button
    button.init(pin_no=DEFAULT_BUTTON_PIN, callback=_on_button_pressed, debounce_ms=200)
    # If schedule not available, button.poll_pressed() will be true and we set fallback
    if hasattr(button, "poll_pressed") and getattr(button, "_HAS_SCHEDULE", True) is False:
        _use_poll_fallback = True
except Exception as e:
    print("button module not available or failed to init:", e)
    _use_poll_fallback = True
    
# Config toggles for debugging and orientation
DEBUG_TOUCH = True      # set True to print raw and mapped values to REPL
ORIENT_SWAP = False     # if True, swap raw indices when mapping to screen X (use if mapping looks flipped)

# counter state and debounce as before
# counter = 0
DEBOUNCE_MS = 300
last_change_ts = 0

def map_touch_to_screen(raw_x, raw_y, lcd):
    """
    Map the raw touch readings to screen X/Y (same math as your working example).
    The demo used get[1] for X mapping; this function supports ORIENT_SWAP to swap indices.
    Returns (mapped_x, mapped_y)
    """
    if ORIENT_SWAP:
        # swap (use raw_x for X mapping instead of raw_y)
        mapped_x = int((raw_x - 430) * lcd.width / 3270)
        mapped_y = 320 - int((raw_y - 430) * 320 / 3270)
    else:
        mapped_x = int((raw_y - 430) * lcd.width / 3270)
        mapped_y = 320 - int((raw_x - 430) * 320 / 3270)

    # clamp mapped_x to [0, lcd.width]
    if mapped_x < 0: mapped_x = 0
    if mapped_x > lcd.width: mapped_x = lcd.width
    return mapped_x, mapped_y


# --------------------------
# Start: show initial player
# --------------------------
print("Showing initial player", current_player)
try:
    show_image(lcd, SERVER, player=current_player, face="front", counter=counter)
except Exception as e:
    print("Initial display error:", e)

# --------------------------
# Main keep-alive loop
# --------------------------
# If fallback polling is required (no micropython.schedule), poll the button.poll_pressed() flag.
# Otherwise we just sleep; scheduled callbacks will run when IRQ fires.
while True:
    if _use_poll_fallback:
        try:
            import button as _btn
            if _btn.poll_pressed():
                _on_button_pressed()
        except Exception:
            # If poll fails, continue; avoid crashing
            pass
    get = None
    try:
        get = lcd.touch_get()
    except Exception as e:
        # keep the loop robust if touch_get fails occasionally
        get = None

    if get is not None:
        raw_x, raw_y = get[0], get[1]

        mapped_x, mapped_y = map_touch_to_screen(raw_x, raw_y, lcd)

        if DEBUG_TOUCH:
            print("raw:", raw_x, raw_y, "mapped:", mapped_x, mapped_y)

        MAP_H = 320

        # thresholds for top / bottom quarters
        bottom_threshold = MAP_H // 4           # bottommost quarter (0..MAP_H/4)
        top_threshold = (MAP_H * 3) // 4        # topmost quarter (3/4..MAP_H)

        now = time.ticks_ms()
        if time.ticks_diff(now, last_change_ts) >= DEBOUNCE_MS:
            # Use mapped_y because the display is rotated: vertical touch axis maps to +/- zones.
            if mapped_y >= top_threshold:
                # topmost quadrant -> increment (this used to be "right")
                counter += 1
                last_change_ts = now
                print("TOUCH TOP -> counter", counter)
                try:
                    show_image(lcd, SERVER, player=current_player, face="front", counter=counter)
                except Exception as e:
                    print("display error:", e)

            elif mapped_y < bottom_threshold:
                # bottommost quadrant -> decrement (this used to be "left")
                counter -= 1
                last_change_ts = now
                print("TOUCH BOTTOM -> counter", counter)
                try:
                    show_image(lcd, SERVER, player=current_player, face="front", counter=counter)
                except Exception as e:
                    print("display error:", e)
            else:
                # middle zone -> flip the card face
                current_face = "back" if current_face == "front" else "front"
                last_change_ts = now
                print("TOUCH MIDDLE -> flip to", current_face)
                try:
                    show_image(lcd, SERVER, player=current_player, face=current_face, counter=counter)
                except Exception as e:
                    print("display error:", e)
        # small settle
        time.sleep_ms(80)
    else:
        # no touch
        time.sleep_ms(80)