from __future__ import annotations

import os
import runpy

from dotenv import dotenv_values

from trenddeck.config import PROJECT_ROOT

configure_token = runpy.run_path(str(PROJECT_ROOT / "scripts/configure-mcp.py"))["configure_token"]


def test_setup_preserves_token_and_other_settings(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# local settings\nTRENDDECK_MCP_ALLOWED_HOSTS=example.com\n", encoding="utf-8")
    assert configure_token(env)
    first = dotenv_values(env)
    assert len(first["TRENDDECK_MCP_TOKEN"]) >= 40
    assert first["TRENDDECK_MCP_ALLOWED_HOSTS"] == "example.com"
    assert not configure_token(env)
    assert dotenv_values(env) == first
    assert configure_token(env, rotate=True)
    second = dotenv_values(env)
    assert second["TRENDDECK_MCP_TOKEN"] != first["TRENDDECK_MCP_TOKEN"]
    assert second["TRENDDECK_MCP_ALLOWED_HOSTS"] == "example.com"
    assert "# local settings" in env.read_text()
    if os.name != "nt":
        assert env.stat().st_mode & 0o777 == 0o600
