from __future__ import annotations

import asyncio
import os
import socket
import threading
import time

import httpx
import pandas as pd
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from trenddeck import market, storage


@pytest.fixture(scope="module")
def server_url():
    from app import app

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        assert not thread.is_alive()


def test_authentication_and_host_origin_validation(server_url, monkeypatch):
    monkeypatch.delenv("TRENDDECK_MCP_TOKEN", raising=False)
    assert httpx.post(server_url + "/mcp", json={}).status_code == 503
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    old_headers = {
        "Authorization": "Bearer previous-test-token",
        "Accept": "application/json, text/event-stream",
    }
    monkeypatch.setenv("TRENDDECK_MCP_TOKEN", "previous-test-token")
    response = httpx.post(server_url + "/mcp", json=request, headers=old_headers)
    assert response.status_code == 200 and len(response.json()["result"]["tools"]) == 8
    monkeypatch.setenv("TRENDDECK_MCP_TOKEN", "")
    assert httpx.post(server_url + "/mcp", json=request, headers=old_headers).status_code == 503
    assert (
        httpx.post(
            server_url + "/mcp", json=request, headers={**old_headers, "Authorization": "Bearer"}
        ).status_code
        == 503
    )
    assert httpx.get(server_url + "/").status_code == 200
    assert httpx.get(server_url + "/api/config").status_code == 200
    assert httpx.get(server_url + "/static/vendor/lightweight-charts-5.0.0.js").status_code == 200
    monkeypatch.setenv("TRENDDECK_MCP_TOKEN", "test-token")
    assert httpx.post(server_url + "/mcp", json=request, headers=old_headers).status_code == 401
    for auth in (None, "Bearer wrong"):
        headers = {} if auth is None else {"Authorization": auth}
        response = httpx.post(server_url + "/mcp", json={}, headers=headers)
        assert response.status_code == 401 and "WWW-Authenticate" in response.headers
    headers = {"Authorization": "Bearer test-token", "Accept": "application/json, text/event-stream"}
    assert (
        httpx.post(server_url + "/mcp", json={}, headers={**headers, "Host": "untrusted.example"}).status_code
        == 421
    )
    assert (
        httpx.post(
            server_url + "/mcp", json={}, headers={**headers, "Origin": "https://untrusted.example"}
        ).status_code
        == 403
    )


