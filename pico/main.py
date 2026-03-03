"""
pi-commander — MicroPython for Pico 2 W + Waveshare Pico-Eval-Board.

Touch zones (landscape 480×320):
    Left  25%  (x < 120)   → -1 counter
    Right 25%  (x >= 360)  → +1 counter
    Centre     (120–359)   → flip front/back, reset counter
KEY button (GP22)          → cycle players

Requires on Pico filesystem:
    ili9488.py    ILI9488 SPI display driver
    xpt2046.py    XPT2046 SPI touch driver
    sdcard.py     SD card block driver (ships with MicroPython extras)

!! Verify all GPIO pins against the Waveshare Pico-Eval-Board schematic !!
   https://www.waveshare.com/wiki/Pico-Eval-Board
"""

import os
import gc
import time
import network
import urequests
from machine import Pin, SPI

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
TOUCH_CS  = 16   # !! check schematic
TOUCH_IRQ = 17   # !! check schematic (IRQ/PENIRQ pin)

# ---- SD card — SPI0 — verify pins! ----
SD_SPI   = 0
SD_SCK   = 18
SD_MOSI  = 19
SD_MISO  = 20
SD_CS    = 22

# ---- KEY button (single physical button on board) — verify pin! ----
BTN_KEY  = Pin(21, Pin.IN, Pin.PULL_UP)  # !! check schematic

# ---- Touch zone x boundaries (display width = 480) ----
ZONE_LEFT_MAX  = 120   # x < 120  → dec counter
ZONE_RIGHT_MIN = 360   # x >= 360 → inc counter

# ---- Touch calibration — tune after first flash ----
TOUCH_X_MIN = 200
TOUCH_X_MAX = 3800
TOUCH_Y_MIN = 200
TOUCH_Y_MAX = 3800

PLAYERS  = [1, 2, 3, 4]
SD_MOUNT = "/sd"
BMP_DIR  = SD_MOUNT + "/bmps"

# ---- Colours RGB565 ----
BLACK  = 0x0000
WHITE  = 0xFFFF
YELLOW = 0xFFE0
RED    = 0xF800


# ---- SD ----

def mount_sd():
    import sdcard
    spi = SPI(SD_SPI, baudrate=4_000_000,
              sck=Pin(SD_SCK), mosi=Pin(SD_MOSI), miso=Pin(SD_MISO))
    sd  = sdcard.SDCard(spi, Pin(SD_CS, Pin.OUT))
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
        width=480, height=320,
    )
    lcd.init()
    return lcd


def show_bmp(lcd, player, face):
    """Stream BMP pixels to display, skipping the 54-byte header."""
    with open(bmp_path(player, face), "rb") as f:
        f.seek(54)
        lcd.set_window(0, 0, 479, 319)
        while True:
            chunk = f.read(4096)
            if not chunk:
                break
            lcd.write_data(chunk)


def draw_counter(lcd, counter):
    """Redraw counter overlay without redrawing the full BMP."""
    lcd.fill_rect(120, 120, 240, 80, BLACK)
    if counter == 0:
        return
    sign  = "+" if counter > 0 else ""
    label = f"{sign}{counter}/{sign}{counter}"
    color = YELLOW if counter > 0 else RED
    x     = 240 - len(label) * 4  # 8px chars, rough centre
    lcd.text(label, x, 152, color)
    lcd.show()


# ---- Touch ----

def init_touch(lcd_spi):
    """XPT2046 shares the LCD SPI bus at a lower baud rate."""
    import xpt2046
    return xpt2046.XPT2046(
        lcd_spi,
        cs=Pin(TOUCH_CS, Pin.OUT),
        int_pin=Pin(TOUCH_IRQ, Pin.IN),
        x_min=TOUCH_X_MIN, x_max=TOUCH_X_MAX,
        y_min=TOUCH_Y_MIN, y_max=TOUCH_Y_MAX,
    )


def get_touch_zone(touch, display_w=480):
    """
    Returns 'dec', 'inc', 'flip', or None.
    Reads raw touch, maps to display pixel x, classifies zone.
    """
    if not touch.is_touched():
        return None
    coords = touch.get_touch()
    if coords is None:
        return None
    tx, _ = coords
    if tx < ZONE_LEFT_MAX:
        return "dec"
    if tx >= ZONE_RIGHT_MIN:
        return "inc"
    return "flip"


# ---- Button debounce ----

_btn_last = 0
DEBOUNCE  = 300  # ms

def key_pressed():
    global _btn_last
    now = time.ticks_ms()
    if not BTN_KEY.value() and time.ticks_diff(now, _btn_last) > DEBOUNCE:
        _btn_last = now
        return True
    return False


# ---- Touch debounce ----
_touch_last = 0
TOUCH_DEBOUNCE = 400  # ms — resistive touch can be noisy

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

    import ili9488
    lcd_spi = SPI(LCD_SPI, baudrate=40_000_000,
                  sck=Pin(LCD_SCK), mosi=Pin(LCD_MOSI), miso=Pin(LCD_MISO))

    import ili9488 as _ili
    lcd = _ili.ILI9488(
        lcd_spi,
        cs=Pin(LCD_CS,  Pin.OUT),
        dc=Pin(LCD_DC,  Pin.OUT),
        rst=Pin(LCD_RST, Pin.OUT),
        bl=Pin(LCD_BL,  Pin.OUT),
        width=480, height=320,
    )
    lcd.init()
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
        # Physical KEY button → cycle player
        if key_pressed():
            player  = (player % len(PLAYERS)) + 1
            face    = "front"
            counter = 0
            show_bmp(lcd, player, face)

        # Touch zones
        zone = touch_event(touch)
        if zone == "flip":
            face    = "back" if face == "front" else "front"
            counter = 0
            show_bmp(lcd, player, face)
        elif zone == "inc":
            counter += 1
            draw_counter(lcd, counter)
        elif zone == "dec":
            counter -= 1
            draw_counter(lcd, counter)

        time.sleep_ms(20)


main()