import urequests, gc

def show_image(lcd, server, player=1, face="front"):
    """Fetch and display a card image from the server, one strip at a time."""
    for strip_idx, show_fn in ((0, lcd.show_up), (1, lcd.show_mid), (2, lcd.show_down)):
        url = "{}/img/{}/{}/raw?strip={}".format(server, player, face, strip_idx)
        r = urequests.get(url)
        if r.status_code == 200:
            lcd.buffer[:] = r.content
        r.close()
        show_fn()
        gc.collect()