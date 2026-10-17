import pandas as pd
import pytest

from trenddeck import market


@pytest.fixture
def history(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "STOCK_DIR", tmp_path)
    monkeypatch.setattr(market, "get_tiingo_api_key_candidates", lambda preferred=None: ["test"])
    market._memory_cache.clear()
    old = pd.DataFrame({
        "Date": pd.to_datetime(["2026-10-07", "2026-10-08"]),
        "Open": [182., 180.], "High": [183., 182.], "Low": [181., 179.],
        "Close": [182., 180.66], "Volume": [100., 200.], "PriceMode": "adjusted",
    })
    market.save_history_cache("DXJ", "3y", old)
    yield old
    market._memory_cache.clear()


def refresh():
    return market.load_history("DXJ", force_refresh=True, require_refresh_success=True,
                               respect_refresh_cooldown=False)


@pytest.mark.parametrize("factor,volume_factor", [(1/3, 3), (0.99, 1)])
def test_refresh_replaces_split_or_dividend_history(history, monkeypatch, factor, volume_factor):
    revised = history.copy()
    revised[["Open", "High", "Low", "Close"]] *= factor
    revised["Volume"] *= volume_factor
    new_bar = revised.tail(1).copy()
    new_bar["Date"] = pd.Timestamp("2026-10-09")
    new_bar[["Open", "High", "Low", "Close"]] *= 1.01
    expected = pd.concat([revised, new_bar], ignore_index=True)
    calls = []

    def fetch(symbol, period, start_date=None, **kwargs):
        calls.append(start_date)
        return expected.copy()

    monkeypatch.setattr(market, "fetch_history_from_tiingo", fetch)
    market.set_cached(("analysis", "DXJ", "SPY"), {"stale": True})
    result = refresh()
    assert pd.Timestamp(calls[0]) <= history.Date.min()
    assert result.Close.iloc[-1] / result.Close.iloc[-2] - 1 == pytest.approx(.01)
    assert result.Volume.iloc[0] == history.Volume.iloc[0] * volume_factor
    assert market.get_cached(("analysis", "DXJ", "SPY")) is None
    pd.testing.assert_frame_equal(market.load_history_cache("DXJ", "3y"), expected)
    # Repeating a refresh must not apply the adjustment a second time.
    pd.testing.assert_frame_equal(refresh(), result)


@pytest.mark.parametrize("response", ["partial", "raw", "empty", "error"])
def test_bad_rebuild_preserves_original_cache(history, monkeypatch, response):
    path = market.history_cache_path("DXJ", "3y")
    before = path.read_bytes()

    def fetch(*args, **kwargs):
        if response == "error":
            raise ValueError("provider unavailable")
        if response == "empty":
            return pd.DataFrame()
        if response == "partial":
            return history.tail(1).copy()
        return history.assign(PriceMode="raw")

    monkeypatch.setattr(market, "fetch_history_from_tiingo", fetch)
    with pytest.raises(ValueError):
        refresh()
    assert path.read_bytes() == before
