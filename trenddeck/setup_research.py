"""Chronological rank evaluation with next-session execution and date-matched controls."""

from __future__ import annotations

import hashlib
from typing import Any

import numpy as np
import pandas as pd

from trenddeck.setup_events import pivot_outcome, summarize_events
from trenddeck.setup_model import SetupModel, load_model
from trenddeck.setups import MIN_BARS, SetupSeries, clean_history


def simulate_pivot_trade(
    series: SetupSeries, report: dict[str, Any], horizon: int = 20, wait_bars: int = 10, cost_bps: float = 10
) -> dict[str, Any]:
    """Freeze today's pivot, wait for a close-confirmed break, enter next open.

    Fixed 2R target and structural stop; stop wins an ambiguous same-day bar.
    This is one testable execution policy, not a claim about the authors' actual trades.
    """
    g = report.get("geometry", {})
    p = series.p
    if (
        not report.get("eligible")
        or g.get("pattern_strength", 0) < p["structure_min_strength"]
        or g.get("pause_width", 1) > p["max_pause_width"]
        or report["status"] in {"outside_stage2", "damaged", "invalidated", "extended", "no_base"}
    ):
        return {"status": "not_setup"}
    pivot, stop = g["pivot"], report["invalidation_level"]
    i = int(np.searchsorted(series.dates, np.datetime64(report["as_of_session"])))
    trigger = i if report["breakout"]["confirmed"] else None
    for j in range(i + 1, min(len(series.c), i + wait_bars + 1)) if trigger is None else []:
        if series.c[j] < stop:
            return {"status": "invalidated_before_entry", "resolved_session": series.date(j)}
        ratio = series.v[j] / max(series.v[j - 50 : j].mean(), 1)
        clv = (series.c[j] - series.low[j]) / max(series.h[j] - series.low[j], 1e-8)
        if (
            series.c[j] > pivot >= series.c[j - 1]
            and ratio >= p["breakout_volume_ratio"]
            and clv >= p["breakout_close_location"]
        ):
            trigger = j
            break
    if trigger is None:
        return {
            "status": "expired" if i + wait_bars < len(series.c) else "censored",
            "resolved_session": series.date(min(i + wait_bars, len(series.c) - 1)),
        }
    first = trigger + 1
    if first >= len(series.c):
        return {"status": "censored"}
    cost = cost_bps / 10000
    entry = series.o[first] * (1 + cost)
    risk = entry - stop
    if series.o[first] > pivot * (1 + p["max_extension"]):
        return {"status": "gap_too_extended", "resolved_session": series.date(first)}
    if not 0 < risk / entry <= p["max_risk"]:
        return {"status": "entry_risk_rejected", "resolved_session": series.date(first)}
    target = entry + 2 * risk
    last = min(len(series.c) - 1, first + horizon - 1)
    exit_price, reason = None, None
    for j in range(first, last + 1):
        if series.o[j] <= stop:
            exit_price, reason = series.o[j], "gap_stop"
        elif series.o[j] >= target:
            exit_price, reason = series.o[j], "gap_target"
        elif series.low[j] <= stop:
            exit_price, reason = stop, "stop"
        elif series.h[j] >= target:
            exit_price, reason = target, "target"
        if reason:
            last = j
            break
    if exit_price is None:
        if first + horizon > len(series.c):
            return {"status": "censored"}
        exit_price, reason = series.c[last], "time_exit"
    if not series.benchmark.empty:
        expected = series.benchmark.loc[
            (series.benchmark.Date > pd.Timestamp(series.date(i)))
            & (series.benchmark.Date <= pd.Timestamp(series.date(last))),
            "Date",
        ]
        if not expected.isin(series.frame.Date).all():
            return {"status": "missing_sessions", "resolved_session": series.date(last)}
    net_exit = exit_price * (1 - cost)
    return {
        "status": "filled",
        "trigger_session": series.date(trigger),
        "entry_session": series.date(first),
        "exit_session": series.date(last),
        "entry": float(entry),
        "exit": float(net_exit),
        "stop": stop,
        "target": float(target),
        "exit_reason": reason,
        "net_return": float(net_exit / entry - 1),
        "net_r": float((net_exit - entry) / risk),
    }


def summarize_triggers(records: list[dict[str, Any]]) -> dict[str, Any]:
    statuses: dict[str, int] = {}
    for r in records:
        statuses[r["status"]] = statuses.get(r["status"], 0) + 1
    filled = [r for r in records if r["status"] == "filled"]
    gains = sum(max(0, r["net_return"]) for r in filled)
    losses = sum(min(0, r["net_return"]) for r in filled)
    return {
        "statuses": statuses,
        "trades": len(filled),
        "win_fraction": float(np.mean([r["net_return"] > 0 for r in filled])) if filled else None,
        "mean_net_return": float(np.mean([r["net_return"] for r in filled])) if filled else None,
        "mean_net_r": float(np.mean([r["net_r"] for r in filled])) if filled else None,
        "profit_factor": float(gains / -losses) if losses else None,
    }


