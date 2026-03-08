import time
from machine import Pin, SPI
import ili9488

# ---- Pins (Waveshare Pico-Eval-Board) ----
LCD_SPI  = 1
LCD_SCK  = 10
LCD_MOSI = 11
LCD_MISO = 12
LCD_CS   = 9
LCD_DC   = 8
LCD_RST  = 15
LCD_BL   = 13

# ---- Colours RGB565 ----
BLACK = 0x0000
WHITE = 0xFFFF
GREEN = 0x07E0

def main():
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
    lcd.rotation(0)   # portrait — 320 wide x 480 tall

    lcd.fill(BLACK)

    # Text is always drawn left-to-right by the driver.
    # In portrait (rotation 0) that already reads naturally top-to-bottom
    # on the long axis, so just write lines at increasing y offsets.
    lines = [
        "pi-commander",
        "boot sync: OK",
        "portrait mode",
        "line 4",
    ]
    y = 20
    for line in lines:
        lcd.text(line, 10, y, GREEN)
        y += 20

    lcd.show()

    while True:
        time.sleep(1)

main()