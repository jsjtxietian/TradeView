"""Causal damage and relative-market evidence, kept separate from pattern rewards.

A lower low is not automatically a broken base: reclaimed shakeouts and repaired
right sides are recorded separately. Thresholds are engineering hypotheses.
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd


def structure_risk(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    volume: np.ndarray,
    trp: np.ndarray,
    ma: dict[int, np.ndarray],
    swings: list[dict[str, Any]],
    dates: np.ndarray,
    index: int,
    geometry: dict[str, Any],
    p: Mapping[str, float],
) -> dict[str, Any]:
    i = index

    def date(j: int) -> str:
        return str(dates[j])[:10]

    atr = float(np.mean(trp[i - 20 : i]))
    clv = (close[i] - low[i]) / max(high[i] - low[i], 1e-8)
    previous_low = float(low[i - 5 : i].min())
    shakeout = bool(low[i] < previous_low and close[i] > previous_low and clv >= 0.6)
    confirmed = [s for s in swings if s["confirmed_index"] <= i and s["index"] >= i - 80]
    highs = [s for s in confirmed if s["kind"] == "high"][-2:]
    lows = [s for s in confirmed if s["kind"] == "low"][-2:]
    tolerance = p["risk_swing_tolerance"]
    lower_highs = len(highs) == 2 and high[highs[-1]["index"]] < high[highs[0]["index"]] * (1 - tolerance)
    lower_lows = len(lows) == 2 and low[lows[-1]["index"]] < low[lows[0]["index"]] * (1 - tolerance)
    last_high = float(high[highs[-1]["index"]]) if highs else None
    last_low = float(low[lows[-1]["index"]]) if lows else None
    ma20_up = bool(ma[20][i] > ma[20][i - 5])
    # A genuine new right-side pause may repair an old descending sequence.
    pause_start = geometry.get("pause_start", "")
    new_pause = bool(
        lows
        and pause_start > date(lows[-1]["index"])
        and geometry.get("pause_width", 1) <= p["max_pause_width"]
        and geometry.get("pause_bars", 0) >= 5
        and geometry.get("pattern", "none") != "none"
        and geometry.get("pattern_strength", 0) >= p["structure_min_strength"]
        and -0.08 <= geometry.get("pause_drift", float("inf")) <= 0.04
        and low[i - 4 : i + 1].min() > last_low
        and close[i] > ma[20][i]
        and ma20_up
    )
    reclaimed_high = last_high is not None and close[i] >= last_high
    descending = bool(lower_highs and lower_lows and not reclaimed_high and not new_pause)
    # Compare the last rally high with its preceding low and with the earlier high.
    failed_rebound = False
    rebound_support = None
    if highs:
        last_hi = highs[-1]["index"]
        preceding = [s for s in confirmed if s["kind"] == "low" and s["index"] < last_hi]
        if preceding and i - last_hi <= 20:
            rebound_support = float(low[preceding[-1]["index"]])
            rally = high[last_hi] / rebound_support - 1
            failed_rebound = bool(
                rally >= 2 * atr and close[i] < rebound_support * (1 - tolerance) and not new_pause
            )
    # Daily lower-low runs need material damage and weak closes, not tiny wick noise.
    run = 0
    active_run = None
    for j in range(i - 7, i + 1):
        run = run + 1 if low[j] < low[j - 1] else 0
        if run >= 3:
            start = j - run
            decline = close[j] / close[start] - 1
            weak_closes = int(np.sum(np.diff(close[start : j + 1]) < 0))
            # Freeze materiality before the run: subsequent wide bars must not
            # erase an unreclaimed decline by inflating today's volatility.
            event_atr = float(np.mean(trp[max(0, start - 20) : start]))
            if (
                decline <= -p["risk_lower_low_atr"] * event_atr
                and weak_closes >= 2
                and close[i] < close[start]
            ):
                active_run = {
                    "start": date(start),
                    "end": date(j),
                    "comparisons": run,
                    "decline": float(decline),
                    "reclaim_level": float(close[start]),
                    "prior_true_range_pct": event_atr,
                }
    # A completed recovery clears the daily warning; a wick reclaim alone does not
    # erase an earlier multi-day breakdown.
    selloffs = []
    for j in range(i - 9, i + 1):
        day_atr = float(np.mean(trp[j - 20 : j]))
        change = close[j] / close[j - 1] - 1
        if change <= -max(p["risk_selloff_min"], p["risk_selloff_atr"] * day_atr):
            selloffs.append(
                {
                    "date": date(j),
                    "return": float(change),
                    "volume_ratio": float(volume[j] / max(volume[j - 50 : j].mean(), 1)),
                    "reclaim_level": float(close[j - 1]),
                }
            )
    repeated = bool(len(selloffs) >= 2 and close[i] < selloffs[0]["reclaim_level"])
    intermediate_down = bool(
        close[i] < ma[50][i] and ma[50][i] < ma[50][i - 10] and close[i] < ma[20][i] and not ma20_up
    )
    extension_atr = float((close[i] / ma[20][i] - 1) / max(atr, 1e-8))
    blocks = []
    if descending:
        blocks.append("unrepaired_lower_highs_and_lower_lows")
    if failed_rebound:
        blocks.append("failed_rebound_below_prior_low")
    if active_run and close[i] < ma[20][i]:
        blocks.append("material_lower_low_sequence")
    if repeated:
        blocks.append("repeated_unrepaired_selloffs")
    if intermediate_down:
        blocks.append("falling_intermediate_trend")
    return {
        "blocks": blocks,
        "shakeout_reclaimed_today": shakeout,
        "lower_highs": bool(lower_highs),
        "lower_lows": bool(lower_lows),
        "last_confirmed_high": last_high,
        "last_confirmed_low": last_low,
        "reclaimed_last_high": bool(reclaimed_high),
        "new_right_side_pause": new_pause,
        "failed_rebound_support": rebound_support,
        "active_lower_low_run": active_run,
        "severe_selloffs_10": selloffs,
        "intermediate_downtrend": intermediate_down,
        "ma20_extension_atr": extension_atr,
        "overextended_in_own_range": bool(extension_atr > p["risk_extension_atr"]),
    }


def relative_context(aligned: pd.DataFrame, p: Mapping[str, float]) -> dict[str, Any]:
    """Match stock and SPY over the exact same sessions and market drawdown episode."""
    if len(aligned) < 64:
        return {"available": False}
    a = aligned.tail(64)
    stock, benchmark = a.Close_x.to_numpy(float), a.Close_y.to_numpy(float)
    rs = stock / benchmark
    change21 = float(rs[-1] / rs[-22] - 1)
    slope21 = float(np.polyfit(np.arange(21), np.log(rs[-21:]), 1)[0])
    rolling_peak = np.maximum.accumulate(benchmark)
    drawdowns = 1 - benchmark / rolling_peak
    trough = int(np.argmax(drawdowns))
    peak = int(np.argmax(benchmark[: trough + 1]))
    market_dd = float(drawdowns[trough])
    same_episode = stock[peak : trough + 1]
    stock_dd = float(np.max(1 - same_episode / np.maximum.accumulate(same_episode)))
    active_episode = market_dd >= p["rs_drawdown_floor"]
    ratios = stock_dd / market_dd if active_episode else None
    recent_stock_return = np.diff(stock) / stock[:-1]
    recent_benchmark_return = np.diff(benchmark) / benchmark[:-1]
    down = recent_benchmark_return <= -0.005
    downside_capture = (
        float(recent_stock_return[down].mean() / recent_benchmark_return[down].mean())
        if down.sum() >= 5
        else None
    )
    return {
        "available": True,
        "relative_return_21": change21,
        "rs_log_slope_21": slope21,
        "rs_distance_from_63_high": float(rs[-1] / rs.max() - 1),
        "rs_improving_21": bool(change21 > 0 and slope21 > 0),
        "market_drawdown_63": market_dd,
        "stock_drawdown_same_episode": stock_dd,
        "market_episode_start": a.Date.iloc[peak].strftime("%Y-%m-%d"),
        "market_episode_low": a.Date.iloc[trough].strftime("%Y-%m-%d"),
        "drawdown_comparison_active": active_episode,
        "drawdown_ratio": ratios,
        "excessive_relative_damage": bool(active_episode and ratios > p["rs_drawdown_multiplier"]),
        "downside_capture": downside_capture,
        "down_market_sessions": int(down.sum()),
    }
