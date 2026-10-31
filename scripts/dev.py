"""One local entry point for serving, checking and testing the project."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["serve", "test", "check"])
    args, extra = parser.parse_known_args()
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    modules = {
        "serve": ["uvicorn", "trenddeck.api.app:app", "--host", "127.0.0.1", "--port", "8000"],
        "test": ["pytest", "-q"],
        "check": ["ruff", "check", "app.py", "trenddeck", "scripts", "tests"],
    }
    return subprocess.call([sys.executable, "-B", "-m", *modules[args.command], *extra], cwd=ROOT, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
