from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from trenddeck.setups import queries as setup_queries
from trenddeck.setups.engine import SetupSeries, confirmed_swings, score_setup
from trenddeck.setups.model import load_model
from trenddeck.setups.research import forward_outcome, simulate_pivot_trade


def pattern_frame() -> tuple[pd.DataFrame, pd.DataFrame]:
    anchors = {
        0: 40,
        180: 85,
        230: 120,
        245: 94,
        260: 118,
        270: 104,
        282: 117,
        289: 111,
        296: 116.2,
        304: 116,
        305: 116.5,
    }
    c = np.interp(np.arange(306), list(anchors), list(anchors.values()))
    f = pd.DataFrame(
        {
            "Date": pd.bdate_range("2024-01-02", periods=len(c)),
            "Open": c * 0.999,
            "High": c * 1.003,
            "Low": c * 0.997,
            "Close": c,
            "Volume": np.full(len(c), 1_000_000.0),
            "PriceMode": "adjusted",
        }
    )
    f.loc[285:295, "Volume"] = 650_000
    f.loc[296:, "Volume"] = 300_000
    b = f.copy()
    for key in ["Open", "High", "Low", "Close"]:
        b[key] = np.linspace(100, 112, len(c)) * {"Open": 0.999, "High": 1.003, "Low": 0.997, "Close": 1}[key]
    return f, b


def breakout_frame() -> tuple[pd.DataFrame, pd.DataFrame]:
    # A recognized short base with enough room for a first breakout before the
    # independent MA20/own-volatility extension check becomes relevant.
    f, b = pattern_frame()
    close = np.r_[np.linspace(40, 98, 260), [100, 97, 94, 96, 98, 96, 97, 99, 98, 99, 99]]
    f, b = f.iloc[:len(close)].copy(), b.iloc[:len(close)].copy()
    for key, multiplier in [("Open", 1), ("High", 1.003), ("Low", .997), ("Close", 1)]:
        f[key] = close * multiplier
    return f, b


def test_historical_score_is_prefix_invariant_and_pivots_have_confirmation_dates():
    f, b = pattern_frame()
    expected = score_setup(f, b)
    future = f.tail(20).copy()
    future.Date = pd.bdate_range(f.Date.iloc[-1] + pd.Timedelta(days=1), periods=20)
    future[["Open", "High", "Low", "Close"]] *= 10
    extended = pd.concat([f, future], ignore_index=True)
    actual = score_setup(extended, b, f.Date.iloc[-1].strftime("%Y-%m-%d"))
    assert actual == expected
    assert SetupSeries(extended, b).score(len(f) - 1) == expected
    assert len(expected["geometry"]["contractions"]) >= 2
    for leg in expected["geometry"]["contractions"]:
        assert leg["peak_date"] < leg["low_date"] < leg["confirmed_at"]
        assert leg["confirmed_at"] <= expected["setup_as_of_session"]
    json.dumps(expected, allow_nan=False)


def test_signal_bar_does_not_move_pivot_and_breakout_requires_volume():
    f, b = breakout_frame()
    before = score_setup(f, b, model=load_model())
    pivot = before["geometry"]["pivot"]
    f.loc[len(f) - 1, ["Open", "Low", "High", "Close", "Volume"]] = [
        pivot * 0.999,
        pivot * 0.997,
        pivot * 1.013,
        pivot * 1.01,
        3_000_000,
    ]
    breakout = score_setup(f, b, model=load_model())
    assert breakout["geometry"]["pivot"] == pivot
    assert breakout["status"] == "breakout_confirmed"
    assert breakout["actionable"]
    f.loc[len(f) - 1, "Volume"] = 200_000
    assert score_setup(f, b, model=load_model())["status"] == "breakout_unconfirmed"
    f.loc[len(f) - 1, ["Open", "Low", "High", "Close"]] = [
        pivot * 1.09,
        pivot * 1.08,
        pivot * 1.12,
        pivot * 1.11,
    ]
    extended = score_setup(f, b, model=load_model())
    assert extended["status"] == "extended" and not extended["actionable"]


