# ---------------------------------------------------------------------------
# MTGJSON booster config
# ---------------------------------------------------------------------------

# Sheet name fragments that map to known slot types understood by the frontend.
# Checked in order — first match wins.
_SHEET_TYPE_MAP = [
    ("timeshifted",  "timeshifted"),
    ("foil",         "foil"),
    ("land",         "land"),
    ("basicland",    "land"),
    ("basic",        "land"),
    ("mythic",       "rare"),    # mythic-only sheet → treat as rare slot
    ("rare",         "rare"),
    ("uncommon",     "uncommon"),
    ("common",       "common"),
]

_SLOT_LABELS = {
    "common":      "Common",
    "uncommon":    "Uncommon",
    "rare":        "Rare",
    "land":        "Land",
    "foil":        "✨",
    "timeshifted": "Timeshifted",
}

MTGJSON_SET_URL = "https://mtgjson.com/api/v5/{code}.json"
MTGJSON_CACHE_HOURS = 24 * 7  # one week — booster configs don't change


def _classify_sheet(sheet_name: str) -> str:
    """Map a raw MTGJSON sheet name to one of our known slot type strings."""
    lower = sheet_name.lower()
    for fragment, slot_type in _SHEET_TYPE_MAP:
        if fragment in lower:
            return slot_type
    return "common"  # safe fallback


def _foil_probability_from_slot(slot: dict | list) -> float:
    """
    Given a raw MTGJSON slot entry (either a string/dict for a fixed sheet,
    or a list of [sheetName, weight] pairs for a variable slot), return the
    probability (0.0–1.0) that the slot produces a foil card.

    Examples
    --------
    "foil"                                 → 1.0
    [["foil", 1], ["common", 4]]           → 0.2
    [["foil", 1], ["uncommon", 1]]         → 0.5
    "common"                               → 0.0
    """
    if isinstance(slot, str):
        return 1.0 if "foil" in slot.lower() else 0.0

    if isinstance(slot, list):
        total = sum(w for _, w in slot)
        if total == 0:
            return 0.0
        foil_weight = sum(w for name, w in slot if "foil" in name.lower())
        return foil_weight / total

    return 0.0


def _parse_mtgjson_slots(booster_data: dict) -> list[dict]:
    """
    Parse a MTGJSON booster config dict (one booster type, e.g. 'default')
    into a flat list of slot descriptors understood by the frontend.

    Each descriptor:
    {
        "type":             str,    # "common" | "uncommon" | "rare" | "land" | "foil" | "timeshifted"
        "count":            int,
        "foil":             bool,
        "foil_probability": float,  # only present when foil > 0
        "label":            str,
    }
    """
    raw_slots = booster_data.get("slots", [])
    sheets = booster_data.get("sheets", {})
    result = []

    for raw in raw_slots:
        deck = raw.get("deck")
        count = raw.get("count", 1)

        if deck is None:
            continue

        # Variable slot: deck is a list of [sheetName, weight] pairs
        if isinstance(deck, list):
            foil_prob = _foil_probability_from_slot(deck)
            # Classify by the highest-weight non-foil sheet name for label/type
            non_foil = [(name, w) for name, w in deck if "foil" not in name.lower()]
            primary = max(non_foil, key=lambda x: x[1])[0] if non_foil else deck[0][0]
            slot_type = "foil" if foil_prob >= 1.0 else _classify_sheet(primary)
            is_foil = foil_prob >= 1.0
        else:
            # Fixed slot: deck is a sheet name string
            sheet_meta = sheets.get(deck, {})
            is_foil = bool(sheet_meta.get("foil", False)) or "foil" in deck.lower()
            foil_prob = 1.0 if is_foil else 0.0
            slot_type = _classify_sheet(deck)

        descriptor: dict = {
            "type":  slot_type,
            "count": count,
            "foil":  is_foil,
            "label": _SLOT_LABELS.get(slot_type, slot_type.capitalize()),
        }
        if foil_prob > 0.0:
            descriptor["foil_probability"] = round(foil_prob, 4)

        result.append(descriptor)

    return result


def _get_booster_config(set_code: str) -> dict:
    """
    Fetch, parse, and cache the MTGJSON booster config for *set_code*.

    Returns:
        {
            "slots": [ { type, count, foil, foil_probability?, label }, ... ],
            "source": "mtgjson" | "fallback"
        }

    Falls back to the classic 10C/3U/1R/1L layout if MTGJSON has no data.
    """
    cache_key = f"booster_config_{set_code}"

    with _cache_lock:
        cached = _get_cached_data(cache_key, cache_hours=MTGJSON_CACHE_HOURS)
        if cached is not None:
            print(f"[booster_config] Cache hit for {set_code}")
            return cached

    print(f"[booster_config] Fetching MTGJSON booster config for {set_code}...")
    url = MTGJSON_SET_URL.format(code=set_code.upper())

    try:
        import urllib.request
        import json as _json

        req = urllib.request.Request(url, headers={"User-Agent": "pi-commander/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = _json.loads(resp.read().decode())

        booster_section = raw.get("data", {}).get("booster", {})

        # Prefer "draft" booster type, then "default", then first available
        booster_data = (
            booster_section.get("draft")
            or booster_section.get("default")
            or next(iter(booster_section.values()), None)
        )

        if booster_data:
            slots = _parse_mtgjson_slots(booster_data)
            if slots:
                result = {"slots": slots, "source": "mtgjson"}
                with _cache_lock:
                    _set_cached_data(cache_key, result)
                print(f"[booster_config] Parsed {len(slots)} slot types for {set_code} from MTGJSON")
                return result

        print(f"[booster_config] No usable booster data in MTGJSON for {set_code}, using fallback")

    except Exception as e:
        print(f"[booster_config] MTGJSON fetch failed for {set_code}: {e} — using fallback")

    # Fallback: classic draft booster layout (no foil slot)
    fallback = {
        "slots": [
            {"type": "common",   "count": 10, "foil": False, "label": "Common"},
            {"type": "uncommon", "count": 3,  "foil": False, "label": "Uncommon"},
            {"type": "rare",     "count": 1,  "foil": False, "label": "Rare"},
            {"type": "land",     "count": 1,  "foil": False, "label": "Land"},
        ],
        "source": "fallback",
    }
    with _cache_lock:
        _set_cached_data(cache_key, fallback)
    return fallback