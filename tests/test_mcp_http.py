from __future__ import annotations

import asyncio
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


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
    assert response.status_code == 200 and len(response.json()["result"]["tools"]) == 3
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
                    assert {tool.name for tool in tools} == {
                        "get_daily_changes",
                        "get_symbol_analysis",
                        "get_price_history",
                    }
                    assert all(
                        tool.annotations.readOnlyHint and not tool.annotations.openWorldHint for tool in tools
                    )
                    daily = await session.call_tool("get_daily_changes", {"symbols": ["NVDA"]})
                    assert not daily.isError
                    assert daily.structuredContent["as_of_session"] == cached_data["date"]
                    detail = await session.call_tool("get_symbol_analysis", {"symbol": "NVDA"})
                    assert not detail.isError and detail.structuredContent["analysis"]["symbol"] == "NVDA"
                    page = await session.call_tool("get_price_history", {"symbol": "NVDA", "limit": 2})
                    assert not page.isError and len(page.structuredContent["rows"]) == 2
                    assert all("refresh" not in tool.inputSchema.get("properties", {}) for tool in tools)
                    for arguments in (
                        {"symbol": "NVDA", "limit": 1000},
                        {"symbol": "MISSING"},
                        {"symbol": "../NVDA"},
                    ):
                        invalid = await session.call_tool("get_price_history", arguments)
                        assert invalid.isError
                    # The SDK ignores undeclared fields. Even if an agent sends
                    # refresh anyway, our tool still cannot call the provider.
                    ignored = await session.call_tool(
                        "get_price_history", {"symbol": "NVDA", "limit": 1, "refresh": True}
                    )
                    assert not ignored.isError and len(ignored.structuredContent["rows"]) == 1

    async def concurrent_clients():
        await asyncio.gather(run_client(), run_client())

    asyncio.run(concurrent_clients())