def test_trend_breakdown_volume_and_price_mode_are_not_buyable():
    f, b = pattern_frame()
    f.loc[len(f) - 1, ["Open", "High", "Low", "Close"]] = [50, 51, 49, 50]
    r = score_setup(f, b)
    assert r["status"] == "outside_stage2" and not r["eligible"] and r["score"] <= 39
    f, b = pattern_frame()
    f.loc[len(f) - 3, "Volume"] = 0
    assert not score_setup(f, b)["eligible"]
    f, b = pattern_frame()
    f["PriceMode"] = "raw"
    assert not score_setup(f, b)["eligible"]


def test_no_future_confirmation_and_bad_rows_rejected():
    c = np.array([100, 110, 105, 103, 106, 114.0])
    swings = confirmed_swings(c, np.full(len(c), 0.05))
    assert swings[0] == {"kind": "high", "index": 1, "confirmed_index": 3}
    assert swings[1] == {"kind": "low", "index": 3, "confirmed_index": 5}
    f, b = pattern_frame()
    assert score_setup(f.head(100), b)["score"] is None
    bad = f.copy()
    bad.loc[20, "High"] = 1
    with pytest.raises(ValueError, match="Inconsistent"):
        score_setup(bad, b)
    with pytest.raises(ValueError, match="duplicate"):
        score_setup(pd.concat([f, f.tail(1)]), b)


def test_gap_evidence_is_present_without_a_valid_base():
    f, b = pattern_frame()
    n = len(f) - 1
    p = f.Close.iloc[-2] * 1.1
    f.loc[n, ["Open", "High", "Low", "Close", "Volume"]] = [p, p * 1.04, p * 0.99, p * 1.035, 5_000_000]
    r = score_setup(f, b)
    assert r["gap_events"][-1]["cause"] == "unknown"
    assert r["gap_events"][-1]["date"] == r["as_of_session"]


def test_rankings_exclude_stale_missing_and_honor_cutoff(cached_data):
    r = setup_queries.get_setup_rankings(symbols=["NVDA", "OLDER", "MISSING"])
    assert r["coverage"]["scored"] == 1
    assert {x["reason"] for x in r["coverage"]["excluded"]} == {"stale", "missing"}
    assert r["as_of_session"] == cached_data["date"]
    assert r["items"][0]["symbol"] == "NVDA"
    earlier = setup_queries.get_setup_report("NVDA", cached_data["previous"])
    assert earlier["as_of_session"] == cached_data["previous"]
    assert earlier["reference_session"] == cached_data["previous"]
    with pytest.raises(ValueError):
        setup_queries.get_setup_report("../NVDA")
    with pytest.raises(ValueError):
        setup_queries.get_setup_rankings(universe="future_winners")


def test_forward_returns_start_at_next_open_and_apply_costs():
    f, _ = pattern_frame()
    s = SetupSeries(f)
    entry, exit_date = s.date(290), s.date(300)
    result = forward_outcome(s, entry, exit_date, 10)
    assert result["net_return"] == pytest.approx(f.Close.iloc[300] * 0.999 / (f.Open.iloc[290] * 1.001) - 1)
    assert forward_outcome(s, entry, "2030-01-01", 10) is None


def test_http_endpoints_and_backtest_validation(cached_data):
    from app import app

    # These synchronous HTTP routes need no lifespan. MCP's manager is single-use
    # and the real-HTTP tests already own its lifespan in this pytest process.
    client = TestClient(app)
    try:
        r = client.get("/api/setups/rankings", params={"symbols": ["NVDA"]})
        assert r.status_code == 200 and r.json()["read_only"]
        assert client.get("/api/setups/report/NVDA").status_code == 200
        assert client.get("/api/setups/report/MISSING").status_code == 400
        assert (
            client.get(
                "/api/setups/backtest", params={"start": "2025-01-01", "end": "2026-02-01", "horizon": 0}
            ).status_code
            == 422
        )
    finally:
        client.close()
    r = setup_queries.get_setup_backtest(
        "2025-12-01",
        "2026-02-01",
        symbols=["NVDA"],
        horizon=5,
        step=5,
        top_n=1,
        holdout_start="2026-01-01",
        include_event_records=True,
    )
    assert r["read_only"] and "holdout" in r["summary"]
    assert r["pivot_events"]["label_version"] == "pivot-events-v1"
    assert all(
        e.get("label_end_session", e["signal_session"]) < "2026-01-01"
        for e in r["pivot_events"]["records"]
        if e["phase"] == "development"
    )
    assert all(c["exit_session"] < "2026-01-01" for c in r["cohorts"] if c["phase"] == "development")
    json.dumps(r, allow_nan=False)


