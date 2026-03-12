import network
import time
import os
import gc
import sys
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

# Additional safety: while this script runs we won't delete these files.
# Remove entries from this set if you want to allow deletion of them while running.
_PROTECT_FROM_DELETE = {"boot.py", "secrets.py"}

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

# ---- helpers ----
def _ensure_dirs(path):
    # Create directories for a given path if they do not exist.
    # e.g. for "sub/dir/file.py" create "sub" and "sub/dir"
    dirpath = path.rsplit("/", 1)[0] if "/" in path else ""
    if not dirpath:
        return
    parts = dirpath.split("/")
    cur = ""
    for p in parts:
        cur = p if not cur else (cur + "/" + p)
        try:
            os.mkdir(cur)
        except OSError:
            # exists or cannot create
            pass

def _gather_local_files(base='.'):
    # Recursively gather files, returning relative paths without leading "./"
    files = []
    try:
        entries = os.listdir(base)
    except OSError:
        return files
    for name in entries:
        path = base + '/' + name if base != '.' else name
        # try to see if path is a directory by listing it
        try:
            os.listdir(path)
            # it's a directory
            files.extend(_gather_local_files(path))
        except OSError:
            files.append(path)
    return files

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

    # Normalize manifest into a dict: filename -> sha (if sha not present, value can be None)
    files_map = {}
    try:
        if isinstance(manifest, dict):
            # common cases:
            # 1) manifest is { "files": { "a.py": "sha", ... } }
            # 2) manifest is { "a.py": "sha", ... }
            if "files" in manifest and isinstance(manifest["files"], dict):
                files_map = manifest["files"]
            else:
                # assume manifest maps names -> sha
                files_map = manifest
        elif isinstance(manifest, list):
            # manifest is a list of entries, try to map entries with 'path' and 'sha'
            for entry in manifest:
                if isinstance(entry, dict):
                    path = entry.get("path") or entry.get("name") or entry.get("filename")
                    sha = entry.get("sha") or entry.get("hash")
                    if path:
                        files_map[path] = sha
                elif isinstance(entry, str):
                    files_map[entry] = None
        else:
            print("boot: unrecognized manifest format, aborting sync")
            # cleanup and return
            try:
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
            gc.collect()
            return
    except Exception as e:
        print("boot: error parsing manifest –", e)
        # cleanup and return
        try:
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
        gc.collect()
        return

    # Lists for reporting
    synced = []
    skipped = []
    protected = []
    failed = []

    # Download/update files
    for fname, remote_sha in files_map.items():
        # compute local sha
        local_sha = _sha256(fname)
        if local_sha is not None and remote_sha is not None and local_sha == remote_sha:
            print("boot: skip (up-to-date):", fname)
            skipped.append(fname)
            continue

        # else download
        print("boot: downloading:", fname)
        try:
            # ensure directories exist
            _ensure_dirs(fname)

            # Minimal url-quote implementation (MicroPython doesn't have urllib.parse)
            def _url_quote(s, safe="/"):
                out = []
                for ch in s:
                    o = ord(ch)
                    # alphanum or - _ . ~ or chars in `safe` remain unchanged
                    if (48 <= o <= 57) or (65 <= o <= 90) or (97 <= o <= 122) or ch in "-_.~" or ch in safe:
                        out.append(ch)
                    else:
                        # UTF-8 bytes -> percent-encode each byte
                        for by in ch.encode("utf-8"):
                            out.append("%%%02X" % by)
                return "".join(out)

            url_name = _url_quote(fname, safe="/")
            url = FILE_URL + url_name
            rfile = urequests.get(url, timeout=20)

            # Prefer streaming write to avoid holding whole file in memory if possible.
            written_ok = False
            try:
                raw = getattr(rfile, "raw", None)
                if raw is not None:
                    # rfile.raw may be a socket-like object supporting read(n)
                    with open(fname, "wb") as f:
                        while True:
                            chunk = raw.read(512)
                            if not chunk:
                                break
                            f.write(chunk)
                    written_ok = True
                else:
                    # fallback: rfile.content (may use more RAM)
                    with open(fname, "wb") as f:
                        f.write(rfile.content)
                    written_ok = True

                if written_ok:
                    print("boot: synced:", fname)
                    synced.append(fname)
                else:
                    raise Exception("no data written")
            except Exception as e:
                print("boot: failed to write:", fname, "-", e)
                failed.append(fname)
            finally:
                try:
                    rfile.close()
                except Exception:
                    pass
                try:
                    del rfile
                except Exception:
                    pass

        except Exception as e:
            print("boot: download failed for", fname, "-", e)
            failed.append(fname)
            try:
                del rfile
            except Exception:
                pass
        # free memory between files
        gc.collect()

    # Prune local files not present in manifest (respecting SKIP_FILES and safety protections)
    print("boot: scanning local files for pruning…")
    local_files = set(_gather_local_files('.'))
    manifest_files = set(files_map.keys())

    to_delete = set()
    for f in local_files:
        # skip directories and special files: leave hidden files alone (optional)
        if f in manifest_files:
            continue
        if f in SKIP_FILES:
            protected.append(f)
            continue
        if f in _PROTECT_FROM_DELETE:
            protected.append(f)
            continue
        # Do not attempt to delete directories here; local_files only contains files.
        to_delete.add(f)

    deleted = []
    for f in sorted(to_delete):
        try:
            os.remove(f)
            print("boot: deleted:", f)
            deleted.append(f)
        except Exception as e:
            print("boot: failed to delete", f, "-", e)

    # final cleanup but KEEP wlan active (don't call wlan.active(False))
    try:
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

    # Print summary
    print("boot: SYNC SUMMARY")
    print("  downloaded/updated:", len(synced))
    for s in synced:
        print("    -", s)
    print("  skipped (up-to-date):", len(skipped))
    for s in skipped:
        print("    -", s)
    print("  protected (skip deletion):", len(protected))
    for s in protected:
        print("    -", s)
    print("  deleted:", len(deleted))
    for s in deleted:
        print("    -", s)
    if failed:
        print("  failed downloads:", len(failed))
        for s in failed:
            print("    -", s)

sync()

# Add this after your sync() call in boot.py

import time, gc, sys, network

def wait_for_network(timeout=20):
    wlan = network.WLAN(network.STA_IF)
    start = time.time()
    while not wlan.isconnected() and (time.time() - start) < timeout:
        print("boot: waiting for Wi-Fi...", int(time.time() - start))
        time.sleep(1)
    if wlan.isconnected():
        print("boot: Wi-Fi ready –", wlan.ifconfig()[0])
        return True
    else:
        print("boot: Wi-Fi not ready after", timeout, "s")
        return False

def run_main_with_retries(retries=3, delay=2):
    # Minimal stabilization before attempting to import main
    time.sleep(1)        # let filesystem / PIO / drivers settle
    gc.collect()
    for attempt in range(1, retries + 1):
        try:
            print("boot: importing main (attempt {}/{})".format(attempt, retries))
            import main  # runs main.py
            print("boot: main imported successfully")
            return True
        except Exception as e:
            print("boot: import main failed:", e)
            try:
                sys.print_exception(e)
            except Exception:
                pass
            if attempt < retries:
                print("boot: retrying in", delay, "s")
                time.sleep(delay)
                gc.collect()
    print("boot: giving up importing main")
    return False

# If your main requires Wi-Fi, wait first; otherwise you can skip this.
if wait_for_network(timeout=15):
    run_main_with_retries(retries=3, delay=2)
else:
    # Optionally still try to run main if it's not network-dependent.
    run_main_with_retries(retries=2, delay=2)