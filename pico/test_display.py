"""
Hardware sanity check — flash this as main.py first.
Tells you exactly what works and what doesn't before running the real app.
!! Adjust pins to match your schematic !!
"""
import time
from machine import Pin, SPI

# ---- Paste your pin values here ----
LCD_SPI  = 1
LCD_SCK  = 10
LCD_MOSI = 11
LCD_MISO = 12
LCD_CS   = 9
LCD_DC   = 8
LCD_RST  = 15
LCD_BL   = 13

BLACK  = 0x0000
WHITE  = 0xFFFF
RED    = 0xF800
GREEN  = 0x07E0
BLUE   = 0x001F

def test():
    print("1. Importing ili9488...")
    import ili9488
    print("   OK")

    print("2. Init SPI...")
    spi = SPI(LCD_SPI, baudrate=10_000_000,
              sck=Pin(LCD_SCK), mosi=Pin(LCD_MOSI), miso=Pin(LCD_MISO))
    print("   OK")

    print("3. Init display...")
    lcd = ili9488.ILI9488(
        spi,
        cs=Pin(LCD_CS,  Pin.OUT),
        dc=Pin(LCD_DC,  Pin.OUT),
        rst=Pin(LCD_RST, Pin.OUT),
        bl=Pin(LCD_BL,  Pin.OUT),
        width=480, height=320,
    )
    lcd.init()
    print("   OK")

    print("4. Testing fill colours (should see RED, GREEN, BLUE, WHITE)...")
    for color in (RED, GREEN, BLUE, WHITE):
        lcd.fill(color)
        time.sleep(1)
    print("   OK")

    print("5. Testing write_cmd (MADCTL for landscape)...")
    try:
        lcd.write_cmd(0x36)
        lcd.write_data(bytes([0x28]))
        print("   OK — write_cmd exists")
    except AttributeError as e:
        print(f"   FAIL — {e}")
        print("   >> Driver does not expose write_cmd/write_data directly")
        print("   >> Check your ili9488.py driver API")

    print("6. Testing set_window...")
    try:
        lcd.set_window(0, 0, 479, 319)
        print("   OK")
    except AttributeError as e:
        print(f"   FAIL — {e}")

    print("7. Testing text overlay...")
    try:
        lcd.fill(BLACK)
        lcd.text("Hello Pico!", 10, 150, WHITE)
        lcd.show()
        print("   OK — check display for text")
    except AttributeError as e:
        print(f"   FAIL — {e}")

    print("\nAll tests done. Check output above for any FAILs.")

test()