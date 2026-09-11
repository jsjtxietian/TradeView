from __future__ import annotations

import json

import pandas as pd
import pytest

from trenddeck import analysis, queries, storage


def test_daily_changes_reports_coverage_and_never_writes(cached_data):
    root = cached_data["root"]
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    result = queries.get_daily_changes(limit=1)
    assert result["as_of_session"] == cached_data["date"]
    assert result["coverage"]["available"] == 2
    assert result["coverage"]["behind_reference"][0]["symbol"] == "OLDER"
    assert result["coverage"]["errors"][0]["symbol"] == "MISSING"
    first = result["items"][0]
    assert first["symbol"] == "NVDA" and first["isHolding"]
    assert first["dailyChangePct"] == pytest.approx(0.1)
    assert {"large_daily_move", "high_volume"}.issubset({e["type"] for e in first["changes"]})
    next_page = queries.get_daily_changes(limit=1, offset=result["next_offset"])
    assert next_page["items"][0]["symbol"] == "SPY"
    assert next_page["snapshot_id"] == result["snapshot_id"]
    assert before == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_daily_historical_cutoff_has_no_future_bars(cached_data):
    result = queries.get_daily_changes(cached_data["previous"], symbols=["NVDA"], include_unchanged=True)
    assert result["items"][0]["dailyChangePct"] < 0.01
    assert result["items"][0]["latestDate"] == cached_data["previous"]
    assert result["market"]["latestDate"] == cached_data["previous"]
    assert not any(e["type"] == "large_daily_move" for e in result["items"][0]["changes"])


def test_symbol_data_combines_analysis_and_history_without_watchlist_membership(cached_data):
    storage.WATCHLIST_FILE.write_text(
        json.dumps({"watchlist": [], "groups": []}), encoding="utf-8"
    )
    result = queries.get_symbol_data("nvda", history_limit=20)
    assert result["analysis"]["symbol"] == "NVDA"
    assert len(result["history"]["rows"]) == 20
    assert result["history"]["rows"][-1]["Date"] == cached_data["date"]
    assert result["history"]["next_before"] == result["history"]["rows"][0]["Date"]


def test_symbol_analysis_matches_web_calculations(cached_data):
    web = analysis.analyze_symbol("NVDA", allow_network=False)
    mcp = queries.get_symbol_analysis("nvda")
    for key in ("latestClose", "rsScore", "trendChecks", "advancedTrendChecks", "sellIndicatorGroups"):
        assert mcp["analysis"][key] == web[key]
    assert "history" not in mcp["analysis"]
    assert mcp["note"]["text"] == "A saved note"
    earlier = queries.get_symbol_analysis("NVDA", cached_data["previous"])
    assert earlier["analysis"]["dailyChangePct"] < 0.01
    assert earlier["benchmark_session"] == cached_data["previous"]


def test_history_pagination_is_complete_ordered_and_bounded(cached_data):
    first = queries.get_price_history("NVDA", limit=60)
    rows = list(first["rows"])
    page = first
    while page["has_more"]:
        page = queries.get_price_history("NVDA", before=page["next_before"], limit=60)
        assert len(page["rows"]) <= 60
        rows.extend(page["rows"])
    assert len(rows) == len({row["Date"] for row in rows}) == 280
    assert first["rows"] == sorted(first["rows"], key=lambda row: row["Date"])
    bounded = queries.get_price_history(
        "NVDA", start_date=cached_data["previous"], end_date=cached_data["date"]
    )
    assert len(bounded["rows"]) == 2
    assert bounded["rows"][-1]["MA200"] is not None  # compute MAs before slicing
    assert not queries.get_price_history("NVDA", before="1900-01-01")["rows"]


def test_disk_updates_visible_without_waiting_for_web_cache(cached_data):
    analysis.analyze_symbol("NVDA", allow_network=False)
    path = cached_data["cache"] / "NVDA_3y_history.csv"
    frame = pd.read_csv(path)
    frame.loc[frame.index[-1], "Close"] = 500
    frame.loc[frame.index[-1], "High"] = 510
    frame.to_csv(path, index=False)
    assert queries.get_symbol_analysis("NVDA")["analysis"]["latestClose"] == 500


def test_empty_watchlist_does_not_fall_back_to_default_symbols(cached_data):
    storage.WATCHLIST_FILE.write_text(json.dumps({"watchlist": [], "groups": []}), encoding="utf-8")
    storage.NOTES_FILE.write_text("{}", encoding="utf-8")
    result = queries.get_daily_changes()
    assert result["universe"] == [] and result["coverage"]["requested"] == 0


@pytest.mark.parametrize(
    "call",
    [
        lambda: queries.get_price_history("../../NVDA"),
        lambda: queries.get_price_history("NVDA", limit=501),
        lambda: queries.get_price_history("NVDA", start_date="2026-02-30"),
        lambda: queries.get_price_history("NVDA", start_date="2026-03-01", end_date="2026-01-01"),
        lambda: queries.get_daily_changes("2099-01-01"),
        lambda: queries.get_daily_changes(symbols=["NVDA"] * 201),
        lambda: queries.get_symbol_analysis("MISSING"),
    ],
)
def test_invalid_or_missing_inputs_do_not_trigger_downloads(cached_data, call):
    with pytest.raises(ValueError):
        call()
