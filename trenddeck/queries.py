"""Read-only, bounded queries for agents. Market providers are never called here."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

from trenddeck import market, storage
from trenddeck.analysis import analyze_frames, build_summary_fields, serialize_history
from trenddeck.config import DEFAULT_BENCHMARK, DEFAULT_HISTORY_PERIOD
from trenddeck.indicators import (
    BASE_TREND_SPECS,
    AnalysisContext,
    add_indicators,
    compute_rs_proxy,
)
from trenddeck.utils import normalize_symbol


def valid_symbol(value: str) -> str:
    symbol = normalize_symbol(value)
    if not re.fullmatch(r"[A-Z0-9^][A-Z0-9^\-]{0,31}", symbol):
        raise ValueError("Use a stock symbol such as NVDA or BRK-B (maximum 32 characters).")
    return symbol


def valid_date(value: str | None) -> str | None:
    if value is None:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Dates must use YYYY-MM-DD.")
    return date.fromisoformat(value).isoformat()


def latest_date(frame: pd.DataFrame) -> str | None:
    return None if frame.empty else frame["Date"].iloc[-1].strftime("%Y-%m-%d")


def read_history(symbol: str, end_date: str | None = None) -> pd.DataFrame:
    # Read disk directly: a separate refresh process or Git update must not leave
    # an agent reading the web process's 30-minute memory cache.
    try:
        frame = market.load_history_cache(symbol, "3y")
    except (OSError, ValueError, KeyError) as exc:
        raise ValueError(f"Cannot read the local price cache for {symbol}.") from exc
    if frame.empty:
        return frame
    required = {"Date", "Open", "High", "Low", "Close", "Volume"}
    if not required.issubset(frame.columns):
        raise ValueError(f"The local price cache for {symbol} is missing OHLCV columns.")
    frame = frame.sort_values("Date").drop_duplicates("Date", keep="last")
    if end_date:
        frame = frame.loc[frame["Date"] <= pd.Timestamp(end_date)]
    return frame.reset_index(drop=True)


def context_for(frame: pd.DataFrame, benchmark: pd.DataFrame) -> AnalysisContext:
    history = add_indicators(frame)
    aligned_benchmark = (
        benchmark.loc[benchmark["Date"] <= frame["Date"].iloc[-1]] if not benchmark.empty else benchmark
    )
    enriched_benchmark = add_indicators(aligned_benchmark) if not aligned_benchmark.empty else pd.DataFrame()
    score, detail = (
        compute_rs_proxy(history, enriched_benchmark)
        if not enriched_benchmark.empty
        else (None, "SPY cache unavailable")
    )
    return AnalysisContext(history, enriched_benchmark, history.iloc[-1], score, detail)


def compact_summary(symbol: str, frame: pd.DataFrame, benchmark: pd.DataFrame) -> dict[str, Any]:
    context = context_for(frame, benchmark)
    summary, checks = build_summary_fields(symbol, context.stock, context)
    keys = (
        "symbol",
        "latestDate",
        "latestClose",
        "dailyChangePct",
        "fiveDayChangePct",
        "latestVolumeRatioMA50",
        "latestMA50",
        "crossedAboveMA50",
        "crossedBelowMA50",
        "isSixMonthHigh",
        "isSixMonthLow",
        "trendPassCount",
        "trendTotal",
        "trendTemplateVariantMatch",
        "previousRangeBelowMA50",
    )
    result = {key: summary[key] for key in keys}
    result["rsScore"] = None if context.rs_score is None else round(context.rs_score, 1)
    result["checks"] = {
        spec.key: {"name": check.name, "passed": check.passed}
        for spec, check in zip(BASE_TREND_SPECS, checks)
    }
    return result


def session_changes(current: dict[str, Any], previous: dict[str, Any] | None) -> list[dict[str, Any]]:
    events = []

    def add(kind: str, value: Any = None) -> None:
        events.append({"type": kind, "value": value})

    if previous is not None:
        now_full = current["trendPassCount"] == current["trendTotal"]
        was_full = previous["trendPassCount"] == previous["trendTotal"]
        if now_full != was_full:
            add("trend_template_entered" if now_full else "trend_template_exited")
        for key, check in current["checks"].items():
            old = previous["checks"][key]["passed"]
            if check["passed"] != old:
                add(
                    "trend_check_changed",
                    {"key": key, "name": check["name"], "before": old, "after": check["passed"]},
                )
    for field, kind in (
        ("crossedAboveMA50", "crossed_above_ma50"),
        ("crossedBelowMA50", "crossed_below_ma50"),
        ("isSixMonthHigh", "six_month_closing_high"),
        ("isSixMonthLow", "six_month_closing_low"),
    ):
        if current[field]:
            add(kind)
    for field, threshold, kind in (
        ("dailyChangePct", 0.05, "large_daily_move"),
        ("fiveDayChangePct", 0.08, "large_five_session_move"),
    ):
        value = current[field]
        if value is not None and abs(value) >= threshold:
            add(kind, value)
    volume = current["latestVolumeRatioMA50"]
    if volume is not None and (volume >= 1.5 or volume <= 0.5):
        add("high_volume" if volume >= 1.5 else "low_volume", volume)
    return events


def metadata() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "local Tiingo daily cache",
        "read_only": True,
        "refresh_status": "not_tracked",
        "units": {
            "returns": "fraction: 0.05 = 5%",
            "volume": "shares",
            "volume_ratio": "multiple of 50-session average",
        },
        "limitations": [
            "Cache dates are data cutoffs, not proof that the latest scheduled refresh succeeded.",
            "RS is a local weighted excess-return score versus SPY, not the official IBD percentile.",
        ],
    }


def get_daily_changes(
    session_date: str | None = None,
    symbols: list[str] | None = None,
    include_unchanged: bool = False,
    limit: int = 30,
    offset: int = 0,
) -> dict[str, Any]:
    """Compare cached bars on one SPY trading session with the preceding session."""
    session_date = valid_date(session_date)
    if not 1 <= limit <= 100 or not 0 <= offset <= 10000:
        raise ValueError("limit must be 1..100; offset must be 0..10000.")
    benchmark = read_history(DEFAULT_BENCHMARK)
    if benchmark.empty:
        raise ValueError("SPY has no local cache. A reference trading session cannot be determined.")
    latest_reference = latest_date(benchmark)
    session_date = session_date or latest_reference
    benchmark = benchmark.loc[benchmark["Date"] <= pd.Timestamp(session_date)].reset_index(drop=True)
    if latest_date(benchmark) != session_date:
        raise ValueError(
            f"No cached SPY bar for {session_date}; latest cached session is {latest_reference}. Use a cached trading date."
        )
    previous_session = latest_date(benchmark.iloc[:-1])
    notes = storage.load_symbol_notes() or {}
    watchlist = storage.load_watchlist_state()
    configured = (
        watchlist["watchlist"] if watchlist is not None else storage.load_configured_watchlist_symbols()
    )
    requested = (
        symbols
        if symbols is not None
        else [*configured, *(s for s, note in notes.items() if note.get("isHolding"))]
    )
    if len(requested) > 200:
        raise ValueError("Query at most 200 symbols per call.")
    universe = list(dict.fromkeys(valid_symbol(symbol) for symbol in requested))
    items, errors, behind_reference, session_gaps = [], [], [], []
    available = 0
    for symbol in universe:
        try:
            frame = benchmark if symbol == DEFAULT_BENCHMARK else read_history(symbol, session_date)
            if frame.empty:
                errors.append({"symbol": symbol, "error": "no_cached_history_on_or_before_session"})
                continue
            if latest_date(frame) != session_date:
                behind_reference.append({"symbol": symbol, "latest_session": latest_date(frame)})
                continue
            current = compact_summary(symbol, frame, benchmark)
            previous_frame = frame.iloc[:-1]
            previous = (
                compact_summary(symbol, previous_frame, benchmark) if not previous_frame.empty else None
            )
            if latest_date(previous_frame) != previous_session:
                session_gaps.append(
                    {"symbol": symbol, "previous_available_session": latest_date(previous_frame)}
                )
            events = session_changes(current, previous)
            available += 1
            if events or include_unchanged:
                current.pop("checks")
                items.append(
                    {
                        **current,
                        "isHolding": bool(notes.get(symbol, {}).get("isHolding")),
                        "previousSession": latest_date(previous_frame),
                        "priceMode": market.get_history_price_mode(frame),
                        "changes": events,
                    }
                )
        except (ValueError, KeyError, TypeError) as exc:
            errors.append({"symbol": symbol, "error": str(exc)})
    items.sort(
        key=lambda item: (
            not item["isHolding"],
            -len(item["changes"]),
            -abs(item["dailyChangePct"] or 0),
            item["symbol"],
        )
    )
    fingerprint = hashlib.sha256(
        json.dumps(
            [session_date, universe, items, errors, behind_reference, session_gaps],
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()[:20]
    market_summary = compact_summary(DEFAULT_BENCHMARK, benchmark, benchmark)
    market_summary.pop("checks")
    return {
        **metadata(),
        "as_of_session": session_date,
        "previous_session": previous_session,
        "latest_reference_session": latest_reference,
        "snapshot_id": fingerprint,
        "market": market_summary,
        "coverage": {
            "requested": len(universe),
            "available": available,
            "behind_reference": behind_reference,
            "session_gaps": session_gaps,
            "errors": errors,
        },
        "universe": universe,
        "total_items": len(items),
        "items": items[offset : offset + limit],
        "next_offset": offset + limit if offset + limit < len(items) else None,
        "comparison": "Changes are recalculated from cached daily bars, not alert creation timestamps. Historical queries use today's watchlist and manually maintained holding flags.",
    }


def get_symbol_analysis(symbol: str, as_of: str | None = None) -> dict[str, Any]:
    symbol, as_of = valid_symbol(symbol), valid_date(as_of)
    frame = read_history(symbol, as_of)
    if frame.empty:
        raise ValueError(f"No local history for {symbol} on or before the requested date.")
    session = latest_date(frame)
    benchmark = read_history(DEFAULT_BENCHMARK, session)
    data = analyze_frames(symbol, frame, benchmark)
    # Full histories and duplicate indicator windows are available separately;
    # the default answer stays small enough for follow-up conversations.
    for key in (
        "history",
        "benchmarkHistory",
        "trendSparklineValues",
        "buyIndicatorGroupsByWindow",
        "sellIndicatorGroupsByWindow",
        "sourceNotes",
    ):
        data.pop(key, None)
    notes = storage.load_symbol_notes() or {}
    return {
        **metadata(),
        "as_of_session": session,
        "requested_as_of": as_of,
        "benchmark_session": latest_date(benchmark),
        "price_mode": market.get_history_price_mode(frame),
        "analysis": data,
        "note": notes.get(symbol),
        "holding_source": "Current manually maintained notes; not a live brokerage position or a historical position snapshot.",
    }


def get_symbol_data(
    symbol: str,
    as_of: str | None = None,
    history_limit: int = 60,
) -> dict[str, Any]:
    """Return analysis and history, fetching the symbol when its cache is absent."""
    symbol = valid_symbol(symbol)
    if not 1 <= history_limit <= 500:
        raise ValueError("history_limit must be 1..500.")
    cached_frame = market.load_history_cache(symbol, DEFAULT_HISTORY_PERIOD)
    cache_status = "fetched" if cached_frame.empty else "refreshed"
    if not cached_frame.empty and market.is_refresh_cooldown_active(symbol, DEFAULT_HISTORY_PERIOD):
        cache_status = "cooldown"
    market.load_history(
        symbol,
        DEFAULT_HISTORY_PERIOD,
        force_refresh=True,
        allow_network=True,
        tiingo_api_key=market.get_tiingo_api_key(),
        require_refresh_success=True,
    )
    analysis_payload = get_symbol_analysis(symbol, as_of)
    history_payload = get_price_history(
        symbol,
        end_date=analysis_payload["as_of_session"],
        limit=history_limit,
    )
    return {
        **analysis_payload,
        "read_only": False,
        "cache": {
            "status": cache_status,
            "fetch_policy": "Refresh and save three-year daily history unless a recent refresh is still within the cooldown; never change the watchlist.",
        },
        "history": {
            "rows": history_payload["rows"],
            "order": history_payload["order"],
            "cached_range": history_payload["cached_range"],
            "has_more": history_payload["has_more"],
            "next_before": history_payload["next_before"],
            "range_note": history_payload["range_note"],
        },
    }


def get_price_history(
    symbol: str,
    start_date: str | None = None,
    end_date: str | None = None,
    before: str | None = None,
    limit: int = 60,
) -> dict[str, Any]:
    symbol = valid_symbol(symbol)
    start_date, end_date, before = valid_date(start_date), valid_date(end_date), valid_date(before)
    if not 1 <= limit <= 500:
        raise ValueError("limit must be 1..500.")
    if start_date and end_date and start_date > end_date:
        raise ValueError("start_date must be on or before end_date.")
    frame = read_history(symbol)
    if frame.empty:
        raise ValueError(f"{symbol} has no local history. MCP never downloads missing data.")
    cached_start, cached_end = frame["Date"].iloc[0].strftime("%Y-%m-%d"), latest_date(frame)
    mode = market.get_history_price_mode(frame)
    history = add_indicators(frame)
    if start_date:
        history = history.loc[history["Date"] >= pd.Timestamp(start_date)]
    if end_date:
        history = history.loc[history["Date"] <= pd.Timestamp(end_date)]
    if before:
        history = history.loc[history["Date"] < pd.Timestamp(before)]
    rows = serialize_history(history.tail(limit))
    return {
        **metadata(),
        "symbol": symbol,
        "price_mode": mode,
        "cached_range": {"start": cached_start, "end": cached_end},
        "as_of_session": cached_end,
        "rows": rows,
        "order": "oldest_to_newest",
        "has_more": len(history) > limit,
        "next_before": rows[0]["Date"] if len(history) > limit else None,
        "range_note": "start_date/end_date are inclusive; before is exclusive. Pagination returns the newest matching page first. No data beyond the local cache is fetched.",
    }
