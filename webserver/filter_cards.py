from typing import Iterable, Optional, Sequence, Set
import json
import ijson


def _process_card(card: dict, drop_fields: Optional[Iterable[str]]) -> dict:
    """Return a shallow copy of card with top-level keys in drop_fields removed."""
    if not drop_fields:
        return card
    # Create a new dict excluding dropped keys to avoid mutating the input
    drop_set: Set[str] = set(drop_fields)
    return {k: v for k, v in card.items() if k not in drop_set}


def iter_cards(path: str):
    """Yield items from a top-level JSON array in `path`, one dict at a time."""
    with open(path, "rb") as f:
        for item in ijson.items(f, "item"):
            yield item


def filter_cards(
    input_path: str,
    output_path: str,
    languages: Sequence[str] = ("en",),
    drop_fields: Optional[Iterable[str]] = None,
    output_format: str = "ndjson",
) -> None:
    """
    Stream-filter `input_path` (top-level array) and write to `output_path`.

    - languages: iterable of allowed .lang values (default ('en',))
    - drop_fields: iterable of top-level keys to remove from each card
    - output_format: 'ndjson' (one JSON object per line) or 'array' (streaming JSON array)
    """
    allow_langs = set(languages)
    drop_fields = list(drop_fields) if drop_fields else None

    if output_format not in ("ndjson", "array"):
        raise ValueError("output_format must be 'ndjson' or 'array'")

    with open(output_path, "w", encoding="utf-8") as out:
        if output_format == "ndjson":
            for card in iter_cards(input_path):
                if card.get("lang") in allow_langs:
                    processed = _process_card(card, drop_fields)
                    out.write(json.dumps(processed, separators=(",", ":"), ensure_ascii=False))
                    out.write("\n")
        else:  # streaming JSON array
            first = True
            out.write("[")
            for card in iter_cards(input_path):
                if card.get("lang") in allow_langs:
                    processed = _process_card(card, drop_fields)
                    if not first:
                        out.write(",")
                    out.write(json.dumps(processed, separators=(",", ":"), ensure_ascii=False))
                    first = False
            out.write("]")


if __name__ == "__main__":
    # Simple CLI so this script can be used directly.
    import argparse

    p = argparse.ArgumentParser(description="Filter top-level-array JSON for language and drop fields.")
    p.add_argument("input", help="Path to input JSON (top-level array)")
    p.add_argument("output", help="Path to output file")
    p.add_argument("--languages", "-l", nargs="+", default=["en"], help="Allowed .lang values (default: en)")
    p.add_argument("--drop", "-d", nargs="*", default=[], help="Top-level fields to remove from each item")
    p.add_argument("--format", "-f", choices=["ndjson", "array"], default="ndjson", help="Output format (ndjson or array)")
    args = p.parse_args()

    filter_cards(args.input, args.output, languages=args.languages, drop_fields=args.drop, output_format=args.format)