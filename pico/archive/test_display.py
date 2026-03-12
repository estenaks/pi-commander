from machine import Pin, SPI, PWM
import framebuf, time, math

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
        self.spi.write(self.buffer)
        self.cs(1)

    def show_up(self):   self._show(0x000, 0x09F)
    def show_mid(self):  self._show(0x0A0, 0x13F)
    def show_down(self): self._show(0x140, 0x1DF)

    def clear(self):
        self.fill(BLACK)
        self.show_up(); self.show_mid(); self.show_down()


def bs(c):
    return ((c & 0xFF) << 8) | (c >> 8)

def rgb(r, g, b):
    r5 = (r >> 3) & 0x1F
    g6 = (g >> 2) & 0x3F
    b5 = (b >> 3) & 0x1F
    return bs((r5 << 11) | (g6 << 5) | b5)

BLACK   = rgb(  0,   0,   0)
WHITE   = rgb(255, 255, 255)
RED     = rgb(255,   0,   0)
GREEN   = rgb(  0, 255,   0)
BLUE    = rgb(  0,   0, 255)
YELLOW  = rgb(255, 255,   0)
CYAN    = rgb(  0, 255, 255)
MAGENTA = rgb(255,   0, 255)
ORANGE  = rgb(255, 165,   0)
GREY    = rgb(128, 128, 128)
DGREY   = rgb( 64,  64,  64)
LGREY   = rgb(192, 192, 192)
NAVY    = rgb(  0,   0, 128)
TEAL    = rgb(  0, 128, 128)
PURPLE  = rgb(128,   0, 128)
LIME    = rgb(  0, 128,   0)


def section_top(lcd):
    bars  = [RED, GREEN, BLUE, YELLOW, CYAN, MAGENTA, ORANGE, WHITE]
    names = ["R",  "G",  "B",   "Y",   "C",   "M",    "O",   "W"]
    bw = 320 // 8
    for i, c in enumerate(bars):
        lcd.fill_rect(i * bw, 0, bw, 55, c)
    for i, name in enumerate(names):
        lcd.text(name, i * bw + 14, 20, BLACK)
    sw = 320 // 32
    for i in range(32):
        v = i * 8
        lcd.fill_rect(i * sw, 56, sw, 25, rgb(v, v, v))
        lcd.fill_rect(i * sw, 82, sw, 12, rgb(v, 0, 0))
        lcd.fill_rect(i * sw, 95, sw, 12, rgb(0, v, 0))
        lcd.fill_rect(i * sw, 108, sw, 12, rgb(0, 0, v))
    lcd.text("grey", 0,  58, WHITE)
    lcd.text("R",    0,  84, WHITE)
    lcd.text("G",    0,  97, WHITE)
    lcd.text("B",    0, 110, WHITE)
    extras = [NAVY, TEAL, PURPLE, LIME, ORANGE,
              rgb(255,128,0), rgb(128,255,0), rgb(0,128,255),
              rgb(255,0,128), rgb(128,0,255), DGREY, LGREY]
    ew = 320 // len(extras)
    for i, c in enumerate(extras):
        lcd.fill_rect(i * ew, 122, ew, 25, c)
    lcd.text("extended palette", 76, 149, WHITE)


def section_mid(lcd):
    for i in range(0, 320, 20):
        c = rgb(i, 255 - i, 128)
        lcd.line(i, 0, 320 - i, 60, c)
    lcd.fill_rect( 10, 65, 80, 50, NAVY)
    lcd.fill_rect( 40, 75, 80, 50, TEAL)
    lcd.fill_rect( 70, 85, 80, 50, PURPLE)
    for i, c in enumerate([RED, GREEN, BLUE, YELLOW, CYAN, MAGENTA]):
        m = i * 8
        lcd.rect(180 + m, 65 + m, 130 - m*2, 80 - m*2, c)
    for y in range(0, 50, 10):
        lcd.hline(0, 120 + y, 160, DGREY)
    for x in range(0, 160, 10):
        lcd.vline(x, 120, 40, LGREY)
    lcd.text("line grid",       4, 124, WHITE)
    lcd.text("pi-commander v1", 165, 120, WHITE)
    lcd.text("320 x 480",       165, 132, GREY)
    lcd.text("portrait mode",   165, 144, CYAN)


_WHEEL = []
for _s in range(12):
    _h = int((_s / 12) * 360)
    _hi = (_h // 60) % 6
    _f  = (_h % 60) / 60
    _q  = int(255 * (1 - _f))
    _t  = int(255 * _f)
    _v  = 255
    _lut = [(_v,_t,0),(_q,_v,0),(0,_v,_t),(0,_q,_v),(_t,0,_v),(_v,0,_q)]
    _cr, _cg, _cb = _lut[_hi]
    _WHEEL.append(rgb(_cr, _cg, _cb))

_SCROLL_MSG = "  pi-commander  320x480  portrait  "
_SCROLL_LEN = len(_SCROLL_MSG) * 8

_frame_lbl = bytearray(b"frame:    ")

def _write_int(buf, pos, val, width):
    for i in range(width - 1, -1, -1):
        buf[pos + i] = ord('0') + (val % 10)
        val //= 10

def section_bot_frame(lcd, frame):
    lcd.fill(BLACK)

    cx, cy, r = 80, 80, 68
    angle_off = frame * 0.15
    for s in range(12):
        a1 = (s / 12) * 6.2832 + angle_off
        a2 = ((s + 1) / 12) * 6.2832 + angle_off
        c  = _WHEEL[s]
        for k in range(1, 13):
            rk = r * k // 12
            xa = cx + int(rk * math.cos(a1))
            ya = cy + int(rk * math.sin(a1))
            xb = cx + int(rk * math.cos(a2))
            yb = cy + int(rk * math.sin(a2))
            lcd.line(xa, ya, xb, yb, c)

    bx = int((math.sin(frame * 0.2) + 1) * 0.5 * 220)
    lcd.fill_rect(170 + bx, 10, 40, 20, RED)
    lcd.text("pi", 180 + bx, 15, WHITE)

    offset = (frame * 4) % _SCROLL_LEN
    lcd.text(_SCROLL_MSG, 320 - offset,               140, CYAN)
    lcd.text(_SCROLL_MSG, 320 - offset + _SCROLL_LEN, 140, CYAN)

    _write_int(_frame_lbl, 6, frame, 4)
    lcd.text(str(_frame_lbl, 'utf-8'), 170, 50, YELLOW)
    lcd.text("rgb() helper OK", 170, 65, GREEN)
    lcd.text("bs()  swap  OK",  170, 80, LIME)


lcd = LCD_3inch5()
lcd.clear()

lcd.fill(BLACK); section_top(lcd);  lcd.show_up()
lcd.fill(BLACK); section_mid(lcd);  lcd.show_mid()

import gc
gc.collect()

frame = 0
while True:
    lcd.fill(BLACK)
    section_bot_frame(lcd, frame)
    lcd.show_down()
    gc.collect()
    frame += 1
    time.sleep_ms(50)