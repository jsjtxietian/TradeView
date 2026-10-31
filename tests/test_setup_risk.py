from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trenddeck.setups.engine import SetupSeries, score_setup
from trenddeck.setups.model import load_model
from trenddeck.setups.research import simulate_pivot_trade
from trenddeck.setups.risk import structure_risk


def risk_inputs() -> dict:
    size = 100
    close = np.full(size, 100.0)
    return {
        "close": close,
        "high": close + 1,
        "low": close - 1,
        "volume": np.full(size, 1_000_000.0),
        "trp": np.full(size, .01),
        "ma": {20: np.full(size, 100.0), 50: np.full(size, 100.0)},
        "swings": [],
        "dates": pd.bdate_range("2025-01-02", periods=size).to_numpy(),
        "index": size - 1,
        "geometry": {},
        "p": load_model().parameters,
    }


def test_lower_low_materiality_survives_later_volatility_and_clears_on_reclaim():
    args = risk_inputs()
    args["close"][93:] = [99, 98, 97, 97, 97, 97, 97]
    args["low"] = args["close"] - .5
    args["high"] = args["close"] + .5
    first = structure_risk(**{**args, "index": 95})
    assert "material_lower_low_sequence" in first["blocks"]
    # Later very wide bars change the current ATR, but not the run's prior ATR.
    args["trp"][96:99] = .30
    args["high"][96:99] = 125
    still_damaged = structure_risk(**args)
    assert "material_lower_low_sequence" in still_damaged["blocks"]
    assert still_damaged["active_lower_low_run"] == first["active_lower_low_run"]
    assert still_damaged["active_lower_low_run"]["prior_true_range_pct"] == pytest.approx(.01)
    args["close"][-1] = 100.5
    args["high"][-1] = 101
    repaired = structure_risk(**args)
    assert repaired["active_lower_low_run"] is None
    assert "material_lower_low_sequence" not in repaired["blocks"]


def descending_inputs() -> dict:
    args = risk_inputs()
    args["close"][:] = 109
    args["high"][:] = 110
    args["low"][:] = 108
    args["high"][[70, 80]] = [130, 120]
    args["low"][[75, 85]] = [100, 90]
    args["swings"] = [
        {"kind": "high", "index": 70, "confirmed_index": 72},
        {"kind": "low", "index": 75, "confirmed_index": 77},
        {"kind": "high", "index": 80, "confirmed_index": 82},
        {"kind": "low", "index": 85, "confirmed_index": 87},
    ]
    args["ma"][20][:] = 106
    args["ma"][20][-1] = 107
    args["geometry"] = {
        "pause_start": str(args["dates"][94])[:10],
        "pause_bars": 5,
        "pause_width": .04,
        "pause_drift": .01,
        "pattern": "short_base",
        "pattern_strength": .8,
    }
    return args


@pytest.mark.parametrize(
    "change",
    [
        {"pattern": "none"},
        {"pattern_strength": .2},
        {"pause_drift": .08},
        {"pause_drift": -.09},
    ],
)
def test_falling_sequence_is_not_repaired_by_weak_or_steep_pause(change):
    args = descending_inputs()
    args["geometry"].update(change)
    evidence = structure_risk(**args)
    assert not evidence["new_right_side_pause"]
    assert "unrepaired_lower_highs_and_lower_lows" in evidence["blocks"]


def test_valid_new_pause_or_reclaimed_resistance_repairs_old_descending_sequence():
    args = descending_inputs()
    repaired = structure_risk(**args)
    assert repaired["lower_highs"] and repaired["lower_lows"]
    assert repaired["new_right_side_pause"]
    assert not repaired["blocks"]
    args["geometry"] = {}
    args["close"][-1] = 121
    args["high"][-1] = 122
    reclaimed = structure_risk(**args)
    assert not reclaimed["new_right_side_pause"]
    assert reclaimed["reclaimed_last_high"]
    assert "unrepaired_lower_highs_and_lower_lows" not in reclaimed["blocks"]