def forward_outcome(
    series: SetupSeries, entry_date: str, exit_date: str, cost_bps: float
) -> dict[str, Any] | None:
    dates = series.dates
    first = int(np.searchsorted(dates, np.datetime64(entry_date)))
    last = int(np.searchsorted(dates, np.datetime64(exit_date)))
    if first >= len(dates) or last >= len(dates):
        return None
    if series.date(first) != entry_date or series.date(last) != exit_date:
        return None
    cost = cost_bps / 10000
    entry = series.o[first] * (1 + cost)
    return {
        "net_return": float(series.c[last] * (1 - cost) / entry - 1),
        "mae": float(series.low[first : last + 1].min() / entry - 1),
        "mfe": float(series.h[first : last + 1].max() / entry - 1),
    }


def summarize(cohorts: list[dict[str, Any]]) -> dict[str, Any]:
    keys = ["top_setup", "all_eligible", "top_rs", "trend_only", "actionable", "bottom_setup"]
    result = {}
    for key in keys:
        complete = [r for r in cohorts if r[key]["net_return"] is not None]
        returns = np.array([r[key]["net_return"] for r in complete])
        result[key] = {
            "cohorts": len(complete),
            "selected_observations": sum(r[key]["selected"] for r in cohorts),
            "missing_outcomes": sum(r[key]["missing"] for r in cohorts),
            "mean_net_return": float(returns.mean()) if len(returns) else None,
            "median_net_return": float(np.median(returns)) if len(returns) else None,
            "positive_cohort_fraction": float(np.mean(returns > 0)) if len(returns) else None,
            "mean_excess_spy": float(np.mean([r[key]["net_return"] - r["spy_return"] for r in complete]))
            if complete
            else None,
            "worst_cohort_return": float(returns.min()) if len(returns) else None,
        }
    paired = [
        r
        for r in cohorts
        if r["top_setup"]["net_return"] is not None and r["all_eligible"]["net_return"] is not None
    ]
    differences = np.array([r["top_setup"]["net_return"] - r["all_eligible"]["net_return"] for r in paired])
    result["top_minus_eligible"] = {
        "cohorts": len(paired),
        "mean": float(differences.mean()) if len(paired) else None,
    }
    # Descriptive only: even non-overlapping cohorts share names/regimes, so no IID confidence claims.
    return result


