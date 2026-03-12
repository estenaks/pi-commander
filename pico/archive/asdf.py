# pico/touch_test.py
from machine import Pin, SPI
import time

TP_CS_PIN = 16
TP_IRQ_PIN = 17

def _new_spi_touch(freq=5_000_000):
    # Explicitly set baudrate and default polarity/phase (explicit is safer)
    try:
        spi = SPI(1, baudrate=freq, sck=Pin(10), mosi=Pin(11), miso=Pin(12), polarity=0, phase=0)
    except TypeError:
        # MicroPython variants sometimes have positional API
        spi = SPI(1, freq, sck=Pin(10), mosi=Pin(11), miso=Pin(12))
    # short settle
    time.sleep_ms(5)
    return spi

tp_cs = Pin(TP_CS_PIN, Pin.OUT)
tp_irq = Pin(TP_IRQ_PIN, Pin.IN, Pin.PULL_UP)

spi = _new_spi_touch(5_000_000)

def touch_read_once():
    # returns (x, y) or None
    if tp_irq.value() != 0:
        return None
    tp_cs(0)
    try:
        X = 0
        Y = 0
        for i in range(3):
            spi.write(bytearray([0xD0]))
            rd = spi.read(2)
            time.sleep_us(10)
            if rd and len(rd) >= 2:
                X += (((rd[0] << 8) + rd[1]) >> 3)
            spi.write(bytearray([0x90]))
            rd = spi.read(2)
            time.sleep_us(10)
            if rd and len(rd) >= 2:
                Y += (((rd[0] << 8) + rd[1]) >> 3)
        return (X / 3.0, Y / 3.0)
    except Exception as e:
        print("touch_test: read error:", e)
        return None
    finally:
        tp_cs(1)

print("touch_test: tp_irq initial value:", tp_irq.value())
print("touch_test: starting loop (touch present -> print raw coords)")

while True:
    try:
        t = touch_read_once()
        if t is None:
            print("touch_test: no touch (irq value {})".format(tp_irq.value()))
        else:
            print("touch_test: raw coords:", t)
    except Exception as e:
        print("touch_test: unexpected error:", e)
    time.sleep_ms(500)