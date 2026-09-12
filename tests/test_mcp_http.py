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

from trenddeck import market


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
    assert response.status_code == 200 and len(response.json()["result"]["tools"]) == 2
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

    async def run_client():
        async with httpx.AsyncClient(headers={"Authorization": "Bearer test-token"}) as http:
            async with streamable_http_client(server_url + "/mcp", http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = (await session.list_tools()).tools
                    assert {tool.name for tool in tools} == {"get_daily_changes", "get_symbol_report"}
                    tools_by_name = {tool.name: tool for tool in tools}
                    report_tool = tools_by_name["get_symbol_report"]
                    assert not report_tool.annotations.readOnlyHint
                    assert report_tool.annotations.openWorldHint
                    assert report_tool.inputSchema["properties"]["refresh"]["default"] is False
                    assert report_tool.inputSchema["properties"]["history_limit"]["default"] == 60
                    assert tools_by_name["get_daily_changes"].annotations.readOnlyHint
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
        return new_bar.copy()

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
