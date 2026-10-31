"""Trailing-only shape diagnostics; these observations do not award score points.

Lines are fitted to confirmed swing prices in log space, never to future extrema.
One trailing median daily true range is the common geometric tolerance. Two
touches only define a tentative line; residuals and penetrations remain visible.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _date(dates: np.ndarray, index: int) -> str:
    return str(dates[index])[:10]


def _boundary(
    prices: np.ndarray,
    close: np.ndarray,
    dates: np.ndarray,
    points: list[dict[str, Any]],
    start: int,
    end: int,
    scale: float,
    kind: str,
) -> dict[str, Any]:
    # A fixed selection rule (all confirmed touches in the window), not the
    # pair of points whose resulting line best predicts the following return.
    unique = {int(p["index"]): p for p in points if p["kind"] == kind}
    touches = [unique[i] for i in sorted(unique)]
    if len(touches) < 2:
        return {"available": False, "reason": "fewer_than_two_confirmed_touches"}
    indices = np.array([int(p["index"]) for p in touches])
    span = int(indices[-1] - indices[0])
    if span < (end - start) / 4:
        return {"available": False, "reason": "touch_span_too_short", "touch_count": len(touches)}
    origin = int(indices[0])
    slope, intercept = np.polyfit(indices - origin, np.log(prices[indices]), 1)
    fitted = intercept + slope * (indices - origin)
    residual = float(np.max(np.abs(np.log(prices[indices]) - fitted)) / scale)
    # Before the first touch there is no fitted support/resistance history.
    x = np.arange(origin, end)
    line = intercept + slope * (x - origin)
    excess = np.log(prices[origin:end]) - line
    if kind == "low":
        excess = -excess
    penetrations = excess > scale
    penetration_fraction = float(penetrations.mean())
    available_at = max(int(p["confirmed_index"]) for p in touches)
    last_line = float(np.exp(intercept + slope * (end - 1 - origin)))
    previous_line = float(np.exp(intercept + slope * (end - 2 - origin)))
    crossed = bool(
        end - 1 >= available_at
        and (
            (close[end - 1] > last_line and close[end - 2] <= previous_line)
            if kind == "high"
            else (close[end - 1] < last_line and close[end - 2] >= previous_line)
        )
    )
    return {
        "available": True,
        "usable": bool(residual <= 1 and penetration_fraction <= 0.25),
        "tentative_two_touch_line": len(touches) == 2,
        "touch_count": len(touches),
        "touch_span_bars": span,
        "touch_coverage": float(span / (end - start - 1)),
        "touches": [
            {
                "session": _date(dates, int(p["index"])),
                "confirmed_at": _date(dates, int(p["confirmed_index"])),
                "price": float(prices[int(p["index"])]),
            }
            for p in touches
        ],
        "origin_session": _date(dates, origin),
        "log_intercept": float(intercept),
        "log_slope_per_session": float(slope),
        "window_change_in_true_ranges": float(slope * (end - start - 1) / scale),
        "maximum_touch_residual_in_true_ranges": residual,
        "penetration_fraction": penetration_fraction,
        "maximum_penetration_in_true_ranges": float(max(0.0, excess.max()) / scale),
        "fit_available_at": _date(dates, available_at),
        "frozen_at_session": _date(dates, end - 1),
        "price_at_window_start": float(np.exp(intercept + slope * (start - origin))),
        "price_at_last_session": last_line,
        "projected_next_session_price": float(np.exp(intercept + slope * (end - origin))),
        "crossed_on_last_observed_session": crossed,
        "crossing_observed_at": _date(dates, end - 1) if crossed else None,
    }


def _weekly(
    close: np.ndarray, high: np.ndarray, low: np.ndarray, volume: np.ndarray, dates: np.ndarray
) -> dict[str, Any]:
    frame = pd.DataFrame(
        {"close": close, "high": high, "low": low, "volume": volume}, index=pd.DatetimeIndex(dates)
    )
    weeks = frame.resample("W-FRI").agg({"close": "last", "high": "max", "low": "min", "volume": "sum"})
    # Friday labels after the cutoff are incomplete. With no exchange calendar,
    # even a Thursday holiday close waits until the next calendar week is known.
    weeks = weeks.loc[(weeks.index <= frame.index[-1]) & weeks.close.notna()]
    if frame.index[0].weekday() != 0 and len(weeks):
        first_label = frame.index[0].to_period("W-FRI").end_time.normalize()
        weeks = weeks.loc[weeks.index != first_label]
    if len(weeks) < 3:
        return {"available": False, "reason": "fewer_than_three_completed_weeks"}
    tail = weeks.tail(3)
    return {
        "available": True,
        "completed_week_count": len(weeks),
        "last_completed_week": weeks.index[-1].strftime("%Y-%m-%d"),
        "close_range_3_weeks": float(tail.close.max() / tail.close.min() - 1),
        "mean_week_range_3_weeks": float((1 - tail.low / tail.high).mean()),
        "weeks": [
            {
                "week_ending": day.strftime("%Y-%m-%d"),
                "close": float(row.close),
                "range": float(1 - row.low / row.high),
                "volume": float(row.volume),
            }
            for day, row in tail.iterrows()
        ],
        "incomplete_week_excluded": bool(frame.index[-1] > weeks.index[-1]),
        "completion_rule": "calendar_Friday_at_or_before_cutoff; holiday_weeks_confirmed_conservatively",
    }


def shape_context(high, low, close, volume, trp, swings, dates, end: int) -> dict[str, Any]:
    """Describe 20/40/80-session boundaries using only rows strictly before end.

    A reported crossing is an observation at the cutoff using the returned frozen
    anchors, not a historical trading signal. Consumers must retain snapshots if
    they want to evaluate future prices against a previously frozen boundary.
    """
    if end < 2 or any(len(a) < end for a in (high, low, close, volume, trp, dates)):
        return {"available": False, "reason": "insufficient_history"}
    h, lo, c, v, tr = (np.asarray(a[:end], dtype=float) for a in (high, low, close, volume, trp))
    ds = np.asarray(dates[:end])
    if (
        not all(np.isfinite(a).all() for a in (h, lo, c, v, tr))
        or any((a <= 0).any() for a in (h, lo, c))
        or (v < 0).any()
        or (tr < 0).any()
    ):
        return {"available": False, "reason": "invalid_observed_data"}
    confirmed = [s for s in swings if 0 <= int(s["index"]) <= int(s["confirmed_index"]) < end]
    windows = []
    for length in (20, 40, 80):
        if end < length:
            continue
        start = end - length
        scale = float(np.median(tr[start:end]))
        if scale <= 0:
            windows.append({"bars": length, "available": False, "reason": "zero_observed_true_range"})
            continue
        points = [s for s in confirmed if int(s["index"]) >= start]
        upper = _boundary(h, c, ds, points, start, end, scale, "high")
        lower = _boundary(lo, c, ds, points, start, end, scale, "low")
        before = max(0, start - length)
        impulse = {"available": False}
        if start - before >= 3:
            trough = before + int(np.argmin(c[before : start + 1]))
            advance = float(c[start] / c[trough] - 1)
            prior_scale = float(np.median(tr[before:start]))
            impulse = {
                "available": True,
                "start_session": _date(ds, trough),
                "end_session": _date(ds, start),
                "return": advance,
                "bars": start - trough,
                "true_range_units": advance / prior_scale if prior_scale > 0 else None,
                "established": bool(
                    prior_scale > 0
                    and start - trough >= 3
                    and c[start] > c[before]
                    and advance >= 2 * prior_scale
                ),
            }
        labels = []
        gap_start = gap_end = None
        usable = upper.get("usable", False) and lower.get("usable", False)
        if usable:
            gap_start = float(np.log(upper["price_at_window_start"] / lower["price_at_window_start"]))
            gap_end = float(np.log(upper["price_at_last_session"] / lower["price_at_last_session"]))
            u, lower_change = upper["window_change_in_true_ranges"], lower["window_change_in_true_ranges"]
            if gap_start > 0 and gap_end > 0:
                if gap_start - gap_end > scale:
                    labels.append("converging_range")
                    if abs(u) <= 1 and lower_change > 1:
                        labels.append("ascending_triangle_geometry")
                if u < -1 and lower_change <= 1 and gap_end <= gap_start + scale:
                    labels.append("descending_consolidation")
        windows.append(
            {
                "available": True,
                "bars": length,
                "start_session": _date(ds, start),
                "end_session": _date(ds, end - 1),
                "range_high": float(h[start:end].max()),
                "range_low": float(lo[start:end].min()),
                "range_width": float(1 - lo[start:end].min() / h[start:end].max()),
                "upper_boundary": upper,
                "lower_boundary": lower,
                "geometry_evidence": labels,
                "boundary_log_width_start": gap_start,
                "boundary_log_width_end": gap_end,
                "prior_impulse": impulse,
                "flag_like_context": bool(
                    "descending_consolidation" in labels and impulse.get("established")
                ),
                "median_true_range_pct": scale,
            }
        )
    nested = {"available": False}
    if end >= 80:
        parent_high, parent_low = float(h[end - 80 : end].max()), float(lo[end - 80 : end].min())
        local_high, local_low = float(h[end - 20 : end].max()), float(lo[end - 20 : end].min())
        width = parent_high - parent_low
        if width > 0:
            nested = {
                "available": True,
                "local_bars": 20,
                "parent_bars": 80,
                "local_range_fraction_of_parent": (local_high - local_low) / width,
                "local_low_position_in_parent": (local_low - parent_low) / width,
                "last_close_position_in_parent": float((c[-1] - parent_low) / width),
                "previous_parent_high": float(h[end - 80 : end - 20].max()),
                "close_above_previous_parent_high": bool(c[-1] > h[end - 80 : end - 20].max()),
            }
    supply = {"available": False}
    if end >= 21 and v[-20:].sum() > 0:
        returns = np.diff(c[-21:])
        vol = v[-20:]
        clv = (c[-20:] - lo[-20:]) / np.maximum(h[-20:] - lo[-20:], np.finfo(float).eps)
        up, down = float(vol[returns > 0].sum()), float(vol[returns < 0].sum())
        supply = {
            "available": True,
            "bars": 20,
            "up_volume": up,
            "down_volume": down,
            "up_down_volume_ratio": up / down if down else None,
            "down_low_close_volume_fraction": float(vol[(returns < 0) & (clv < 0.5)].sum() / vol.sum()),
            "down_high_close_volume_fraction": float(vol[(returns < 0) & (clv >= 0.5)].sum() / vol.sum()),
            "interpretation": "OHLCV_proxy_not_observed_institutional_order_flow",
        }
    return {
        "available": bool(windows),
        "as_of_session": _date(ds, end - 1),
        "diagnostic_only": True,
        "windows": windows,
        "nested_context": nested,
        "weekly_tightness": _weekly(c, h, lo, v, ds),
        "volume_balance": supply,
    }
