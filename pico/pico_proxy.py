from machine import Pin, SPI, PWM
import framebuf, time, gc

try:
    gc.collect()
    print("lcd_test3: free mem before alloc:", gc.mem_free())
except Exception:
    pass

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
        # Lower frequency
        self.spi = SPI(1, 10_000_000, sck=Pin(10), mosi=Pin(11), miso=Pin(12))
        try:
            self.buffer = bytearray(self.width * self.height * 2)
        except Exception as e:
            print("lcd_test3: framebuffer alloc failed:", e)
            gc.collect()
            print("lcd_test3: free after GC:", gc.mem_free())
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
        import time as _t
        self._cmd(0x2A)
        self._dat(0x00); self._dat(0x00)
        self._dat(0x01); self._dat(0x3F)
        self._cmd(0x2B)
        self._dat(y_start >> 8); self._dat(y_start & 0xFF)
        self._dat(y_end   >> 8); self._dat(y_end & 0xFF)
        self._cmd(0x2C)

        self.cs(1); self.dc(1); self.cs(0)
        try:
            mv = memoryview(self.buffer)
            chunk_size = 1024  # smaller chunks
            for i in range(0, len(mv), chunk_size):
                self.spi.write(mv[i:i+chunk_size])
                _t.sleep_ms(1)
        except Exception as e:
            print("lcd_test3: chunked write error:", e)
            try:
                self.spi.write(self.buffer)
            except Exception as e2:
                print("lcd_test3: fallback write failed:", e2)
        finally:
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
        try:
            self.spi.init(baudrate=5_000_000)
        except Exception:
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
            try:
                self.spi.init(baudrate=10_000_000)
            except Exception:
                self.spi = SPI(1, 10_000_000, sck=Pin(10), mosi=Pin(11), miso=Pin(12))
        return [X_Point, Y_Point]

lcd = LCD_3inch5()
try:
    gc.collect()
    print("lcd_test3: free mem after alloc:", gc.mem_free())
except Exception:
    pass