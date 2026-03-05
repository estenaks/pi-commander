"""
pi-commander — MicroPython for Pico 2 W + Waveshare Pico-Eval-Board.

Touch zones (landscape 480×320):

    y < 30                        → brightness up   (full width)
    y >= 290                      → brightness down (full width)
    y 30–289, x < 120             → -1 counter
    y 30–289, x >= 360            → +1 counter
    y 30–289, x 120–359           → flip front/back, reset counter

Backlight:
    Full brightness on boot
    No touch for 30s → dim to 20%
    First touch while dimmed → wake only (no card action)
    Next touch → normal action

KEY button (physical):
    Cycles players 1→2→3→4→1, always acts (never swallowed by dim state)

Requires on Pico filesystem:
    ili9488.py    ILI9488 SPI display driver
    xpt2046.py    XPT2046 SPI touch driver
    sdcard.py     SD card block driver

!! Verify all GPIO pins against the Waveshare Pico-Eval-Board schematic !!
   https://www.waveshare.com/wiki/Pico-Eval-Board
"""

import os
import gc
import time
import network
import urequests
from machine import Pin, SPI, PWM

# ---- User config ----
WIFI_SSID = "your-ssid"
WIFI_PASS = "your-password"
SERVER    = "http://raspberrypi.local"  # or bare IP

# ---- Display — ILI9488, SPI1 — verify pins! ----
LCD_SPI  = 1
LCD_SCK  = 10
LCD_MOSI = 11
LCD_MISO = 12
LCD_CS   = 9
LCD_DC   = 8
LCD_RST  = 15
LCD_BL   = 13

# ---- Touch — XPT2046, same SPI bus, separate CS — verify pins! ----
TOUCH_CS  = 16
TOUCH_IRQ = 17

# ---- SD card — SPI0 — verify pins! ----
SD_SPI   = 0
SD_SCK   = 18
SD_MOSI  = 19
SD_MISO  = 20
SD_CS    = 22

# ---- KEY button — verify pin! ----
BTN_KEY = Pin(21, Pin.IN, Pin.PULL_UP)

# ---- Touch zones (landscape 480×320) ----
ZONE_LEFT_MAX  = 120   # x < 120  → dec
ZONE_RIGHT_MIN = 360   # x >= 360 → inc
ZONE_TOP_MAX   = 30    # y < 30   → brightness up   (full width, claims corners)
ZONE_BOT_MIN   = 290   # y >= 290 → brightness down (full width, claims corners)

# ---- Touch calibration — tune after first flash ----
TOUCH_X_MIN = 200
TOUCH_X_MAX = 3800
TOUCH_Y_MIN = 200
TOUCH_Y_MAX = 3800

# ---- Backlight ----
BL_FREQ     = 1000
BL_FULL     = 65535  # 100%
BL_DIM      = 13107  # 20% — auto-dim level
BL_STEP     = 6553   # 10% per tap
BL_MIN      = 6553   # 10% — manual minimum so screen never goes fully dark
DIM_TIMEOUT = 30_000 # ms

# ---- App ----
PLAYERS  = [1, 2, 3, 4]
SD_MOUNT = "/sd"
BMP_DIR  = SD_MOUNT + "/bmps"

# ---- Colours RGB565 ----
BLACK        = 0x0000
WHITE        = 0xFFFF
DARK_BLUE    = 0x0299   # +1 counter text
DARK_RED     = 0x6001   # -1 counter text
DARK_BLUE_BG = 0x0102   # +1 counter rect background
DARK_RED_BG  = 0x3000   # -1 counter rect background

# ---- OTA ----

OTA_VERSION_URL = SERVER + "/ota/version"
OTA_SOURCE_URL  = SERVER + "/ota/main.py"
OTA_SHA_FILE    = "/ota_sha.txt"   # stored on Pico flash


def _ota_read_local_sha() -> str:
    try:
        with open(OTA_SHA_FILE) as f:
            return f.read().strip()
    except OSError:
        return ""


def _ota_write_local_sha(sha: str):
    with open(OTA_SHA_FILE, "w") as f:
        f.write(sha)


def ota_check_and_update():
    """
    Compare the server's git SHA for pico/main.py against the last-known SHA.
    If different: download the new file, save it, persist the SHA, reboot.
    Safe to call before display/touch init — only needs WiFi.
    """
    print("OTA: checking…")
    try:
        r = urequests.get(OTA_VERSION_URL, timeout=10)
        data = r.json()
        r.close()
        remote_sha = data.get("sha", "")
    except Exception as e:
        print("OTA: version check failed:", e)
        return  # non-fatal — carry on with existing main.py

    local_sha = _ota_read_local_sha()
    print(f"OTA: local={local_sha[:12] or 'none'}  remote={remote_sha[:12]}")

    if remote_sha and remote_sha != local_sha:
        print("OTA: update found — downloading…")
        try:
            r = urequests.get(OTA_SOURCE_URL, timeout=30)
            new_code = r.content
            r.close()
        except Exception as e:
            print("OTA: download failed:", e)
            return  # non-fatal

        # Write to a temp file first so a power-cut can't brick the Pico
        with open("/main_new.py", "wb") as f:
            f.write(new_code)

        # Atomic-ish swap (MicroPython has no os.rename on flash, so we copy)
        import os as _os
        try:
            _os.remove("/main.py")
        except OSError:
            pass
        _os.rename("/main_new.py", "/main.py")

        _ota_write_local_sha(remote_sha)
        print("OTA: update applied — rebooting…")
        import machine
        machine.reset()  # reboot into the new code
    else:
        print("OTA: up to date.")


