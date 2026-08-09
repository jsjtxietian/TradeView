#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
WATCHLIST_FILE = ROOT / ".trade" / "watchlist.json"
DEFAULT_SYMBOLS = ["AAPL", "NVDA", "MSFT", "TSLA", "SPY"]


def normalize_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper().replace(".", "-")
    normalized = "-".join(part for part in normalized.split() if part)
    while "--" in normalized:
        normalized = normalized.replace("--", "-")
    return normalized


def load_symbols() -> list[str]:
    if not WATCHLIST_FILE.exists():
        return DEFAULT_SYMBOLS
    payload = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
    raw_symbols = payload.get("watchlist", []) if isinstance(payload, dict) else []
    symbols: list[str] = []
    seen: set[str] = set()
    for raw_symbol in raw_symbols:
        symbol = normalize_symbol(str(raw_symbol))
        if symbol and symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    return symbols or DEFAULT_SYMBOLS


def chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def refresh_batch(base_url: str, symbols: list[str], timeout: int) -> tuple[int, list[str]]:
    query = urlencode({"symbols": ",".join(symbols)})
    url = f"{base_url.rstrip('/')}/api/watchlist/refresh?{query}"
    with urlopen(url, data=b"", timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    items = payload.get("items", [])
    errors = [
        f"{item.get('symbol')}: {item.get('error')}"
        for item in items
        if isinstance(item, dict) and item.get("error")
    ]
    return len(items), errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh TrendDeck local market-data cache.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--sleep", type=float, default=2.0)
    parser.add_argument("symbols", nargs="*", help="Optional symbols; defaults to .trade/watchlist.json")
    args = parser.parse_args()

    symbols = [normalize_symbol(symbol) for symbol in args.symbols] if args.symbols else load_symbols()
    symbols = [symbol for symbol in symbols if symbol]
    if "SPY" not in symbols:
        symbols.append("SPY")
    if not symbols:
        print("No symbols to refresh.", file=sys.stderr)
        return 2

    total = 0
    all_errors: list[str] = []
    for index, batch in enumerate(chunks(symbols, max(1, args.batch_size)), start=1):
        try:
            count, errors = refresh_batch(args.base_url, batch, args.timeout)
        except (HTTPError, URLError, TimeoutError) as exc:
            print(f"Batch {index} failed: {exc}", file=sys.stderr)
            return 1
        total += count
        all_errors.extend(errors)
        print(f"Batch {index}: refreshed {count} symbols")
        if index * args.batch_size < len(symbols):
            time.sleep(args.sleep)

    if all_errors:
        print("Refresh completed with symbol errors:", file=sys.stderr)
        for error in all_errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print(f"Refresh completed: {total} symbols")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