def evaluate_rankings(
    frames: dict[str, pd.DataFrame],
    benchmark: pd.DataFrame,
    start: str,
    end: str,
    horizon: int = 20,
    step: int = 20,
    top_n: int = 10,
    cost_bps: float = 10,
    holdout_start: str | None = None,
    model: SetupModel | None = None,
) -> dict[str, Any]:
    model = model or load_model()
    benchmark = clean_history(benchmark)
    benchmark = benchmark.loc[benchmark.Date <= pd.Timestamp(end)].reset_index(drop=True)
    reference = SetupSeries(benchmark, model=model)
    series, invalid = {}, []
    for symbol, frame in frames.items():
        try:
            series[symbol] = SetupSeries(frame.loc[frame.Date <= pd.Timestamp(end)], benchmark, model)
        except ValueError as exc:
            invalid.append({"symbol": symbol, "reason": str(exc)})
    indices = [
        i for i, d in enumerate(reference.dates) if str(d)[:10] >= start and i + 1 < len(reference.dates)
    ]
    indices = indices[::step]
    if len(indices) * len(series) > 20000:
        raise ValueError(
            "Research request exceeds 20,000 symbol-sessions; narrow dates/universe or increase step"
        )
    cohorts, failures = [], {}
    triggers: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    occupied_until: dict[tuple[str, str], str] = {}
    censored = 0
    for bi in indices:
        if bi + horizon >= len(reference.dates):
            censored += 1
            continue
        date, entry, exit_date = reference.date(bi), reference.date(bi + 1), reference.date(bi + horizon)
        # Purge outcomes that straddle the temporal split from development summaries.
        phase = "all"
        if holdout_start:
            phase = (
                "holdout"
                if date >= holdout_start
                else ("development" if exit_date < holdout_start else "purged")
            )
        scores = []
        for symbol, s in series.items():
            i = int(np.searchsorted(s.dates, np.datetime64(date)))
            if i >= len(s.dates) or s.date(i) != date or i + 1 < MIN_BARS:
                continue
            expected = reference.dates[max(0, bi - 252) : bi + 1]
            if not np.isin(expected, s.dates[max(0, i - 252) : i + 1]).all():
                continue
            r = s.score(i)
            # Selection is complete before looking at ANY forward observations.
            if r.get("eligible"):
                scores.append({"symbol": symbol, **r})
        scores.sort(key=lambda r: (-r["score"], r["symbol"]))
        groups = {
            "top_setup": scores[:top_n],
            "all_eligible": scores,
            "top_rs": sorted(scores, key=lambda r: (-(r.get("relative_return_63") or 0), r["symbol"]))[
                :top_n
            ],
            "trend_only": [r for r in scores if r.get("strict_price_template")],
            "actionable": [r for r in scores if r.get("actionable")][:top_n],
            "bottom_setup": scores[-top_n:] if len(scores) >= 2 * top_n else [],
        }
        spy = forward_outcome(reference, entry, exit_date, cost_bps)
        cohort = {
            "session": date,
            "entry_session": entry,
            "exit_session": exit_date,
            "phase": phase,
            "eligible": len(scores),
            "spy_return": spy["net_return"],
        }
        event_cache = {}
        for key, group in groups.items():
            outcomes, missing = [], []
            for r in group:
                symbol = r["symbol"]
                if symbol not in event_cache:
                    label = pivot_outcome(series[symbol], r, horizon)
                    event_phase = phase
                    if holdout_start and date < holdout_start <= label.get("label_end_session", date):
                        event_phase = "purged"
                    si = int(np.searchsorted(series[symbol].dates, np.datetime64(date)))
                    earliest = series[symbol].date(max(0, si - 125))
                    prior_failures = (
                        sum(e["failure_session"] >= earliest for e in r["pivot_failure_events"])
                        if model.parameters["damage_filters_enabled"]
                        else None
                    )
                    event_cache[symbol] = {
                        "symbol": symbol,
                        "signal_session": date,
                        "score": r["score"],
                        "signal_status": r["status"],
                        "pattern": r["pattern"],
                        "prior_failures_126": prior_failures,
                        "phase": event_phase,
                        **label,
                    }
                events.append({"cohort": key, **event_cache[symbol]})
                outcome = forward_outcome(series[r["symbol"]], entry, exit_date, cost_bps)
                # Reject incomplete paths as well as missing entry/exit sessions.
                expected = reference.dates[bi + 1 : bi + horizon + 1]
                if not np.isin(expected, series[r["symbol"]].dates).all():
                    outcome = None
                if outcome is None:
                    missing.append(r["symbol"])
                    failures[r["symbol"]] = failures.get(r["symbol"], 0) + 1
                else:
                    outcomes.append(outcome)
                if key in {"top_setup", "top_rs"}:
                    slot = (key, r["symbol"])
                    if occupied_until.get(slot, "") >= date:
                        continue
                    trade = simulate_pivot_trade(series[r["symbol"]], r, horizon, 10, cost_bps)
                    if trade["status"] == "filled":
                        occupied_until[slot] = trade["exit_session"]
                    elif trade["status"] != "not_setup":
                        # Do not open overlapping pending orders for the same stock.
                        si = int(np.searchsorted(series[r["symbol"]].dates, np.datetime64(date)))
                        occupied_until[slot] = trade.get(
                            "resolved_session",
                            series[r["symbol"]].date(min(si + 10, len(series[r["symbol"]].c) - 1)),
                        )
                    trade_phase = phase
                    if (
                        holdout_start
                        and date < holdout_start
                        and trade.get("exit_session", trade.get("resolved_session", "")) >= holdout_start
                    ):
                        trade_phase = "purged"
                    triggers.append(
                        {
                            "cohort": key,
                            "signal_session": date,
                            "symbol": r["symbol"],
                            "score": r["score"],
                            "pattern": r["pattern"],
                            "phase": trade_phase,
                            **trade,
                        }
                    )
            cohort[key] = {
                "selected": len(group),
                "missing": len(missing),
                "symbols": [r["symbol"] for r in group],
                "net_return": float(np.mean([o["net_return"] for o in outcomes]))
                if outcomes and not missing
                else None,
                "mean_mae": float(np.mean([o["mae"] for o in outcomes]))
                if outcomes and not missing
                else None,
                "mean_mfe": float(np.mean([o["mfe"] for o in outcomes]))
                if outcomes and not missing
                else None,
            }
        cohorts.append(cohort)
    summaries = {"all": summarize([r for r in cohorts if r["phase"] != "purged"])}
    if holdout_start:
        summaries.update(
            {p: summarize([r for r in cohorts if r["phase"] == p]) for p in ["development", "holdout"]}
        )
    fingerprint = hashlib.sha256()
    for symbol, s in sorted({**series, "__SPY__": reference}.items()):
        fingerprint.update(symbol.encode())
        fingerprint.update(pd.util.hash_pandas_object(s.frame, index=False).values.tobytes())
    return {
        "schema_version": 1,
        "model_version": model.version,
        "config_hash": model.config_hash,
        "engine_hash": model.engine_hash,
        "data_fingerprint": fingerprint.hexdigest()[:16],
        "read_only": True,
        "parameters": {
            "start": start,
            "end": end,
            "horizon": horizon,
            "step": step,
            "top_n": top_n,
            "cost_bps_per_side": cost_bps,
            "holdout_start": holdout_start,
        },
        "coverage": {
            "loaded_symbols": len(series),
            "invalid": invalid,
            "censored_cohorts": censored,
            "missing_outcome_counts": failures,
        },
        "method": "Close-time ranking; equal-weight next-session open to horizon-session close; fixed rules",
        "limitations": [
            "Current cached universe has survivorship/selection bias; no delisted universe",
            "Retrospective time split, not an untouched prospective validation",
            "Ranking study and separate fixed-pivot trade study; neither is a capital-constrained portfolio",
            "Cohorts overlap when step < horizon; observations are not independent",
            "Missing selected outcomes invalidate that cohort, never silently remove losers",
            "Adjusted daily prices; no earnings calendar, intraday fills, or borrow assumptions",
        ],
        "summary": summaries,
        "cohorts": cohorts,
        "pivot_events": {
            "label_version": "pivot-events-v1",
            "primary_question": "Confirmed breakout before an 8% fall from observation close; then post-breakout failure risk",
            "horizon": horizon,
            "rules": {
                "pre_breakout_reference": "Observation close; diagnostic reference, not a claimed fill",
                "post_breakout_reference": "Next session open after close/volume confirmation",
                "pivot": "Frozen at observation; never redrawn using future prices",
                "same_day_order": "Intraday stop precedes close-confirmed breakout",
                "price_acceptance": "Rolling three sessions starting with observation day; two closes above pivot including current close",
                "usable_subset": "Strict diagnostic subset: no prior structure touch by that confirmation, no extended or below-structure entry; not a buy recommendation",
                "failure": "8% stop and frozen structure stop are separate; close below pivot alone is a diagnostic",
                "post_breakout_window": "Another horizon sessions; incomplete paths censored",
                "sampling": "Observation cohorts; repeated symbols/setups are not independent events",
                "entry_gaps": "Post-breakout gaps beyond 5% flagged separately, not idealized pivot fills",
            },
            "summary": {
                phase_name: {
                    group: summarize_events(
                        [
                            r
                            for r in events
                            if r["cohort"] == group
                            and r["phase"] != "purged"
                            and (phase_name == "all" or r["phase"] == phase_name)
                        ]
                    )
                    for group in [
                        "top_setup",
                        "all_eligible",
                        "top_rs",
                        "trend_only",
                        "actionable",
                        "bottom_setup",
                    ]
                }
                for phase_name in (["all", "development", "holdout"] if holdout_start else ["all"])
            },
            "failure_history_groups": {
                phase_name: {
                    bucket: summarize_events(
                        [
                            r
                            for r in events
                            if r["cohort"] == "all_eligible"
                            and r["phase"] != "purged"
                            and (phase_name == "all" or r["phase"] == phase_name)
                            and (
                                "unknown"
                                if r["prior_failures_126"] is None
                                else str(min(r["prior_failures_126"], 2))
                            )
                            == bucket
                        ]
                    )
                    for bucket in ["0", "1", "2", "unknown"]
                }
                for phase_name in (["all", "development", "holdout"] if holdout_start else ["all"])
            },
            "records": events,
        },
        "pivot_trades": {
            "policy": "Frozen pivot; wait 10 bars; volume/close confirmation; next open within 5%; structural stop; 2R target; stop first on ambiguous bars",
            "same_symbol_overlap": "No overlapping positions/pending orders within each comparison cohort",
            "summary": {
                phase_name: {
                    group: summarize_triggers(
                        [
                            r
                            for r in triggers
                            if r["cohort"] == group
                            and r["phase"] != "purged"
                            and (phase_name == "all" or r["phase"] == phase_name)
                        ]
                    )
                    for group in ["top_setup", "top_rs"]
                }
                for phase_name in (["all", "development", "holdout"] if holdout_start else ["all"])
            },
            "records": triggers,
        },
    }