def test_normal_reclaimed_shakeout_and_tiny_lower_lows_are_not_damage():
    args = risk_inputs()
    args["low"][-4:] = [98.98, 98.97, 98.96, 98.5]
    args["close"][-1] = 100.5
    evidence = structure_risk(**args)
    assert evidence["shakeout_reclaimed_today"]
    assert evidence["active_lower_low_run"] is None
    assert not evidence["blocks"]


def test_unconfirmed_future_swings_do_not_change_risk():
    args = descending_inputs()
    expected = structure_risk(**args)
    args["swings"].append({"kind": "high", "index": 96, "confirmed_index": 101})
    assert structure_risk(**args) == expected


def score_frame() -> pd.DataFrame:
    anchors = {0: 40, 180: 85, 230: 120, 245: 94, 260: 118, 270: 104,
               282: 117, 289: 111, 296: 116.2, 304: 116, 305: 116.5}
    close = np.interp(np.arange(306), list(anchors), list(anchors.values()))
    # Include genuinely negative, expanding-volume bars at multiple nominal
    # scales so the regression catches an accidental absolute-price threshold.
    close[291:296] = [114, 113.5, 114.5, 113.9, 115]
    volume = np.full(len(close), 2_000_000.0)
    volume[285:296] = [1_600_000, 1_700_000, 1_800_000, 1_900_000, 2_000_000,
                      2_100_000, 1_600_000, 2_000_000, 1_700_000, 2_200_000, 1_800_000]
    volume[296:] = 800_000
    return pd.DataFrame({
        "Date": pd.bdate_range("2024-01-02", periods=len(close)),
        "Open": close * 1.001, "High": close * 1.004, "Low": close * .999,
        "Close": close, "Volume": volume, "PriceMode": "adjusted",
    })


@pytest.mark.parametrize("scale", [.01, 10.0])
def test_nominal_price_scaling_preserves_score_state_and_distribution(scale):
    frame = score_frame()
    model = load_model()
    expected = score_setup(frame, model=model)
    scaled = frame.copy()
    scaled[["Open", "High", "Low", "Close"]] *= scale
    # Preserve dollar liquidity and all relative-volume ratios under the scale.
    scaled["Volume"] /= scale
    actual = score_setup(scaled, model=model)
    assert expected["eligible"] and actual["eligible"]
    for key in ["score", "quality_score", "status", "actionable", "candidate_eligible",
                "pattern", "distribution_days_20", "penalties", "components"]:
        assert actual[key] == expected[key], key
    assert actual["risk_evidence"]["blocks"] == expected["risk_evidence"]["blocks"]


def test_open_above_target_fills_before_later_intraday_stop():
    close = np.full(305, 100.0)
    frame = pd.DataFrame({
        "Date": pd.bdate_range("2024-01-02", periods=len(close)),
        "Open": close.copy(), "High": close + 1, "Low": close - 1,
        "Close": close, "Volume": 2_000_000.0, "PriceMode": "adjusted",
    })
    # Signal at 300, entry 301 at 100: stop 95, target 110. At 302 open
    # is already 112; that known opening fill precedes its later low of 90.
    frame.loc[302, ["Open", "High", "Low", "Close"]] = [112, 113, 90, 100]
    series = SetupSeries(frame)
    report = {
        "eligible": True, "status": "breakout_confirmed",
        "as_of_session": series.date(300), "invalidation_level": 95,
        "breakout": {"confirmed": True},
        "geometry": {"pattern_strength": 1., "pause_width": .05, "pivot": 100},
    }
    trade = simulate_pivot_trade(series, report, horizon=3, cost_bps=0)
    assert trade["status"] == "filled"
    assert trade["exit_reason"] == "gap_target"
    assert trade["exit_session"] == series.date(302)
    assert trade["exit"] == 112
    assert trade["net_return"] == pytest.approx(.12)
