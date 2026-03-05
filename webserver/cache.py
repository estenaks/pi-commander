from __future__ import annotations
import json
import os
from datetime import datetime, timedelta
import threading

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
MAX_CACHE_SIZE_GB = 20
CACHE_EXPIRY_DAYS = 365

_cache_lock = threading.Lock()

def _ensure_cache_dir():
    """Ensure the cache directory exists."""
    os.makedirs(CACHE_DIR, exist_ok=True)


def _get_cache_path(cache_key: str) -> str:
    """Get the full path for a cache file."""
    return os.path.join(CACHE_DIR, f"{cache_key}.json")


def _get_cache_size_gb() -> float:
    """Get current cache size in GB."""
    if not os.path.exists(CACHE_DIR):
        return 0.0

    total_size = 0
    for filename in os.listdir(CACHE_DIR):
        file_path = os.path.join(CACHE_DIR, filename)
        if os.path.isfile(file_path):
            total_size += os.path.getsize(file_path)

    return total_size / (1024 ** 3)


def _cleanup_old_cache():
    """Remove cache files older than CACHE_EXPIRY_DAYS or if cache is too large."""
    if not os.path.exists(CACHE_DIR):
        return

    now = datetime.now()
    files_info = []

    for filename in os.listdir(CACHE_DIR):
        if not filename.endswith('.json'):
            continue

        file_path = os.path.join(CACHE_DIR, filename)
        if os.path.isfile(file_path):
            mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
            size = os.path.getsize(file_path)
            files_info.append((file_path, mtime, size))

    for file_path, mtime, _ in files_info:
        if now - mtime > timedelta(days=CACHE_EXPIRY_DAYS):
            try:
                os.remove(file_path)
                print(f"[cache] Removed expired cache file: {os.path.basename(file_path)}")
            except Exception as e:
                print(f"[cache] Failed to remove expired file {file_path}: {e}")

    current_size = _get_cache_size_gb()
    if current_size > MAX_CACHE_SIZE_GB:
        files_info.sort(key=lambda x: x[1])

        for file_path, _, size in files_info:
            if current_size <= MAX_CACHE_SIZE_GB:
                break

            try:
                os.remove(file_path)
                current_size -= size / (1024 ** 3)
                print(f"[cache] Removed old cache file to free space: {os.path.basename(file_path)}")
            except Exception as e:
                print(f"[cache] Failed to remove file {file_path}: {e}")


def _get_cached_data(cache_key: str, cache_hours: int = 24) -> dict | None:
    """Get data from cache if it exists and is not expired."""
    cache_path = _get_cache_path(cache_key)

    if not os.path.exists(cache_path):
        return None

    try:
        if cache_key.startswith("set_") and cache_key.endswith("_full"):
            with open(cache_path, 'r', encoding='utf-8') as f:
                return json.load(f)

        mtime = datetime.fromtimestamp(os.path.getmtime(cache_path))
        if datetime.now() - mtime > timedelta(hours=cache_hours):
            os.remove(cache_path)
            print(f"[cache] Removed expired cache file: {cache_key} (older than {cache_hours} hours)")
            return None

        with open(cache_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"[cache] Error reading cache file {cache_key}: {e}")
        return None


def _set_cached_data(cache_key: str, data: dict) -> None:
    """Save data to cache."""
    _ensure_cache_dir()
    cache_path = _get_cache_path(cache_key)

    try:
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        print(f"[cache] Cached data for key: {cache_key}")
    except Exception as e:
        print(f"[cache] Error writing cache file {cache_key}: {e}")