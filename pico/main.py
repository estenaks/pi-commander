from machine import Pin, SPI, PWM
import framebuf, time

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
                    (0x3A,[0x55]),(0x11,[]),(0x29,[]),(0xB6,[0x00,0x62]),
                    (0x36,[0x88])]:
            self._cmd(c)
            for b in d: self._dat(b)
        time.sleep_ms(120)

    def _show(self, y_start, y_end):
        self._cmd(0x2A)
        self._dat(0x00); self._dat(0x00)
        self._dat(0x01); self._dat(0x3F)
        self._cmd(0x2B)
        self._dat(y_start >> 8); self._dat(y_start & 0xFF)
        self._dat(y_end   >> 8); self._dat(y_end   & 0xFF)
        self._cmd(0x2C)
        self.cs(1); self.dc(1); self.cs(0)
        self.spi.write(self.buffer); self.cs(1)

    def show_up(self):   self._show(0x000, 0x09F)
    def show_mid(self):  self._show(0x0A0, 0x13F)
    def show_down(self): self._show(0x140, 0x1DF)

    def clear(self):
        self.fill(0x0000)
        self.show_up()
        self.show_mid()
        self.show_down()


# ---- BRG layout (empirically confirmed) ----
# bits 15-11 = blue  (5-bit, max 31)
# bits 10-5  = red   (6-bit, max 63)
# bits 4-0   = green (5-bit, max 31)
#
# brg(b, r, g) packs correct values — b,r,g each 0-31
# red gets doubled into the 6-bit field so brightness matches

def brg(b, r, g):
    return ((b & 0x1F) << 11) | ((r & 0x1F) << 6) | (g & 0x1F)
    # red is 6-bit field but we treat it as 5-bit and shift by 6
    # this means red uses bits 10-6 only, bit 5 always 0
    # good enough for now — gives consistent brightness across channels

BLACK   = 0x0000
WHITE   = 0xFFFF
RED     = brg( 0, 31,  0)
GREEN   = brg( 0,  0, 31)
BLUE    = brg(31,  0,  0)
YELLOW  = brg( 0, 31, 31)   # red + green
CYAN    = brg(31,  0, 31)   # blue + green
MAGENTA = brg(31, 31,  0)   # blue + red
ORANGE  = brg( 0, 31,  8)   # red + a little green
GREY    = brg(15, 15, 15)   # equal mid


def section_top(lcd):
    bars  = [RED, GREEN, BLUE, YELLOW, CYAN, MAGENTA, ORANGE, WHITE]
    names = ["R",  "G",  "B",   "Y",   "C",   "M",    "O",   "W"]
    bw = 320 // len(bars)
    for i, c in enumerate(bars):
        lcd.fill_rect(i * bw, 0, bw, 60, c)
    for i, name in enumerate(names):
        lcd.text(name, i * bw + 14, 24, BLACK)

    # Greyscale — step b, r, g equally using brg()
    # 8 steps so each jump is 4 (0,4,8,12,16,20,24,28 out of 31)
    for i in range(8):
        v = i * 4
        lcd.fill_rect(i * 40, 65, 40, 30, brg(v, v, v))
    # second row lighter half
    for i in range(8):
        v = 16 + i * 2
        lcd.fill_rect(i * 40, 95, 40, 20, brg(v, v, v))

    lcd.text("greyscale ^",  96, 120, WHITE)
    lcd.text("colour bars ^", 84, 135, WHITE)
    lcd.text("320x480 portrait",  68, 150, GREY)


def section_mid(lcd):
    colors = [RED, GREEN, BLUE, YELLOW, CYAN, MAGENTA]
    for i, c in enumerate(colors):
        m = i * 12
        lcd.rect(m, m + 5, 320 - m * 2, 140 - m * 2, c)
    lcd.text("nested rects", 96, 72, WHITE)


def section_bot(lcd):
    sq = 20
    for r in range(160 // sq):
        for c in range(320 // sq):
            color = WHITE if (r + c) % 2 == 0 else BLACK
            lcd.fill_rect(c * sq, r * sq, sq, sq, color)
    lcd.text("checkerboard", 88,  60, RED)
    lcd.text("pi-commander", 88,  76, GREEN)
    lcd.text("display  OK",  96,  92, BLUE)
    lcd.text("orange:", 88, 108, WHITE)
    lcd.fill_rect(168, 106, 60, 12, ORANGE)


lcd = LCD_3inch5()
lcd.clear()

lcd.fill(0x0000); section_top(lcd);  lcd.show_up()
lcd.fill(0x0000); section_mid(lcd);  lcd.show_mid()
lcd.fill(0x0000); section_bot(lcd);  lcd.show_down()

while True:
    time.sleep(1)