from __future__ import annotations

import json
import os

import pandas as pd
import pytest

from trenddeck import alerts, analysis, market, queries, storage


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


def test_symbol_report_explicit_refresh_without_watchlist_membership(cached_data, monkeypatch):
    storage.WATCHLIST_FILE.write_text(json.dumps({"watchlist": [], "groups": []}), encoding="utf-8")
    source = pd.read_csv(cached_data["cache"] / "NVDA_3y_history.csv")
    source["Date"] = pd.to_datetime(source["Date"])
    calls = []

    def fake_fetch(symbol, period, start_date=None, end_date=None, api_key=None):
        calls.append((symbol, period, start_date))
        return source.copy()

    def save_cache(symbol, period, frame):
        frame.to_csv(cached_data["cache"] / f"{symbol}_{period}_history.csv", index=False)

    monkeypatch.setattr(market, "fetch_history_from_tiingo", fake_fetch)
    monkeypatch.setattr(market, "save_history_cache", save_cache)
    monkeypatch.setattr(market, "get_tiingo_api_key_candidates", lambda preferred=None: ["test-key"])
    os.utime(cached_data["cache"] / "NVDA_3y_history.csv", (0, 0))

    result = queries.get_symbol_report("nvda", refresh=True, history_limit=20)
    assert result["cache"]["status"] == "refreshed"
    assert result["read_only"] is False
    assert result["analysis"]["symbol"] == "NVDA"
    assert len(result["history"]["rows"]) == 20
    assert result["history"]["rows"][-1]["Date"] == cached_data["date"]
    assert result["history"]["next_before"] == result["history"]["rows"][0]["Date"]
    next_date = (pd.Timestamp(cached_data["date"]) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    assert calls == [("NVDA", "3y", next_date)]

    fetched = queries.get_symbol_report("OUTSIDE", refresh=True, history_limit=5)
    assert fetched["cache"]["status"] == "fetched"
    assert fetched["analysis"]["symbol"] == "OUTSIDE"
    assert len(fetched["history"]["rows"]) == 5
    assert calls[-1] == ("OUTSIDE", "3y", None)

    cached = queries.get_symbol_report("OUTSIDE", history_limit=5)
    assert cached["cache"]["status"] == "cached"
    assert len(calls) == 2
    # An explicit refresh really contacts the provider, even immediately afterwards.
    refreshed = queries.get_symbol_report("OUTSIDE", refresh=True, history_limit=5)
    assert refreshed["cache"]["status"] == "refreshed"
    assert refreshed["refresh_status"] == "succeeded"
    assert len(calls) == 3
    assert storage.load_watchlist_state()["watchlist"] == []


def test_symbol_report_defaults_to_cache_only(cached_data):
    result = queries.get_symbol_report("NVDA", history_limit=5)
    assert result["cache"]["status"] == "cached"
    assert result["read_only"] is True
    assert result["refresh_status"] == "not_requested"
    assert result["analysis"]["symbol"] == "NVDA"


def test_symbol_report_surfaces_rate_limit_instead_of_using_stale_cache(cached_data, monkeypatch):
    monkeypatch.setattr(market, "get_tiingo_api_key_candidates", lambda preferred=None: ["test-key"])
    os.utime(cached_data["cache"] / "NVDA_3y_history.csv", (0, 0))

    def rate_limited(*args, **kwargs):
        raise market.MarketDataRateLimitError("Tiingo API 限流: HTTP 429，Retry-After: 60。")

    monkeypatch.setattr(market, "fetch_history_from_tiingo", rate_limited)
    with pytest.raises(ValueError, match="HTTP 429.*Retry-After: 60"):
        queries.get_symbol_report("NVDA", refresh=True)


def test_tiingo_429_reports_retry_after(monkeypatch):
    class Response:
        status_code = 429
        headers = {"Retry-After": "120"}

    class Session:
        def get(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(market, "get_session", lambda: Session())
    with pytest.raises(market.MarketDataRateLimitError, match="HTTP 429.*Retry-After: 120"):
        market.fetch_history_from_tiingo("NVDA", "3y", api_key="test-key")


def test_symbol_analysis_matches_web_calculations(cached_data):
    web = analysis.analyze_symbol("NVDA", allow_network=False)
    mcp = queries.get_symbol_report("nvda")
    for key in (
        "latestClose",
        "rsScore",
        "trendChecks",
        "advancedTrendChecks",
        "sellIndicatorGroups",
        "buyIndicatorGroupsByWindow",
        "sellIndicatorGroupsByWindow",
    ):
        assert mcp["analysis"][key] == web[key]
    assert "history" not in mcp["analysis"]
    assert mcp["note"]["text"] == "A saved note"
    earlier = queries.get_symbol_report("NVDA", as_of=cached_data["previous"])
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


def test_daily_latest_alert_batch_and_watchlist_scope(cached_data):
    entries = [
        {"symbol": "NVDA", "message": "older", "createdAt": "2026-09-01T08:00:00Z"},
        {"symbol": "NVDA", "message": "latest A", "createdAt": "2026-09-02T09:00:00Z"},
        {"symbol": "NVDA", "message": "latest B", "createdAt": "2026-09-02T17:00:00+08:00"},
        # String sorting puts this after the latest batch, but its instant is older.
        {"symbol": "NVDA", "message": "older offset", "createdAt": "2026-09-02T18:00:00+10:00"},
        {"symbol": "OLDER", "message": "stale stock alert", "createdAt": "2026-09-01T08:00:00Z"},
        {"symbol": "MISSING", "message": "missing stock alert", "createdAt": "2026-09-01T08:00:00Z"},
    ]
    alerts.ALERTS_FILE.write_text(json.dumps(entries), encoding="utf-8")
    result = queries.get_daily_changes()
    assert {a["message"] for a in result["items"][0]["latestAlerts"]} == {"latest A", "latest B"}
    assert result["items"][1]["latestAlerts"] == []
    assert result["coverage"]["behind_reference"][0]["latestAlerts"][0]["symbol"] == "OLDER"
    assert result["coverage"]["errors"][0]["latestAlerts"][0]["symbol"] == "MISSING"
    historical = queries.get_daily_changes(cached_data["previous"])
    assert historical["items"][0]["latestAlerts"] == result["items"][0]["latestAlerts"]
    assert queries.get_symbol_report("NVDA")["latestAlerts"] == result["items"][0]["latestAlerts"]
    storage.WATCHLIST_FILE.write_text(json.dumps({"watchlist": ["SPY"], "groups": []}), encoding="utf-8")
    assert queries.get_daily_changes()["universe"] == ["SPY"]  # NVDA is still marked held
    with pytest.raises(ValueError, match="current watchlist"):
        queries.get_daily_changes(symbols=["NVDA"])


def test_daily_includes_quiet_stocks_by_default(cached_data, monkeypatch):
    monkeypatch.setattr(queries, "session_changes", lambda current, previous: [])
    assert len(queries.get_daily_changes()["items"]) == 2
    assert queries.get_daily_changes(include_unchanged=False)["items"] == []


def test_report_read_only_history_pages_and_cutoff(cached_data):
    root = cached_data["root"]
    before_files = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    report = queries.get_symbol_report("NVDA", as_of=cached_data["previous"], history_limit=252)
    assert len(report["history"]["rows"]) == 252
    page = queries.get_symbol_report(
        "NVDA", as_of=cached_data["previous"], before=report["history"]["next_before"]
    )
    rows = page["history"]["rows"] + report["history"]["rows"]
    assert len(rows) == len({row["Date"] for row in rows}) == 279
    assert rows == sorted(rows, key=lambda row: row["Date"])
    assert report["analysis"] == page["analysis"]
    assert report["as_of_session"] == cached_data["previous"]
    with pytest.raises(ValueError, match="refresh=true"):
        queries.get_symbol_report("MISSING")
    assert before_files == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"symbol": "../NVDA"},
        {"symbol": "NVDA", "as_of": "invalid"},
        {"symbol": "NVDA", "before": "2026-02-30"},
        {"symbol": "NVDA", "history_limit": 501},
    ],
)
def test_report_validates_before_refresh(cached_data, monkeypatch, kwargs):
    def forbidden(*args, **kwargs):
        raise AssertionError("Invalid inputs must not trigger refresh")

    monkeypatch.setattr(market, "load_history", forbidden)
    with pytest.raises(ValueError):
        queries.get_symbol_report(refresh=True, **kwargs)


def test_existing_refresh_flow_still_respects_cooldown(cached_data):
    # The fixture forbids network and writes: the existing default must still reuse cache.
    frame = market.load_history("NVDA", force_refresh=True, allow_network=True)
    assert queries.latest_date(frame) == cached_data["date"]


def test_daily_empty_symbols_means_all_and_nonempty_filters(cached_data):
    full = queries.get_daily_changes()
    for symbols in (None, []):
        result = queries.get_daily_changes(symbols=symbols)
        assert result["universe"] == full["universe"]
        assert result["snapshot_id"] == full["snapshot_id"]
    filtered = queries.get_daily_changes(symbols=["nvda"])
    assert filtered["universe"] == ["NVDA"]
    assert [item["symbol"] for item in filtered["items"]] == ["NVDA"]
