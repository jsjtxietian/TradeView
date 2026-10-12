from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from trenddeck import alerts, market, queries, storage


def test_membership_persistence_idempotency_and_preserved_data(cached_data):
    state = storage.load_watchlist_state()
    state["groups"] = [
        {"id": "AI", "name": "AI", "symbols": ["NVDA", "SPY", "BRK-B"]},
        {"id": "TECH", "name": "Tech", "symbols": ["NVDA"]},
    ]
    storage.save_watchlist_state(state)
    alerts.write_alert_snapshot({"NVDA": {"latestClose": 100}, "SPY": {"latestClose": 200}})
    alerts.write_alert_log([
        {"symbol": "NVDA", "message": "Saved", "createdAt": "2026-01-01T00:00:00Z"}
    ])
    preserved = {
        path: path.read_bytes()
        for path in cached_data["root"].rglob("*")
        if path.is_file() and path.name not in {"watchlist.json", "alerts_snapshot.json"}
    }
    added = queries.add_watchlist_symbol(" brk.b ")
    assert added["changed"] and added["is_in_watchlist"]
    assert added["symbol"] == "BRK-B"
    assert added["watchlist"] == ["NVDA", "SPY", "OLDER", "MISSING", "BRK-B"]
    assert all("BRK-B" not in group["symbols"] for group in added["groups"])
    assert not queries.add_watchlist_symbol("BRK-B")["changed"]
    removed = queries.remove_watchlist_symbol(" nvda ")
    assert removed["changed"] and not removed["is_in_watchlist"]
    assert all("NVDA" not in group["symbols"] for group in removed["groups"])
    assert storage.load_watchlist_state()["watchlist"] == removed["watchlist"]
    assert alerts.load_alert_snapshot() == {"SPY": {"latestClose": 200}}
    assert not queries.remove_watchlist_symbol("NVDA")["changed"]
    assert not queries.remove_watchlist_symbol("ABSENT")["changed"]
    assert all(path.read_bytes() == content for path, content in preserved.items())


def test_default_state_and_empty_watchlist(cached_data):
    storage.WATCHLIST_FILE.unlink()
    assert storage.load_configured_watchlist_symbols() == storage.DEFAULT_WATCHLIST
    result = queries.add_watchlist_symbol("EXTRA")
    assert result["watchlist"] == [*storage.DEFAULT_WATCHLIST, "EXTRA"]
    assert result["groups"] == storage.normalize_watchlist_state(
        {"watchlist": [], "groups": storage.DEFAULT_WATCHLIST_GROUPS}
    )["groups"]
    for symbol in result["watchlist"]:
        queries.remove_watchlist_symbol(symbol)
    assert storage.load_watchlist_state()["watchlist"] == []
    assert storage.load_configured_watchlist_symbols() == []
    assert queries.get_daily_changes()["universe"] == []
    assert queries.add_watchlist_symbol("NVDA")["watchlist"] == ["NVDA"]


def test_concurrent_membership_updates_do_not_lose_symbols(cached_data):
    symbols = [f"TEST{i}" for i in range(25)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(queries.add_watchlist_symbol, symbols))
    assert all(result["changed"] for result in results)
    assert set(storage.load_watchlist_state()["watchlist"]) == {
        "NVDA", "SPY", "OLDER", "MISSING", *symbols
    }
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(queries.remove_watchlist_symbol, symbols))
    assert storage.load_watchlist_state()["watchlist"] == ["NVDA", "SPY", "OLDER", "MISSING"]


@pytest.mark.parametrize("operation", [queries.add_watchlist_symbol, queries.remove_watchlist_symbol])
def test_invalid_symbols_and_broken_storage_never_overwrite(cached_data, operation):
    original = storage.WATCHLIST_FILE.read_bytes()
    for symbol in ["", "../NVDA", "A" * 33]:
        with pytest.raises(ValueError, match="stock symbol"):
            operation(symbol)
    assert storage.WATCHLIST_FILE.read_bytes() == original
    storage.WATCHLIST_FILE.write_text("broken json", encoding="utf-8")
    with pytest.raises(ValueError, match="自选股文件读取失败"):
        operation("NVDA")
    assert storage.WATCHLIST_FILE.read_text(encoding="utf-8") == "broken json"


