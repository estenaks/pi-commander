import urequests, gc

def show_image(lcd, server, player=1, face="front", counter=None):
    """Fetch and display a card image from the server, one strip at a time.

    Optional:
      counter: int | None
        If int and non-zero, draw a small black rectangle with white text
        (e.g. '+1/+1' or '-1/-1') centered near the bottom of the top strip.
        If 0 or None, draw nothing.
    """
    for strip_idx, show_fn in ((0, lcd.show_up), (1, lcd.show_mid), (2, lcd.show_down)):
        url = "{}/img/{}/{}/raw?strip={}".format(server, player, face, strip_idx)
        try:
            r = urequests.get(url)
        except Exception as e:
            # network failure — skip this strip
            print("show_image: request failed for", url, ":", e)
            continue

        if r.status_code == 200:
            # raw bytes are placed into the framebuffer buffer
            try:
                lcd.buffer[:] = r.content
            except Exception as e:
                print("show_image: failed to write buffer:", e)

            # Draw overlay only on the top strip (strip_idx == 0)
            if strip_idx == 0 and counter is not None and counter != 0:
                try:
                    rect_w = 110
                    rect_h = 30
                    x = (lcd.width - rect_w) // 2
                    y = lcd.height - rect_h - 8  # a few pixels above bottom of top strip

                    # Black rectangle
                    lcd.fill_rect(x, y, rect_w, rect_h, 0x0000)

                    # Compose the message
                    if counter > 0:
                        msg = "+{n}/+{n}".format(n=counter)
                    else:
                        msg = "-{n}/-{n}".format(n=abs(counter))

                    # Approximate centering (8 px per char)
                    char_w = 8
                    text_x = x + max(6, (rect_w - len(msg) * char_w) // 2)
                    text_y = y + (rect_h // 2) - 0

                    # Draw text (some builds may not implement text; protect with try)
                    try:
                        lcd.text(msg, text_x, text_y, 0xFFFF)
                    except Exception:
                        # If text() is not available, leave the black box as indicator
                        pass
                except Exception as e:
                    print("show_image: overlay draw failed:", e)

        else:
            print("show_image: got status", r.status_code, "for", url)

        try:
            r.close()
        except Exception:
            pass

        # push this strip to screen
        try:
            show_fn()
        except Exception as e:
            print("show_image: show_fn failed for strip", strip_idx, ":", e)

        gc.collect()