from __future__ import annotations

import base64
from io import BytesIO

import numpy as np
import pandas as pd
import pytest
from PIL import Image

from trenddeck.setups.queries import get_setup_report


@pytest.mark.parametrize("setup_type", ["base", "pullback"])
def test_chart_matches_report_and_ignores_future_bars(cached_data, setup_type):
    if setup_type == "base":
        c = np.r_[np.linspace(40, 98, 260), [100, 97, 94, 96, 98, 96, 97, 99, 98, 99, 103, 104, 105]]
    else:
        c = np.r_[
            np.linspace(80, 140, 260),
            np.linspace(140, 100, 20),
            np.linspace(100, 128, 15),
            np.tile([127, 125, 123, 125, 127, 128, 126, 124, 126, 127], 6),
            [134, 135],
        ]
    frame = pd.DataFrame(
        {
            "Date": pd.bdate_range("2023-01-02", periods=len(c)),
            "Open": c,
            "High": c * 1.003,
            "Low": c * 0.997,
            "Close": c,
            "Volume": 1e6,
            "PriceMode": "adjusted",
        }
    )
    path = cached_data["cache"] / "CHART_3y_history.csv"
    frame.to_csv(path, index=False)
    day = frame.Date.iloc[-1].strftime("%Y-%m-%d")
    plain = get_setup_report("CHART", day, setup_type=setup_type, include_details=True)
    plotted = get_setup_report("CHART", day, setup_type=setup_type, include_details=True, include_chart=True)
    chart = plotted.pop("chart")
    assert plotted == plain
    assert chart["base"] is not None
    evidence = plain["details"]["base"] if setup_type == "pullback" else plain["details"]["geometry"]
    assert chart["base"]["base_id"] == evidence["base_id"]
    assert chart["base"]["breakout_session"] == evidence["breakout_session"]
    assert chart["base"]["upper"] == evidence["upper" if setup_type == "pullback" else "pivot"]
    assert chart["as_of_session"] == chart["display_end_session"] == day
    with Image.open(BytesIO(base64.b64decode(chart["data"]))) as image:
        assert image.format == "PNG"
        assert image.size == (chart["width"], chart["height"])
        image.verify()
    # Future prices must not alter either the historical evidence or the raster.
    future = frame.iloc[-1:].copy()
    future["Date"] += pd.Timedelta(days=3)
    future[["Open", "High", "Low", "Close"]] *= 3
    pd.concat([frame, future]).to_csv(path, index=False)
    replay = get_setup_report("CHART", day, setup_type=setup_type, include_details=True, include_chart=True)
    assert replay == {**plain, "chart": chart}


@pytest.mark.parametrize("setup_type", ["base", "pullback"])
def test_no_base_and_short_history_do_not_fabricate_a_box(cached_data, setup_type):
    for cutoff in [None, "2025-01-03"]:
        report = get_setup_report("NVDA", cutoff, setup_type=setup_type, include_chart=True)
        assert report["chart"]["base"] is None
        assert report["chart"]["display_end_session"] == report["as_of_session"]
        if cutoff:
            assert report["status"] == "insufficient_history"


def test_http_chart_is_opt_in(cached_data):
    from fastapi.testclient import TestClient

    from app import app

    client = TestClient(app)
    try:
        plain = client.get("/api/setups/report/NVDA").json()
        assert "chart" not in plain
        result = client.get("/api/setups/report/NVDA", params={"include_chart": True}).json()
        chart = result.pop("chart")
        assert result == plain
        assert chart["mimeType"] == "image/png"
        assert base64.b64decode(chart["data"]).startswith(b"\x89PNG")
        assert client.get("/api/setups/report/MISSING", params={"include_chart": True}).status_code == 400
    finally:
        client.close()
