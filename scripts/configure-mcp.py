#!/usr/bin/env python3
"""Create or rotate a private MCP bearer token without printing it."""

from __future__ import annotations

import argparse
import runpy
import secrets
from pathlib import Path

from dotenv import dotenv_values, set_key

ROOT = Path(__file__).resolve().parents[1]


def configure_token(env_path: Path, rotate: bool = False) -> bool:
    """Preserve existing settings; only generate a token when needed or requested."""
    existing = (dotenv_values(env_path).get("TRENDDECK_MCP_TOKEN") or "").strip() if env_path.exists() else ""
    if existing and not rotate:
        env_path.chmod(0o600)
        return False
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.touch(mode=0o600, exist_ok=True)
    env_path.chmod(0o600)
    set_key(str(env_path), "TRENDDECK_MCP_TOKEN", secrets.token_urlsafe(32), quote_mode="never")
    env_path.chmod(0o600)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rotate",
        action="store_true",
        help="Replace the token; update clients and restart the service afterwards",
    )
    args = parser.parse_args()
    runpy.run_path(str(ROOT / "scripts/migrate-local.py"))["migrate_local"](ROOT)
    changed = configure_token(ROOT / "local/.env", rotate=args.rotate)
    print("MCP token saved to local/.env." if changed else "Existing local MCP token preserved.")
    print("Show client configuration when needed: python -m trenddeck.api.mcp")


if __name__ == "__main__":
    main()
