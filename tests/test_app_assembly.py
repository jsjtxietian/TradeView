from fastapi.testclient import TestClient

from trenddeck.api.app import create_app


def test_independent_app_lifespans_keep_web_and_mcp_routes(cached_data, monkeypatch):
    monkeypatch.delenv("TRENDDECK_MCP_TOKEN", raising=False)
    for _ in range(2):
        with TestClient(create_app()) as client:
            assert client.get("/").status_code == 200
            assert client.get("/review").status_code == 200
            assert client.get("/static/app.js").status_code == 200
            assert client.get("/api/config").status_code == 200
            assert client.get("/api/setups/report/NVDA").status_code == 200
            assert client.post("/mcp", json={}).status_code == 503