def test_group_only_reference_is_removed(cached_data):
    storage.WATCHLIST_FILE.write_text(json.dumps({
        "watchlist": [], "groups": [{"id": "AI", "name": "AI", "symbols": ["NVDA"]}]
    }), encoding="utf-8")
    result = queries.remove_watchlist_symbol("NVDA")
    assert result["changed"]
    assert storage.load_watchlist_state()["groups"][0]["symbols"] == []


def test_group_assignment_and_moving_existing_membership(cached_data):
    state = storage.load_watchlist_state()
    state["groups"] = [
        {"id": "AI", "name": "AI 区", "symbols": ["NVDA", "SPY"]},
        {"id": "TECH", "name": "科技区", "symbols": []},
    ]
    storage.save_watchlist_state(state)
    added = queries.add_watchlist_symbol("brk.b", group_id=" ai ")
    assert added["changed"]
    assert added["groups"][0]["symbols"] == ["NVDA", "SPY", "BRK-B"]
    assert not queries.add_watchlist_symbol("BRK-B", group_id="AI")["changed"]
    assert not queries.add_watchlist_symbol("NVDA", group_id="AI")["changed"]
    assert not queries.add_watchlist_symbol("BRK-B")["changed"]
    moved = queries.add_watchlist_symbol("NVDA", group_id="TECH")
    assert moved["changed"]
    assert moved["watchlist"] == added["watchlist"]
    assert moved["groups"][0]["symbols"] == ["SPY", "BRK-B"]
    assert moved["groups"][1]["symbols"] == ["NVDA"]
    assert not queries.add_watchlist_symbol("NVDA", group_id="TECH")["changed"]
    assert storage.load_watchlist_state()["groups"] == moved["groups"]


@pytest.mark.parametrize("group_id", ["MISSING", "", " "])
def test_unknown_group_does_not_add_or_move_stock(cached_data, group_id):
    before = storage.WATCHLIST_FILE.read_bytes()
    for symbol in ["EXTRA", "NVDA"]:
        with pytest.raises(ValueError, match="分组不存在"):
            queries.add_watchlist_symbol(symbol, group_id=group_id)
    assert storage.WATCHLIST_FILE.read_bytes() == before


def test_get_watchlist_is_independent_of_prices_and_never_writes(cached_data, monkeypatch):
    state = storage.load_watchlist_state()
    state["groups"] = [{"id": "AI", "name": "AI 区", "symbols": ["NVDA"]}]
    storage.save_watchlist_state(state)

    def forbidden(*args, **kwargs):
        raise AssertionError("get_watchlist must not read or fetch prices")

    monkeypatch.setattr(market, "load_history_cache", forbidden)
    before = {p: p.read_bytes() for p in cached_data["root"].rglob("*") if p.is_file()}
    result = queries.get_watchlist()
    assert result["read_only"] and result["configured"]
    assert result["watchlist"] == state["watchlist"]
    assert result["groups"] == state["groups"]
    assert result["total_symbols"] == 4
    assert result["items"][0] == {
        "symbol": "NVDA", "group_ids": ["AI"], "isHolding": True, "costBasis": 100, "shares": 10
    }
    assert result["items"][1]["isHolding"] is False
    assert result["items"][1]["costBasis"] is None
    assert before == {p: p.read_bytes() for p in cached_data["root"].rglob("*") if p.is_file()}
    storage.save_watchlist_state({"watchlist": [], "groups": []})
    assert queries.get_watchlist()["items"] == []
    assert queries.get_watchlist()["watchlist"] == []
    storage.WATCHLIST_FILE.unlink()
    defaults = queries.get_watchlist()
    assert not defaults["configured"]
    assert defaults["watchlist"] == storage.DEFAULT_WATCHLIST
    assert not storage.WATCHLIST_FILE.exists()
