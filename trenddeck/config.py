from __future__ import annotations

import os
from datetime import timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = Path(__file__).resolve().parent
LOCAL_DIR = PROJECT_ROOT / "local"
ENV_FILE = LOCAL_DIR / ".env"
SECRETS_FILE = LOCAL_DIR / "secrets.toml"

# Process environment > new local configuration > unmigrated legacy file.
load_dotenv(ENV_FILE, override=False)
load_dotenv(PROJECT_ROOT / ".env", override=False)
_data_root = Path(os.environ.get("TRENDDECK_DATA_DIR", "data")).expanduser()
DATA_DIR = (_data_root if _data_root.is_absolute() else PROJECT_ROOT / _data_root).resolve()
STOCK_DIR = DATA_DIR / "stock"
TRADE_DIR = DATA_DIR / "trade"
TRADE_FILE = TRADE_DIR / "trades.json"
LEDGER_FILE = TRADE_DIR / "ledger.json"
WATCHLIST_FILE = TRADE_DIR / "watchlist.json"
NOTES_FILE = TRADE_DIR / "notes.json"
ALERTS_FILE = TRADE_DIR / "alerts.json"
ALERTS_SNAPSHOT_FILE = TRADE_DIR / "alerts_snapshot.json"
STATIC_DIR = PACKAGE_ROOT / "web"
PROMPT_TEMPLATE_PATH = PACKAGE_ROOT / "resources" / "templates" / "analysis_prompt.md"
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
