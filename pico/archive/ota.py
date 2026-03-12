import network
import urequests
import time
import machine
import os

# ---- Config (loaded from secrets.py — not in repo) ----
try:
    from secrets import WIFI_SSID, WIFI_PASS, SERVER
except ImportError:
    raise RuntimeError("Missing secrets.py on Pico! See pico/secrets.py.example in the repo.")

OTA_VERSION_URL = SERVER + "/ota/version"
OTA_SOURCE_URL  = SERVER + "/ota/main.py"
OTA_SHA_FILE    = "/ota_sha.txt"
# --------------------------------------------------------


def _connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        return True
    print("OTA: connecting to WiFi…")
    wlan.connect(WIFI_SSID, WIFI_PASS)
    for _ in range(20):
        if wlan.isconnected():
            print("OTA: WiFi OK:", wlan.ifconfig()[0])
            return True
        time.sleep(1)
    print("OTA: WiFi failed")
    return False


def _read_local_sha():
    try:
        with open(OTA_SHA_FILE) as f:
            return f.read().strip()
    except OSError:
        return ""


def _write_local_sha(sha):
    with open(OTA_SHA_FILE, "w") as f:
        f.write(sha)


def check_and_update():
    if not _connect_wifi():
        return  # no WiFi — run existing main.py as-is

    # ---- Check remote SHA ----
    print("OTA: checking version…")
    try:
        r = urequests.get(OTA_VERSION_URL, timeout=10)
        remote_sha = r.json().get("sha", "")
        r.close()
    except Exception as e:
        print("OTA: version check failed:", e)
        return  # non-fatal

    local_sha = _read_local_sha()
    print(f"OTA: local={local_sha[:12] or 'none'}  remote={remote_sha[:12]}")

    if not remote_sha or remote_sha == local_sha:
        print("OTA: up to date.")
        return

    # ---- Download new main.py ----
    print("OTA: update found — downloading…")
    try:
        r = urequests.get(OTA_SOURCE_URL, timeout=30)
        new_code = r.content
        r.close()
    except Exception as e:
        print("OTA: download failed:", e)
        return  # non-fatal — keep running existing code

    # Write to temp file first (power-cut safety)
    with open("/main_new.py", "wb") as f:
        f.write(new_code)

    # Swap in
    try:
        os.remove("/main.py")
    except OSError:
        pass
    os.rename("/main_new.py", "/main.py")

    _write_local_sha(remote_sha)
    print("OTA: update applied — rebooting…")
    time.sleep(1)
    machine.reset()