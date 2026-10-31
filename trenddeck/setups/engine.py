"""Causal OHLCV setup research. Scores are evidence summaries, not probabilities.

Geometry is frozen at the previous close; the current bar tests that geometry.
No centered smoothing, future extrema, fitted weights, or external model calls.
See docs/setups.md for source mapping, thresholds and limitations.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from trenddeck.setups.model import SetupModel, load_model
from trenddeck.setups.risk import relative_context, structure_risk
from trenddeck.setups.validity import base_validity

MIN_BARS = 253


def unit(value: float, low: float, high: float) -> float:
    return float(np.clip((value - low) / (high - low), 0, 1))


def clean_history(frame: pd.DataFrame) -> pd.DataFrame:
    required = ["Date", "Open", "High", "Low", "Close", "Volume"]
    if frame.empty:
        return pd.DataFrame(columns=required)
    if not set(required).issubset(frame):
        raise ValueError("OHLCV columns are required")
    f = frame.copy()
    f["Date"] = pd.to_datetime(f["Date"], errors="raise").dt.tz_localize(None)
    if f["Date"].isna().any() or f["Date"].duplicated().any():
        raise ValueError("Missing or duplicate session dates")
    f = f.sort_values("Date").reset_index(drop=True)
    a = f[required[1:]].to_numpy(dtype=float)
    if not np.isfinite(a).all() or (a[:, :4] <= 0).any() or (a[:, 4] < 0).any():
        raise ValueError("Prices must be positive and OHLCV finite; volume cannot be negative")
    f[required[1:]] = a
    if (
        (f.High < f[["Open", "Close", "Low"]].max(axis=1))
        | (f.Low > f[["Open", "Close", "High"]].min(axis=1))
    ).any():
        raise ValueError("Inconsistent OHLC bars")
    return f


def confirmed_swings(close: np.ndarray, threshold: np.ndarray) -> list[dict[str, Any]]:
    """Directional changes on closes, threshold frozen at each new extreme.

    The last leg remains unconfirmed. A pivot's index is NOT its availability
    time: confirmed_index is when the reversal was observed.
    """
    if len(close) < 2:
        return []
    result = []
    extreme, direction = 0, 0
    for i in range(1, len(close)):
        if direction >= 0:
            if close[i] >= close[extreme]:
                extreme = i
            elif close[i] / close[extreme] - 1 <= -threshold[extreme]:
                result.append({"kind": "high", "index": extreme, "confirmed_index": i})
                extreme, direction = i, -1
        else:
            if close[i] <= close[extreme]:
                extreme = i
            elif close[i] / close[extreme] - 1 >= threshold[extreme]:
                result.append({"kind": "low", "index": extreme, "confirmed_index": i})
                extreme, direction = i, 1
    return result


class SetupSeries:
    """Precompute trailing-only indicators once for repeated historical scoring."""

    def __init__(
        self, frame: pd.DataFrame, benchmark: pd.DataFrame | None = None, model: SetupModel | None = None
    ):
        self.model = model or load_model()
        self.p = self.model.parameters
        self.frame = clean_history(frame)
        f = self.frame
        self.dates = f.Date.to_numpy()
        self.o, self.h, self.low, self.c, self.v = (
            f[k].to_numpy(float) for k in ["Open", "High", "Low", "Close", "Volume"]
        )
        self.ma = {n: f.Close.rolling(n).mean().to_numpy() for n in [20, 50, 150, 200]}
        prev = f.Close.shift()
        tr = pd.concat([f.High - f.Low, (f.High - prev).abs(), (f.Low - prev).abs()], axis=1).max(axis=1)
        self.trp = (tr / prev).fillna(0).to_numpy()
        self.threshold = np.clip(
            pd.Series(self.trp).rolling(20, min_periods=1).mean() * self.p["swing_atr_multiplier"],
            self.p["swing_min_reversal"],
            self.p["swing_max_reversal"],
        )
        self.swings = confirmed_swings(self.c, np.asarray(self.threshold))
        # Separate base-scale contractions from the finer swings used for damage.
        coarse = np.clip(pd.Series(self.trp).rolling(20, min_periods=1).mean() * 2.5, 0.03, 0.15)
        self.base_swings = confirmed_swings(self.c, np.asarray(coarse))
        self.benchmark = clean_history(benchmark) if benchmark is not None else pd.DataFrame()
        self.price_mode = str(f.PriceMode.iloc[-1]) if len(f) and "PriceMode" in f else "unknown"
        from trenddeck.setups.bases import BaseSeries

        self.bases = BaseSeries(self)

    def date(self, i: int) -> str:
        return str(self.dates[i])[:10]

    def gap_events(self, i: int) -> list[dict[str, Any]]:
        events = []
        for j in range(max(50, i - 39), i + 1):
            gap = self.o[j] / self.c[j - 1] - 1
            vr = self.v[j] / max(self.v[j - 50 : j].mean(), 1)
            if (
                gap >= self.p["gap_min_return"]
                and vr >= self.p["gap_min_volume_ratio"]
                and self.c[j] > self.o[j]
                and self.c[j] >= self.low[j] + self.p["gap_min_close_location"] * (self.h[j] - self.low[j])
            ):
                events.append(
                    {
                        "date": self.date(j),
                        "gap": float(gap),
                        "volume_ratio": float(vr),
                        "gap_day_low": float(self.low[j]),
                        "bars_since": i - j,
                        "low_held": bool(self.low[j : i + 1].min() >= self.low[j] * 0.99),
                        "mature_extension_warning": bool(
                            j >= 63
                            and self.c[j] / self.c[j - 63] > 2
                            and (self.c[j] / self.ma[20][j] - 1) > 4 * self.trp[j - 20 : j].mean()
                        ),
                        "cause": "unknown",
                    }
                )
        return events

    def prior_advance(self, peak: int) -> dict[str, float]:
        """Measure the advance BEFORE the base, in units of its own daily true range."""
        start = max(0, peak - 63)
        trough = start + int(np.argmin(self.c[start : peak + 1]))
        runup = float(self.c[peak] / self.c[trough] - 1)
        atr = float(np.mean(self.trp[start : peak + 1]))
        units = runup / max(atr, 1e-8)
        quality = unit(units, self.p["prior_advance_min_atr"], self.p["prior_advance_full_atr"])
        # A single jump is not yet an established trend. Gap setups have their own route.
        quality *= unit(peak - trough, 3, 10)
        return {
            "prior_advance_return": runup,
            "prior_advance_atr_units": units,
            "prior_advance_bars": peak - trough,
            "prior_advance_quality": quality,
        }

    def _repairing_trend(self, i: int) -> bool:
        """An observed right-side repair, not a prediction that a downtrend will turn."""
        known = [s for s in self.swings if s["confirmed_index"] <= i and s["index"] >= i - 80]
        highs = [s["index"] for s in known if s["kind"] == "high"][-2:]
        lows = [s["index"] for s in known if s["kind"] == "low"][-2:]
        tolerance = self.p["risk_swing_tolerance"]
        return bool(
            len(highs) == len(lows) == 2
            and self.h[highs[-1]] > self.h[highs[0]] * (1 + tolerance)
            and self.low[lows[-1]] > self.low[lows[0]] * (1 + tolerance)
            and self.c[i] >= self.h[highs[-1]]
            and self.c[i] > max(self.ma[20][i], self.ma[50][i], self.ma[200][i])
            and self.ma[20][i] > self.ma[20][i - 5]
            and self.ma[50][i] > self.ma[50][i - 10]
        )

    def _geometry(self, end: int) -> dict[str, Any]:
        """Only the current base-first engine; end excludes the signal bar."""
        return self.bases.snapshot(end)["selected"]

    def pivot_failures(self, i: int, current_geometry: dict[str, Any]) -> list[dict[str, Any]]:
        return self.bases.failures(i + 1)

    def score(self, index: int | None = None) -> dict[str, Any]:
        i = len(self.c) - 1 if index is None else index
        if i < 0 or i >= len(self.c):
            raise ValueError("No bars at requested cutoff")
        price_mode = (
            "adjusted"
            if "PriceMode" in self.frame and self.frame.PriceMode.iloc[: i + 1].eq("adjusted").all()
            else "unknown_or_mixed"
        )
        base = {
            "model_version": self.model.version,
            "config_hash": self.model.config_hash,
            "engine_hash": self.model.engine_hash,
            "as_of_session": self.date(i),
            "bars": i + 1,
            "price_mode": price_mode,
        }
        if i + 1 < MIN_BARS:
            return {
                **base,
                "status": "insufficient_history",
                "score": None,
                "eligible": False,
                "required_bars": MIN_BARS,
            }
        c, h, low, v = self.c, self.h, self.low, self.v
        close = c[i]
        ma = {n: self.ma[n][i] for n in self.ma}
        checks = {
            "above_long_mas": close > max(ma[150], ma[200]),
            "ma150_above_ma200": ma[150] > ma[200],
            "ma200_rising": ma[200] > self.ma[200][i - 21],
            "ma50_above_long_mas": ma[50] > max(ma[150], ma[200]),
            "above_ma50": close > ma[50],
            "above_52w_low": close >= low[i - 251 : i + 1].min() * 1.3,
            "near_52w_high": close >= h[i - 251 : i + 1].max() * 0.75,
        }
        # Core Stage 2 gate allows early cheat setups whose MA50 is still repairing.
        stage2 = bool(checks["above_long_mas"] and checks["ma200_rising"] and checks["ma150_above_ma200"])
        repairing = bool(not stage2 and self._repairing_trend(i))
        trend_ok = stage2 or repairing
        g = self._geometry(i)
        has_base = bool(g) and g["pattern"] != "none"
        if not g:
            # Retain trend/RS/gap evidence even when no defensible base exists.
            g = {
                "pattern": "none",
                "pattern_strength": 0.0,
                "pivot": float(h[i - 20 : i].max()),
                "floor": float(low[i - 20 : i].min()),
                "tightness": 0.0,
                "volume_quality": 0.0,
                "base_depth": float(1 - low[i - 20 : i].min() / h[i - 20 : i].max()),
                "pause_width": float(1 - low[i - 5 : i].min() / h[i - 5 : i].max()),
                "contractions": [],
            }
        pivot, floor = g["pivot"], g["floor"]
        distance = float(close / pivot - 1)
        risk = float(1 - floor * (1 - self.p["stop_buffer"]) / close)
        volume_ratio = float(v[i] / max(np.mean(v[i - 50 : i]), 1))
        close_location = float((close - low[i]) / max(h[i] - low[i], 1e-8))
        crossed = bool(close > pivot and c[i - 1] <= pivot)
        confirmed = (
            crossed
            and volume_ratio >= self.p["breakout_volume_ratio"]
            and close_location >= self.p["breakout_close_location"]
        )
        rs = None
        market_regime = "unknown"
        benchmark_session = None
        relative = {"available": False}
        if not self.benchmark.empty:
            b = self.benchmark.loc[self.benchmark.Date <= pd.Timestamp(self.dates[i])]
            if len(b):
                benchmark_session = b.Date.iloc[-1].strftime("%Y-%m-%d")
            if len(b) >= 200:
                market_regime = (
                    "uptrend"
                    if b.Close.iloc[-1] > b.Close.tail(50).mean() > b.Close.tail(200).mean()
                    else "mixed_or_weak"
                )
            aligned = self.frame.iloc[: i + 1][["Date", "Close"]].merge(b[["Date", "Close"]], on="Date")
            if len(aligned) >= 64 and benchmark_session == self.date(i):
                rs = float(
                    (aligned.Close_x.iloc[-1] / aligned.Close_x.iloc[-64])
                    / (aligned.Close_y.iloc[-1] / aligned.Close_y.iloc[-64])
                    - 1
                )
                relative = relative_context(aligned, self.p)
        parts = {
            "trend": sum(checks.values()) / len(checks),
            "structure": g["pattern_strength"],
            "tightness": g["tightness"],
            "volume": g["volume_quality"],
            "relative_strength": unit(rs, -0.05, 0.20) if rs is not None else 0.0,
            "location": 1 - unit(abs(distance), 0.01, 0.10),
            "risk": 1 - unit(risk, 0.04, 0.12),
        }
        if self.p["relative_context_enabled"] and relative["available"]:
            parts["relative_strength"] = (
                0.6 * parts["relative_strength"]
                + 0.25 * unit(relative["relative_return_21"], -0.05, 0.05)
                + 0.15 * (1 - unit(-relative["rs_distance_from_63_high"], 0.02, 0.15))
            )
        raw = sum(parts[k] * w for k, w in self.model.weights.items())
        daily_returns = c[i - 19 : i + 1] / c[i - 20 : i] - 1
        daily_clv = (c[i - 19 : i + 1] - low[i - 19 : i + 1]) / np.maximum(
            h[i - 19 : i + 1] - low[i - 19 : i + 1], 1e-8
        )
        distribution = int(
            np.sum((daily_returns < -0.02) & (v[i - 19 : i + 1] > v[i - 20 : i]) & (daily_clv < 0.5))
        )
        penalties = {
            "distribution": min(12, max(0, distribution - 2) * 3),
            "deep_base": 10 * unit(g["base_depth"], 0.35, 0.60),
        }
        quality = max(0.0, raw - sum(penalties.values()))
        volume_valid = bool(np.all(v[i - 59 : i + 1] > 0))
        liquid = bool(np.mean(c[i - 19 : i + 1] * v[i - 19 : i + 1]) >= self.p["min_dollar_volume"])
        structural = (
            g["pattern_strength"] >= self.p["structure_min_strength"]
            and g["pause_width"] <= self.p["max_pause_width"]
        )
        # The books require an established advance, not just low absolute volatility.
        leadership = bool(g.get("prior_advance_quality", 0) >= 0.5)
        eligible = trend_ok and liquid and volume_valid and price_mode == "adjusted"
        damage = structure_risk(c, h, low, v, self.trp, self.ma, self.swings, self.dates, i, g, self.p)
        failures = self.pivot_failures(i, g) if self.p["damage_filters_enabled"] else []
        if any(e["active"] for e in failures):
            damage["blocks"].append("failed_frozen_pivot")
        damage_blocked = bool(self.p["damage_filters_enabled"] and damage["blocks"])
        overextended = bool(
            close / ma[20] - 1 > self.p["max_ma20_extension"]
            or (self.p["damage_filters_enabled"] and damage["overextended_in_own_range"])
        )
        if not trend_ok:
            status = "outside_stage2"
        elif damage_blocked:
            status = "damaged"
        elif not has_base:
            status = "no_base"
        elif close < floor * (1 - self.p["stop_buffer"]):
            status = "invalidated"
        elif overextended:
            status = "extended"
        elif confirmed and structural:
            status = "breakout_confirmed"
        elif crossed and structural:
            status = "breakout_unconfirmed"
        elif distance > 0:
            status = "above_pivot"
        elif (
            structural
            and distance >= -self.p["ready_distance"]
            and g["volume_quality"] >= self.p["ready_min_volume_quality"]
        ):
            status = "ready"
        else:
            status = "developing"
        caps = {"outside_stage2": 39, "damaged": 24, "no_base": 49, "invalidated": 25, "extended": 49}
        score = min(quality, caps.get(status, 100), 100 if eligible else 39)
        gaps = self.gap_events(i)
        reasons = []
        if damage_blocked:
            reasons.extend(damage["blocks"])
        if repairing:
            reasons.append("early_trend_repair")
        elif not stage2:
            reasons.append("core_stage2_not_met")
        if not liquid:
            reasons.append("average_dollar_volume_below_2m")
        if not has_base and g.get("completed_base_count", 0):
            reasons.append("completed_base_needs_new_consolidation")
        if not leadership and has_base:
            reasons.append("prior_advance_weak_relative_to_own_volatility")
        if not volume_valid:
            reasons.append("zero_volume_in_last_60_bars")
        if price_mode != "adjusted":
            reasons.append("adjusted_prices_required")
        if rs is None:
            reasons.append("aligned_benchmark_unavailable_no_rs_points")
        if status == "extended" or overextended:
            reasons.append("extended_from_pivot_or_ma20")
        result = {
            **base,
            "status": status,
            "score": round(float(score), 2),
            "quality_score": round(float(quality), 2),
            "leadership": leadership,
            "eligible": bool(eligible),
            "candidate_eligible": bool(
                eligible
                and not damage_blocked
                and structural
                and status not in {"extended", "invalidated", "no_base"}
            ),
            "actionable": bool(
                eligible and 0 < risk <= self.p["max_risk"] and status in {"ready", "breakout_confirmed"}
            ),
            "pattern": g["pattern"],
            "close": float(close),
            "distance_to_pivot": distance,
            "risk_to_structure": risk,
            "invalidation_level": float(floor * (1 - self.p["stop_buffer"])),
            "setup_as_of_session": self.date(i - 1),
            "geometry": g,
            "components": {
                k: {"points": round(parts[k] * w, 2), "maximum": w} for k, w in self.model.weights.items()
            },
            "penalties": penalties,
            "trend_checks": {k: bool(x) for k, x in checks.items()},
            "strict_price_template": bool(all(checks.values())),
            "relative_return_63": rs,
            "relative_context": relative,
            "risk_evidence": damage,
            "pivot_failure_events": failures,
            "benchmark_session": benchmark_session,
            "market_regime": market_regime,
            "distribution_days_20": distribution,
            "breakout": {
                "crossed_today": crossed,
                "volume_ratio": volume_ratio,
                "close_location": close_location,
                "confirmed": bool(confirmed and structural),
            },
            "gap_events": gaps,
            "warnings": reasons,
        }
        from trenddeck.setups.shapes import shape_context

        result["trend_phase"] = "stage2" if stage2 else "repairing" if repairing else "unconfirmed"
        result["shape_context"] = shape_context(h, low, c, v, self.trp, self.swings, self.dates, i)
        result["score_scope"] = "overall_shape_not_exact_entry_timing"
        from trenddeck.setups.base_stage import stage_context

        context = self.bases.snapshot(i)
        result["base_context"] = {
            "selected_base_id": g.get("base_id"),
            "active_count": context["active_count"],
            "catalogue": context["catalogue"],
            "stage": stage_context(context["catalogue"], c, self.ma[200], self.dates, i, self.p),
        }
        result["invalidation"] = base_validity(self, i, g, result["invalidation_level"])
        return result


def score_setup(
    frame: pd.DataFrame,
    benchmark: pd.DataFrame | None = None,
    as_of: str | None = None,
    model: SetupModel | None = None,
) -> dict[str, Any]:
    # Cut before validation/preparation so even malformed future rows cannot affect history.
    if as_of is not None:
        frame = frame.loc[pd.to_datetime(frame.Date) <= pd.Timestamp(as_of)]
        if benchmark is not None and not benchmark.empty:
            benchmark = benchmark.loc[pd.to_datetime(benchmark.Date) <= pd.Timestamp(as_of)]
    return SetupSeries(frame, benchmark, model).score()
