from __future__ import annotations

import os
from datetime import timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("TRENDDECK_DATA_DIR", str(PROJECT_ROOT))).resolve()
CACHE_DIR = DATA_DIR / ".cache"
TRADE_DIR = DATA_DIR / ".trade"
TRADE_FILE = TRADE_DIR / "trades.json"
LEDGER_FILE = TRADE_DIR / "ledger.json"
WATCHLIST_FILE = TRADE_DIR / "watchlist.json"
NOTES_FILE = TRADE_DIR / "notes.json"
ALERTS_FILE = TRADE_DIR / "alerts.json"
ALERTS_SNAPSHOT_FILE = CACHE_DIR / "alerts_snapshot.json"
STATIC_DIR = PROJECT_ROOT / "static"
PROMPT_TEMPLATE_PATH = PROJECT_ROOT / "prompt_template.md"
DEFAULT_HISTORY_PERIOD = "3y"
DEFAULT_BENCHMARK = "SPY"
RELATIVE_MARKET_SCORE_THRESHOLD = 60
DEFAULT_WATCHLIST = ["AAPL", "NVDA", "MSFT", "TSLA"]
DEFAULT_WATCHLIST_GROUPS = [
    {"id": "ai", "name": "AI 区", "symbols": ["NVDA", "MSFT", "MU", "AMD", "AVGO"]},
    {"id": "tech", "name": "科技区", "symbols": ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA"]},
    {"id": "regional", "name": "地区指标区", "symbols": ["EWY", "DXJ", "FXI", "EWH"]},
]
PERIOD_TO_DAYS = {"1y": 365, "2y": 730, "3y": 1095, "5y": 1825}
MEMORY_CACHE_TTL = 1800
REFRESH_COOLDOWN_SECONDS = 900
PRICE_MODE_COLUMN = "PriceMode"
PREFERRED_PRICE_MODE = "adjusted"
LEGACY_PRICE_MODE = "raw"
TIINGO_REFRESH_BATCH_SIZE = 50
ALERT_TIMEZONE = timezone(timedelta(hours=8))

# Fixed project token; reused across restarts and deployments.
# TRENDDECK_MCP_TOKEN can optionally override it for a particular deployment.
MCP_BEARER_TOKEN = '8pfM3_IKCwtdKJGz-s19PbgNJ5Qk19OvEF9yQwsjWVQ'
