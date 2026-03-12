import network
import time
import urequests
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
    if not _connect():
        return

    print("boot: fetching manifest…")
    try:
        r = urequests.get(MANIFEST_URL, timeout=10)
        manifest = r.json()   # {"files": {"main.py": "<sha256>", ...}}
        r.close()
    except Exception as e:
        print("boot: manifest fetch failed –", e)
        return

    changed = False
    download_failed = False

    manifest_files = set(manifest.get("files", {}).keys())

    for filename, remote_sha in manifest.get("files", {}).items():
        if filename in SKIP_FILES:
            # We still include protected files in manifest_files so pruning won't remove them,
            # but we skip forcing an update if you prefer to leave it local-only.
            pass

        local_path = "/" + filename
        local_sha = _sha256(local_path)

        if local_sha == remote_sha:
            print("boot: up-to-date –", filename)
            continue

        print("boot: downloading –", filename)
        try:
            r = urequests.get(FILE_URL + filename, timeout=30)
            tmp = local_path + ".tmp"
            with open(tmp, "wb") as f:
                while True:
                    chunk = r.raw.read(4096)
                    if not chunk:
                        break
                    f.write(chunk)
            r.close()
            try:
                os.remove(local_path)
            except OSError:
                pass
            os.rename(tmp, local_path)
            print("boot: updated –", filename)
            changed = True
        except Exception as e:
            print("boot: failed to download", filename, "–", e)
            download_failed = True
            # don't break here — try to continue to report all failures

    # ---- Prune local files not present in manifest ----
    # Only prune if manifest was fully processed without download failures.
    if download_failed:
        print("boot: download failures detected — skipping prune to avoid data loss")
    else:
        try:
            # list top-level .py files (adjust if you want to prune other extensions)
            local_candidates = []
            for name in os.listdir("/"):
                # ignore temporary files and only consider .py files
                if not name.endswith(".py"):
                    continue
                # skip files we definitely don't want to touch
                if name in SKIP_FILES:
                    continue
                # ensure it's a regular file we can access (skip if _sha256 can't read it)
                if _sha256("/" + name) is None:
                    # if file unreadable, skip it (conservative)
                    continue
                local_candidates.append(name)

            to_remove = [n for n in local_candidates if n not in manifest_files and n not in SKIP_FILES]
            if to_remove:
                for n in to_remove:
                    p = "/" + n
                    try:
                        os.remove(p)
                        print("boot: removed unused file –", n)
                    except OSError as e:
                        print("boot: failed to remove", n, "–", e)
            else:
                print("boot: no unused files to remove")
        except Exception as e:
            print("boot: pruning error –", e)

    if changed:
        print("boot: files updated — rebooting…")
        time.sleep(1)
        import machine
        machine.reset()
    else:
        print("boot: all files current")

sync()