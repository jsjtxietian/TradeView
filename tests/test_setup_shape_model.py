from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from trenddeck.setups.engine import SetupSeries, score_setup
from trenddeck.setups.model import load_model


def bars(anchors: dict[int, float]) -> pd.DataFrame:
    c = np.interp(np.arange(max(anchors) + 1), list(anchors), list(anchors.values()))
    return pd.DataFrame({
        "Date": pd.bdate_range("2024-01-02", periods=len(c)),
        "Open": c * .999, "High": c * 1.003, "Low": c * .997, "Close": c,
        "Volume": np.full(len(c), 1_000_000.0), "PriceMode": "adjusted",
    })


def vcp() -> pd.DataFrame:
    f = bars({0: 40, 180: 85, 230: 120, 245: 94, 260: 118, 270: 104,
              282: 117, 289: 111, 296: 116.2, 304: 116, 305: 116.5})
    f.loc[285:295, "Volume"] = 650_000
    f.loc[296:, "Volume"] = 300_000
    return f


def test_shape_weights_preserve_relative_evidence_without_entry_timing_points():
    model = load_model()
    assert model.weights["location"] == model.weights["risk"] == 0
    assert sum(model.weights.values()) == pytest.approx(100)
    assert model.weights["structure"] / model.weights["trend"] == pytest.approx(25 / 20)
    assert model.weights["tightness"] == model.weights["volume"]


def test_quiet_tail_cannot_borrow_old_advance_to_become_a_flat_base():
    f = bars({0: 40, 230: 80, 250: 120, 260: 85, 275: 90, 305: 90})
    report = score_setup(f)
    assert report["pattern"] == "none" and report["status"] == "no_base"
    assert 0 < report["score"] <= 49
    assert not report["candidate_eligible"]


def test_completed_old_vcp_does_not_score_a_new_micro_pause():
    f = bars({0: 40, 180: 85, 230: 120, 245: 94, 260: 118, 270: 104,
              282: 117, 289: 111, 300: 116, 310: 129, 320: 130, 329: 129})
    report = score_setup(f)
    assert report["pattern"] == "none" and not report["candidate_eligible"]
    assert any(base["breakout_index"] is not None for base in report["base_context"]["catalogue"])


def test_local_base_owns_its_actual_recent_range_and_is_not_called_flat():
    f = bars({0: 40, 180: 65, 240: 85, 270: 110, 280: 155,
              285: 150, 290: 154, 295: 151, 299: 153})
    g = SetupSeries(f)._geometry(299)
    assert g["pattern"] == "short_base" and g["base_bars"] < 25
    assert g["base_start"] < g["base_known_session"] < str(f.Date.iloc[299])[:10]
    assert g["base_start"] >= str(f.Date.iloc[275])[:10]
    window = f.loc[(f.Date >= g["base_start"]) & (f.Date < f.Date.iloc[299])]
    assert g["base_depth"] == pytest.approx(1 - window.Low.min() / window.High.max())
    assert g["base_low_price"] == window.Low.min()


def test_exact_breakout_volume_changes_confirmation_not_shape_score():
    model = load_model()
    f = bars({0: 40, 259: 98,
              **dict(enumerate([100, 97, 94, 96, 98, 96, 97, 99, 98, 99, 99], start=260))})
    signal = len(f) - 1
    pivot = score_setup(f, model=model)["geometry"]["pivot"]
    f.loc[signal, ["Open", "Low", "High", "Close", "Volume"]] = [
        pivot * .999, pivot * .997, pivot * 1.013, pivot * 1.01, 3_000_000,
    ]
    confirmed = score_setup(f, model=model)
    f.loc[signal, "Volume"] = 200_000
    unconfirmed = score_setup(f, model=model)
    assert confirmed["status"] == "breakout_confirmed"
    assert unconfirmed["status"] == "breakout_unconfirmed"
    assert confirmed["score"] == unconfirmed["score"]
    assert unconfirmed["score"] == unconfirmed["quality_score"]


def test_early_repair_requires_observed_higher_highs_lows_and_reclaim():
    f = vcp()
    series = SetupSeries(f)
    i = 305
    # Isolate the trend classifier from pattern recognition. All inputs refer to
    # already observed bars; a merely rising long average is insufficient.
    series.swings = [{"kind": kind, "index": index, "confirmed_index": index + 1}
                     for kind, index in [("high", 280), ("low", 285), ("high", 295), ("low", 300)]]
    series.h[280], series.h[295] = 110, 114
    series.low[285], series.low[300] = 100, 110
    assert series._repairing_trend(i)
    series.low[300] = 98
    assert not series._repairing_trend(i)
    series.low[300] = 110
    series.h[295] = 120
    assert not series._repairing_trend(i)


def test_shape_diagnostics_and_score_are_prefix_invariant():
    model = load_model()
    f = vcp()
    expected = score_setup(f, model=model)
    future = f.tail(20).copy()
    future.Date = pd.bdate_range(f.Date.iloc[-1] + pd.Timedelta(days=1), periods=20)
    future[["Open", "High", "Low", "Close"]] *= 10
    extended = pd.concat([f, future], ignore_index=True)
    assert SetupSeries(extended, model=model).score(305) == expected
    assert score_setup(extended, as_of=str(f.Date.iloc[-1])[:10], model=model) == expected
    json.dumps(expected, allow_nan=False)
