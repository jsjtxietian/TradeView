from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from trenddeck import market, storage


@pytest.fixture
def cached_data(tmp_path, monkeypatch):
    cache = tmp_path / ".cache"
    trade = tmp_path / ".trade"
    cache.mkdir()
    trade.mkdir()
    monkeypatch.setattr(market, "CACHE_DIR", cache)
    monkeypatch.setattr(storage, "WATCHLIST_FILE", trade / "watchlist.json")
    monkeypatch.setattr(storage, "NOTES_FILE", trade / "notes.json")
    market._memory_cache.clear()

    def forbidden(*args, **kwargs):
        raise AssertionError("Read-only queries must never fetch or save market data")

    monkeypatch.setattr(market, "fetch_history_from_tiingo", forbidden)
    monkeypatch.setattr(market, "save_history_cache", forbidden)
    dates = pd.bdate_range("2025-01-02", periods=280)
    for symbol, step in [("SPY", 0.1), ("NVDA", 0.5), ("OLDER", 0.2)]:
        closes = 100 + np.arange(len(dates)) * step
        volume = np.full(len(dates), 1000.0)
        if symbol == "NVDA":
            closes[-1] = closes[-2] * 1.10
            volume[-1] = 2500
        frame = pd.DataFrame(
            {
                "Date": dates,
                "Open": closes * 0.99,
                "High": closes * 1.01,
                "Low": closes * 0.98,
                "Close": closes,
                "Volume": volume,
                "PriceMode": "adjusted",
            }
        )
        if symbol == "OLDER":
            frame = frame.iloc[:-2]
        frame.to_csv(cache / f"{symbol}_3y_history.csv", index=False)
    storage.WATCHLIST_FILE.write_text(
        json.dumps({"watchlist": ["NVDA", "SPY", "OLDER", "MISSING"], "groups": []}), encoding="utf-8"
    )
    storage.NOTES_FILE.write_text(
        json.dumps({"NVDA": {"text": "A saved note", "isHolding": True, "costBasis": 100, "shares": 10}}),
        encoding="utf-8",
    )
    yield {
        "root": tmp_path,
        "cache": cache,
        "date": dates[-1].strftime("%Y-%m-%d"),
        "previous": dates[-2].strftime("%Y-%m-%d"),
    }
    market._memory_cache.clear()