def test_real_mcp_clients_discover_and_call_tools(server_url, cached_data, monkeypatch):
    monkeypatch.setenv("TRENDDECK_MCP_TOKEN", "test-token")
    monkeypatch.setattr(
        market,
        "load_realtime_quote",
        lambda symbol, preferred_api_key=None: {
            "_feed": "consolidated",
            "ticker": symbol,
            "tngoLast": 218.36,
            "prevClose": 220.0,
            "open": 219.0,
            "high": 221.0,
            "low": 217.0,
            "volume": 123456,
            "lqRefPrice": 218.36,
            "lqSpread": 0.0001,
            "lqBidPrice": 218.35,
            "lqAskPrice": 218.37,
            "timestamp": "2026-10-07T14:30:00Z",
        },
    )

    async def run_client():
        async with httpx.AsyncClient(headers={"Authorization": "Bearer test-token"}) as http:
            async with streamable_http_client(server_url + "/mcp", http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = (await session.list_tools()).tools
                    assert {tool.name for tool in tools} == {
                        "get_daily_changes",
                        "get_watchlist",
                        "get_symbol_report",
                        "get_realtime_quote",
                        "get_setup_report",
                        "get_setup_rankings",
                        "add_watchlist_symbol",
                        "remove_watchlist_symbol",
                    }
                    tools_by_name = {tool.name: tool for tool in tools}
                    for name in ("get_setup_report", "get_setup_rankings"):
                        assert tools_by_name[name].annotations.readOnlyHint
                        assert not tools_by_name[name].annotations.openWorldHint
                    setups = await session.call_tool("get_setup_rankings", {"symbols": ["NVDA"]})
                    assert not setups.isError and setups.structuredContent["read_only"]
                    assert setups.structuredContent["schema_version"] == 2
                    assert set(setups.structuredContent["items"][0]) == {
                        "symbol", "rank", "score", "status", "reasons"}
                    setup = await session.call_tool("get_setup_report", {"symbol": "NVDA"})
                    assert not setup.isError and setup.structuredContent["model_version"]
                    assert "details" not in setup.structuredContent
                    assert setup.structuredContent["reasons"]
                    assert setup.structuredContent["conditions_markdown"] == setups.structuredContent["conditions_markdown"]
                    for name, arguments in [
                        ("get_setup_report", {"symbol": "NVDA"}),
                        ("get_setup_rankings", {"symbols": ["NVDA"]}),
                    ]:
                        assert tools_by_name[name].inputSchema["properties"]["include_details"]["default"] is False
                        detailed = await session.call_tool(name, {**arguments, "include_details": True})
                        assert not detailed.isError and "details" in detailed.structuredContent
                        payload = detailed.structuredContent
                        diagnostics = payload["details"] if name == "get_setup_report" else payload["items"][0]["details"]
                        assert "geometry" in diagnostics and "components" in diagnostics
                    backtest = await session.call_tool("get_setup_backtest", {
                        "start": "2025-12-01", "end": "2026-02-01"})
                    assert backtest.isError
                    watchlist_tool = tools_by_name["get_watchlist"]
                    assert watchlist_tool.annotations.readOnlyHint
                    assert not watchlist_tool.annotations.openWorldHint
                    watchlist = await session.call_tool("get_watchlist", {})
                    assert not watchlist.isError and watchlist.structuredContent["read_only"]
                    assert watchlist.structuredContent["watchlist"] == ["NVDA", "SPY", "OLDER", "MISSING"]
                    report_tool = tools_by_name["get_symbol_report"]
                    assert not report_tool.annotations.readOnlyHint
                    assert report_tool.annotations.openWorldHint
                    assert report_tool.inputSchema["properties"]["refresh"]["default"] is False
                    assert report_tool.inputSchema["properties"]["history_limit"]["default"] == 60
                    assert tools_by_name["get_daily_changes"].annotations.readOnlyHint
                    quote_tool = tools_by_name["get_realtime_quote"]
                    assert quote_tool.annotations.readOnlyHint
                    assert quote_tool.annotations.openWorldHint
                    assert not quote_tool.annotations.idempotentHint
                    daily = await session.call_tool("get_daily_changes", {"symbols": ["NVDA"]})
                    assert not daily.isError
                    assert daily.structuredContent["as_of_session"] == cached_data["date"]
                    assert "latestAlerts" in daily.structuredContent["items"][0]
                    assert daily.structuredContent["universe"] == ["NVDA"]
                    full = await session.call_tool("get_daily_changes", {"symbols": []})
                    assert not full.isError
                    assert full.structuredContent["coverage"]["requested"] == 4
                    default_report = await session.call_tool("get_symbol_report", {"symbol": "NVDA"})
                    assert not default_report.isError
                    assert len(default_report.structuredContent["history"]["rows"]) == 60
                    report = await session.call_tool(
                        "get_symbol_report", {"symbol": "NVDA", "history_limit": 20}
                    )
                    assert not report.isError
                    data = report.structuredContent
                    assert data["cache"]["status"] == "cached"
                    assert data["analysis"]["symbol"] == "NVDA"
                    assert len(data["history"]["rows"]) == 20
                    page = await session.call_tool(
                        "get_symbol_report",
                        {
                            "symbol": "NVDA",
                            "history_limit": 20,
                            "before": data["history"]["next_before"],
                            "as_of": data["as_of_session"],
                        },
                    )
                    assert not page.isError
                    assert (
                        page.structuredContent["history"]["rows"][-1]["Date"]
                        < data["history"]["rows"][0]["Date"]
                    )
                    for arguments in (
                        {"symbol": "NVDA", "history_limit": 1000},
                        {"symbol": "MISSING"},
                        {"symbol": "../NVDA"},
                    ):
                        assert (await session.call_tool("get_symbol_report", arguments)).isError
                    for name in ("get_symbol_data", "get_symbol_analysis", "get_price_history"):
                        assert (await session.call_tool(name, {"symbol": "NVDA"})).isError
                    quote = await session.call_tool("get_realtime_quote", {"symbol": "NVDA"})
                    assert not quote.isError
                    assert quote.structuredContent["price"] == 218.36
                    assert quote.structuredContent["feed"] == "consolidated"
                    assert quote.structuredContent["lq_bid_price"] == 218.35
                    assert not quote.structuredContent["fallback_used"]

    async def concurrent_clients():
        await asyncio.gather(run_client(), run_client())

    asyncio.run(concurrent_clients())


def test_symbol_report_returns_upstream_rate_limit_as_mcp_error(server_url, cached_data, monkeypatch):
    monkeypatch.setenv("TRENDDECK_MCP_TOKEN", "test-token")
    os.utime(cached_data["cache"] / "NVDA_3y_history.csv", (0, 0))
    monkeypatch.setattr(market, "get_tiingo_api_key_candidates", lambda preferred=None: ["test-key"])

    def rate_limited(*args, **kwargs):
        raise market.MarketDataRateLimitError("Tiingo API 限流: HTTP 429，Retry-After: 60。")

    monkeypatch.setattr(market, "fetch_history_from_tiingo", rate_limited)

    async def run_client():
        async with httpx.AsyncClient(headers={"Authorization": "Bearer test-token"}, trust_env=False) as http:
            async with streamable_http_client(server_url + "/mcp", http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("get_symbol_report", {"symbol": "NVDA", "refresh": True})
                    message = " ".join(item.text for item in result.content if hasattr(item, "text"))
                    assert result.isError
                    assert "HTTP 429" in message and "Retry-After: 60" in message

    asyncio.run(run_client())


def test_realtime_quote_returns_upstream_rate_limit_as_mcp_error(server_url, monkeypatch):
    monkeypatch.setenv("TRENDDECK_MCP_TOKEN", "test-token")

    def rate_limited(*args, **kwargs):
        raise ValueError(
            "NVDA Tiingo IEX 查询失败（已尝试 2 个 API key）："
            "Tiingo IEX API 限流: HTTP 429，Retry-After: 60。"
        )

    monkeypatch.setattr(market, "load_realtime_quote", rate_limited)

    async def run_client():
        async with httpx.AsyncClient(headers={"Authorization": "Bearer test-token"}, trust_env=False) as http:
            async with streamable_http_client(server_url + "/mcp", http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("get_realtime_quote", {"symbol": "NVDA"})
                    message = " ".join(item.text for item in result.content if hasattr(item, "text"))
                    assert result.isError
                    assert "HTTP 429" in message and "Retry-After: 60" in message

    asyncio.run(run_client())


def test_report_refresh_persists_new_bar_and_invalidates_web_cache(server_url, cached_data, monkeypatch):
    from trenddeck import analysis

    monkeypatch.setenv("TRENDDECK_MCP_TOKEN", "test-token")
    old_close = analysis.analyze_symbol("NVDA", allow_network=False)["latestClose"]
    source = pd.read_csv(cached_data["cache"] / "NVDA_3y_history.csv")
    source["Date"] = pd.to_datetime(source["Date"])
    new_bar = source.tail(1).copy()
    next_date = source["Date"].iloc[-1] + pd.offsets.BDay(1)
    new_bar["Date"] = next_date
    new_bar["Close"] = old_close + 10
    new_bar["High"] = old_close + 15
    calls = []

    def fetch(symbol, period, **kwargs):
        calls.append(symbol)
        return pd.concat([source, new_bar], ignore_index=True)

    def save(symbol, period, frame):
        frame.to_csv(cached_data["cache"] / f"{symbol}_{period}_history.csv", index=False)

    monkeypatch.setattr(market, "fetch_history_from_tiingo", fetch)
    monkeypatch.setattr(market, "save_history_cache", save)
    monkeypatch.setattr(market, "get_tiingo_api_key_candidates", lambda preferred=None: ["test-key"])

    async def run_client():
        async with httpx.AsyncClient(headers={"Authorization": "Bearer test-token"}) as http:
            async with streamable_http_client(server_url + "/mcp", http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("get_symbol_report", {"symbol": "NVDA", "refresh": True})
                    assert not result.isError
                    report = result.structuredContent
                    assert report["refresh_status"] == "succeeded"
                    assert report["as_of_session"] == next_date.strftime("%Y-%m-%d")
                    assert report["benchmark_session"] == cached_data["date"]
                    assert report["history"]["rows"][-1]["Close"] == old_close + 10

    asyncio.run(run_client())
    assert calls == ["NVDA"]
    assert analysis.analyze_symbol("NVDA", allow_network=False)["latestClose"] == old_close + 10


def test_watchlist_tools_persist_shared_web_state_and_return_errors(server_url, cached_data, monkeypatch):
    monkeypatch.setenv("TRENDDECK_MCP_TOKEN", "test-token")
    state = storage.load_watchlist_state()
    state["groups"] = [{"id": "AI", "name": "AI 区", "symbols": ["NVDA"]}]
    storage.save_watchlist_state(state)

    async def run_client():
        async with httpx.AsyncClient(headers={"Authorization": "Bearer test-token"}, trust_env=False) as http:
            async with streamable_http_client(server_url + "/mcp", http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                    for name in ("add_watchlist_symbol", "remove_watchlist_symbol"):
                        assert not tools[name].annotations.readOnlyHint
                        assert tools[name].annotations.idempotentHint
                        assert not tools[name].annotations.openWorldHint
                    assert not tools["add_watchlist_symbol"].annotations.destructiveHint
                    assert tools["remove_watchlist_symbol"].annotations.destructiveHint
                    assert tools["add_watchlist_symbol"].inputSchema["properties"]["group_id"]["default"] is None
                    listed = await session.call_tool("get_watchlist", {})
                    assert not listed.isError
                    assert listed.structuredContent["groups"][0]["id"] == "AI"
                    added = await session.call_tool(
                        "add_watchlist_symbol", {"symbol": " brk.b ", "group_id": "ai"}
                    )
                    assert not added.isError and added.structuredContent["changed"]
                    assert added.structuredContent["symbol"] == "BRK-B"
                    web = (await http.get(server_url + "/api/watchlist/state")).json()
                    assert web["watchlist"] == added.structuredContent["watchlist"]
                    assert web["groups"][0]["symbols"] == ["NVDA", "BRK-B"]
                    assert not (await session.call_tool(
                        "add_watchlist_symbol", {"symbol": "BRK-B", "group_id": "AI"}
                    )).structuredContent["changed"]
                    assert not (await session.call_tool(
                        "add_watchlist_symbol", {"symbol": "BRK-B"}
                    )).structuredContent["changed"]
                    removed = await session.call_tool("remove_watchlist_symbol", {"symbol": "BRK-B"})
                    assert not removed.isError and removed.structuredContent["changed"]
                    assert storage.load_watchlist_state()["watchlist"] == removed.structuredContent["watchlist"]
                    assert not (await session.call_tool(
                        "remove_watchlist_symbol", {"symbol": "BRK-B"}
                    )).structuredContent["changed"]
                    invalid = await session.call_tool("add_watchlist_symbol", {"symbol": "../NVDA"})
                    assert invalid.isError
                    assert "stock symbol" in " ".join(item.text for item in invalid.content)
                    invalid_group = await session.call_tool(
                        "add_watchlist_symbol", {"symbol": "EXTRA", "group_id": "MISSING"}
                    )
                    assert invalid_group.isError
                    assert "get_watchlist" in " ".join(item.text for item in invalid_group.content)
                    assert "EXTRA" not in storage.load_watchlist_state()["watchlist"]

                    def cannot_save(*args, **kwargs):
                        raise OSError("watchlist disk write failed")

                    with monkeypatch.context() as patch:
                        patch.setattr(storage, "save_watchlist_state", cannot_save)
                        failed = await session.call_tool("add_watchlist_symbol", {"symbol": "EXTRA"})
                        assert failed.isError
                        assert "disk write failed" in " ".join(item.text for item in failed.content)
                    assert "EXTRA" not in storage.load_watchlist_state()["watchlist"]

    asyncio.run(run_client())
