from machine import Pin, SPI, PWM
import framebuf, time, math

# ── LCD driver (identical to test_display.py) ─────────────────────────────────

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
        for c, d in [
            (0x21, []), (0xC2, [0x33]), (0xC5, [0x00, 0x1e, 0x80]),
            (0xB1, [0xB0]), (0x36, [0x28]),
            (0xE0, [0x00,0x13,0x18,0x04,0x0F,0x06,0x3a,0x56,0x4d,0x03,0x0a,0x06,0x30,0x3e,0x0f]),
            (0xE1, [0x00,0x13,0x18,0x01,0x11,0x06,0x38,0x34,0x4d,0x06,0x0d,0x0b,0x31,0x37,0x0f]),
            (0x3A, [0x55]), (0x11, []), (0x29, []), (0xB6, [0x00, 0x62]),
            (0x36, [0x88]),
        ]:
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

    def clear(self, c=0):
        self.fill(c)
        self.show_up(); self.show_mid(); self.show_down()


# ── colour helpers ────────────────────────────────────────────────────────────

def bs(c):
    return ((c & 0xFF) << 8) | (c >> 8)

def rgb(r, g, b):
    return bs(((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3))

BLACK = rgb(0, 0, 0)
WHITE = rgb(255, 255, 255)


# ── SECTION TOP — expanded 32-colour named palette ────────────────────────────
#
# The original 16 colours + 16 midtone/mixed colours that we suspect work fine
# but have never deliberately tested.  Rendered as labelled swatches so you can
# immediately see if any of them look wrong on the hardware.

PALETTE_32 = [
    # row 0 — primaries & secondaries (known-good)
    ("BLK", rgb(  0,   0,   0)),
    ("WHT", rgb(255, 255, 255)),
    ("RED", rgb(255,   0,   0)),
    ("GRN", rgb(  0, 255,   0)),
    ("BLU", rgb(  0,   0, 255)),
    ("YEL", rgb(255, 255,   0)),
    ("CYN", rgb(  0, 255, 255)),
    ("MAG", rgb(255,   0, 255)),
    # row 0 cont — extended (known-good)
    ("ORG", rgb(255, 165,   0)),
    ("GRY", rgb(128, 128, 128)),
    ("DGY", rgb( 64,  64,  64)),
    ("LGY", rgb(192, 192, 192)),
    ("NVY", rgb(  0,   0, 128)),
    ("TEL", rgb(  0, 128, 128)),
    ("PRP", rgb(128,   0, 128)),
    ("LIM", rgb(  0, 128,   0)),
    # row 1 — NEW midtones & mixed colours
    ("R50", rgb(128,   0,   0)),   # dark red
    ("G50", rgb(  0, 128,   0)),   # already LIME — alias check
    ("B50", rgb(  0,   0, 128)),   # already NAVY — alias check
    ("SKN", rgb(255, 200, 150)),   # skin tone
    ("PNK", rgb(255, 105, 180)),   # hot pink
    ("BRN", rgb(139,  69,  19)),   # brown
    ("TAN", rgb(210, 180, 140)),   # tan
    ("GLD", rgb(255, 215,   0)),   # gold
    ("SVR", rgb(192, 192, 192)),   # silver (= LGREY — alias check)
    ("MNT", rgb(  0, 255, 127)),   # mint / spring green
    ("VLT", rgb(238, 130, 238)),   # violet
    ("IDG", rgb( 75,   0, 130)),   # indigo
    ("AZR", rgb(  0, 127, 255)),   # azure
    ("CRL", rgb(255, 127,  80)),   # coral
    ("C50", rgb(  0, 128, 128)),   # mid-cyan (= TEAL — alias check)
    ("LVN", rgb(230, 230, 250)),   # lavender
]

def section_top(lcd):
    """Two rows of 16 swatches each, with short name labels."""
    sw = 320 // 16  # 20 px per swatch
    for row in range(2):
        for col in range(16):
            idx = row * 16 + col
            name, c = PALETTE_32[idx]
            x = col * sw
            y = row * 72          # row 0: y=0, row 1: y=72
            lcd.fill_rect(x, y, sw, 65, c)
            # label in contrasting colour
            lbl_c = BLACK if c != BLACK else WHITE
            lcd.text(name[:3], x + 1, y + 27, lbl_c)


# ── SECTION MID — smooth gradients across the full width ─────────────────────
#
# Each horizontal band is a 320-step gradient.  If the display can render
# intermediate colours, these will look smooth.  Banding = limited colour depth.

def section_mid(lcd):
    band_h = 20
    gradients = [
        # (start_rgb, end_rgb, label)
        ((  0,   0,   0), (255, 255, 255), "BLK->WHT"),
        ((255,   0,   0), (  0,   0, 255), "RED->BLU"),
        ((  0, 255,   0), (255,   0, 255), "GRN->MAG"),
        ((  0,   0, 255), (255, 255,   0), "BLU->YEL"),
        ((255, 165,   0), (  0,   0, 128), "ORG->NVY"),
        ((255, 105, 180), (  0, 255, 127), "PNK->MNT"),
        ((139,  69,  19), (230, 230, 250), "BRN->LVN"),
        ((255, 215,   0), ( 75,   0, 130), "GLD->IDG"),
    ]
    for band, ((r0,g0,b0), (r1,g1,b1), lbl) in enumerate(gradients):
        y = band * band_h
        for x in range(320):
            t = x / 319
            r = int(r0 + (r1 - r0) * t)
            g = int(g0 + (g1 - g0) * t)
            b = int(b0 + (b1 - b0) * t)
            lcd.pixel(x, y,            rgb(r, g, b))
            lcd.pixel(x, y + 1,        rgb(r, g, b))
            lcd.pixel(x, y + band_h-2, rgb(r, g, b))
            lcd.pixel(x, y + band_h-1, rgb(r, g, b))
        # fill middle rows with solid start colour for legibility
        lcd.fill_rect(0, y + 2, 320, band_h - 4, rgb(r0, g0, b0))
        lcd.text(lbl, 4, y + 6, WHITE if (r0+g0+b0) < 384 else BLACK)


# ── SECTION BOT — 2-D colour map: hue × lightness ────────────────────────────
#
# X axis = hue (0..360), Y axis = value (255 top → 0 bottom).
# This is the richest test: every pixel is a unique colour computed via rgb().
# Smooth transitions = the display handles the full RGB565 gamut correctly.

def section_bot(lcd):
    for x in range(320):
        hue = (x / 320) * 360
        hi  = int(hue // 60) % 6
        f   = (hue % 60) / 60
        q   = int(255 * (1 - f))
        t   = int(255 * f)
        v   = 255
        lut = [(v,t,0),(q,v,0),(0,v,t),(0,q,v),(t,0,v),(v,0,q)]
        hr, hg, hb = lut[hi]
        for y in range(160):
            # top half: full saturation, fade value top→bottom
            s = 1.0 - (y / 159) * 0.85
            r = int(hr * s)
            g = int(hg * s)
            b = int(hb * s)
            lcd.pixel(x, y, rgb(r, g, b))


# ── main ──────────────────────────────────────────────────────────────────────

import gc

lcd = LCD_3inch5()
lcd.clear()

print("section_top (32-colour palette swatches)…")
lcd.fill(BLACK); section_top(lcd);  lcd.show_up()

print("section_mid (gradient bands)…")
lcd.fill(BLACK); section_mid(lcd);  lcd.show_mid()

print("section_bot (hue×value 2-D map)…")
gc.collect()
lcd.fill(BLACK); section_bot(lcd);  lcd.show_down()

print("done — static image, no loop")