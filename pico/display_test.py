"""
Quick display test — fill screen red, green, blue in sequence.
Uses your actual pin assignments from main.py.
"""
from machine import Pin, SPI, PWM
from ili9488 import Display, color565
import time

# Backlight on full
bl = PWM(Pin(13), freq=1000)
bl.duty_u16(65535)

# SPI1 — matches main.py
spi = SPI(1, baudrate=40_000_000,
          sck=Pin(10), mosi=Pin(11), miso=Pin(12))

display = Display(spi,
                  dc=Pin(8,  Pin.OUT),
                  cs=Pin(9,  Pin.OUT),
                  rst=Pin(15, Pin.OUT))

print("Display init OK")

# Fill screen with three colours in sequence
for colour, name in [
    (color565(255, 0,   0),   "RED"),
    (color565(0,   255, 0),   "GREEN"),
    (color565(0,   0,   255), "BLUE"),
]:
    print(f"Filling {name}…")
    display.clear(colour)
    time.sleep(1)

# Draw a white cross so you can see orientation
display.draw_hline(0, 160, 480, color565(255, 255, 255))
display.draw_vline(240, 0, 320, color565(255, 255, 255))

print("Done — you should see a white cross on blue.")