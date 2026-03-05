from machine import Pin, SPI, PWM
import framebuf, time

LCD = None

class LCD_3inch5(framebuf.FrameBuffer):
    def __init__(self):
        self.cs  = Pin(9,  Pin.OUT)
        self.rst = Pin(15, Pin.OUT)
        self.dc  = Pin(8,  Pin.OUT)
        self.cs(1); self.dc(1); self.rst(1)
        self.spi = SPI(1, 60_000_000, sck=Pin(10), mosi=Pin(11), miso=Pin(12))
        self.width  = 480
        self.height = 160
        self.buffer = bytearray(self.width * self.height * 2)
        super().__init__(self.buffer, self.width, self.height, framebuf.RGB565)
        self._init()
        PWM(Pin(13), freq=1000).duty_u16(65535)

    def _cmd(self, c):
        self.cs(1); self.dc(0); self.cs(0)
        self.spi.write(bytearray([c])); self.cs(1)
    def _dat(self, d):
        self.cs(1); self.dc(1); self.cs(0)
        self.spi.write(bytearray([d])); self.cs(1)

    def _init(self):
        self.rst(1); time.sleep_ms(5)
        self.rst(0); time.sleep_ms(10)
        self.rst(1); time.sleep_ms(5)
        for c,d in [(0x21,[]),(0xC2,[0x33]),(0xC5,[0x00,0x1e,0x80]),
                    (0xB1,[0xB0]),(0x36,[0x28]),(0xE0,[0x00,0x13,0x18,0x04,0x0F,0x06,
                    0x3a,0x56,0x4d,0x03,0x0a,0x06,0x30,0x3e,0x0f]),
                    (0xE1,[0x00,0x13,0x18,0x01,0x11,0x06,0x38,0x34,
                    0x4d,0x06,0x0d,0x0b,0x31,0x37,0x0f]),
                    (0x3A,[0x55]),(0x11,[]),(0x29,[]),(0xB6,[0x00,0x62]),(0x36,[0x28])]:
            self._cmd(c)
            for b in d: self._dat(b)
        time.sleep_ms(120)

    def _show(self, y_start, y_end):
        self._cmd(0x2A)
        for b in [0x00,0x00,0x01,0xDF]: self._dat(b)
        self._cmd(0x2B)
        self._dat(y_start >> 8); self._dat(y_start & 0xFF)
        self._dat(y_end   >> 8); self._dat(y_end   & 0xFF)
        self._cmd(0x2C)
        self.cs(1); self.dc(1); self.cs(0)
        self.spi.write(self.buffer); self.cs(1)

    def show_up(self):   self._show(0x00, 0x9F)   # y 0–159
    def show_down(self): self._show(0xA0, 0x13F)   # y 160–319

lcd = LCD_3inch5()

# TOP half (y 0-159) — corners of the top half
lcd.fill(0x0000)
lcd.text("TL",   0,   0, 0xFFFF)   # top-left
lcd.text("TR", 464,   0, 0xFFFF)   # top-right  (480 - 16 = 464, text is 8px per char)
lcd.text("BL",   0, 151, 0xFFFF)   # bottom-left of top half  (159 - 8 = 151)
lcd.text("BR", 464, 151, 0xFFFF)   # bottom-right of top half
lcd.show_up()

# BOTTOM half (y 160-319) — corners of the bottom half
lcd.fill(0x0000)
lcd.text("TL",   0,   0, 0xFFFF)   # top-left of bottom half
lcd.text("TR", 464,   0, 0xFFFF)   # top-right of bottom half
lcd.text("BL",   0, 151, 0xFFFF)   # bottom-left  (159 - 8 = 151)
lcd.text("BR", 464, 151, 0xFFFF)   # bottom-right
lcd.show_down()