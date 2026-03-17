from __future__ import annotations
import argparse
import json
import os
import sys
import time
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, List, Tuple
from urllib.parse import urlparse
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
import multiprocessing

# Attempt to import the filter_cards module from the same package or as a top-level module.
# This keeps the import tolerant for different execution contexts (module vs script).
try:
    import webserver.filter_cards as filter_cards
except Exception:
    try:
        import filter_cards as filter_cards
    except Exception:
        filter_cards = None

# --- Config (legacy-compatible) ----------------------------------------------
DEFAULT_CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
METADATA_FILENAME = "metadata.json"
SOURCES = [
    "https://api.scryfall.com/bulk-data",
]

SCRYFALL_USER_AGENT = os.getenv("SCRYFALL_USER_AGENT", "raspberrypi-webserver-poc/0.1")
SCRYFALL_ACCEPT = os.getenv("SCRYFALL_ACCEPT", "application/json")

SCRYFALL_HEADERS = {
    "User-Agent": SCRYFALL_USER_AGENT,
    "Accept": SCRYFALL_ACCEPT,
}

# --- utilities ----------------------------------------------------------------

def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)

def load_metadata(cache_dir: str) -> Dict[str, Any]:
    path = os.path.join(cache_dir, METADATA_FILENAME)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_metadata(cache_dir: str, metadata: Dict[str, Any]) -> None:
    path = os.path.join(cache_dir, METADATA_FILENAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, sort_keys=True)

def parse_iso(s: str) -> datetime:
    try:
        if s.endswith("Z"):
            s2 = s[:-1] + "+00:00"
        else:
            s2 = s
        return datetime.fromisoformat(s2)
    except Exception:
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                return datetime.strptime(s, fmt)
            except Exception:
                pass
        raise

def filename_from_url(url: str) -> str:
    p = urlparse(url).path
    return os.path.basename(p)

def safe_filename(prefix: str, url: str) -> str:
    base = filename_from_url(url) or "data"
    candidate = f"{prefix}__{base}"
    return re.sub(r'[^A-Za-z0-9._-]', '_', candidate)

def is_older_than(dt: datetime, days: int) -> bool:
    now = datetime.now(timezone.utc)
    return (now - dt) > timedelta(days=days)

# --- HTTP helpers (urllib) ---------------------------------------------------

