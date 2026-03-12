import network
import time
import os

# ---- Config ----
try:
    from secrets import WIFI_SSID, WIFI_PASS, SERVER
except ImportError:
    raise RuntimeError("Missing secrets.py — copy pico/secrets.py.example and fill it in.")

MANIFEST_URL = SERVER + "/ota/manifest"
FILE_URL     = SERVER + "/ota/file/"

# Deletion protection: only files listed here will be preserved even if not in the manifest.
# Keep this empty if you want the device to exactly match the server pico/ directory.
# Do NOT include "boot.py" or "secrets.py" here if you want them to be updated via OTA.
SKIP_FILES = set()
# Example to protect a local-only file: SKIP_FILES = {"local_config.py"}

# ---- WiFi ----
def _connect():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        return True
    print("boot: connecting to WiFi…")
    wlan.connect(WIFI_SSID, WIFI_PASS)
    for _ in range(20):
        if wlan.isconnected():
            print("boot: WiFi OK –", wlan.ifconfig()[0])
            return True
        time.sleep(1)
    print("boot: WiFi failed — running existing files")
    return False

# ---- SHA-256 of a local file (MicroPython hashlib) ----
def _sha256(path):
    import hashlib
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(512)
                if not chunk:
                    break
                h.update(chunk)
        return "".join("{:02x}".format(b) for b in h.digest())
    except OSError:
        return None

# ---- Sync ----
def sync():
    # import urequests lazily so boot.py itself doesn't keep the module and its buffers resident
    try:
        import urequests
    except Exception:
        urequests = None

    if not _connect():
        # if we couldn't connect, ensure we drop any imported module and collect
        try:
            del urequests
        except Exception:
            pass
        gc.collect()
        return

    print("boot: fetching manifest…")
    try:
        r = urequests.get(MANIFEST_URL, timeout=10)
        manifest = r.json()
        r.close()
    except Exception as e:
        print("boot: manifest fetch failed –", e)
        # cleanup before return
        try:
            del r
        except Exception:
            pass
        try:
            del urequests
        except Exception:
            pass
        gc.collect()
        return

    # ... existing sync logic (downloads, pruning) unchanged ...

    # final cleanup but KEEP wlan active (don't call wlan.active(False))
    try:
        # remove local large refs used in sync to free memory
        del manifest
    except Exception:
        pass
    try:
        del r
    except Exception:
        pass
    try:
        del urequests
    except Exception:
        pass

    # force collection and print free heap for debugging (serial)
    try:
        gc.collect()
        print("boot: free memory after cleanup:", gc.mem_free())
    except Exception:
        pass

sync()