# ---- Backlight ----

class Backlight:
    def __init__(self, pin: int):
        self._pwm   = PWM(Pin(pin), freq=BL_FREQ)
        self._level = BL_FULL
        self._pwm.duty_u16(BL_FULL)

    def set(self, duty: int):
        self._level = max(BL_MIN, min(BL_FULL, duty))
        self._pwm.duty_u16(self._level)

    def full(self):
        self._level = BL_FULL
        self._pwm.duty_u16(BL_FULL)

    def dim(self):
        # Don't update _level — so step_up/down resume from pre-dim level
        self._pwm.duty_u16(BL_DIM)

    def step_up(self):
        self.set(self._level + BL_STEP)

    def step_down(self):
        self.set(self._level - BL_STEP)


# ---- Display state ----

class DisplayState:
    """Tracks awake/dimmed state and last-touch timestamp."""
    AWAKE  = "awake"
    DIMMED = "dimmed"

    def __init__(self, backlight: Backlight):
        self.bl          = backlight
        self.state       = self.AWAKE
        self._last_touch = time.ticks_ms()

    def touch(self) -> bool:
        """
        Call on every touch/button event.
        Returns True  → act on the touch normally.
        Returns False → wake-only, swallow the touch.
        """
        self._last_touch = time.ticks_ms()
        if self.state == self.DIMMED:
            self.state = self.AWAKE
            self.bl.full()
            return False
        return True

    def tick(self):
        """Call every main loop iteration — triggers auto-dim after timeout."""
        if self.state == self.AWAKE:
            if time.ticks_diff(time.ticks_ms(), self._last_touch) > DIM_TIMEOUT:
                self.state = self.DIMMED
                self.bl.dim()


# ---- SD ----

def mount_sd():
    import sdcard
    spi = SPI(SD_SPI, baudrate=4_000_000,
              sck=Pin(SD_SCK), mosi=Pin(SD_MOSI), miso=Pin(SD_MISO))
    sd = sdcard.SDCard(spi, Pin(SD_CS, Pin.OUT))
    os.mount(os.VfsFat(sd), SD_MOUNT)
    print("SD mounted")
    try:
        os.mkdir(BMP_DIR)
    except OSError:
        pass  # already exists


# ---- WiFi ----

def wifi_connect():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        return
    print(f"Connecting to {WIFI_SSID}…")
    wlan.connect(WIFI_SSID, WIFI_PASS)
    for _ in range(20):
        if wlan.isconnected():
            print("WiFi OK:", wlan.ifconfig()[0])
            return
        time.sleep(1)
    raise RuntimeError("WiFi connection failed")


# ---- Download ----

def bmp_path(player, face):
    return f"{BMP_DIR}/player{player}_{face}.bmp"


def download_all(lcd=None):
    print("Downloading BMPs…")
    r = urequests.get(f"{SERVER}/bmp/all", timeout=10)
    manifest = r.json()
    r.close()
    for entry in manifest["files"]:
        p, face, url = entry["player"], entry["face"], entry["url"]
        path = bmp_path(p, face)
        print(f"  {url} -> {path}")
        if lcd:
            lcd.fill_rect(0, 130, 480, 30, BLACK)
            lcd.text(f"GET player{p} {face}…", 10, 140, WHITE)
            lcd.show()
        r = urequests.get(f"{SERVER}{url}", timeout=15)
        with open(path, "wb") as f:
            while True:
                chunk = r.raw.read(4096)
                if not chunk:
                    break
                f.write(chunk)
        r.close()
        gc.collect()
    print("Done.")


# ---- Display ----

def init_display():
    import ili9488
    spi = SPI(LCD_SPI, baudrate=40_000_000,
              sck=Pin(LCD_SCK), mosi=Pin(LCD_MOSI), miso=Pin(LCD_MISO))
    lcd = ili9488.ILI9488(
        spi,
        cs=Pin(LCD_CS,  Pin.OUT),
        dc=Pin(LCD_DC,  Pin.OUT),
        rst=Pin(LCD_RST, Pin.OUT),
        bl=Pin(LCD_BL,  Pin.OUT),
    )
    lcd.init()
    lcd.rotation(1)   # landscape — try rotation(3) if upside down
    return lcd, spi   # return spi so touch can share the bus