def fetch_bulk_index() -> Dict[str, Any]:
    last_exc = None
    for src in SOURCES:
        try:
            req = urllib.request.Request(src, headers=SCRYFALL_HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_exc = e
            continue
        except Exception as e:
            last_exc = e
            continue
    raise last_exc or Exception("Failed to fetch bulk index")

def download_to_file(url: str, dest_path: str, retries: int = 3, timeout: int = 60) -> None:
    """
    Download using urllib.request.urlopen in streaming mode. Writes to dest_path + ".part" and then os.replace.
    Retries on errors with exponential backoff.
    """
    attempt = 0
    while True:
        try:
            req = urllib.request.Request(url, headers=SCRYFALL_HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                tmp = dest_path + ".part"
                ensure_dir(os.path.dirname(dest_path) or ".")
                with open(tmp, "wb") as f:
                    while True:
                        chunk = resp.read(8192)
                        if not chunk:
                            break
                        f.write(chunk)
                os.replace(tmp, dest_path)
                return
        except Exception as e:
            attempt += 1
            if attempt > retries:
                raise
            wait = 2 ** attempt
            print(f"Download error for {url} ({e}), retrying in {wait}s... (attempt {attempt}/{retries})", file=sys.stderr)
            time.sleep(wait)

# --- main logic ---------------------------------------------------------------

def sync_bulk(cache_dir: str, days: int = 7, check_only: bool = False, force_refresh: bool = False, workers: Optional[int] = None) -> None:
    ensure_dir(cache_dir)
    metadata = load_metadata(cache_dir)

    print("Fetching bulk-data index...")
    index = fetch_bulk_index()
    if isinstance(index, dict) and "data" in index and isinstance(index["data"], list):
        data_list = index["data"]
    elif isinstance(index, list):
        data_list = index
    else:
        raise RuntimeError("Unexpected bulk index format")

    remote_by_type: Dict[str, Any] = {item["type"]: item for item in data_list}

    # prepare list of tasks to download in parallel
    download_tasks: List[Tuple[str, str, str, str]] = []
    # (data_type, download_uri, dest_path, remote_updated_at_str)

    for data_type, remote_item in remote_by_type.items():
        remote_updated_at_str = remote_item.get("updated_at") or remote_item.get("date")
        if not remote_updated_at_str:
            print(f"[{data_type}] warning: no updated_at on remote item; skipping", file=sys.stderr)
            continue
        remote_updated_at = parse_iso(remote_updated_at_str)
        download_uri = remote_item.get("download_uri") or remote_item.get("download_url") or remote_item.get("uri")
        if not download_uri:
            print(f"[{data_type}] warning: no download_uri; skipping", file=sys.stderr)
            continue

        entry = metadata.get(data_type)
        if entry is None:
            print(f"[{data_type}] new -> will download")
            if not check_only:
                fname = safe_filename(data_type, download_uri)
                dest_path = os.path.join(cache_dir, fname)
                download_tasks.append((data_type, download_uri, dest_path, remote_updated_at_str))
            else:
                print(f"  check-only: would download {download_uri}")
        else:
            local_updated_at = parse_iso(entry["updated_at"])
            if force_refresh:
                print(f"[{data_type}] force-refresh -> will download replacement")
                if not check_only:
                    fname = safe_filename(data_type, download_uri)
                    dest_path = os.path.join(cache_dir, fname)
                    download_tasks.append((data_type, download_uri, dest_path, remote_updated_at_str))
                else:
                    print(f"  check-only: would re-download {download_uri}")
                continue

            if not is_older_than(local_updated_at, days):
                age_days = (datetime.now(timezone.utc) - local_updated_at).days
                print(f"[{data_type}] cached {age_days} days old; younger than threshold ({days}d) -> skip")
                entry["last_checked"] = datetime.now(timezone.utc).isoformat()
                metadata[data_type] = entry
                continue

            # Local is older than threshold -> check remote updated_at
            if remote_updated_at > local_updated_at:
                print(f"[{data_type}] remote updated ({remote_updated_at_str}) > local ({entry[\"updated_at\"]}) -> will update")
                if not check_only:
                    fname = safe_filename(data_type, download_uri)
                    dest_path = os.path.join(cache_dir, fname)
                    download_tasks.append((data_type, download_uri, dest_path, remote_updated_at_str))
                else:
                    print(f"  check-only: would update {download_uri}")
            else:
                print(f"[{data_type}] remote not newer ({remote_updated_at_str} <= {entry[\"updated_at\"]}) -> skip")
                entry["last_checked"] = datetime.now(timezone.utc).isoformat()
                metadata[data_type] = entry

    # execute downloads in parallel
    if download_tasks:
        if check_only:
            print("\nCheck-only mode: no downloads performed. Planned downloads:")
            for data_type, url, dest, upd in download_tasks:
                print(f" - {data_type}: {url} -> {dest} (remote updated_at: {upd})")
        else:
            # determine worker count
            if workers is None:
                cpu = multiprocessing.cpu_count() or 1
                workers = min(4, cpu)
            workers = max(1, int(workers))
            print(f"\nStarting downloads with {workers} worker(s) ...")
            futures = {}
            with ThreadPoolExecutor(max_workers=workers) as exe:
                for data_type, url, dest, remote_updated_at_str in download_tasks:
                    future = exe.submit(download_to_file, url, dest)
                    futures[future] = (data_type, url, dest, remote_updated_at_str)

                for fut in as_completed(futures):
                    data_type, url, dest, remote_updated_at_str = futures[fut]
                    try:
                        fut.result()
                        # successful download -> update metadata
                        metadata[data_type] = {
                            "updated_at": remote_updated_at_str,
                            "file": os.path.basename(dest),
                            "download_uri": url,
                            "last_checked": datetime.now(timezone.utc).isoformat()
                        }
                        print(f"[{data_type}] downloaded -> {metadata[data_type][\"file\"]}")
                    except Exception as e:
                        print(f"[{data_type}] download failed: {e}", file=sys.stderr)

    # detect types present in metadata but not in remote index
    removed = [t for t in metadata.keys() if t not in remote_by_type]
    if removed:
        print("\nDetected types present in metadata but not in remote index:")
        for t in removed:
            print(f" - {t} (kept in cache; remove manually if desired)")

    save_metadata(cache_dir, metadata)

    # If a downloaded 'all_cards__*' file exists but a corresponding
    # 'all_cards_filtered__*' does not, run the filter to create it.
    # This keeps the filtered NDJSON cache available without re-processing
    # every time.
    if filter_cards is not None:
        try:
            for fname in os.listdir(cache_dir):
                if not fname.startswith("all_cards__"):
                    continue
                rest = fname[len("all_cards__"):]  
                filtered_name = f"all_cards_filtered__{rest}"
                input_path = os.path.join(cache_dir, fname)
                filtered_path = os.path.join(cache_dir, filtered_name)
                if os.path.exists(filtered_path):
                    # already present
                    continue
                print(f"[filter] creating {filtered_name} from {fname} ...")
                try:
                    # create NDJSON filtered output keeping only english cards
                    filter_cards.filter_cards(input_path, filtered_path, languages=("en",), drop_fields=None, output_format="ndjson")
                    print(f"[filter] wrote {filtered_name}")
                except Exception as e:
                    print(f"[filter] failed to create {filtered_name}: {e}", file=sys.stderr)
        except Exception as e:
            print(f"[filter] error while scanning cache dir: {e}", file=sys.stderr)

    print("Done. Metadata saved to", os.path.join(cache_dir, METADATA_FILENAME))

# --- CLI ---------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="Sync Scryfall bulk-data into a local cache with weekly checks (parallel downloads). Uses urllib.request and env-driven headers.")
    parser.add_argument("--cache-dir", "-d", default=DEFAULT_CACHE_DIR, help="Directory to store cached files and metadata")
    parser.add_argument("--days", "-n", type=int, default=7, help="How old (in days) a local item must be before re-checking remote (default: 7)")
    parser.add_argument("--check-only", action="store_true", help="Don't download anything; only report what would be downloaded")
    parser.add_argument("--force-refresh", action="store_true", help="Force re-download of all entries (overrides --days)")
    parser.add_argument("--workers", "-w", type=int, default=None, help="Number of parallel download workers (default: min(4, cpu_count()))")
    args = parser.parse_args(argv)

    try:
        sync_bulk(args.cache_dir, days=args.days, check_only=args.check_only, force_refresh=args.force_refresh, workers=args.workers)
    except Exception as e:
        print("Error:", e, file=sys.stderr)
        sys.exit(2)

if __name__ == "__main__":
    main()