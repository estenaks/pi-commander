# Debug fetch: prints URL, raw body, parsed JSON, 'colors' field, and full exception trace.
import sys, time
import urequests
from secrets import SERVER

def fetch_player_color_code_debug(player):
    resp = None
    url = "http://{}/api/current/{}".format(SERVER, player)
    print("DEBUG: fetching URL ->", url)
    try:
        resp = urequests.get(url)
        # print status if present
        print("HTTP status:", getattr(resp, "status_code", getattr(resp, "status", "unknown")))

        # read raw body safely
        raw = "<could not read body>"
        try:
            raw = resp.text
        except Exception:
            try:
                raw = resp.content.decode("utf-8")
            except Exception:
                try:
                    raw = str(resp.content)
                except Exception:
                    pass
        print("Raw response body:\n", raw)

        # try parse json
        data = None
        try:
            data = resp.json()
            print("Parsed JSON (repr):", repr(data))
            print("Parsed JSON type:", type(data))
        except Exception as e:
            print("JSON parse failed:", e)
            # show small snippet of raw to help
            print("Raw (first 400 chars):", raw[:400])

        # always close
        try:
            resp.close()
        except Exception:
            pass
        resp = None

        if not data:
            print("No JSON parsed (data is None/empty).")
            return None

        colors = data.get("colors")
        print("colors field raw:", repr(colors), "type:", type(colors))
        # Normalize to string
        if isinstance(colors, (list, tuple)):
            s = "".join(str(c).strip() for c in colors if c is not None)
            s = s.upper() if s else None
            print("Normalized colors ->", s)
            return s
        if isinstance(colors, str):
            s = colors.strip().upper()
            print("Normalized colors ->", s)
            return s if s else None
        # fallback
        s = str(colors).strip().upper()
        print("Fallback normalized colors ->", s)
        return s if s else None

    except Exception as e:
        print("Exception while fetching color code:")
        try:
            sys.print_exception(e)
        except Exception:
            # print minimal repr if print_exception not available
            print("Exception repr:", repr(e))
        try:
            if resp is not None:
                resp.close()
        except Exception:
            pass
        return None

# One-shot test when run directly:
if __name__ == "__main__":
    print("Running debug fetch for player 1")
    res = fetch_player_color_code_debug(1)
    print("Result:", res)