def show_bmp(lcd, player, face):
    """Stream BMP pixels directly to the display, skipping the 54-byte header."""
    path = bmp_path(player, face)
    print(f"Displaying {path}")
    with open(path, "rb") as f:
        f.seek(54)
        lcd.set_window(0, 0, 479, 319)
        while True:
            chunk = f.read(4096)
            if not chunk:
                break
            lcd.write_data(chunk)


def draw_counter(lcd, counter):
    """Colored rect + text overlay centred on the card."""
    rx, ry, rw, rh = 160, 130, 160, 60

    if counter == 0:
        lcd.fill_rect(rx, ry, rw, rh, BLACK)
        return

    sign  = "+" if counter > 0 else ""
    label = f"{sign}{counter}/{sign}{counter}"
    color = DARK_BLUE    if counter > 0 else DARK_RED
    bg    = DARK_BLUE_BG if counter > 0 else DARK_RED_BG

    lcd.fill_rect(rx, ry, rw, rh, bg)
    x = rx + (rw - len(label) * 8) // 2
    y = ry + (rh - 8) // 2
    lcd.text(label, x, y, color)
    lcd.show()


# ---- Touch ----

def init_touch(spi):
    import xpt2046
    return xpt2046.XPT2046(
        spi,
        cs=Pin(TOUCH_CS, Pin.OUT),
        int_pin=Pin(TOUCH_IRQ, Pin.IN),
        x_min=TOUCH_X_MIN, x_max=TOUCH_X_MAX,
        y_min=TOUCH_Y_MIN, y_max=TOUCH_Y_MAX,
    )


def get_touch_zone(touch):
    """
    Returns 'dec', 'inc', 'flip', 'bright_up', 'bright_down', or None.

    Zone map (480×320 landscape):
        y < 30                    → bright_up   (full width, claims corners)
        y >= 290                  → bright_down (full width, claims corners)
        y 30–289, x < 120         → dec
        y 30–289, x >= 360        → inc
        y 30–289, x 120–359       → flip
    """
    if not touch.is_touched():
        return None
    coords = touch.get_touch()
    if coords is None:
        return None
    tx, ty = coords

    # Top/bottom brightness strips — full width, corners included
    if ty < ZONE_TOP_MAX:
        return "bright_up"
    if ty >= ZONE_BOT_MIN:
        return "bright_down"

    # Middle band — side and centre zones
    if tx < ZONE_LEFT_MAX:
        return "dec"
    if tx >= ZONE_RIGHT_MIN:
        return "inc"
    return "flip"


# ---- Debounce ----

_btn_last      = 0
_touch_last    = 0
DEBOUNCE       = 300   # ms
TOUCH_DEBOUNCE = 400   # ms


def key_pressed():
    global _btn_last
    now = time.ticks_ms()
    if not BTN_KEY.value() and time.ticks_diff(now, _btn_last) > DEBOUNCE:
        _btn_last = now
        return True
    return False


def touch_event(touch):
    """Returns zone string or None, with debounce."""
    global _touch_last
    now = time.ticks_ms()
    if time.ticks_diff(now, _touch_last) < TOUCH_DEBOUNCE:
        return None
    zone = get_touch_zone(touch)
    if zone:
        _touch_last = now
    return zone


# ---- Main ----

def main():
    mount_sd()
    wifi_connect()
    ota_check_and_update()

    bl           = Backlight(LCD_BL)
    lcd, lcd_spi = init_display()
    ds           = DisplayState(bl)

    lcd.fill(BLACK)
    lcd.text("Downloading BMPs…", 10, 140, WHITE)
    lcd.show()

    download_all(lcd=lcd)

    touch = init_touch(lcd_spi)

    player  = 1
    face    = "front"
    counter = 0
    show_bmp(lcd, player, face)

    while True:
        ds.tick()  # check dim timeout every loop

        # KEY button — always wakes and acts, never swallowed
        if key_pressed():
            ds.touch()  # reset dim timer without swallowing
            player  = (player % len(PLAYERS)) + 1
            face    = "front"
            counter = 0
            show_bmp(lcd, player, face)

        # Touch
        raw_zone = touch_event(touch)
        if raw_zone:
            act = ds.touch()  # False = wake-only, True = act on it
            if act:
                if raw_zone == "flip":
                    face    = "back" if face == "front" else "front"
                    counter = 0
                    show_bmp(lcd, player, face)
                elif raw_zone == "inc":
                    counter += 1
                    draw_counter(lcd, counter)
                elif raw_zone == "dec":
                    counter -= 1
                    draw_counter(lcd, counter)
                elif raw_zone == "bright_up":
                    bl.step_up()
                elif raw_zone == "bright_down":
                    bl.step_down()

        time.sleep_ms(20)


main()