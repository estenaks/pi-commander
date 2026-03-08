import urequests, gc

def show_image(lcd, server, player=2, face="front"):
    """Fetch and display a card image from the server, one strip at a time."""
    for strip_idx, show_fn in ((0, lcd.show_up), (1, lcd.show_mid), (2, lcd.show_down)):
        url = "{}/img/{}/{}/raw?strip={}".format(server, player, face, strip_idx)
        r = urequests.get(url)
        # read directly into the framebuffer
        mv = memoryview(lcd.buffer)
        total = 0
        while total < len(lcd.buffer):
            chunk = r.raw.read(4096)
            if not chunk:
                break
            n = len(chunk)
            mv[total:total + n] = chunk
            total += n
        r.close()
        show_fn()
        gc.collect()