from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trenddeck.setups.shapes import shape_context


def shape_inputs(kind: str = "descending") -> dict:
    size = 100
    close = np.linspace(65., 110., size)
    high, low = close * 1.01, close * .99
    x = np.arange(20)
    if kind == "descending":
        high[-20:] = 120 * np.exp(-.002 * x)
        low[-20:] = 100 * np.exp(-.001 * x)
    else:
        high[-20:] = 120
        low[-20:] = 100 * np.exp(.002 * x)
    close[-20:] = np.sqrt(high[-20:] * low[-20:])
    swings = [{"kind": side, "index": i, "confirmed_index": i + 1}
              for side, indices in [("high", [82, 89, 97]), ("low", [84, 91, 98])]
              for i in indices]
    return {"high": high, "low": low, "close": close,
            "volume": np.full(size, 1_000_000.), "trp": np.full(size, .01),
            "swings": swings, "dates": pd.bdate_range("2025-01-06", periods=size).to_numpy(),
            "end": size}


def test_descending_geometry_requires_prior_impulse_to_be_flag_like():
    args = shape_inputs()
    result = shape_context(**args)
    local = result["windows"][0]
    assert "descending_consolidation" in local["geometry_evidence"]
    assert "converging_range" in local["geometry_evidence"]
    assert local["flag_like_context"]
    assert local["upper_boundary"]["touch_count"] == 3
    assert local["upper_boundary"]["log_slope_per_session"] == pytest.approx(-.002)
    assert result["diagnostic_only"]
    # Exactly the same local shape in a sustained decline is not a bull flag.
    args["close"][:80] = np.linspace(160, 120, 80)
    args["high"][:80] = args["close"][:80] * 1.01
    args["low"][:80] = args["close"][:80] * .99
    other = shape_context(**args)["windows"][0]
    assert "descending_consolidation" in other["geometry_evidence"]
    assert not other["flag_like_context"]


def test_flat_upper_and_rising_lower_are_triangle_geometry_not_a_trade_signal():
    local = shape_context(**shape_inputs("triangle"))["windows"][0]
    assert "ascending_triangle_geometry" in local["geometry_evidence"]
    assert local["upper_boundary"]["usable"]
    assert local["lower_boundary"]["usable"]
    assert not local["flag_like_context"]
    assert not local["upper_boundary"]["crossed_on_last_observed_session"]


def test_future_rows_and_unconfirmed_extrema_cannot_change_prefix():
    args = shape_inputs()
    expected = shape_context(**args)
    for key in ["high", "low", "close", "volume", "trp"]:
        args[key] = np.concatenate([args[key], [np.nan] * 10])
    args["dates"] = np.concatenate([args["dates"], pd.bdate_range("2030-01-01", periods=10).to_numpy()])
    args["swings"] += [{"kind": "high", "index": 99, "confirmed_index": 105},
                       {"kind": "low", "index": 102, "confirmed_index": 107}]
    assert shape_context(**args) == expected


def test_large_penetrations_disqualify_line_but_remain_visible():
    args = shape_inputs()
    # Keep the confirmed touch geometry unchanged, but contradict the upper
    # boundary on most bars. A fitted line must not hide these real prices.
    indices = [i for i in range(82, 100) if i not in (82, 89, 97)]
    args["high"][indices] *= 1.15
    local = shape_context(**args)["windows"][0]
    assert local["upper_boundary"]["available"]
    assert not local["upper_boundary"]["usable"]
    assert local["upper_boundary"]["penetration_fraction"] > .25
    assert local["geometry_evidence"] == []


def test_line_requires_spread_out_confirmed_touches_and_records_frozen_crossing():
    args = shape_inputs()
    args["swings"] = [{"kind": "high", "index": i, "confirmed_index": i + 1} for i in (94, 96)]
    upper = shape_context(**args)["windows"][0]["upper_boundary"]
    assert not upper["available"]
    assert upper["reason"] == "touch_span_too_short"
    args = shape_inputs()
    args["close"][-1] = 119
    args["high"][-1] = 120
    upper = shape_context(**args)["windows"][0]["upper_boundary"]
    assert upper["crossed_on_last_observed_session"]
    assert upper["crossing_observed_at"] == str(args["dates"][-1])[:10]
    assert upper["frozen_at_session"] == upper["crossing_observed_at"]
    assert upper["fit_available_at"] <= upper["frozen_at_session"]
    assert upper["projected_next_session_price"] < upper["price_at_last_session"]


def test_weekly_diagnostics_never_use_incomplete_current_week():
    args = shape_inputs()
    # 100 bars from a Monday end on Friday; extend only to Wednesday.
    for key in ["high", "low", "close"]:
        args[key] = np.concatenate([args[key], [200., 210., 220.]])
    for key in ["volume", "trp"]:
        args[key] = np.concatenate([args[key], [args[key][-1]] * 3])
    args["dates"] = pd.bdate_range("2025-01-06", periods=103).to_numpy()
    original = shape_context(**{**args, "end": 100})["weekly_tightness"]
    current = shape_context(**{**args, "end": 103})["weekly_tightness"]
    assert original["weeks"] == current["weeks"]
    assert original["close_range_3_weeks"] == current["close_range_3_weeks"]
    assert not original["incomplete_week_excluded"]
    assert current["incomplete_week_excluded"]


def test_insufficient_touches_and_zero_volume_are_unavailable_not_invented():
    args = shape_inputs()
    args["swings"] = []
    args["volume"][:] = 0
    result = shape_context(**args)
    assert not result["volume_balance"]["available"]
    assert not result["windows"][0]["upper_boundary"]["available"]
    assert result["windows"][0]["geometry_evidence"] == []
    assert not shape_context(**{**args, "end": 1})["available"]


def test_geometry_and_volume_diagnostics_are_price_scale_invariant():
    args = shape_inputs()
    original = shape_context(**args)
    for key in ["high", "low", "close"]:
        args[key] = args[key] * 100
    scaled = shape_context(**args)
    assert original["windows"][0]["geometry_evidence"] == scaled["windows"][0]["geometry_evidence"]
    assert original["windows"][0]["upper_boundary"]["log_slope_per_session"] == pytest.approx(
        scaled["windows"][0]["upper_boundary"]["log_slope_per_session"])
    assert original["weekly_tightness"]["close_range_3_weeks"] == pytest.approx(
        scaled["weekly_tightness"]["close_range_3_weeks"])