def test_model_version_is_explicit_validated_and_hash_changes(tmp_path, monkeypatch):
    from trenddeck.setups import model as setup_model

    original = load_model()
    data = json.loads((setup_model.MODEL_DIR / "base-v4.json").read_text())
    data["version"] = "base-test"
    data["parameters"]["breakout_volume_ratio"] = 2.5
    path = tmp_path / "base-test.json"
    path.write_text(json.dumps(data))
    monkeypatch.setattr(setup_model, "MODEL_DIR", tmp_path)
    modified = load_model("base-test")
    assert modified.config_hash != original.config_hash
    f, b = pattern_frame()
    assert score_setup(f, b, model=modified)["model_version"] == "base-test"
    with pytest.raises(ValueError):
        load_model("../base-v4")
    # Schema 3 accepts relative weights, so totals other than 100 normalize.
    data["weights"]["trend"] = 300
    path.write_text(json.dumps(data))
    weighted = load_model("base-test")
    assert sum(weighted.weights.values()) == pytest.approx(100)
    assert weighted.config_hash != modified.config_hash
    data["weights"] = {key: 0 for key in data["weights"]}
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="positive sum"):
        load_model("base-test")


@pytest.mark.parametrize("schema", [1, 2])
def test_model_loader_rejects_retired_configuration_schemas(tmp_path, monkeypatch, schema):
    from trenddeck.setups import model as setup_model

    data = json.loads((setup_model.MODEL_DIR / "base-v4.json").read_text(encoding="utf-8"))
    data.update(version="unsupported-schema", schema_version=schema)
    (tmp_path / "unsupported-schema.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(setup_model, "MODEL_DIR", tmp_path)
    with pytest.raises(ValueError, match="schema mismatch"):
        load_model("unsupported-schema")


def test_pivot_trade_waits_for_confirmation_and_handles_stop_before_target():
    f, b = breakout_frame()
    report = score_setup(f, b, model=load_model())
    pivot = report["geometry"]["pivot"]
    f.loc[len(f) - 1, ["Open", "Low", "High", "Close", "Volume"]] = [
        pivot,
        pivot * 0.997,
        pivot * 1.013,
        pivot * 1.01,
        3_000_000,
    ]
    report = score_setup(f, b, model=load_model())
    future = f.tail(5).copy()
    future.Date = pd.bdate_range(f.Date.iloc[-1] + pd.Timedelta(days=1), periods=5)
    entry = pivot * 1.01
    future[["Open", "High", "Low", "Close"]] = [entry, entry * 1.20, pivot * 0.90, entry]
    s = SetupSeries(pd.concat([f, future], ignore_index=True), b, load_model())
    trade = simulate_pivot_trade(s, report, horizon=5)
    assert trade["status"] == "filled" and trade["exit_reason"] == "stop"
    assert trade["entry_session"] > report["as_of_session"]
    assert trade["net_return"] < 0
    future[["Open", "High", "Low", "Close"]] = [pivot * 1.15, pivot * 1.17, pivot * 1.14, pivot * 1.16]
    s = SetupSeries(pd.concat([f, future], ignore_index=True), b, load_model())
    assert simulate_pivot_trade(s, report)["status"] == "gap_too_extended"


def test_prior_advance_uses_relative_scale_without_excluding_low_volatility():
    f, b = pattern_frame()
    s = SetupSeries(f, b)
    original = s.prior_advance(230)
    # Compress percentage moves and candle ranges: a meaningful advance relative
    # to this instrument's own range still counts, with no ETF blacklist.
    for key in ["Open", "High", "Low", "Close"]:
        f[key] = 100 + (f[key] - 40) * 0.1
    quiet = SetupSeries(f, b).prior_advance(230)
    assert quiet["prior_advance_return"] < 0.10
    assert quiet["prior_advance_quality"] >= 0.5
    assert original["prior_advance_quality"] >= 0.5






def test_price_mode_from_future_does_not_change_past_score():
    f, b = pattern_frame()
    expected = SetupSeries(f, b).score(280)
    f.loc[281:, "PriceMode"] = "raw"
    assert SetupSeries(f, b).score(280) == expected
