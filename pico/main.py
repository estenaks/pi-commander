from machine import Pin, SPI, PWM
import framebuf, time, gc
import urequests
from display import show_image
from secrets import SERVER

# --------------------------
# LCD driver (unchanged)
# --------------------------
class LCD_3inch5(framebuf.FrameBuffer):
    def __init__(self):
        self.cs  = Pin(9,  Pin.OUT)
        self.rst = Pin(15, Pin.OUT)
        self.dc  = Pin(8,  Pin.OUT)
        self.cs(1); self.dc(1); self.rst(1)
        self.spi = SPI(1, 60_000_000, sck=Pin(10), mosi=Pin(11), miso=Pin(12))
        self.width  = 320
        self.height = 160
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
DEFAULT_BUTTON_PIN = 4   # placeholder — replace with physical pin number on your board
LED_PIN = 25              # Pico on-board LED is usually GPIO25

current_player = 1
_busy = False  # prevent re-entrancy

# LED pin init
led = Pin(LED_PIN, Pin.OUT)
led.value(0)

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
        show_image(lcd, SERVER, player=current_player, face="front")
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

# --------------------------
# Start: show initial player
# --------------------------
print("Showing initial player", current_player)
try:
    show_image(lcd, SERVER, player=current_player, face="front")
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
    # Keep the interpreter alive; scheduled callbacks will run
    time.sleep(0.25)