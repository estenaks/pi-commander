import gc

def show_image(lcd, server, player=1, face="front", counter=None):
    """
    Stream an image strip-by-strip into the lcd.buffer without creating large
    temporary bytes objects.  This reduces peak heap pressure and avoids
    large transient allocations that can cause GC/IRQ/timing issues.

    Behavior:
      - Reads response.raw in 4KB chunks directly into the framebuffer memoryview.
      - Prints light diagnostics (strip start/finish and free heap) so you can
        see where a freeze occurs.
      - Does a safe overlay draw for the top strip (same as before).
      - Ensures resp and urequests references are deleted and gc.collect() is run.
    """
    # expected bytes per strip (RGB565)
    expected_len = lcd.width * lcd.height // 3  # not used; strips are full buffer portions in current layout
    # note: original code expects the server to return exactly the bytes for the strip;
    # we will stream into lcd.buffer starting at 0 for each strip (server provides exactly that strip)
    for strip_idx, show_fn in ((0, lcd.show_up), (1, lcd.show_mid), (2, lcd.show_down)):
        url = "{}/img/{}/{}/raw?strip={}".format(server, player, face, strip_idx)
        resp = None
        try:
            # lazy import to avoid keeping networking module + buffers resident when not fetching
            import urequests
        except Exception as e:
            print("show_image: cannot import urequests:", e)
            return

        try:
            print("show_image: fetching strip", strip_idx, "->", url)
            resp = urequests.get(url, timeout=10)
        except Exception as e:
            print("show_image: request failed for", url, ":", e)
            try:
                if resp is not None:
                    resp.close()
            except Exception:
                pass
            # cleanup references and continue to next strip
            try:
                del resp
            except Exception:
                pass
            try:
                del urequests
            except Exception:
                pass
            gc.collect()
            continue

        # Read the response body into the framebuffer without building large temps.
        try:
            mv = memoryview(lcd.buffer)
            off = 0
            # read in chunks from the response raw stream (urequests compatible)
            while True:
                chunk = None
                try:
                    # r.raw.read may return fewer bytes than requested; read up to 4096
                    chunk = resp.raw.read(4096)
                except Exception as e:
                    # some urequests builds may not expose raw or may raise; fall back to resp.content
                    # but avoid creating huge temporary if possible
                    # we'll break here and try fallback below
                    # print debug and break to fallback
                    # print("show_image: raw.read failed:", e)
                    chunk = None
                    break

                if not chunk:
                    break
                ln = len(chunk)
                mv[off:off+ln] = chunk
                off += ln
                # defensive: avoid overrunning lcd.buffer
                if off >= len(mv):
                    break

            # if raw.read wasn't available or returned nothing, fallback to resp.content
            if off == 0:
                try:
                    b = resp.content
                    if b:
                        mv[0:len(b)] = b
                        off = len(b)
                except Exception as e:
                    print("show_image: fallback to resp.content failed:", e)

            if off == 0:
                print("show_image: got zero bytes for", url)
            else:
                print("show_image: received %d bytes for strip %d" % (off, strip_idx))

            # Draw overlay only on the top strip (strip_idx == 0)
            if strip_idx == 0 and counter is not None and counter != 0:
                try:
                    rect_w = 90
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
                    text_y = y + (rect_h // 2) - 3

                    # Draw text (some builds may not implement text; protect with try)
                    try:
                        lcd.text(msg, text_x, text_y, 0xFFFF)
                    except Exception:
                        pass
                except Exception as e:
                    print("show_image: overlay draw failed:", e)

        except Exception as e:
            print("show_image: failed to stream into buffer for", url, ":", e)
        finally:
            try:
                resp.close()
            except Exception:
                pass
            try:
                del resp
            except Exception:
                pass
            try:
                del urequests
            except Exception:
                pass

        # push this strip to screen
        try:
            print("show_image: pushing strip", strip_idx, "to screen")
            show_fn()
            print("show_image: pushed strip", strip_idx)
        except Exception as e:
            print("show_image: show_fn failed for strip", strip_idx, ":", e)

        # force GC and print free heap so we can observe memory behavior between strips
        try:
            gc.collect()
            print("show_image: free mem after strip %d: %d" % (strip_idx, gc.mem_free()))
        except Exception:
            pass