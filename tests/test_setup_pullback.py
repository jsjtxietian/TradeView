from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trenddeck.setups.pullback import PullbackSeries
from trenddeck.setups.queries import get_setup_rankings, get_setup_report


def frame(tail=None):
    base = np.tile([127, 125, 123, 125, 127, 128, 126, 124, 126, 127], 6) + np.arange(60) * 0.015
    c = np.r_[
        np.linspace(80, 140, 260),
        np.linspace(140, 100, 20),
        np.linspace(100, 128, 15),
        base,
        np.linspace(134, 149, 15) if tail is None else tail,
    ]
    return pd.DataFrame(
        {
            "Date": pd.bdate_range("2020-01-01", periods=len(c)),
            "Open": c * 0.999,
            "High": c * 1.005,
            "Low": c * 0.995,
            "Close": c,
            "Volume": 1e6,
            "PriceMode": "adjusted",
        }
    )


def test_recognition_precedes_breakout_and_freezes_boundaries():
    series = PullbackSeries(frame())
    series.report()
    breaks = [r for r in series.timeline if r["status"] == "breakout"]
    assert breaks
    b = breaks[0]["base"]
    assert b["ordinal"] == 1
    assert b["start_session"] <= b["range_known_session"] <= b["known_session"] < b["breakout_session"]
    observations = [r["base"] for r in series.timeline if r["base"] and r["base"]["base_id"] == b["base_id"]]
    assert len({(x["upper"], x["lower"]) for x in observations}) == 1
    assert all(r["score"] is None for r in series.timeline)


def test_full_history_and_prefix_reports_match_including_first_identity():
    f = frame()
    series = PullbackSeries(f)
    series.report()
    for i in [275, 300, 325, 345, 354, 355, 356, len(f) - 1]:
        assert series.report(i) == PullbackSeries(f.iloc[: i + 1]).report()


def test_downtrend_pauses_are_not_qualified_and_failure_is_not_erased():
    f = frame(np.linspace(120, 90, 20))
    s = PullbackSeries(f)
    result = s.report()
    assert result["cycle"]["attempts"]
    first = result["cycle"]["attempts"][0]
    assert first["ordinal"] == 1 and first["invalidated_session"]
    assert first["breakout_session"] is None
    assert not result["actionable"]
    assert all("ma50_not_falling" not in r["reasons"] for r in result["cycle"]["rejected"])
    for r in s.timeline:
        if r["actionable"]:
            assert all(r["trend_checks"].values())


def test_local_repair_can_qualify_while_ma50_is_still_falling():
    s = PullbackSeries(frame())
    s.report()
    first = next(r for r in s.timeline if r["base"])
    assert all(first["trend_checks"].values())
    assert not first["trend_context"]["ma50_not_falling"]
    assert "ma50_not_falling" not in first["trend_checks"]
    # MA50 is diagnostic: changing it cannot change the local confirmation gate.
    i = next(i for i, r in enumerate(s.timeline) if r["base"])
    before = s._trend(i)
    s.s.ma[50][i] = s.s.c[i] * 2
    assert s._trend(i) == before


@pytest.mark.parametrize("rising", [True, False])
def test_below_ma200_blocks_repair_even_when_average_is_rising(rising):
    s = PullbackSeries(frame())
    values = np.linspace(200, 250, len(s.s.c))
    s.s.ma[200][:] = values if rising else values[::-1]
    result = s.report()
    assert result["base"] is None
    assert not any(r["actionable"] for r in s.timeline)
    assert not result["trend_checks"]["above_ma200"]
    assert any("above_ma200" in r["reasons"] for r in result["cycle"]["rejected"])


def test_losing_ma200_disables_actionability_without_erasing_base():
    s = PullbackSeries(frame())
    s.report()
    i = next(
        i
        for i, r in enumerate(s.timeline)
        if r["actionable"] and r["as_of_session"] > r["base"]["known_session"]
    )
    prior = s.report(i)
    s.s.ma[200][i] = s.s.c[i] * 1.01
    s.timeline = []
    current = s.report(i)
    assert not current["actionable"]
    assert current["base"]["base_id"] == prior["base"]["base_id"]
    assert any("MA200" in reason for reason in current["reasons"])


def test_no_retroactive_base_when_jump_precedes_repair():
    f = frame().iloc[:295].copy()
    extra = frame().iloc[295:300].copy()
    for col, factor in [("Open", 1), ("High", 1.005), ("Low", 0.995), ("Close", 1)]:
        extra[col] = np.arange(150, 155) * factor
    result = PullbackSeries(pd.concat([f, extra], ignore_index=True)).report()
    assert result["base"] is None
    assert not result["actionable"]


def test_pullback_api_does_not_treat_null_score_as_insufficient(cached_data):
    r = get_setup_report("NVDA", setup_type="pullback")
    assert r["score"] is None and r["status"] == "no_pullback"
    rankings = get_setup_rankings(symbols=["NVDA", "SPY"], setup_type="pullback", include_details=True)
    assert len(rankings["items"]) == 2
    assert rankings["ordering"] == "status_then_symbol_not_quality_score"
    assert all("percentile" not in row["details"] for row in rankings["items"])
    assert rankings["conditions_markdown"] == r["conditions_markdown"]
    assert get_setup_rankings(symbols=["NVDA"], setup_type="pullback", actionable_only=True)["items"] == []
    with pytest.raises(ValueError, match="setup_type"):
        get_setup_report("NVDA", setup_type="typo")


def test_http_pullback_and_invalid_type(cached_data):
    from fastapi.testclient import TestClient

    from app import app

    client = TestClient(app)
    try:
        report = client.get("/api/setups/report/NVDA", params={"setup_type": "pullback"}).json()
        assert report == get_setup_report("NVDA", setup_type="pullback")
        ranks = client.get(
            "/api/setups/rankings", params={"setup_type": "pullback", "symbols": "NVDA"}
        ).json()
        assert ranks["items"][0]["score"] is None
        assert client.get("/api/setups/report/NVDA", params={"setup_type": "typo"}).status_code == 400
    finally:
        client.close()
