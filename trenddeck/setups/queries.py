"""Read-only cache services shared by HTTP and MCP setup endpoints."""

from __future__ import annotations

from typing import Any

import pandas as pd

from trenddeck import market, storage
from trenddeck.queries import latest_date, read_history, valid_date, valid_symbol
from trenddeck.setups.engine import score_setup
from trenddeck.setups.explanations import conditions_markdown, score_reasons
from trenddeck.setups.model import MODEL_VERSION, load_model


def valid_setup_type(setup_type: str) -> str:
    if setup_type not in {"base", "pullback"}:
        raise ValueError("setup_type must be base or pullback")
    return setup_type


def universe_symbols(symbols: list[str] | None, universe: str = "watchlist") -> list[str]:
    if universe not in {"watchlist", "cache"}:
        raise ValueError("universe must be watchlist or cache")
    if symbols:
        if len(symbols) > 200:
            raise ValueError("At most 200 symbols")
        return list(dict.fromkeys(valid_symbol(s) for s in symbols))
    if universe == "cache":
        return sorted(
            valid_symbol(p.name.removesuffix("_3y_history.csv"))
            for p in market.STOCK_DIR.glob("*_3y_history.csv")
            if not p.name.startswith("SPY_")
        )
    return [s for s in storage.load_configured_watchlist_symbols() if s != "SPY"]


def get_setup_report(
    symbol: str,
    as_of: str | None = None,
    model_version: str = MODEL_VERSION,
    include_details: bool = False,
    setup_type: str = "base",
    include_chart: bool = False,
) -> dict[str, Any]:
    valid_setup_type(setup_type)
    symbol, as_of = valid_symbol(symbol), valid_date(as_of)
    stock, benchmark = read_history(symbol, as_of), read_history("SPY", as_of)
    if stock.empty:
        raise ValueError(f"No cached bars for {symbol} at this cutoff")
    cutoff = latest_date(stock)
    model = load_model(model_version)
    if setup_type == "pullback":
        from trenddeck.setups.pullback import PullbackSeries, compact_context, fingerprints
        from trenddeck.setups.pullback import conditions_markdown as pullback_conditions

        result = PullbackSeries(stock, benchmark, model).report()
        response = {
            "schema_version": 2,
            "read_only": True,
            "setup_type": setup_type,
            "symbol": symbol,
            "as_of_session": result["as_of_session"],
            "score": None,
            "status": result["status"],
            "reasons": result["reasons"],
            "actionable": result["actionable"],
            "conditions_markdown": pullback_conditions(),
            "model_version": model.version,
            **fingerprints(model),
            "requested_as_of": as_of,
            "reference_session": latest_date(benchmark),
            "stale": bool(not benchmark.empty and latest_date(benchmark) > cutoff),
            "coverage": {"bars": len(stock), "benchmark_available": not benchmark.empty},
        }
        response["pullback"] = compact_context(result)
        if include_details:
            response["details"] = result
        return _with_chart(response, stock, result, setup_type, include_chart)
    result = score_setup(stock, benchmark, cutoff, model)
    response = {
        "schema_version": 2,
        "read_only": True,
        "symbol": symbol,
        "as_of_session": result["as_of_session"],
        "score": result["score"],
        "status": result["status"],
        "reasons": score_reasons(result, model),
        "conditions_markdown": conditions_markdown(model),
        "model_version": model.version,
        "config_hash": model.config_hash,
        "engine_hash": model.engine_hash,
        "requested_as_of": as_of,
        "reference_session": latest_date(benchmark),
        "stale": bool(not benchmark.empty and latest_date(benchmark) > cutoff),
        "coverage": {"bars": len(stock), "benchmark_available": not benchmark.empty},
    }
    if include_details:
        response["details"] = {
            **result,
            "model": {"parameters": dict(model.parameters), "weights": dict(model.weights)},
        }
    return _with_chart(response, stock, result, setup_type, include_chart)


def _with_chart(response, stock, result, setup_type, include_chart):
    if include_chart:
        from trenddeck.setups.chart import render_setup_chart

        response["chart"] = render_setup_chart(stock, response, result, setup_type)
    return response


