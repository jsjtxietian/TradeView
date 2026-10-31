"""Run cache-only setup research with the current model."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trenddeck.setups.model import MODEL_VERSION  # noqa: E402
from trenddeck.setups.queries import get_setup_backtest, get_setup_rankings, get_setup_report  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2024-06-03")
    parser.add_argument("--end", required=True, help="Inclusive signal AND outcome cutoff YYYY-MM-DD")
    parser.add_argument("--holdout-start", default="2026-01-01")
    parser.add_argument("--universe", choices=["watchlist", "cache"], default="cache")
    parser.add_argument("--symbols", help="Comma-separated explicit symbols (overrides universe)")
    parser.add_argument("--horizon", type=int, default=20)
    parser.add_argument("--step", type=int, default=20)
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--cost-bps", type=float, default=10)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "local/artifacts/setup-research/latest.json")
    args = parser.parse_args()
    symbols = args.symbols.split(",") if args.symbols else None
    version = MODEL_VERSION
    result = get_setup_backtest(
        args.start,
        args.end,
        symbols,
        args.universe,
        args.horizon,
        args.step,
        args.top_n,
        args.cost_bps,
        args.holdout_start,
        version,
        include_event_records=True,
    )
    result["latest_rankings"] = get_setup_rankings(
        args.end, symbols, args.universe, limit=100, model_version=version
    )
    print(
        json.dumps(
            {
                "model_version": version,
                "summary": result["summary"],
                "pivot_events": result["pivot_events"]["summary"],
                "pivot_trades": result["pivot_trades"]["summary"],
            },
            ensure_ascii=False,
        )
    )
    # A case study is descriptive and never feeds parameter selection or ranking.
    if "DELL" in result["universe"]:
        result["dell_cases"] = [
            get_setup_report("DELL", d, version)
            for d in ["2026-02-26", "2026-02-27", "2026-08-31", "2026-09-02", "2026-09-30", "2026-10-16"]
            if d <= args.end
        ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({version: result}, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    print(f"Saved {args.output.resolve()}")


if __name__ == "__main__":
    main()
