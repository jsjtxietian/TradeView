"""Forward labels for entry-location quality, separate from trade exit policies.

Freeze the observed pivot. Ask which happens first: a close/volume confirmed
breakout or an 8% adverse move from the observation close. This is a forecast
label, not a hypothetical fill at the pivot. Post-breakout risk uses next open.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np


def breach_episodes(series, start: int, end: int, level: float) -> list[dict[str, Any]]:
    """Describe undercuts without erasing actual stop touches on later recovery.

    An episode ends at the first close back at/above the frozen level. Unresolved
    tail episodes stay open. Intraday recoveries have zero below-level closes.
    """
    episodes = []
    j = start
    while j <= end:
        if series.low[j] >= level:
            j += 1
            continue
        first = j
        minimum = float(series.low[j])
        below_closes = 0
        recovered = None
        while j <= end:
            minimum = min(minimum, float(series.low[j]))
            if series.c[j] >= level:
                recovered = j
                break
            below_closes += 1
            j += 1
        episodes.append(
            {
                "start": series.date(first),
                "minimum": minimum,
                "reclaimed_at": series.date(recovered) if recovered is not None else None,
                "sessions_to_reclaim": recovered - first if recovered is not None else None,
                "closes_below": below_closes,
                "persistent_3_closes": below_closes >= 3,
            }
        )
        j += 1
    return episodes


def pivot_outcome(series, report: dict[str, Any], horizon: int = 20) -> dict[str, Any]:
    g, p = report.get("geometry", {}), series.p
    if (
        not report.get("eligible")
        or g.get("pattern_strength", 0) < p["structure_min_strength"]
        or g.get("pause_width", 1) > p["max_pause_width"]
        or report["status"] in {"damaged", "invalidated", "extended", "no_base", "outside_stage2"}
    ):
        return {"status": "not_setup"}
    confirmed_now = report["breakout"]["confirmed"]
    if report["close"] > g["pivot"] and not confirmed_now:
        return {"status": "already_above_pivot"}
    i = int(np.searchsorted(series.dates, np.datetime64(report["as_of_session"])))
    end = i + horizon
    if end >= len(series.c):
        return {"status": "censored"}
    expected = (
        series.benchmark.loc[
            (series.benchmark.Date > series.frame.Date.iloc[i])
            & (series.benchmark.Date <= series.frame.Date.iloc[end]),
            "Date",
        ]
        if not series.benchmark.empty
        else None
    )
    if expected is not None and (len(expected) != horizon or not expected.isin(series.frame.Date).all()):
        return {"status": "missing_sessions"}
    pivot, reference = g["pivot"], float(series.c[i])
    stop8, structural_stop = reference * 0.92, report["invalidation_level"]
    confirmed = i if confirmed_now else None
    first_stop8, first_structure, first_touch, first_close, accepted = None, None, None, None, None
    if confirmed_now:
        first_close, first_touch = i, i
    for j in range(i + 1, end + 1):
        if first_stop8 is None and series.low[j] <= stop8:
            first_stop8 = j
        if first_structure is None and series.low[j] <= structural_stop:
            first_structure = j
        if first_touch is None and series.h[j] > pivot:
            first_touch = j
        if first_close is None and series.c[j] > pivot >= series.c[j - 1]:
            first_close = j
        # A second independent definition, not retroactively assigned to the first
        # crossing day: at least two of three closes above, including today's close.
        if (
            accepted is None
            and j >= i + 2
            and series.c[j] > pivot
            and np.sum(series.c[j - 2 : j + 1] > pivot) >= 2
        ):
            accepted = j
        vr = series.v[j] / max(series.v[j - 50 : j].mean(), 1)
        clv = (series.c[j] - series.low[j]) / max(series.h[j] - series.low[j], 1e-8)
        if (
            confirmed is None
            and series.c[j] > pivot >= series.c[j - 1]
            and vr >= p["breakout_volume_ratio"]
            and clv >= p["breakout_close_location"]
        ):
            confirmed = j

    def race(stop, breakout=confirmed):
        # Low occurred by the close; a close-confirmed break on the same day loses.
        if breakout == i:
            return "breakout_at_observation"
        if stop is not None and (breakout is None or stop <= breakout):
            return "stop_first"
        return "breakout_first" if breakout is not None else "neither"

    def date(index):
        return series.date(index) if index is not None else None

    result = {
        "status": "evaluated",
        "reference_close": reference,
        "frozen_pivot": pivot,
        "stop_8pct": stop8,
        "structural_stop": structural_stop,
        "race_8pct": race(first_stop8),
        "race_structure": race(first_structure),
        "race_price_acceptance_8pct": race(first_stop8, accepted),
        "race_price_cross_8pct": race(first_stop8, first_close),
        "race_price_cross_structure": race(first_structure, first_close),
        "race_price_acceptance_structure": race(first_structure, accepted),
        "first_intraday_touch": date(first_touch),
        "first_close_cross": date(first_close),
        "price_acceptance_known_at": date(accepted),
        "confirmed_breakout": date(confirmed),
        "first_8pct_stop": date(first_stop8),
        "first_structure_stop": date(first_structure),
        "bars_to_breakout": confirmed - i if confirmed is not None else None,
        "pre_breakout_window_end": series.date(end),
        "label_end_session": series.date(end),
        "post_breakout": {"status": "not_triggered"},
    }
    for key, crossing in [
        ("post_breakout", confirmed),
        ("price_cross_post_breakout", first_close),
        ("price_acceptance_post_breakout", accepted),
    ]:
        if crossing is None or race(first_stop8, crossing) == "stop_first":
            result[key] = {"status": "not_triggered"}
            continue
        outcome, label_end = post_breakout_outcome(
            series, crossing, horizon, pivot, structural_stop, p["max_extension"]
        )
        if outcome["status"] == "evaluated":
            outcome["structure_touched_before_confirmation"] = race(first_structure, crossing) == "stop_first"
        result[key] = outcome
        if label_end:
            result["label_end_session"] = max(result["label_end_session"], label_end)
    return result


def post_breakout_outcome(
    series, confirmed: int, horizon: int, pivot: float, structural_stop: float, max_extension: float
) -> tuple[dict[str, Any], str | None]:
    first, last = confirmed + 1, confirmed + horizon
    if last >= len(series.c):
        return {"status": "censored"}, None
    if not series.benchmark.empty:
        expected = series.benchmark.loc[
            (series.benchmark.Date > series.frame.Date.iloc[confirmed])
            & (series.benchmark.Date <= series.frame.Date.iloc[last]),
            "Date",
        ]
        if len(expected) != horizon or not expected.isin(series.frame.Date).all():
            return {"status": "missing_sessions"}, None
    entry = float(series.o[first])
    structure_hit = np.flatnonzero(series.low[first : last + 1] <= structural_stop)
    stop_hit = np.flatnonzero(series.low[first : last + 1] <= entry * 0.92)
    back_under = np.flatnonzero(series.c[first : last + 1] < pivot)
    outcome = {
        "status": "evaluated",
        "entry_session": series.date(first),
        "window_end_session": series.date(last),
        "entry_open": entry,
        "entry_extended": bool(entry > pivot * (1 + max_extension)),
        "entry_below_structure": bool(entry <= structural_stop),
        "stop_8pct_from_entry": entry * 0.92,
        "hit_8pct_stop": bool(len(stop_hit)),
        "hit_structure_stop": bool(len(structure_hit)),
        "closed_back_below_pivot": bool(len(back_under)),
        "first_8pct_stop": series.date(first + int(stop_hit[0])) if len(stop_hit) else None,
        "first_structure_stop": series.date(first + int(structure_hit[0])) if len(structure_hit) else None,
        "mae": float(series.low[first : last + 1].min() / entry - 1),
        "mfe": float(series.h[first : last + 1].max() / entry - 1),
        "pivot_breaches": breach_episodes(series, first, last, pivot),
        "structure_breaches": breach_episodes(series, first, last, structural_stop),
    }
    return outcome, series.date(last)


def summarize_events(records: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = dict(Counter(r["status"] for r in records))
    evaluated = [r for r in records if r["status"] == "evaluated"]
    prospective = [r for r in evaluated if r["race_8pct"] != "breakout_at_observation"]
    races = dict(Counter(r["race_8pct"] for r in prospective))
    structural = dict(Counter(r["race_structure"] for r in prospective))
    accepted = dict(Counter(r["race_price_acceptance_8pct"] for r in prospective))
    crossed = dict(Counter(r["race_price_cross_8pct"] for r in prospective))
    post = [r["post_breakout"] for r in evaluated if r["post_breakout"]["status"] == "evaluated"]
    usable = [
        r["post_breakout"]
        for r in evaluated
        if r["post_breakout"]["status"] == "evaluated"
        and not r["post_breakout"]["structure_touched_before_confirmation"]
        and not r["post_breakout"]["entry_extended"]
        and not r["post_breakout"]["entry_below_structure"]
    ]

    def rate(count, denominator):
        return count / denominator if denominator else None

    variants = {}
    for key in ["price_cross_post_breakout", "price_acceptance_post_breakout"]:
        complete = [r[key] for r in evaluated if r[key]["status"] == "evaluated"]
        usable_price = [
            p
            for p in complete
            if not p["entry_extended"]
            and not p["entry_below_structure"]
            and not p["structure_touched_before_confirmation"]
        ]
        variants[key] = {
            "complete": len(complete),
            "censored": sum(r[key]["status"] == "censored" for r in evaluated),
            "hit_8pct_rate": rate(sum(p["hit_8pct_stop"] for p in complete), len(complete)),
            "hit_structure_rate": rate(sum(p["hit_structure_stop"] for p in complete), len(complete)),
            "usable_entries": len(usable_price),
            "usable_hit_8pct_rate": rate(sum(p["hit_8pct_stop"] for p in usable_price), len(usable_price)),
        }
    return {
        "price_confirmation_variants": variants,
        "first_event_price_cross_8pct": crossed,
        "price_cross_before_8pct_rate": rate(crossed.get("breakout_first", 0), len(prospective)),
        "observations": len(records),
        "statuses": statuses,
        "pre_breakout_setups": len(prospective),
        "already_confirmed": len(evaluated) - len(prospective),
        "first_event_8pct": races,
        "first_event_structure": structural,
        "first_event_price_acceptance_8pct": accepted,
        "price_acceptance_before_8pct_rate": rate(accepted.get("breakout_first", 0), len(prospective)),
        "breakout_before_8pct_rate": rate(races.get("breakout_first", 0), len(prospective)),
        "stop8_before_breakout_rate": rate(races.get("stop_first", 0), len(prospective)),
        "neither_rate": rate(races.get("neither", 0), len(prospective)),
        "breakout_before_structure_rate": rate(structural.get("breakout_first", 0), len(prospective)),
        "post_breakout_complete": len(post),
        "post_breakout_censored": sum(r["post_breakout"]["status"] == "censored" for r in evaluated),
        "post_breakout_8pct_failure_rate": rate(sum(r["hit_8pct_stop"] for r in post), len(post)),
        "post_breakout_structure_failure_rate": rate(sum(r["hit_structure_stop"] for r in post), len(post)),
        "post_breakout_pivot_reentry_rate": rate(sum(r["closed_back_below_pivot"] for r in post), len(post)),
        "post_breakout_extended_entries": sum(r["entry_extended"] for r in post),
        "post_breakout_quick_pivot_reclaims": sum(
            any(
                e["sessions_to_reclaim"] is not None and e["sessions_to_reclaim"] <= 1
                for e in r["pivot_breaches"]
            )
            for r in post
        ),
        "post_breakout_persistent_structure_failures": sum(
            any(e["persistent_3_closes"] for e in r["structure_breaches"]) for r in post
        ),
        "post_breakout_usable_entries": len(usable),
        "usable_entry_8pct_failure_rate": rate(sum(r["hit_8pct_stop"] for r in usable), len(usable)),
        "usable_entry_structure_failure_rate": rate(
            sum(r["hit_structure_stop"] for r in usable), len(usable)
        ),
    }
