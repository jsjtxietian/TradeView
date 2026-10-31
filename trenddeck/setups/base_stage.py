"""Causal, descriptive base maturity; never a score or permission to buy.

The 20% separation and moving-average repair rules are engineering conventions,
not an exact reproduction of either author's discretionary base count. Catalog
geometry must be frozen when known; breakout_index is its acceptance date.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np


def stage_context(
    records: Sequence[Mapping[str, Any]],
    close: np.ndarray,
    ma200: np.ndarray,
    dates: np.ndarray,
    end: int,
    p: Mapping[str, float],
) -> dict[str, Any]:
    """Describe independent observed bases using rows strictly before ``end``.

    Missing cycle history is explicitly censored. An observed lower bound of
    four independent bases can establish maturity despite censoring, but a small
    observed count cannot establish an early cycle. Failed breaks never erase
    history. Inputs are not mutated and future confirmations are ignored.
    """
    n = min(max(0, int(end)), len(close), len(ma200), len(dates))
    c = np.asarray(close[:n], dtype=float)
    ma = np.asarray(ma200[:n], dtype=float)
    advance = max(0.0, float(p.get("base_count_min_advance", 0.20)))
    late = max(3, int(p.get("base_late_ordinal", 4)))
    reset_days = max(2, int(p.get("base_reset_below_ma_days", 20)))

    def date(i: int | None) -> str | None:
        return str(dates[i])[:10] if i is not None and 0 <= i < n else None

    # A catalog may contain several snapshots of the same frozen base.
    known: dict[str, dict[str, Any]] = {}
    for source in records:
        start, at = int(source["start_index"]), int(source["known_index"])
        if not 0 <= start <= at < n:
            continue
        upper, lower = float(source["upper"]), float(source["lower"])
        if not np.isfinite([upper, lower]).all() or not 0 < lower <= upper:
            continue
        raw_break = source.get("breakout_index")
        accepted = int(raw_break) if raw_break is not None else None
        if accepted is not None and not at <= accepted < n:
            accepted = None
        item = {
            "base_id": str(source["base_id"]),
            "start_index": start,
            "known_index": at,
            "breakout_index": accepted,
            "upper": upper,
            "lower": lower,
        }
        existing = known.get(item["base_id"])
        if existing is None:
            known[item["base_id"]] = item
        elif accepted is not None:
            prior = existing["breakout_index"]
            existing["breakout_index"] = min(prior, accepted) if prior is not None else accepted
    ordered = sorted(known.values(), key=lambda x: (x["start_index"], x["known_index"], x["base_id"]))

    # Only a completed below-MA regime followed by independently formed repair
    # can anchor a cycle. A rolling window beginning in an uptrend cannot do so.
    below_runs: list[tuple[int, int]] = []
    run_start: int | None = None
    for i in range(n + 1):
        below = i < n and np.isfinite(ma[i]) and np.isfinite(c[i]) and c[i] < ma[i]
        if below and run_start is None:
            run_start = i
        if not below and run_start is not None:
            if i - run_start >= reset_days and ma[i - 1] < ma[run_start]:
                below_runs.append((run_start, i - 1))
            run_start = None
    anchors = []
    for run_start, run_end in below_runs:
        for item in sorted(
            ordered,
            key=lambda x: (x["breakout_index"] if x["breakout_index"] is not None else n, x["known_index"]),
        ):
            b = item["breakout_index"]
            if b is None or item["start_index"] <= run_end or b < reset_days:
                continue
            if np.isfinite(ma[b - reset_days : b + 1]).all() and c[b] > ma[b] and ma[b] >= ma[b - reset_days]:
                anchors.append(
                    {
                        "base_id": item["base_id"],
                        "start_index": item["start_index"],
                        "confirmed_index": b,
                        "decline_start_index": run_start,
                        "decline_end_index": run_end,
                    }
                )
                break
    anchor = max(anchors, key=lambda x: x["confirmed_index"]) if anchors else None
    active = [x for x in ordered if anchor is None or x["start_index"] >= anchor["start_index"]]

    groups: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    for item in active:
        if not groups:
            groups.append({"root": item, "members": [item], "breakout_index": item["breakout_index"]})
            continue
        previous = groups[-1]
        root, b = previous["root"], previous["breakout_index"]
        # All interval-overlapping structures remain one episode, even if an
        # inner handle happens to be accepted before its outer base.
        last_known = max(x["known_index"] for x in previous["members"])
        if (
            b is None
            and item["start_index"] > last_known
            and item["breakout_index"] is not None
            and item["upper"] >= root["upper"]
        ):
            # An obsolete, never-accepted catalog proposal must not swallow all
            # later genuine advances. Replace it within this same episode;
            # count only the later base that actually earned acceptance.
            relations.append(
                {
                    "base_id": root["base_id"],
                    "parent_base_id": item["base_id"],
                    "relation": "unaccepted_predecessor_replaced",
                }
            )
            previous["root"] = item
            previous["members"].append(item)
            previous["breakout_index"] = item["breakout_index"]
            continue
        separate_time = b is not None and item["start_index"] > max(b, last_known)
        advance_seen = False
        if separate_time:
            segment = c[b : item["start_index"] + 1]
            finite = segment[np.isfinite(segment)]
            advance_seen = bool(len(finite) and finite.max() >= root["upper"] * (1 + advance))
        separate_price = item["upper"] >= root["upper"] * (1 + advance)
        if separate_time and advance_seen and separate_price:
            groups.append({"root": item, "members": [item], "breakout_index": item["breakout_index"]})
        else:
            if separate_time and item["upper"] > root["upper"] and item["lower"] >= root["lower"]:
                reason = "base_on_base_candidate"
            elif not separate_time:
                reason = "overlapping_or_nested"
            elif item["upper"] <= root["upper"]:
                reason = "same_roof_or_lower_retry"
            else:
                reason = "insufficient_independent_advance"
            relations.append(
                {"base_id": item["base_id"], "parent_base_id": root["base_id"], "relation": reason}
            )
            previous["members"].append(item)
            # Acceptance of a lower internal trigger cannot complete its outer
            # base. Only the frozen root's own accepted breakout counts.

    completed = sum(g["breakout_index"] is not None for g in groups)
    # The current group's ordinal is a lower bound when history is censored.
    ordinal = len(groups) if groups else None
    resets: list[dict[str, Any]] = []
    for g in groups:
        b, root = g["breakout_index"], g["root"]
        if b is None:
            continue
        broken = np.flatnonzero(c[b + 1 :] < root["lower"])
        if len(broken):
            at = int(b + 1 + broken[0])
            resets.append(
                {
                    "reason": "accepted_base_floor_lost",
                    "base_id": root["base_id"],
                    "observed_index": at,
                    "observed_at": date(at),
                }
            )
    for start, stop in below_runs:
        if anchor is None or stop > anchor["confirmed_index"]:
            resets.append(
                {
                    "reason": "sustained_declining_ma200_regime",
                    "base_id": None,
                    "observed_index": stop,
                    "observed_at": date(stop),
                }
            )
    pending = bool(resets)
    left_censored = anchor is None
    if not ordinal or pending:
        maturity = "unknown"
    elif ordinal >= late:
        maturity = "late"
    elif ordinal >= 3:
        maturity = "mature"
    elif not left_censored:
        maturity = "early"
    else:
        maturity = "unknown"
    events = [
        {
            "base_id": g["root"]["base_id"],
            "member_base_ids": [x["base_id"] for x in g["members"]],
            "start_index": g["root"]["start_index"],
            "known_index": g["root"]["known_index"],
            "breakout_index": g["breakout_index"],
            "breakout_confirmed_at": date(g["breakout_index"]),
        }
        for g in groups
    ]
    return {
        "method": "base-stage-v1",
        "diagnostic_only": True,
        "as_of": date(n - 1),
        "maturity": maturity,
        "history_left_censored": left_censored,
        "history_start": date(0),
        "history_bars": n,
        "observed_completed_bases": completed,
        "current_ordinal_lower_bound": ordinal,
        "current_ordinal_exact": ordinal if not left_censored and not pending else None,
        "count_is_conservative": True,
        "cycle_anchor": (
            {
                **anchor,
                "confirmed_at": date(anchor["confirmed_index"]),
                "reason": "accepted_new_base_after_observed_ma200_repair",
            }
            if anchor
            else None
        ),
        "reset_pending": pending,
        "reset_evidence": resets,
        "base_on_base_relations": relations,
        "independent_base_events": events,
        "limitations": [
            "price_only_engineering_count",
            "maturity_is_not_entry_distance",
            "base_on_base_market_context_not_confirmed",
        ],
    }