def get_setup_rankings(
    as_of: str | None = None,
    symbols: list[str] | None = None,
    universe: str = "watchlist",
    actionable_only: bool = False,
    limit: int = 30,
    offset: int = 0,
    model_version: str = MODEL_VERSION,
    include_details: bool = False,
    setup_type: str = "base",
) -> dict[str, Any]:
    valid_setup_type(setup_type)
    as_of = valid_date(as_of)
    model = load_model(model_version)
    if not 1 <= limit <= 100 or not 0 <= offset <= 10000:
        raise ValueError("limit must be 1..100; offset 0..10000")
    selected = universe_symbols(symbols, universe)
    benchmark = read_history("SPY", as_of)
    if benchmark.empty:
        raise ValueError("SPY cache required to establish a common ranking session")
    session = latest_date(benchmark)
    rows, excluded = [], []
    for symbol in selected:
        try:
            frame = read_history(symbol, session)
            if frame.empty:
                excluded.append({"symbol": symbol, "reason": "missing"})
                continue
            if latest_date(frame) != session:
                excluded.append({"symbol": symbol, "reason": "stale", "latest_session": latest_date(frame)})
                continue
            # Missing middle sessions can distort contractions and rolling windows.
            expected = benchmark.Date.tail(253)
            expected = expected[expected >= frame.Date.iloc[0]]
            if not expected.isin(frame.Date).all():
                excluded.append({"symbol": symbol, "reason": "missing_sessions"})
                continue
            if setup_type == "pullback":
                from trenddeck.setups.pullback import PullbackSeries

                report = PullbackSeries(frame, benchmark, model).report()
            else:
                report = score_setup(frame, benchmark, model=model)
            if report["status"] == "insufficient_history":
                excluded.append({"symbol": symbol, "reason": report["status"], "bars": len(frame)})
                continue
            rows.append({"symbol": symbol, **report})
        except (ValueError, OSError, KeyError) as exc:
            excluded.append({"symbol": symbol, "reason": "invalid_data", "detail": str(exc)})
    if setup_type == "pullback":
        from trenddeck.setups.pullback import STATUS_ORDER

        rows.sort(key=lambda r: (STATUS_ORDER[r["status"]], r["symbol"]))
    else:
        rows.sort(key=lambda r: (-r["score"], r["symbol"]))
    total_scored = len(rows)
    for i, row in enumerate(rows):
        row["universe_rank"] = i + 1
        if setup_type == "base":
            row["percentile"] = round(100 * (len(rows) - 1 - i) / max(len(rows) - 1, 1), 2)
    if actionable_only:
        rows = [r for r in rows if r.get("actionable")]
    items = []
    for row in rows[offset : offset + limit]:
        item = {
            "symbol": row["symbol"],
            "rank": row["universe_rank"],
            "score": row["score"],
            "status": row["status"],
            "reasons": row["reasons"] if setup_type == "pullback" else score_reasons(row, model),
        }
        if setup_type == "pullback":
            from trenddeck.setups.pullback import compact_context

            item["actionable"] = row["actionable"]
            item["pullback"] = compact_context(row)
        if include_details:
            item["details"] = row
        items.append(item)
    response = {
        "schema_version": 2,
        "model_version": model.version,
        "config_hash": model.config_hash,
        "engine_hash": model.engine_hash,
        "read_only": True,
        "requested_as_of": as_of,
        "as_of_session": session,
        "universe_source": "explicit_symbols" if symbols else universe,
        "historical_universe": "current_membership_not_point_in_time",
        "coverage": {"requested": len(selected), "scored": total_scored, "excluded": excluded},
        "total": len(rows),
        "items": items,
        "conditions_markdown": conditions_markdown(model),
        "next_offset": offset + limit if offset + limit < len(rows) else None,
    }
    if include_details:
        response["details"] = {
            "universe": selected,
            "model": {"parameters": dict(model.parameters), "weights": dict(model.weights)},
        }
    if setup_type == "pullback":
        from trenddeck.setups.pullback import conditions_markdown as pullback_conditions
        from trenddeck.setups.pullback import fingerprints

        response.update(
            setup_type=setup_type,
            conditions_markdown=pullback_conditions(),
            ordering="status_then_symbol_not_quality_score",
            **fingerprints(model),
        )
    return response


def get_setup_backtest(
    start: str,
    end: str,
    symbols: list[str] | None = None,
    universe: str = "watchlist",
    horizon: int = 20,
    step: int = 20,
    top_n: int = 10,
    cost_bps: float = 10,
    holdout_start: str | None = None,
    model_version: str = MODEL_VERSION,
    include_event_records: bool = False,
) -> dict[str, Any]:
    from trenddeck.setups.research import evaluate_rankings

    start, end, holdout_start = valid_date(start), valid_date(end), valid_date(holdout_start)
    if not start or not end or start > end:
        raise ValueError("start and end must be valid ordered dates")
    if not 5 <= horizon <= 60 or not 5 <= step <= 60 or not 1 <= top_n <= 30:
        raise ValueError("horizon/step must be 5..60; top_n 1..30")
    if not 0 <= cost_bps <= 100 or not pd.notna(cost_bps):
        raise ValueError("cost_bps must be finite and 0..100 per side")
    if holdout_start and not start < holdout_start <= end:
        raise ValueError("holdout_start must be after start and no later than end")
    benchmark = read_history("SPY", end)
    if benchmark.empty:
        raise ValueError("SPY cache required")
    selected = universe_symbols(symbols, universe)
    frames, excluded = {}, []
    for symbol in selected:
        try:
            f = read_history(symbol, end)
            if f.empty:
                excluded.append({"symbol": symbol, "reason": "missing"})
            else:
                frames[symbol] = f
        except (ValueError, OSError, KeyError) as exc:
            excluded.append({"symbol": symbol, "reason": str(exc)})
    result = evaluate_rankings(
        frames,
        benchmark,
        start,
        end,
        horizon,
        step,
        top_n,
        cost_bps,
        holdout_start,
        load_model(model_version),
    )
    result["coverage"]["load_excluded"] = excluded
    result["universe"] = selected
    event_records = result["pivot_events"]["records"]
    result["pivot_events"]["record_count"] = len(event_records)
    result["pivot_events"]["records_included"] = include_event_records
    if not include_event_records:
        del result["pivot_events"]["records"]
    return result
