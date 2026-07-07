from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import time
import tomllib
from typing import Any, Callable

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import numpy as np
import pandas as pd
from curl_cffi import requests as curl_requests


CACHE_DIR = Path(".cache")
CACHE_DIR.mkdir(exist_ok=True)
TRADE_DIR = Path(".trade")
TRADE_FILE = TRADE_DIR / "trades.json"
LEDGER_FILE = TRADE_DIR / "ledger.json"
WATCHLIST_FILE = TRADE_DIR / "watchlist.json"
STATIC_DIR = Path("static")
PROMPT_TEMPLATE_PATH = Path("prompt_template.md")
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


@dataclass
class CheckResult:
    name: str
    passed: bool | None
    detail: str


@dataclass(frozen=True)
class CheckSpec:
    key: str
    name: str
    evaluator: Callable[["AnalysisContext"], tuple[bool | None, str]]


@dataclass
class AnalysisContext:
    stock: pd.DataFrame
    benchmark: pd.DataFrame
    latest: pd.Series
    rs_score: float | None
    rs_detail: str


_memory_cache: dict[tuple[Any, ...], tuple[float, Any]] = {}


def load_trade_store() -> list[dict[str, Any]]:
    if not TRADE_FILE.exists():
        return []
    try:
        payload = json.loads(TRADE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"交易复盘文件读取失败: {exc}") from exc
    if not isinstance(payload, list):
        raise ValueError("交易复盘文件格式无效。")
    return normalize_trade_store(payload)


def save_trade_store(payload: list[dict[str, Any]]) -> None:
    TRADE_DIR.mkdir(exist_ok=True)
    temp_path = TRADE_FILE.with_suffix(".json.tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(TRADE_FILE)


def normalize_trade_store(payload: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for raw_trade in payload:
        if not isinstance(raw_trade, dict):
            continue
        try:
            trade_id = int(raw_trade.get("id"))
        except (TypeError, ValueError):
            continue
        symbol = normalize_symbol(str(raw_trade.get("symbol", "")))
        currency = str(raw_trade.get("currency", "USD")).strip().upper() or "USD"
        direction = str(raw_trade.get("direction", "long")).strip().lower()
        note = str(raw_trade.get("note", "")).strip()
        if trade_id <= 0 or trade_id in seen_ids or not symbol or direction not in {"long", "short"}:
            continue
        seen_ids.add(trade_id)
        raw_transactions = raw_trade.get("transactions", [])
        transactions = []
        if isinstance(raw_transactions, list):
            for raw_transaction in raw_transactions:
                try:
                    transactions.append(normalize_transaction(raw_transaction))
                except ValueError:
                    continue
        normalized.append({
            "id": trade_id,
            "symbol": symbol,
            "currency": currency,
            "direction": direction,
            "note": note,
            "transactions": transactions,
        })
    return sorted(normalized, key=lambda item: int(item["id"]))


def normalize_transaction(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("交易记录格式无效。")
    trade_date = str(data.get("date", "")).strip()
    action = str(data.get("action", "")).strip().lower()
    note = str(data.get("note", "")).strip()
    try:
        parsed_date = datetime.strptime(trade_date, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("交易日期必须是 YYYY-MM-DD 格式。") from exc
    if parsed_date.strftime("%Y-%m-%d") != trade_date:
        raise ValueError("交易日期无效。")
    if action not in {"buy", "add", "sell", "short", "add_short", "cover"}:
        raise ValueError("交易行为无效。")
    try:
        price = float(data.get("price"))
    except (TypeError, ValueError) as exc:
        raise ValueError("请输入有效的交易价格。") from exc
    if not np.isfinite(price) or price <= 0:
        raise ValueError("交易价格必须大于 0。")
    try:
        quantity = float(data.get("quantity"))
    except (TypeError, ValueError) as exc:
        raise ValueError("请输入有效的交易数量。") from exc
    if not np.isfinite(quantity) or quantity <= 0:
        raise ValueError("交易数量必须大于 0。")
    return {
        "date": trade_date,
        "quantity": round(quantity, 4),
        "price": round(price, 4),
        "action": action,
        "note": note,
    }


def list_all_trades() -> list[dict[str, Any]]:
    return load_trade_store()


def load_ibkr_review_ledger() -> dict[str, Any]:
    if not LEDGER_FILE.exists():
        return {
            "available": False,
            "baseCurrency": "USD",
            "sourceFile": LEDGER_FILE.name,
            "startDate": None,
            "endDate": None,
            "entries": [],
        }
    try:
        payload = json.loads(LEDGER_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"账户账务文件读取失败: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        raise ValueError("账户账务文件格式无效。")
    return {
        "available": True,
        "baseCurrency": str(payload.get("baseCurrency", "USD")).upper(),
        "sourceFile": LEDGER_FILE.name,
        "startDate": payload.get("startDate"),
        "endDate": payload.get("endDate"),
        "entries": payload["entries"],
    }


def create_trade(
    symbol: str,
    note: str = "",
    currency: str = "USD",
    direction: str = "long",
) -> dict[str, Any]:
    payload = load_trade_store()
    normalized_direction = direction.strip().lower()
    if normalized_direction not in {"long", "short"}:
        raise ValueError("交易方向必须是多头或空头。")
    trade = {
        "id": max((int(item["id"]) for item in payload), default=0) + 1,
        "symbol": symbol,
        "currency": currency.strip().upper() or "USD",
        "direction": normalized_direction,
        "note": note.strip(),
        "transactions": [],
    }
    payload.append(trade)
    save_trade_store(payload)
    return trade


def find_trade(payload: list[dict[str, Any]], trade_id: int) -> dict[str, Any] | None:
    return next((item for item in payload if int(item["id"]) == trade_id), None)


def update_trade_note(trade_id: int, note: str) -> dict[str, Any]:
    payload = load_trade_store()
    trade = find_trade(payload, trade_id)
    if trade is None:
        raise ValueError("交易复盘不存在。")
    trade["note"] = note.strip()
    save_trade_store(payload)
    return trade


def create_transaction(trade_id: int, data: dict[str, Any]) -> dict[str, Any]:
    payload = load_trade_store()
    trade = find_trade(payload, trade_id)
    if trade is None:
        raise ValueError("交易复盘不存在。")
    transaction = normalize_transaction(data)
    trade["transactions"].append(transaction)
    save_trade_store(payload)
    return transaction


def delete_transaction(trade_id: int, transaction_index: int) -> bool:
    payload = load_trade_store()
    trade = find_trade(payload, trade_id)
    if trade is None:
        return False
    transactions = trade.get("transactions", [])
    if not 0 <= transaction_index < len(transactions):
        return False
    transactions.pop(transaction_index)
    save_trade_store(payload)
    return True


def get_cached(key: tuple[Any, ...]) -> Any | None:
    cached = _memory_cache.get(key)
    if not cached:
        return None
    timestamp, value = cached
    if time.time() - timestamp > MEMORY_CACHE_TTL:
        _memory_cache.pop(key, None)
        return None
    return value


def set_cached(key: tuple[Any, ...], value: Any) -> Any:
    _memory_cache[key] = (time.time(), value)
    return value


def clear_symbol_memory_cache(symbol: str) -> None:
    for key in list(_memory_cache.keys()):
        if len(key) > 1 and key[1] == symbol:
            _memory_cache.pop(key, None)


def normalize_symbol(raw_symbol: str) -> str:
    text = raw_symbol.strip().upper()
    text = re.sub(r"[\s.]+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    return text


def normalize_watchlist_state(payload: dict[str, Any]) -> dict[str, Any]:
    raw_watchlist = payload.get("watchlist", [])
    if not isinstance(raw_watchlist, list):
        raise ValueError("自选股列表格式无效。")
    watchlist: list[str] = []
    for raw_symbol in raw_watchlist:
        symbol = normalize_symbol(str(raw_symbol))
        if symbol and symbol not in watchlist:
            watchlist.append(symbol)

    raw_groups = payload.get("groups", [])
    if not isinstance(raw_groups, list):
        raise ValueError("自选股分组格式无效。")
    groups: list[dict[str, Any]] = []
    seen_group_ids: set[str] = set()
    for raw_group in raw_groups:
        if not isinstance(raw_group, dict):
            continue
        group_id = normalize_symbol(str(raw_group.get("id", "")))
        name = str(raw_group.get("name", "")).strip()
        raw_symbols = raw_group.get("symbols", [])
        if not group_id or not name or group_id in seen_group_ids or not isinstance(raw_symbols, list):
            continue
        seen_group_ids.add(group_id)
        symbols: list[str] = []
        for raw_symbol in raw_symbols:
            symbol = normalize_symbol(str(raw_symbol))
            if symbol and symbol not in symbols:
                symbols.append(symbol)
        groups.append({"id": group_id, "name": name, "symbols": symbols})
    return {"version": 1, "watchlist": watchlist, "groups": groups}


def load_watchlist_state() -> dict[str, Any] | None:
    if not WATCHLIST_FILE.exists():
        return None
    try:
        payload = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"自选股文件读取失败: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("自选股文件格式无效。")
    return normalize_watchlist_state(payload)


def save_watchlist_state(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_watchlist_state(payload)
    TRADE_DIR.mkdir(exist_ok=True)
    temp_path = WATCHLIST_FILE.with_suffix(".json.tmp")
    temp_path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(WATCHLIST_FILE)
    return normalized


def strip_check_name_prefix(name: str) -> str:
    text = str(name or "").strip()
    for separator in ("：", ":"):
        index = text.find(separator)
        if index >= 0:
            return text[index + 1 :].strip() or text
    return text


def load_local_secrets() -> dict[str, Any]:
    secrets_path = Path(".streamlit") / "secrets.toml"
    if not secrets_path.exists():
        return {}
    try:
        return tomllib.loads(secrets_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_secret(name: str) -> str:
    direct = os.getenv(name, "").strip()
    if direct:
        return direct
    secrets = load_local_secrets()
    return str(secrets.get(name, "")).strip()


def get_session() -> curl_requests.Session:
    session = curl_requests.Session(impersonate="chrome")
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    return session


def get_tiingo_api_key() -> str:
    return get_secret("TIINGO_API_KEY")


def get_tiingo_api_keys() -> list[str]:
    keys: list[str] = []
    for secret_name in ("TIINGO_API_KEY", "TIINGO_API_KEY_2"):
        value = get_secret(secret_name)
        if value and value not in keys:
            keys.append(value)
    return keys


def get_refresh_api_key_for_index(index: int) -> str:
    keys = get_tiingo_api_keys()
    if not keys:
        return ""
    batch_index = max(0, index) // TIINGO_REFRESH_BATCH_SIZE
    if batch_index >= len(keys):
        batch_index = len(keys) - 1
    return keys[batch_index]


def get_tiingo_api_key_candidates(preferred_api_key: str | None = None) -> list[str]:
    candidates: list[str] = []
    preferred = (preferred_api_key or "").strip()
    if preferred:
        candidates.append(preferred)
    for key in get_tiingo_api_keys():
        if key and key not in candidates:
            candidates.append(key)
    return candidates


def period_start(period: str) -> str:
    days = PERIOD_TO_DAYS.get(period, 1095)
    start = pd.Timestamp.utcnow().tz_localize(None) - pd.Timedelta(days=days + 20)
    return start.strftime("%Y-%m-%d")


def history_cache_path(symbol: str, period: str) -> Path:
    safe_symbol = symbol.replace("/", "_").replace("\\", "_")
    return CACHE_DIR / f"{safe_symbol}_{period}_history.csv"


def annotate_history_price_mode(frame: pd.DataFrame, price_mode: str) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    annotated = frame.copy()
    annotated[PRICE_MODE_COLUMN] = price_mode
    return annotated


def get_history_price_mode(frame: pd.DataFrame) -> str:
    if frame.empty or PRICE_MODE_COLUMN not in frame.columns:
        return LEGACY_PRICE_MODE
    values = frame[PRICE_MODE_COLUMN].dropna().astype(str).str.strip().str.lower().unique().tolist()
    if len(values) == 1 and values[0]:
        return values[0]
    return LEGACY_PRICE_MODE


def is_preferred_price_mode(frame: pd.DataFrame) -> bool:
    return get_history_price_mode(frame) == PREFERRED_PRICE_MODE


def is_refresh_cooldown_active(symbol: str, period: str) -> bool:
    cache_file = history_cache_path(symbol, period)
    if not cache_file.exists():
        return False
    modified_at = cache_file.stat().st_mtime
    return (time.time() - modified_at) < REFRESH_COOLDOWN_SECONDS


def load_history_cache(symbol: str, period: str) -> pd.DataFrame:
    cache_file = history_cache_path(symbol, period)
    if not cache_file.exists():
        return pd.DataFrame()
    frame = pd.read_csv(cache_file)
    if frame.empty:
        return pd.DataFrame()
    frame["Date"] = pd.to_datetime(frame["Date"]).dt.tz_localize(None)
    if is_preferred_price_mode(frame):
        frame.attrs["source_note"] = f"{symbol} 使用本地前复权缓存数据，可能不是最新交易日。"
    else:
        frame.attrs["source_note"] = f"{symbol} 使用旧版未复权缓存数据，点击“拉新”后会重建为前复权口径。"
    return frame


def save_history_cache(symbol: str, period: str, frame: pd.DataFrame) -> None:
    history_cache_path(symbol, period).parent.mkdir(exist_ok=True)
    annotate_history_price_mode(frame, get_history_price_mode(frame)).to_csv(
        history_cache_path(symbol, period),
        index=False,
    )


def merge_history_frames(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    if existing.empty:
        return incoming.copy()
    if incoming.empty:
        return existing.copy()
    merged = pd.concat([existing, incoming], ignore_index=True)
    merged["Date"] = pd.to_datetime(merged["Date"]).dt.tz_localize(None)
    merged = merged.sort_values("Date").drop_duplicates(subset=["Date"], keep="last").reset_index(drop=True)
    return merged


def fetch_history_from_tiingo(
    symbol: str,
    period: str,
    start_date: str | None = None,
    end_date: str | None = None,
    api_key: str | None = None,
) -> pd.DataFrame:
    api_key = (api_key or get_tiingo_api_key()).strip()
    if not api_key:
        return pd.DataFrame()

    response = get_session().get(
        f"https://api.tiingo.com/tiingo/daily/{symbol}/prices",
        params={
            "startDate": start_date or period_start(period),
            "endDate": end_date or pd.Timestamp.utcnow().tz_localize(None).strftime("%Y-%m-%d"),
            "resampleFreq": "daily",
        },
        headers={"Authorization": f"Token {api_key}"},
        timeout=30,
    )
    if response.status_code in (401, 403):
        raise ValueError("Tiingo API key 无效或当前账户无权限访问该接口。")
    if response.status_code == 404:
        return pd.DataFrame()
    if response.status_code >= 400:
        raise ValueError(f"Tiingo 请求失败: HTTP {response.status_code}")

    payload = response.json()
    if not isinstance(payload, list) or not payload:
        return pd.DataFrame()

    frame = pd.DataFrame(payload)
    adjusted_columns = {
        "date": "Date",
        "adjOpen": "Open",
        "adjHigh": "High",
        "adjLow": "Low",
        "adjClose": "Close",
        "adjVolume": "Volume",
    }
    raw_columns = {
        "date": "Date",
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
    }
    use_adjusted = all(field in frame.columns for field in adjusted_columns)
    price_mode = PREFERRED_PRICE_MODE if use_adjusted else LEGACY_PRICE_MODE
    frame = frame.rename(columns=adjusted_columns if use_adjusted else raw_columns)
    expected = ["Date", "Open", "High", "Low", "Close", "Volume"]
    available = [field for field in expected if field in frame.columns]
    frame = frame[available].copy()
    frame["Date"] = pd.to_datetime(frame["Date"], utc=True).dt.tz_localize(None)
    for field in ["Open", "High", "Low", "Close", "Volume"]:
        if field in frame.columns:
            frame[field] = pd.to_numeric(frame[field], errors="coerce")
    frame = frame.dropna(subset=["Date", "Close"]).sort_values("Date").reset_index(drop=True)
    return annotate_history_price_mode(frame, price_mode)


def load_history(
    symbol: str,
    period: str = DEFAULT_HISTORY_PERIOD,
    force_refresh: bool = False,
    allow_network: bool = True,
    tiingo_api_key: str | None = None,
) -> pd.DataFrame:
    cache_key = ("history", symbol, period)
    cached = get_cached(cache_key)
    if cached is not None and not force_refresh:
        return cached.copy()

    disk_cached = load_history_cache(symbol, period)
    legacy_cache_needs_rebuild = not disk_cached.empty and not is_preferred_price_mode(disk_cached)
    if not force_refresh and not disk_cached.empty:
        return set_cached(cache_key, disk_cached.copy()).copy()
    if not allow_network:
        return pd.DataFrame()
    if force_refresh and not disk_cached.empty and not legacy_cache_needs_rebuild and is_refresh_cooldown_active(symbol, period):
        disk_cached.attrs["source_note"] = f"{symbol} 刚刚已拉新过，短时间内直接复用本地缓存。"
        return set_cached(cache_key, disk_cached.copy()).copy()

    incremental_start = None
    tiingo_had_no_data = False
    if not disk_cached.empty and not legacy_cache_needs_rebuild:
        incremental_start = (disk_cached["Date"].max() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    tiingo_error: Exception | None = None
    api_keys = get_tiingo_api_key_candidates(tiingo_api_key)
    if api_keys:
        for api_key in api_keys:
            try:
                frame = fetch_history_from_tiingo(symbol, period, start_date=incremental_start, api_key=api_key)
                if legacy_cache_needs_rebuild and not frame.empty:
                    merged = frame.copy()
                    merged.attrs["source_note"] = f"{symbol} 行情已按前复权口径重建缓存，价格数据源: Tiingo"
                    save_history_cache(symbol, period, merged)
                    return set_cached(cache_key, merged.copy()).copy()
                if not legacy_cache_needs_rebuild and (not frame.empty or not disk_cached.empty):
                    merged = merge_history_frames(disk_cached, frame)
                    merged.attrs["source_note"] = (
                        f"{symbol} 前复权行情已增量更新，价格数据源: Tiingo"
                        if not disk_cached.empty
                        else f"{symbol} 前复权行情数据源: Tiingo"
                    )
                    save_history_cache(symbol, period, merged)
                    return set_cached(cache_key, merged.copy()).copy()
                tiingo_had_no_data = True
                tiingo_error = None
                break
            except Exception as exc:
                tiingo_error = exc

    if not disk_cached.empty:
        if legacy_cache_needs_rebuild:
            disk_cached.attrs["source_note"] = f"{symbol} 仍在使用旧版未复权缓存；本次未能完成前复权重建，当前处于离线或接口失败回退状态。"
        else:
            disk_cached.attrs["source_note"] = f"{symbol} 使用本地前复权缓存数据，当前处于离线或接口失败回退状态。"
        return set_cached(cache_key, disk_cached.copy()).copy()

    if tiingo_error is not None:
        raise ValueError(f"{symbol} Tiingo 失败: {tiingo_error}")
    if tiingo_had_no_data:
        raise ValueError(f"{symbol} 在 Tiingo 中没有返回可用行情，代码可能无效。")
    raise ValueError(f"{symbol} 未返回任何价格数据，可能是代码无效或 Tiingo 当前失败。")


def add_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    enriched = frame.copy()
    for window in (20, 50, 150, 200):
        enriched[f"MA{window}"] = enriched["Close"].rolling(window).mean()
    enriched["PctFrom52WLow"] = enriched["Close"] / enriched["Low"].rolling(252).min() - 1
    enriched["PctFrom52WHigh"] = 1 - enriched["Close"] / enriched["High"].rolling(252).max()
    return enriched


def compute_rs_proxy(stock: pd.DataFrame, benchmark: pd.DataFrame) -> tuple[float | None, str]:
    horizons = [63, 126, 189, 252]
    merged = pd.merge(
        stock[["Date", "Close"]],
        benchmark[["Date", "Close"]],
        on="Date",
        how="inner",
        suffixes=("_stock", "_bench"),
    )
    if len(merged) < max(horizons) + 1:
        return None, "历史数据不足，无法计算相对 SPY 表现分"

    merged = merged.sort_values("Date").reset_index(drop=True)
    weights = [0.4, 0.2, 0.2, 0.2]
    excess_returns = []
    for days, weight in zip(horizons, weights):
        stock_return = merged["Close_stock"].iloc[-1] / merged["Close_stock"].iloc[-days - 1] - 1
        bench_return = merged["Close_bench"].iloc[-1] / merged["Close_bench"].iloc[-days - 1] - 1
        excess_returns.append((stock_return - bench_return) * weight)

    weighted_excess = float(sum(excess_returns))
    score = float(np.clip(50 + weighted_excess * 100, 1, 99))
    return score, f"相对 {DEFAULT_BENCHMARK} 表现分: {score:.1f}"


def require_values(*values: float | None) -> bool:
    return all(pd.notna(value) for value in values)


def evaluate_price_above_long_mas(context: AnalysisContext) -> tuple[bool | None, str]:
    latest = context.latest
    if not require_values(latest["Close"], latest["MA150"], latest["MA200"]):
        return None, "MA150 或 MA200 数据不足"
    passed = bool(latest["Close"] > latest["MA150"] and latest["Close"] > latest["MA200"])
    return passed, f"现价 {fmt_price(latest['Close'])} / MA150 {fmt_price(latest['MA150'])} / MA200 {fmt_price(latest['MA200'])}"


def evaluate_ma150_above_ma200(context: AnalysisContext) -> tuple[bool | None, str]:
    latest = context.latest
    if not require_values(latest["MA150"], latest["MA200"]):
        return None, "MA150 或 MA200 数据不足"
    passed = bool(latest["MA150"] > latest["MA200"])
    return passed, f"MA150 {fmt_price(latest['MA150'])} / MA200 {fmt_price(latest['MA200'])}"


def evaluate_ma200_uptrend(context: AnalysisContext) -> tuple[bool | None, str]:
    if len(context.stock) < 222:
        return None, "200 日均线历史不足，至少需要约 222 个交易日"
    latest = context.latest
    ma200_month_ago = context.stock["MA200"].iloc[-22]
    if not require_values(latest["MA200"], ma200_month_ago):
        return None, "MA200 数据不足"
    passed = bool(latest["MA200"] > ma200_month_ago)
    return passed, f"当前 MA200 {fmt_price(latest['MA200'])} / 约 1 个月前 {fmt_price(ma200_month_ago)}"


def evaluate_ma50_above_long_mas(context: AnalysisContext) -> tuple[bool | None, str]:
    latest = context.latest
    if not require_values(latest["MA50"], latest["MA150"], latest["MA200"]):
        return None, "MA50、MA150 或 MA200 数据不足"
    passed = bool(latest["MA50"] > latest["MA150"] and latest["MA50"] > latest["MA200"])
    return passed, f"MA50 {fmt_price(latest['MA50'])} / MA150 {fmt_price(latest['MA150'])} / MA200 {fmt_price(latest['MA200'])}"


def evaluate_price_above_ma50(context: AnalysisContext) -> tuple[bool | None, str]:
    latest = context.latest
    if not require_values(latest["Close"], latest["MA50"]):
        return None, "MA50 数据不足"
    passed = bool(latest["Close"] > latest["MA50"])
    return passed, f"现价 {fmt_price(latest['Close'])} / MA50 {fmt_price(latest['MA50'])}"


def evaluate_above_52w_low(context: AnalysisContext) -> tuple[bool | None, str]:
    if len(context.stock) < 252:
        return None, "52 周低点所需历史不足"
    latest = context.latest
    low_52w = context.stock["Low"].tail(252).min()
    if not require_values(latest["Close"], low_52w):
        return None, "52 周低点数据不足"
    passed = bool(latest["Close"] >= low_52w * 1.3)
    return passed, f"现价 {fmt_price(latest['Close'])} / 52 周低点 {fmt_price(low_52w)}"


def evaluate_near_52w_high(context: AnalysisContext) -> tuple[bool | None, str]:
    if len(context.stock) < 252:
        return None, "52 周高点所需历史不足"
    latest = context.latest
    high_52w = context.stock["High"].tail(252).max()
    if not require_values(latest["Close"], high_52w):
        return None, "52 周高点数据不足"
    passed = bool(latest["Close"] >= high_52w * 0.75)
    return passed, f"现价 {fmt_price(latest['Close'])} / 52 周高点 {fmt_price(high_52w)}"


def evaluate_rs_proxy_threshold(context: AnalysisContext) -> tuple[bool | None, str]:
    if context.rs_score is None:
        return None, context.rs_detail
    return bool(context.rs_score >= RELATIVE_MARKET_SCORE_THRESHOLD), context.rs_detail


def evaluate_market_pullback_resilience(context: AnalysisContext) -> tuple[bool | None, str]:
    benchmark_tail = context.benchmark.tail(63).reset_index(drop=True)
    if len(benchmark_tail) < 30:
        return None, f"{DEFAULT_BENCHMARK} 历史不足，无法识别近期回调波段"

    rolling_peak = benchmark_tail["Close"].cummax()
    drawdowns = 1 - benchmark_tail["Close"] / rolling_peak
    if drawdowns.isna().all():
        return None, f"{DEFAULT_BENCHMARK} 回调段数据不足"

    trough_idx = int(drawdowns.idxmax())
    benchmark_drawdown = float(drawdowns.iloc[trough_idx])
    if benchmark_drawdown < 0.08:
        return None, f"近 3 个月 {DEFAULT_BENCHMARK} 最大回撤 {fmt_pct(benchmark_drawdown)}，回调不够明确"

    peak_idx = int(benchmark_tail["Close"].iloc[: trough_idx + 1].idxmax())
    bench_peak = benchmark_tail["Close"].iloc[peak_idx]
    bench_low = benchmark_tail["Close"].iloc[trough_idx]
    peak_date = benchmark_tail["Date"].iloc[peak_idx]
    trough_date = benchmark_tail["Date"].iloc[trough_idx]

    if not require_values(bench_peak, bench_low) or bench_peak == 0:
        return None, f"{DEFAULT_BENCHMARK} 回调段数据不足"

    stock_segment = context.stock[
        (context.stock["Date"] >= peak_date) & (context.stock["Date"] <= trough_date)
    ].copy().reset_index(drop=True)
    if len(stock_segment) < 5:
        return None, "个股与基准对齐后的样本不足"

    stock_rolling_peak = stock_segment["Close"].cummax()
    stock_drawdowns = 1 - stock_segment["Close"] / stock_rolling_peak
    if stock_drawdowns.isna().all():
        return None, "个股回调段数据不足"

    stock_trough_idx = int(stock_drawdowns.idxmax())
    stock_peak_idx = int(stock_segment["Close"].iloc[: stock_trough_idx + 1].idxmax())
    stock_peak = stock_segment["Close"].iloc[stock_peak_idx]
    stock_low = stock_segment["Close"].iloc[stock_trough_idx]
    if not require_values(stock_peak, stock_low) or stock_peak == 0:
        return None, "个股回调段数据不足"
    stock_drawdown = 1 - stock_low / stock_peak

    higher_low = False
    if len(context.stock) >= 30 and len(context.benchmark) >= 30:
        stock_recent_low = context.stock["Low"].tail(15).min()
        stock_prior_low = context.stock["Low"].tail(30).head(15).min()
        bench_recent_low = context.benchmark["Low"].tail(15).min()
        bench_prior_low = context.benchmark["Low"].tail(30).head(15).min()
        higher_low = bool(
            require_values(stock_recent_low, stock_prior_low, bench_recent_low, bench_prior_low)
            and stock_recent_low > stock_prior_low
            and bench_recent_low <= bench_prior_low
        )

    outperformed = stock_drawdown <= benchmark_drawdown * 0.75
    passed = bool(outperformed or higher_low)
    detail = (
        f"近 3 个月 {DEFAULT_BENCHMARK} 最大回撤 {fmt_pct(benchmark_drawdown)}"
        f"（{peak_date.strftime('%Y-%m-%d')} -> {trough_date.strftime('%Y-%m-%d')}）"
        f"\n个股同期最大回撤 {fmt_pct(stock_drawdown)}"
    )
    if higher_low:
        detail += "，个股近期低点高于上一轮低点"
    elif outperformed:
        detail += "，个股明显更抗跌"
    else:
        detail += "，暂未显示明显抗跌优势"
    return passed, detail


BUY_LOOKBACK_WINDOWS = {
    21: "近 1 个月",
    63: "近 3 个月",
    126: "近 6 个月",
}


def fmt_short_date(value: pd.Timestamp) -> str:
    return value.strftime("%m-%d")


def calculate_max_drawdown(frame: pd.DataFrame) -> dict[str, Any] | None:
    clean = frame[["Date", "Close"]].dropna().sort_values("Date").reset_index(drop=True)
    if len(clean) < 2:
        return None
    rolling_peak = clean["Close"].cummax()
    drawdowns = 1 - clean["Close"] / rolling_peak
    if drawdowns.isna().all():
        return None
    trough_idx = int(drawdowns.idxmax())
    peak_idx = int(clean["Close"].iloc[: trough_idx + 1].idxmax())
    return {
        "drawdown": float(drawdowns.iloc[trough_idx]),
        "peak_date": clean["Date"].iloc[peak_idx],
        "trough_date": clean["Date"].iloc[trough_idx],
    }


def get_aligned_lookback_frames(
    context: AnalysisContext,
    lookback_days: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    common_dates = pd.merge(
        context.stock[["Date"]],
        context.benchmark[["Date"]],
        on="Date",
        how="inner",
    ).tail(lookback_days)["Date"]
    if common_dates.empty:
        return pd.DataFrame(), pd.DataFrame()
    dates = set(common_dates.tolist())
    stock = context.stock[context.stock["Date"].isin(dates)].sort_values("Date").reset_index(drop=True)
    benchmark = context.benchmark[context.benchmark["Date"].isin(dates)].sort_values("Date").reset_index(drop=True)
    return stock, benchmark


def evaluate_relative_max_drawdown(
    context: AnalysisContext,
    lookback_days: int,
) -> tuple[bool | None, str]:
    stock, benchmark = get_aligned_lookback_frames(context, lookback_days)
    window_label = BUY_LOOKBACK_WINDOWS[lookback_days]
    if len(stock) < min(lookback_days, 15) or len(benchmark) < min(lookback_days, 15):
        return None, f"{window_label}个股或 {DEFAULT_BENCHMARK} 数据不足"
    stock_result = calculate_max_drawdown(stock)
    benchmark_result = calculate_max_drawdown(benchmark)
    if not stock_result or not benchmark_result:
        return None, f"{window_label}最大回撤数据不足"
    stock_drawdown = stock_result["drawdown"]
    benchmark_drawdown = benchmark_result["drawdown"]
    limit = benchmark_drawdown * 2.5
    passed = bool(stock_drawdown <= limit)
    detail = (
        f"{DEFAULT_BENCHMARK} {fmt_pct(benchmark_drawdown)} · "
        f"个股 {fmt_pct(stock_drawdown)} · 阈值 {fmt_pct(limit)}"
        f"\n{DEFAULT_BENCHMARK} {fmt_short_date(benchmark_result['peak_date'])}至"
        f"{fmt_short_date(benchmark_result['trough_date'])} · "
        f"个股 {fmt_short_date(stock_result['peak_date'])}至"
        f"{fmt_short_date(stock_result['trough_date'])}"
    )
    return passed, detail


def evaluate_absolute_max_drawdown(
    context: AnalysisContext,
    lookback_days: int,
) -> tuple[bool | None, str]:
    stock = context.stock.tail(lookback_days)
    window_label = BUY_LOOKBACK_WINDOWS[lookback_days]
    result = calculate_max_drawdown(stock)
    if not result:
        return None, f"{window_label}最大回撤数据不足"
    drawdown = result["drawdown"]
    return (
        bool(drawdown < 0.35),
        f"个股 {fmt_pct(drawdown)} · 上限 35%"
        f"\n{fmt_short_date(result['peak_date'])}至{fmt_short_date(result['trough_date'])}",
    )


def evaluate_leaders_bottom_first(
    context: AnalysisContext,
    lookback_days: int,
) -> tuple[bool | None, str]:
    stock, benchmark = get_aligned_lookback_frames(context, lookback_days)
    window_label = BUY_LOOKBACK_WINDOWS[lookback_days]
    benchmark_result = calculate_max_drawdown(benchmark)
    if not benchmark_result:
        return None, f"{window_label} {DEFAULT_BENCHMARK} 回撤数据不足"
    if benchmark_result["drawdown"] < 0.05:
        return None, (
            f"{window_label} {DEFAULT_BENCHMARK} 最大回撤仅 "
            f"{fmt_pct(benchmark_result['drawdown'])}，尚未形成明确调整"
        )

    peak_date = benchmark_result["peak_date"]
    trough_date = benchmark_result["trough_date"]
    correction = stock[(stock["Date"] >= peak_date) & (stock["Date"] <= trough_date)].copy()
    if len(correction) < 5 or correction["Low"].dropna().empty:
        return None, "个股与大盘调整区间对齐后的低点样本不足"

    stock_low_idx = correction["Low"].idxmin()
    stock_low_date = correction.loc[stock_low_idx, "Date"]
    stock_low = correction.loc[stock_low_idx, "Low"]
    later_lows = correction[correction["Date"] > stock_low_date]["Low"].dropna()
    bottomed_earlier = bool(stock_low_date < trough_date)
    held_higher_low = bool(
        bottomed_earlier
        and not later_lows.empty
        and later_lows.min() > stock_low
    )
    passed = bool(bottomed_earlier and held_higher_low)
    detail = (
        f"{DEFAULT_BENCHMARK} 低点 {fmt_short_date(trough_date)} · "
        f"个股低点 {fmt_short_date(stock_low_date)}"
    )
    if passed:
        detail += "\n个股见低后未再创新低"
    elif bottomed_earlier:
        detail += "\n较早见低，但随后再次触及或跌破"
    else:
        detail += "\n个股未早于大盘见低"
    return passed, detail


def evaluate_current_volume_below_ma50(context: AnalysisContext) -> tuple[bool | None, str]:
    if len(context.stock) < 50:
        return None, "50 日成交量历史不足"
    volume_ma50 = context.stock["Volume"].rolling(50).mean().iloc[-1]
    current_volume = context.latest["Volume"]
    if not require_values(volume_ma50, current_volume) or volume_ma50 == 0:
        return None, "近期成交量数据不足"
    ratio = float(current_volume / volume_ma50)
    return (
        bool(current_volume < volume_ma50),
        f"当前 {fmt_volume(current_volume)} · MA50 "
        f"{fmt_volume(volume_ma50)} · {ratio:.2f}x",
    )


def evaluate_latest_range_below_ma50(context: AnalysisContext) -> tuple[bool | None, str]:
    if len(context.stock) < 50:
        return None, "前一日波动率至少需要 50 个交易日数据"
    frame = context.stock.copy()
    frame["RangePct"] = (frame["High"] - frame["Low"]) / frame["Close"]
    latest_range = frame["RangePct"].iloc[-1]
    range_ma50 = frame["RangePct"].rolling(50).mean().iloc[-1]
    if not require_values(latest_range, range_ma50) or range_ma50 == 0:
        return None, "前一日波动率数据不足"
    return (
        bool(latest_range < range_ma50),
        f"前一日振幅 {fmt_pct(latest_range)} / 50 日均振幅 {fmt_pct(range_ma50)}",
    )


def evaluate_price_not_extended_from_ma20(context: AnalysisContext) -> tuple[bool | None, str]:
    latest = context.latest
    if not require_values(latest["Close"], latest["MA20"]) or latest["MA20"] == 0:
        return None, "MA20 数据不足"
    extension = float(latest["Close"] / latest["MA20"] - 1)
    return (
        bool(extension <= 0.10),
        f"现价 {fmt_price(latest['Close'])} / MA20 {fmt_price(latest['MA20'])} · 延伸 {fmt_signed_pct(extension)} / 上限 +10%",
    )


def evaluate_recent_volatility_contraction(context: AnalysisContext) -> tuple[bool | None, str]:
    if len(context.stock) < 15:
        return None, "近期波动区间至少需要 15 个交易日数据"
    tail = context.stock.tail(15).copy()
    tail["DailyRangePct"] = (tail["High"] - tail["Low"]) / tail["Close"]
    prior_average = tail.head(5)["DailyRangePct"].replace([np.inf, -np.inf], np.nan).mean()
    recent_average = tail.tail(10)["DailyRangePct"].replace([np.inf, -np.inf], np.nan).mean()
    recent_high_close = tail.tail(10)["Close"].max()
    recent_low_close = tail.tail(10)["Close"].min()
    total_range = (
        1 - recent_low_close / recent_high_close
        if recent_high_close
        else np.nan
    )
    if not require_values(prior_average, recent_average, total_range) or prior_average == 0:
        return None, "近期波动区间样本不足"
    passed = bool(total_range < 0.10)
    return (
        passed,
        f"最高收盘至最低收盘 {fmt_pct(total_range)} · 上限 10%"
        f"\n近 10 日日均单日振幅 {fmt_pct(recent_average)}"
        f"（再前 5 日 {fmt_pct(prior_average)}）",
    )


def evaluate_eight_week_explosive_gain(context: AnalysisContext) -> tuple[bool | None, str]:
    window = context.stock.tail(40).copy().reset_index(drop=True)
    if len(window) < 20:
        return None, "8 周爆发涨幅历史不足"
    best_gain = -np.inf
    best_low_date = None
    best_high_date = None
    running_low = np.inf
    running_low_date = None
    for _, row in window.iterrows():
        low = row["Low"]
        high = row["High"]
        if require_values(low) and low < running_low:
            running_low = float(low)
            running_low_date = row["Date"]
        if require_values(high) and running_low_date is not None and running_low > 0:
            gain = float(high / running_low - 1)
            if gain > best_gain:
                best_gain = gain
                best_low_date = running_low_date
                best_high_date = row["Date"]
    if not require_values(best_gain) or best_low_date is None or best_high_date is None:
        return None, "8 周先低后高涨幅数据不足"
    return (
        bool(best_gain >= 1.0),
        f"近 8 周最大先低后高涨幅 {fmt_pct(best_gain)}"
        f"（{best_low_date.strftime('%Y-%m-%d')} -> {best_high_date.strftime('%Y-%m-%d')}）"
        " / 门槛 100%",
    )


def find_power_play_setup(context: AnalysisContext) -> dict[str, Any] | None:
    if len(context.stock) < 60:
        return None
    frame = context.stock.tail(110).reset_index(drop=True)
    best_candidate = None
    for base_len in range(15, 31):
        run_end = len(frame) - base_len
        run = frame.iloc[max(0, run_end - 40) : run_end]
        base = frame.iloc[run_end:]
        if len(run) < 30 or len(base) < 15:
            continue
        run_low = run["Low"].min()
        run_high = run["High"].max()
        base_low_close = base["Close"].min()
        base_high_close = base["Close"].max()
        if (
            not require_values(run_low, run_high, base_low_close, base_high_close)
            or run_low == 0
            or base_high_close == 0
        ):
            continue
        candidate = {
            "base_len": base_len,
            "run_gain": float(run_high / run_low - 1),
            "base_drawdown": float(1 - base_low_close / base_high_close),
        }
        candidate["passed"] = bool(
            candidate["run_gain"] >= 1.0
            and candidate["base_drawdown"] <= 0.20
        )
        if best_candidate is None:
            best_candidate = candidate
        elif candidate["passed"] and not best_candidate["passed"]:
            best_candidate = candidate
        elif candidate["passed"] == best_candidate["passed"]:
            current_distance = abs(candidate["run_gain"] - 1.0) + candidate["base_drawdown"]
            best_distance = abs(best_candidate["run_gain"] - 1.0) + best_candidate["base_drawdown"]
            if current_distance < best_distance:
                best_candidate = candidate
    return best_candidate


def build_buy_indicator_groups(
    context: AnalysisContext,
    lookback_days: int,
) -> list[dict[str, Any]]:
    def item(name: str, result: tuple[bool | None, str]) -> dict[str, Any]:
        return {"name": name, "passed": result[0], "detail": result[1]}

    context_items = [
        item("个股最大回撤不超过 SPY 的 2.5 倍", evaluate_relative_max_drawdown(context, lookback_days)),
        item("个股最大回撤小于 35%", evaluate_absolute_max_drawdown(context, lookback_days)),
        item("Leaders Bottom First", evaluate_leaders_bottom_first(context, lookback_days)),
    ]
    volume_item = item("当前成交量低于 50 日均量", evaluate_current_volume_below_ma50(context))
    extension_item = item("距 MA20 不超过 10%", evaluate_price_not_extended_from_ma20(context))
    contraction_item = item("近 10 日收盘区间小于 10%", evaluate_recent_volatility_contraction(context))
    mvp_item = item("MVP 动量量价共振", evaluate_mvp_burst(context))
    power_play = find_power_play_setup(context)
    if power_play is None:
        burst_item = {"name": "8 周涨幅达到 100%", "passed": None, "detail": "Power Play 样本不足"}
        base_item = {"name": "3-6 周收盘区间不超过 20%", "passed": None, "detail": "Power Play 样本不足"}
    else:
        burst_item = {
            "name": "8 周涨幅达到 100%",
            "passed": bool(power_play["run_gain"] >= 1.0),
            "detail": f"8 周涨幅 {fmt_pct(power_play['run_gain'])} · 门槛 100%",
        }
        base_item = {
            "name": "3-6 周收盘区间不超过 20%",
            "passed": bool(power_play["base_drawdown"] <= 0.20),
            "detail": (
                f"整理 {power_play['base_len']} 日 · "
                f"最高收盘至最低收盘 {fmt_pct(power_play['base_drawdown'])} · 上限 20%"
            ),
        }

    pattern_a_items = [contraction_item]
    pattern_b_items = [burst_item, base_item]

    def group(key: str, title: str, subtitle: str, items: list[dict[str, Any]], observational: bool = False) -> dict[str, Any]:
        states = [entry["passed"] for entry in items]
        passed = None if any(state is None for state in states) else all(state is True for state in states)
        return {
            "key": key,
            "title": title,
            "subtitle": subtitle,
            "passed": passed,
            "observational": observational,
            "items": items,
        }

    return [
        group(
            "common_volume",
            "买入点推荐",
            "不追高 AND 当日缩量",
            [extension_item, volume_item],
        ),
        group(
            "pattern_a",
            "标准波动率收缩",
            "近期平台收紧",
            pattern_a_items,
        ),
        group(
            "market_context",
            "市场与抗跌背景",
            f"{BUY_LOOKBACK_WINDOWS[lookback_days]}窗口，仅作背景观察",
            context_items,
            observational=True,
        ),
        group(
            "mvp",
            "MVP 指标",
            "15 日动量 AND 量能扩张",
            [mvp_item],
        ),
        group(
            "pattern_b",
            "Power Play",
            "强势爆发 AND 紧凑整理",
            pattern_b_items,
        ),
    ]


def flatten_buy_indicator_groups(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened = []
    seen = set()
    for group in groups:
        for item in group["items"]:
            if item["name"] in seen:
                continue
            seen.add(item["name"])
            flattened.append(item)
    return flattened


def find_recent_breakout(stock: pd.DataFrame, lookback_days: int = 10, base_days: int = 20) -> dict[str, Any] | None:
    if len(stock) < max(55, base_days + lookback_days + 1):
        return None

    frame = stock.copy()
    frame["VolumeMA50"] = frame["Volume"].rolling(50).mean()
    start_idx = max(base_days, len(frame) - lookback_days)
    for idx in range(len(frame) - 1, start_idx - 1, -1):
        row = frame.iloc[idx]
        prior = frame.iloc[idx - base_days : idx]
        if len(prior) < base_days or pd.isna(row["VolumeMA50"]):
            continue
        prior_high = prior["High"].max()
        if not require_values(prior_high, row["Close"], row["Volume"], row["VolumeMA50"]):
            continue
        if row["Close"] > prior_high and row["Volume"] >= row["VolumeMA50"] * 1.2:
            return {
                "index": idx,
                "date": row["Date"],
                "prior_high": float(prior_high),
                "volume_ratio": float(row["Volume"] / row["VolumeMA50"]) if row["VolumeMA50"] else np.nan,
            }
    return None


def evaluate_mvp_burst(context: AnalysisContext) -> tuple[bool | None, str]:
    if len(context.stock) < 45:
        return None, "MVP 至少需要约 45 个交易日数据"

    recent = context.stock.tail(15).copy()
    prior = context.stock.iloc[-45:-15].copy()
    if recent.empty or prior.empty:
        return None, "MVP 样本不足"

    up_days = int((recent["Close"].diff() > 0).sum())
    start_close = recent["Close"].iloc[0]
    end_close = recent["Close"].iloc[-1]
    prior_avg_volume = prior["Volume"].mean()
    recent_avg_volume = recent["Volume"].mean()
    if not require_values(start_close, end_close, prior_avg_volume, recent_avg_volume) or prior_avg_volume == 0:
        return None, "MVP 数据不足"

    price_move = float(end_close / start_close - 1)
    volume_ratio = float(recent_avg_volume / prior_avg_volume)
    passed = bool(up_days >= 12 and price_move >= 0.20 and volume_ratio >= 1.25)
    detail = (
        f"15 日上涨天数 {up_days}/15"
        f"\n15 日累计涨幅 {fmt_signed_pct(price_move)}"
        f"\n近 15 日均量 / 前 30 日均量 {volume_ratio:.2f}x"
    )
    return passed, detail


def evaluate_follow_through_count(context: AnalysisContext) -> tuple[bool | None, str]:
    breakout = find_recent_breakout(context.stock)
    if breakout is None:
        return None, "近 10 日未识别到有效放量突破"

    start_idx = breakout["index"]
    after_breakout = context.stock.iloc[start_idx + 1 : start_idx + 9].copy()
    if len(after_breakout) < 2:
        return None, f"突破日 {breakout['date'].strftime('%Y-%m-%d')}，后续样本还不足 2 天"

    after_breakout["PrevClose"] = after_breakout["Close"].shift(1)
    after_breakout.iloc[0, after_breakout.columns.get_loc("PrevClose")] = context.stock["Close"].iloc[start_idx]
    day_change = after_breakout["Close"] / after_breakout["PrevClose"] - 1
    first_four = day_change.head(4)
    first_eight = day_change.head(8)
    up4 = int((first_four > 0).sum())
    up8 = int((first_eight > 0).sum())

    if len(first_eight) >= 8:
        passed = bool(up8 >= 6)
        strength = "强机构吸筹特征" if up8 >= 7 else "达到 8 日至少 6 涨"
    elif len(first_four) >= 4:
        passed = bool(up4 >= 3)
        strength = "达到 4 日至少 3 涨" if passed else "未达到 4 日至少 3 涨"
    else:
        return None, (
            f"突破日 {breakout['date'].strftime('%Y-%m-%d')}"
            f"，后续样本还不足 4 天"
        )

    detail = (
        f"突破日 {breakout['date'].strftime('%Y-%m-%d')}"
        f"\n后续 4 日上涨 {up4}/{len(first_four)}"
        f"\n后续 8 日上涨 {up8}/{len(first_eight)}"
        f"\n{strength}"
    )
    return passed, detail


def evaluate_good_closes(context: AnalysisContext) -> tuple[bool | None, str]:
    if len(context.stock) < 10:
        return None, "好收盘统计至少需要近 10 个交易日"

    tail = context.stock.tail(10).copy()
    full_range = tail["High"] - tail["Low"]
    valid = full_range > 0
    if not valid.any():
        return None, "近期高低点区间不足"

    close_location = (tail["Close"] - tail["Low"]) / full_range.where(valid, np.nan)
    good_count = int((close_location >= 0.5).sum())
    bad_count = int((close_location < 0.5).sum())
    passed = bool(good_count > bad_count)
    detail = f"近 10 日上半区收盘 {good_count} 天\n近 10 日下半区收盘 {bad_count} 天"
    return passed, detail


def evaluate_no_three_lower_lows(context: AnalysisContext) -> tuple[bool | None, str]:
    if len(context.stock) < 25:
        return None, "三连阴破位至少需要约 25 个交易日数据"

    tail = context.stock.tail(8).reset_index(drop=True)
    lower_low_flags = (tail["Low"].diff() < 0).tolist()
    longest_run = 0
    run_end = 0
    current_run = 0
    for idx, is_lower in enumerate(lower_low_flags):
        current_run = current_run + 1 if is_lower else 0
        if current_run >= longest_run:
            longest_run = current_run
            run_end = idx

    danger_found = longest_run >= 3
    if not danger_found:
        return True, f"近 8 日最长连续更低低点 {longest_run} 天，未达到 3 天警戒线"

    run_start = run_end - longest_run
    segment = tail.iloc[run_start : run_end + 1]
    falling_closes = int((segment["Close"].diff().iloc[1:] < 0).sum())
    rising_volume_steps = int((segment["Volume"].diff().iloc[1:] > 0).sum())
    volume_steps = max(len(segment) - 1, 1)
    volume_expanding = rising_volume_steps == volume_steps
    severity = "量能逐日放大，风险更强" if volume_expanding else "量能未连续放大，仍需注意"
    detail = (
        f"{segment['Date'].iloc[0].strftime('%Y-%m-%d')} -> {segment['Date'].iloc[-1].strftime('%Y-%m-%d')}"
        f"\n连续 {longest_run} 天更低低点 · 收盘走低 {falling_closes}/{volume_steps} 天"
        f"\n{severity}"
    )
    return False, detail


def evaluate_price_above_ma20_follow_through(context: AnalysisContext) -> tuple[bool | None, str]:
    latest = context.latest
    if not require_values(latest["Close"], latest["MA20"]):
        return None, "MA20 数据不足"

    distance = float(latest["Close"] / latest["MA20"] - 1) if latest["MA20"] else np.nan
    passed = bool(latest["Close"] >= latest["MA20"])
    detail = (
        f"现价 {fmt_price(latest['Close'])} / MA20 {fmt_price(latest['MA20'])}"
        f" · 相差 {fmt_signed_pct(distance)}"
    )
    if not passed:
        detail += "\n收盘低于 MA20，突破后的支撑转弱"
    return passed, detail


SELL_LOOKBACK_WINDOWS = {
    5: "近 5 日",
    10: "近 10 日",
    20: "近 20 日",
}


def find_recent_climax_advance(
    context: AnalysisContext,
) -> tuple[bool | None, str]:
    if len(context.stock) < 16:
        return None, "高潮涨幅至少需要 16 个交易日数据"

    frame = context.stock.tail(16).reset_index(drop=True)
    best: dict[str, Any] | None = None
    end_index = len(frame) - 1
    for window in range(5, 16):
        start_index = end_index - window
        start_close = frame["Close"].iloc[start_index]
        end_close = frame["Close"].iloc[end_index]
        if not require_values(start_close, end_close) or start_close == 0:
            continue
        move = float(end_close / start_close - 1)
        candidate = {
            "window": window,
            "move": move,
            "start": frame["Date"].iloc[start_index],
            "end": frame["Date"].iloc[end_index],
        }
        if best is None or candidate["move"] > best["move"]:
            best = candidate

    if best is None:
        return None, "近期高潮涨幅数据不足"

    danger = bool(best["move"] >= 0.25)
    return (
        not danger,
        f"截至最新日最佳 {best['window']} 日涨幅 {fmt_signed_pct(best['move'])}"
        f" · 警戒线 25%"
        f"\n{fmt_short_date(best['start'])} 至 {fmt_short_date(best['end'])}",
    )


def find_accelerated_up_day_density(
    context: AnalysisContext,
) -> tuple[bool | None, str]:
    if len(context.stock) < 16:
        return None, "上涨日密度至少需要 16 个交易日数据"

    frame = context.stock.tail(16).copy().reset_index(drop=True)
    frame["UpDay"] = frame["Close"].diff() > 0
    best: dict[str, Any] | None = None
    end_index = len(frame) - 1
    for window in range(7, 16):
        start_index = end_index - window
        comparisons = frame["UpDay"].iloc[start_index + 1 : end_index + 1]
        up_days = int(comparisons.sum())
        down_days = int(window - up_days)
        up_ratio = float(up_days / window)
        start_close = frame["Close"].iloc[start_index]
        end_close = frame["Close"].iloc[end_index]
        move = (
            float(end_close / start_close - 1)
            if require_values(start_close, end_close) and start_close
            else np.nan
        )
        candidate = {
            "window": window,
            "up_days": up_days,
            "down_days": down_days,
            "up_ratio": up_ratio,
            "move": move,
            "end": frame["Date"].iloc[end_index],
        }
        candidate_rank = (
            candidate["up_ratio"],
            candidate["move"] if pd.notna(candidate["move"]) else -np.inf,
        )
        best_rank = (
            best["up_ratio"],
            best["move"] if best is not None and pd.notna(best["move"]) else -np.inf,
        ) if best is not None else None
        if best is None or candidate_rank > best_rank:
            best = candidate

    if best is None or not require_values(best["move"]):
        return None, "上涨日密度数据不足"

    danger = bool(best["up_ratio"] >= 0.70 and best["move"] >= 0.10)
    return (
        not danger,
        f"最佳 {best['window']} 日：上涨 {best['up_days']} 天 / 下跌 {best['down_days']} 天"
        f" · 上涨日占比 {best['up_ratio']:.0%}"
        f"\n截至 {fmt_short_date(best['end'])} · 同期涨幅 "
        f"{fmt_signed_pct(best['move'])} · 警戒线 70% 且涨幅至少 10%",
    )


def approximate_stage_two_advance(stock: pd.DataFrame) -> dict[str, Any] | None:
    frame = stock.tail(252).copy().reset_index(drop=True)
    if len(frame) < 30:
        return None

    search_end = max(1, len(frame) - 5)
    candidates: list[dict[str, Any]] = []
    for index in range(10, search_end):
        left = frame["Close"].iloc[max(0, index - 10) : index + 1]
        if frame["Close"].iloc[index] != left.min():
            continue
        subsequent_high = frame["Close"].iloc[index + 1 :].max()
        start_close = frame["Close"].iloc[index]
        if not require_values(start_close, subsequent_high) or start_close == 0:
            continue
        advance = float(subsequent_high / start_close - 1)
        if advance >= 0.30:
            candidates.append({"index": index, "advance": advance, "fallback": False})

    if candidates:
        selected = max(candidates, key=lambda candidate: candidate["index"])
    else:
        fallback_frame = frame.tail(min(126, len(frame)))
        selected = {
            "index": int(fallback_frame["Close"].idxmin()),
            "advance": np.nan,
            "fallback": True,
        }

    move = frame.iloc[selected["index"] :].copy().reset_index(drop=True)
    if len(move) < 6:
        return None
    start_close = move["Close"].iloc[0]
    peak_close = move["Close"].max()
    advance = (
        float(peak_close / start_close - 1)
        if require_values(start_close, peak_close) and start_close
        else np.nan
    )
    return {
        "frame": move,
        "start_date": move["Date"].iloc[0],
        "advance": advance,
        "fallback": selected["fallback"],
    }


def find_long_move_exhaustion_signs(
    context: AnalysisContext,
    signal_days: int,
) -> list[dict[str, Any]]:
    def item(name: str, safe: bool | None, detail: str) -> dict[str, Any]:
        return {"name": name, "passed": safe, "detail": detail}

    names = [
        "长升段最大上涨日出现在观察期",
        "长升段最宽振幅日出现在观察期",
        "观察期内出现衰竭缺口",
    ]
    if len(context.stock) < 30:
        detail = "长升段末端观察至少需要 30 个交易日数据"
        return [item(name, None, detail) for name in names]

    stage = approximate_stage_two_advance(context.stock)
    if stage is None:
        detail = "近期低点之后的长升段样本不足"
        return [item(name, None, detail) for name in names]
    move = stage["frame"].copy()

    move["PrevClose"] = move["Close"].shift(1)
    move["PrevHigh"] = move["High"].shift(1)
    move["DayReturn"] = move["Close"] / move["PrevClose"] - 1
    move["DailySpread"] = (move["High"] - move["Low"]) / move["PrevClose"]
    move["GapUp"] = move["Open"] / move["PrevHigh"] - 1
    recent_start = max(0, len(move) - signal_days)

    largest_up_index = int(move["DayReturn"].idxmax()) if move["DayReturn"].notna().any() else -1
    widest_spread_index = int(move["DailySpread"].idxmax()) if move["DailySpread"].notna().any() else -1
    largest_up_recent = largest_up_index >= recent_start
    widest_spread_recent = widest_spread_index >= recent_start

    largest_up_detail = "长升段上涨日数据不足"
    if largest_up_index >= 0:
        row = move.iloc[largest_up_index]
        largest_up_detail = (
            f"最大上涨日 {fmt_short_date(row['Date'])} · {fmt_signed_pct(row['DayReturn'])}"
        )

    widest_spread_detail = "长升段振幅数据不足"
    if widest_spread_index >= 0:
        row = move.iloc[widest_spread_index]
        widest_spread_detail = (
            f"最宽振幅日 {fmt_short_date(row['Date'])} · {fmt_pct(row['DailySpread'])}"
        )

    recent = move.tail(signal_days)
    exhaustion_gaps = recent[recent["GapUp"] >= 0.02]
    gap_danger = not exhaustion_gaps.empty
    if gap_danger:
        gap_row = exhaustion_gaps.sort_values("GapUp", ascending=False).iloc[0]
        gap_detail = (
            f"{SELL_LOOKBACK_WINDOWS[signal_days]}最大向上缺口 {fmt_short_date(gap_row['Date'])}"
            f" · {fmt_pct(gap_row['GapUp'])}"
            f"\n开盘价相对前一日最高价 · 警戒线 2%"
        )
    else:
        max_gap = recent["GapUp"].replace([np.inf, -np.inf], np.nan).max()
        gap_detail = (
            f"{SELL_LOOKBACK_WINDOWS[signal_days]}最大向上缺口 "
            f"{fmt_pct(max_gap)} · 警戒线 2%"
        )

    return [
        item(names[0], not largest_up_recent, largest_up_detail),
        item(names[1], not widest_spread_recent, widest_spread_detail),
        item(names[2], not gap_danger, gap_detail),
    ]


def find_weakness_signals(
    context: AnalysisContext,
    signal_days: int,
) -> list[dict[str, Any]]:
    def item(
        name: str,
        safe: bool | None,
        detail: str,
        severity: str | None = None,
    ) -> dict[str, Any]:
        return {
            "name": name,
            "passed": safe,
            "detail": detail,
            "severity": severity if safe is False else None,
        }

    stage = approximate_stage_two_advance(context.stock)
    names = [
        "出现本轮最大单日 / 单周跌幅",
        "下跌日出现阶段高额成交量",
    ]
    if stage is None:
        return [item(name, None, "Stage 2 近似区间样本不足") for name in names]

    frame = stage["frame"].copy()
    frame["PrevClose"] = frame["Close"].shift(1)
    frame["DayReturn"] = frame["Close"] / frame["PrevClose"] - 1
    frame["WeekReturn"] = frame["Close"] / frame["Close"].shift(5) - 1
    frame["PriorWorstDay"] = frame["DayReturn"].shift(1).cummin()
    frame["PriorWorstWeek"] = frame["WeekReturn"].shift(1).cummin()
    frame["PriorMaxVolume"] = frame["Volume"].shift(1).cummax()
    frame["PriorVolumeMA20"] = frame["Volume"].shift(1).rolling(20, min_periods=10).mean()
    frame["PriorVolumeP90"] = (
        frame["Volume"].shift(1).expanding(min_periods=10).quantile(0.90)
    )
    recent = frame.tail(signal_days).copy()
    daily_hits = recent[
        (recent["DayReturn"] < 0)
        & recent["PriorWorstDay"].notna()
        & (recent["DayReturn"] <= recent["PriorWorstDay"])
    ]
    daily_danger = not daily_hits.empty
    daily_row = daily_hits.sort_values("DayReturn").iloc[0] if daily_danger else recent.loc[recent["DayReturn"].idxmin()]
    prior_day = daily_row["PriorWorstDay"]
    daily_detail = (
        f"{fmt_short_date(daily_row['Date'])} 跌幅 {fmt_signed_pct(daily_row['DayReturn'])}"
        f" · 此前最差 {fmt_signed_pct(prior_day)}"
    )

    weekly_hits = recent[
        (recent["WeekReturn"] < 0)
        & recent["PriorWorstWeek"].notna()
        & (recent["WeekReturn"] <= recent["PriorWorstWeek"])
    ]
    weekly_danger = not weekly_hits.empty
    weekly_row = weekly_hits.sort_values("WeekReturn").iloc[0] if weekly_danger else recent.loc[recent["WeekReturn"].idxmin()]
    prior_week = weekly_row["PriorWorstWeek"]
    weekly_detail = (
        f"截至 {fmt_short_date(weekly_row['Date'])} 的 5 日跌幅 "
        f"{fmt_signed_pct(weekly_row['WeekReturn'])} · 此前最差 {fmt_signed_pct(prior_week)}"
    )

    unusually_high_volume = (
        recent["PriorVolumeMA20"].notna()
        & (recent["Volume"] >= recent["PriorVolumeMA20"] * 1.5)
    )
    top_decile_volume = (
        recent["PriorVolumeP90"].notna()
        & (recent["Volume"] >= recent["PriorVolumeP90"])
    )
    volume_hits = recent[
        (recent["DayReturn"] < 0)
        & (unusually_high_volume | top_decile_volume)
    ]
    volume_danger = not volume_hits.empty
    if volume_danger:
        volume_row = volume_hits.sort_values("Volume", ascending=False).iloc[0]
    else:
        negative_recent = recent[recent["DayReturn"] < 0]
        volume_row = (
            negative_recent.sort_values("Volume", ascending=False).iloc[0]
            if not negative_recent.empty
            else recent.iloc[-1]
        )
    prior_max_volume = volume_row["PriorMaxVolume"]
    volume_ma20_ratio = (
        float(volume_row["Volume"] / volume_row["PriorVolumeMA20"])
        if require_values(volume_row["Volume"], volume_row["PriorVolumeMA20"])
        and volume_row["PriorVolumeMA20"]
        else np.nan
    )
    volume_p90_ratio = (
        float(volume_row["Volume"] / volume_row["PriorVolumeP90"])
        if require_values(volume_row["Volume"], volume_row["PriorVolumeP90"])
        and volume_row["PriorVolumeP90"]
        else np.nan
    )
    is_stage_high = bool(
        require_values(volume_row["Volume"], prior_max_volume)
        and volume_row["Volume"] >= prior_max_volume
    )
    volume_detail = (
        f"{fmt_short_date(volume_row['Date'])} 涨跌 {fmt_signed_pct(volume_row['DayReturn'])}"
        f" · 成交量 {fmt_volume(volume_row['Volume'])}"
    )
    if volume_danger:
        reasons = []
        if require_values(volume_ma20_ratio) and volume_ma20_ratio >= 1.5:
            reasons.append("高于 20 日均量 1.5 倍")
        if require_values(volume_p90_ratio) and volume_p90_ratio >= 1:
            reasons.append("进入阶段成交量前 10%")
        volume_detail += f"\n{'、'.join(reasons)}"
        if is_stage_high:
            volume_detail += " · 创阶段最高量"

    return [
        item(
            names[0],
            not (daily_danger or weekly_danger),
            f"单日：{daily_detail.splitlines()[0]}"
            f"\n单周：{weekly_detail.splitlines()[0]}",
            "critical",
        ),
        item(names[1], not volume_danger, volume_detail, "warning"),
    ]


def build_sell_indicator_groups(
    context: AnalysisContext,
    signal_days: int,
) -> list[dict[str, Any]]:
    def item(name: str, result: tuple[bool | None, str]) -> dict[str, Any]:
        return {"name": name, "passed": result[0], "detail": result[1]}

    def group(
        key: str,
        title: str,
        subtitle: str,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        states = [entry["passed"] for entry in items]
        passed = None if any(state is None for state in states) else all(state is True for state in states)
        return {
            "key": key,
            "title": title,
            "subtitle": subtitle,
            "passed": passed,
            "attention": True,
            "items": items,
        }

    strength_items = [
        item(
            "Climax Top：近 1-3 周上涨至少 25%",
            find_recent_climax_advance(context),
        ),
        item(
            "上涨日密度：出现高密度加速上涨",
            find_accelerated_up_day_density(context),
        ),
        *find_long_move_exhaustion_signs(context, signal_days),
    ]
    follow_through_items = [
        item("股价保持在 MA20 上方", evaluate_price_above_ma20_follow_through(context)),
        item("突破后 4 日 3 涨或 8 日 6 涨", evaluate_follow_through_count(context)),
        item("未出现连续 3-4 个更低低点", evaluate_no_three_lower_lows(context)),
        item("好收盘多于坏收盘", evaluate_good_closes(context)),
    ]
    stage = approximate_stage_two_advance(context.stock)
    if stage is None:
        stage_summary = "Stage 2 近似区间样本不足"
    else:
        stage_summary = (
            f"Stage 2 近似起点 {fmt_short_date(stage['start_date'])}"
            f" · 阶段最大涨幅 {fmt_signed_pct(stage['advance'])}"
        )
        if stage["fallback"]:
            stage_summary += " · 起点回退为近半年最低收盘"
    return [
        group(
            "follow_through",
            "突破后跟进指标",
            "突破后的支撑与转弱信号",
            follow_through_items,
        ),
        group(
            "selling_into_strength",
            "Selling Into Strength",
            stage_summary,
            strength_items,
        ),
        group(
            "selling_into_weakness",
            "Selling Into Weakness",
            "",
            find_weakness_signals(context, signal_days),
        ),
    ]


BASE_TREND_SPECS = [
    CheckSpec("trend_1", "当前股价高于 150 日和 200 日均线", evaluate_price_above_long_mas),
    CheckSpec("trend_2", "150 日均线高于 200 日均线", evaluate_ma150_above_ma200),
    CheckSpec("trend_3", "200 日均线至少连续 1 个月上升", evaluate_ma200_uptrend),
    CheckSpec("trend_4", "50 日均线高于 150 日和 200 日均线", evaluate_ma50_above_long_mas),
    CheckSpec("trend_5", "当前股价高于 50 日均线", evaluate_price_above_ma50),
    CheckSpec("trend_6", "当前股价较 52 周低点至少高出 30%", evaluate_above_52w_low),
    CheckSpec("trend_7", "当前股价距离 52 周高点不超过 25%", evaluate_near_52w_high),
    CheckSpec("trend_8", "相对 SPY 表现分不低于 60", evaluate_rs_proxy_threshold),
]

TREND_TEMPLATE_VARIANT_EXCLUDED_NAMES = {"相对 SPY 表现分不低于 60"}

def build_checks(specs: list[CheckSpec], context: AnalysisContext) -> list[CheckResult]:
    results: list[CheckResult] = []
    for spec in specs:
        passed, detail = spec.evaluator(context)
        results.append(CheckResult(spec.name, passed, detail))
    return results


def fmt_price(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{value:,.2f}"


def fmt_pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{value:.1%}"


def fmt_signed_pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1%}"


def fmt_volume(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    absolute = abs(float(value))
    if absolute >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if absolute >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if absolute >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:,.0f}"


def fmt_prompt_return(history: pd.DataFrame, days: int) -> str:
    if len(history) <= days:
        return "数据不足"
    latest_close = history["Close"].iloc[-1]
    base_close = history["Close"].iloc[-days - 1]
    if not require_values(latest_close, base_close) or base_close == 0:
        return "数据不足"
    return fmt_signed_pct(float(latest_close / base_close - 1))


def fmt_prompt_ma_position(latest: pd.Series, field: str) -> str:
    ma_value = latest.get(field)
    close_value = latest.get("Close")
    if not require_values(close_value, ma_value) or ma_value == 0:
        return f"{field} 数据不足"
    delta = float(close_value / ma_value - 1)
    relation = "上方" if delta >= 0 else "下方"
    return f"{field} {relation} {fmt_pct(abs(delta))}"


def build_check_summary_lines(checks: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in checks:
        name = strip_check_name_prefix(item.get("name", ""))
        passed = item.get("passed")
        detail = str(item.get("detail", "")).strip().replace("\n", "；")
        status = "通过" if passed is True else "未通过" if passed is False else "待确认"
        lines.append(f"- {name}：{status}；{detail}")
    return lines


def build_indicator_group_summary_lines(
    groups: list[dict[str, Any]],
    signal_semantics: bool = False,
) -> list[str]:
    lines: list[str] = []
    for group in groups:
        title = str(group.get("title", "")).strip()
        subtitle = str(group.get("subtitle", "")).strip()
        header = f"- **{title}**"
        if subtitle:
            header += f"：{subtitle}"
        lines.append(header)
        for item in group.get("items", []):
            passed = item.get("passed")
            if signal_semantics:
                status = "未触发" if passed is True else "已触发" if passed is False else "待确认"
            else:
                status = "通过" if passed is True else "未通过" if passed is False else "待确认"
            name = strip_check_name_prefix(item.get("name", ""))
            detail = str(item.get("detail", "")).strip().replace("\n", "；")
            lines.append(f"  - {name}：{status}；{detail}")
    return lines


def fmt_close_in_range(high: float | None, low: float | None, close: float | None) -> str:
    if not require_values(high, low, close) or high == low:
        return "区间位置数据不足"
    ratio = (float(close) - float(low)) / (float(high) - float(low))
    return f"收盘位于日内区间 {ratio * 100:.0f}%"


def build_raw_session_line(row: pd.Series, prev_close: float | None, volume_ma50: float | None) -> str:
    day_change = None
    if require_values(row.get("Close"), prev_close) and prev_close and prev_close != 0:
        day_change = float(row["Close"] / prev_close - 1)
    volume_ratio = None
    if require_values(row.get("Volume"), volume_ma50) and volume_ma50 and volume_ma50 != 0:
        volume_ratio = float(row["Volume"] / volume_ma50)

    parts = [
        f"{row.get('Date', '-')}",
        f"O {fmt_price(row.get('Open'))}",
        f"H {fmt_price(row.get('High'))}",
        f"L {fmt_price(row.get('Low'))}",
        f"C {fmt_price(row.get('Close'))}",
        f"日涨跌 {fmt_signed_pct(day_change)}",
        f"量 {fmt_volume(row.get('Volume'))}",
    ]
    if volume_ratio is not None:
        parts.append(f"量/50日均量 {volume_ratio:.2f}x")
    parts.append(fmt_close_in_range(row.get("High"), row.get("Low"), row.get("Close")))
    return "；".join(parts)


def read_prompt_template() -> str:
    if not PROMPT_TEMPLATE_PATH.exists():
        raise ValueError(f"缺少提示词模板文件: {PROMPT_TEMPLATE_PATH}")
    text = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"提示词模板文件为空: {PROMPT_TEMPLATE_PATH}")
    return text


def summarize_check_group(checks: list[CheckResult]) -> tuple[int, int, str]:
    passed = sum(item.passed is True for item in checks)
    total = len(checks)
    if total and all(item.passed is True for item in checks):
        return passed, total, "是"
    if any(item.passed is None for item in checks) and not any(item.passed is False for item in checks):
        return passed, total, "待确认"
    return passed, total, "否"


def serialize_checks(checks: list[CheckResult]) -> list[dict[str, Any]]:
    return [asdict(check) for check in checks]


def serialize_history(frame: pd.DataFrame) -> list[dict[str, Any]]:
    columns = ["Date", "Open", "High", "Low", "Close", "Volume", "MA20", "MA50", "MA150", "MA200"]
    subset = frame[columns].copy()
    subset["Date"] = subset["Date"].dt.strftime("%Y-%m-%d")
    subset = subset.replace({np.nan: None})
    return subset.to_dict(orient="records")


def serialize_price_history(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    subset = frame[["Date", "Open", "High", "Low", "Close"]].copy()
    subset["Date"] = subset["Date"].dt.strftime("%Y-%m-%d")
    subset = subset.replace({np.nan: None})
    return subset.to_dict(orient="records")


def build_trend_sparkline(frame: pd.DataFrame) -> dict[str, Any]:
    window = frame.tail(min(35, len(frame))).copy()
    if window.empty:
        return {"direction": "flat", "values": []}

    ma20 = window["MA20"].copy()
    fallback = frame["Close"].expanding(min_periods=1).mean().tail(len(window)).reset_index(drop=True)
    ma20 = ma20.reset_index(drop=True)
    close = window["Close"].reset_index(drop=True)
    ma20_base = ma20.where(ma20.notna(), fallback)
    base = ma20_base * 0.7 + close * 0.3
    smooth = base.ewm(span=3, adjust=False).mean().dropna()
    values = [round(float(value), 4) for value in smooth.tolist()]

    if len(values) < 2:
        return {"direction": "flat", "values": values}

    recent = values[-10:] if len(values) >= 10 else values
    start = recent[0]
    end = recent[-1]
    move_pct = 0.0 if start == 0 else float(end / start - 1)
    x = np.arange(len(recent), dtype=float)
    slope = float(np.polyfit(x, np.array(recent, dtype=float), 1)[0]) if len(recent) >= 2 else 0.0

    if move_pct >= 0.015 and slope > 0:
        direction = "up"
    elif move_pct <= -0.015 and slope < 0:
        direction = "down"
    else:
        direction = "flat"

    return {
        "direction": direction,
        "values": values,
    }


def build_technical_summary(data: dict[str, Any]) -> str:
    history = pd.DataFrame(data.get("history") or [])
    if history.empty:
        return "- 本地没有可用价格历史。"

    history = history.copy()
    history["PrevClose"] = history["Close"].shift(1)
    history["VolumeMA50Calc"] = history["Volume"].rolling(50).mean()
    latest = history.iloc[-1]
    recent_20 = history.tail(min(20, len(history))).copy()
    recent_volume_ma50 = history["Volume"].rolling(50).mean().iloc[-1] if "Volume" in history else np.nan
    volume_ratio = None
    if require_values(latest.get("Volume"), recent_volume_ma50) and recent_volume_ma50:
        volume_ratio = float(latest["Volume"] / recent_volume_ma50)

    latest_close = data.get("latestClose")
    six_month_high = data.get("sixMonthHigh")
    six_month_low = data.get("sixMonthLow")
    distance_from_high = None
    distance_from_low = None
    if require_values(latest_close, six_month_high) and six_month_high:
        distance_from_high = max(0.0, 1 - float(latest_close) / float(six_month_high))
    if require_values(latest_close, six_month_low) and six_month_low:
        distance_from_low = max(0.0, float(latest_close) / float(six_month_low) - 1)

    range_position = None
    accumulation_days = 0
    distribution_days = 0
    if len(recent_20) >= 2:
        recent_high = recent_20["High"].max()
        recent_low = recent_20["Low"].min()
        if require_values(latest_close, recent_high, recent_low) and recent_high != recent_low:
            range_position = float((float(latest_close) - float(recent_low)) / (float(recent_high) - float(recent_low)))

        recent_20["PrevClose"] = recent_20["Close"].shift(1)
        recent_20["PrevVolume"] = recent_20["Volume"].shift(1)
        accumulation_days = int(((recent_20["Close"] > recent_20["PrevClose"]) & (recent_20["Volume"] > recent_20["PrevVolume"])).sum())
        distribution_days = int(((recent_20["Close"] < recent_20["PrevClose"]) & (recent_20["Volume"] > recent_20["PrevVolume"])).sum())

    price_snapshot = [
        f"最新收盘 {data.get('latestCloseText', '-')}",
        f"较前收盘 {data.get('dailyChangePctText', '-')}",
        f"最新收盘日成交量 {data.get('latestVolumeText', '-')}",
    ]
    if volume_ratio is not None:
        price_snapshot.append(f"约为 50 日均量的 {volume_ratio:.2f} 倍")

    summary_lines = [
        "### 价格与位置",
        f"- {'；'.join(price_snapshot)}",
        f"- 阶段涨跌幅：5 日 {fmt_prompt_return(history, 5)}；20 日 {fmt_prompt_return(history, 20)}；60 日 {fmt_prompt_return(history, 60)}；126 日 {fmt_prompt_return(history, 126)}",
        f"- 均线位置：{fmt_prompt_ma_position(latest, 'MA20')}；{fmt_prompt_ma_position(latest, 'MA50')}；{fmt_prompt_ma_position(latest, 'MA150')}；{fmt_prompt_ma_position(latest, 'MA200')}",
        f"- 近 6 个月位置：距高点 {fmt_pct(distance_from_high)}；距低点 {fmt_pct(distance_from_low)}",
        "### 趋势模板",
        f"- 基础趋势模板 1-8：{data.get('trendPassCount', 0)}/{data.get('trendTotal', 0)}",
    ]

    base_failures = [
        strip_check_name_prefix(item.get("name", ""))
        for item in data.get("trendChecks", [])
        if item.get("passed") is False
    ]
    if base_failures:
        summary_lines.append(f"- 当前未通过项：{'；'.join(base_failures)}")
    summary_lines.append("### 趋势模板检查明细")
    summary_lines.extend(build_check_summary_lines(data.get("trendChecks", [])))

    rs_detail = str(data.get("rsDetail", "")).strip()
    if rs_detail:
        summary_lines.extend([
            "### 相对大盘表现",
            f"- {rs_detail}",
        ])

    summary_lines.append("### 吸筹/派发线索")
    if range_position is not None:
        summary_lines.append(f"- 近 20 日区间位置：约处在区间的 {range_position * 100:.0f}% 位置")
    summary_lines.append(f"- 近 20 日疑似吸筹日 {accumulation_days} 天；疑似派发日 {distribution_days} 天（定义：涨/跌且成交量高于前一日）")
    summary_lines.append("### 最近 20 个交易日原始量价")
    recent_20_raw = history.tail(min(20, len(history)))
    for _, row in recent_20_raw.iterrows():
        summary_lines.append(f"- {build_raw_session_line(row, row.get('PrevClose'), row.get('VolumeMA50Calc'))}")

    summary_lines.append("### 近 20 日关键量价日")
    key_days = recent_20.copy()
    key_days["VolumeRatioCalc"] = key_days["Volume"] / key_days["VolumeMA50Calc"]
    key_days = key_days.replace([np.inf, -np.inf], np.nan).dropna(subset=["VolumeRatioCalc"])
    key_days = key_days.sort_values("VolumeRatioCalc", ascending=False).head(4).sort_values("Date")
    if key_days.empty:
        summary_lines.append("- 量比数据不足。")
    else:
        for _, row in key_days.iterrows():
            summary_lines.append(f"- {build_raw_session_line(row, row.get('PrevClose'), row.get('VolumeMA50Calc'))}")

    summary_lines.append("### 买入指标观察（默认近 3 个月回撤窗口）")
    buy_groups = data.get("buyIndicatorGroupsByWindow", {}).get("63", [])
    if buy_groups:
        summary_lines.extend(build_indicator_group_summary_lines(buy_groups))
    else:
        summary_lines.extend(build_check_summary_lines(data.get("advancedTrendChecks", [])))

    summary_lines.append("### 卖出指标观察（默认近 10 个交易日信号窗口）")
    sell_groups = data.get("sellIndicatorGroupsByWindow", {}).get("10", [])
    if sell_groups:
        summary_lines.extend(
            build_indicator_group_summary_lines(sell_groups, signal_semantics=True)
        )
    else:
        summary_lines.extend(build_check_summary_lines(data.get("patternRiskChecks", [])))
    return "\n".join(summary_lines)


def build_holding_block(holding: dict[str, Any] | None) -> str:
    if not holding or not holding.get("isHolding"):
        return "（当前标的未标记为持仓）"

    cost_basis = coerce_float(holding.get("costBasis"))
    shares = coerce_float(holding.get("shares"))
    latest_close = coerce_float(holding.get("latestClose"))
    pnl_pct = coerce_float(holding.get("pnlPct"))
    pnl_value = coerce_float(holding.get("pnlValue"))

    lines = ["- 当前标的已标记为持仓。"]
    if cost_basis is not None:
        lines.append(f"- 成本价：{fmt_price(cost_basis)}")
    if shares is not None:
        lines.append(f"- 股数：{shares:g}")
    if latest_close is not None:
        lines.append(f"- 当前用于估算的最新收盘价：{fmt_price(latest_close)}")
    if pnl_pct is not None:
        lines.append(f"- 当前浮盈亏比例：{fmt_signed_pct(pnl_pct)}")
    if pnl_value is not None:
        lines.append(f"- 当前浮盈亏金额：{fmt_signed_price(pnl_value)}")
    return "\n".join(lines)


def coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(numeric):
        return None
    return numeric


def fmt_signed_price(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:,.2f}"


def build_prompt_from_analysis(data: dict[str, Any], note: str = "", holding: dict[str, Any] | None = None) -> str:
    template = read_prompt_template()
    note_text = note.strip()
    note_block = note_text if note_text else "（无）"
    replacements = {
        "{{symbol}}": str(data.get("symbol", "")).strip(),
        "{{latest_date}}": str(data.get("latestDate", "")).strip(),
        "{{technical_summary}}": build_technical_summary(data),
        "{{holding_block}}": build_holding_block(holding),
        "{{note}}": note_text,
        "{{note_block}}": note_block,
    }
    prompt = template
    for key, value in replacements.items():
        prompt = prompt.replace(key, value)
    return prompt


def analyze_symbol(
    symbol: str,
    force_refresh: bool = False,
    allow_network: bool = True,
    refresh_benchmark: bool = False,
    tiingo_api_key: str | None = None,
) -> dict[str, Any]:
    normalized = normalize_symbol(symbol)
    if not normalized:
        raise ValueError("请输入有效的股票代码。")

    cache_key = ("analysis", normalized)
    cached = get_cached(cache_key)
    if cached is not None and not force_refresh:
        return cached

    if force_refresh:
        clear_symbol_memory_cache(normalized)
        if refresh_benchmark:
            clear_symbol_memory_cache(DEFAULT_BENCHMARK)

    raw_history = load_history(
        normalized,
        DEFAULT_HISTORY_PERIOD,
        force_refresh=force_refresh,
        allow_network=allow_network,
        tiingo_api_key=tiingo_api_key,
    )
    if raw_history.empty:
        if allow_network:
            raise ValueError(f"{normalized} 未返回任何价格数据，可能是代码无效或接口当前失败。")
        raise ValueError(f"{normalized} 本地还没有缓存数据。请点击“拉新”获取后再查看。")

    history = add_indicators(raw_history)
    rs_score = None
    rs_detail = f"本地未缓存 {DEFAULT_BENCHMARK}，相对 SPY 表现分暂不可用。点击“拉新”后可补齐。"
    benchmark_history = pd.DataFrame()
    raw_benchmark_history = raw_history if normalized == DEFAULT_BENCHMARK else load_history(
        DEFAULT_BENCHMARK,
        DEFAULT_HISTORY_PERIOD,
        force_refresh=force_refresh and refresh_benchmark,
        allow_network=allow_network,
        tiingo_api_key=tiingo_api_key,
    )
    if not raw_benchmark_history.empty:
        benchmark_history = add_indicators(raw_benchmark_history)
        rs_score, rs_detail = compute_rs_proxy(history, benchmark_history)
    analysis_context = AnalysisContext(
        stock=history,
        benchmark=benchmark_history,
        latest=history.iloc[-1],
        rs_score=rs_score,
        rs_detail=rs_detail,
    )
    trend_checks = build_checks(BASE_TREND_SPECS, analysis_context)
    previous_range_check = CheckResult(
        "前一日振幅低于 50 日均振幅",
        *evaluate_latest_range_below_ma50(analysis_context),
    )
    buy_indicator_groups_by_window = {
        str(days): build_buy_indicator_groups(analysis_context, days)
        for days in BUY_LOOKBACK_WINDOWS
    }
    advanced_trend_checks = flatten_buy_indicator_groups(buy_indicator_groups_by_window["63"])
    sell_indicator_groups_by_window = {
        str(days): build_sell_indicator_groups(analysis_context, days)
        for days in SELL_LOOKBACK_WINDOWS
    }
    sell_indicator_groups = sell_indicator_groups_by_window["10"]
    pattern_risk_checks = flatten_buy_indicator_groups(sell_indicator_groups)
    latest = analysis_context.latest
    prev_close = history["Close"].iloc[-2] if len(history) >= 2 else np.nan
    daily_change_pct = None
    if require_values(latest["Close"], prev_close) and prev_close != 0:
        daily_change_pct = float(latest["Close"] / prev_close - 1)
    recent_five_window = history["Close"].tail(5)
    five_day_change_pct = None
    if len(recent_five_window) >= 5:
        base_close = recent_five_window.iloc[0]
        latest_close = recent_five_window.iloc[-1]
        if require_values(base_close, latest_close) and base_close != 0:
            five_day_change_pct = float(latest_close / base_close - 1)
    trend_sparkline = build_trend_sparkline(history)
    recent_window = history.tail(min(126, len(history)))
    six_month_high = recent_window["Close"].max() if not recent_window.empty else np.nan
    six_month_low = recent_window["Close"].min() if not recent_window.empty else np.nan
    is_six_month_high = bool(require_values(latest["Close"], six_month_high) and latest["Close"] >= six_month_high)
    is_six_month_low = bool(require_values(latest["Close"], six_month_low) and latest["Close"] <= six_month_low)
    trend_pass_count, trend_total, trend_status = summarize_check_group(trend_checks)
    trend_variant_base_checks = [
        check
        for check in trend_checks
        if check.name not in TREND_TEMPLATE_VARIANT_EXCLUDED_NAMES
    ]
    trend_variant_match = bool(
        trend_variant_base_checks
        and all(check.passed is True for check in trend_variant_base_checks)
    )
    advanced_trend_pass_count, advanced_trend_total, advanced_trend_status = summarize_check_group(
        [
            CheckResult(item["name"], item["passed"], item["detail"])
            for item in advanced_trend_checks
        ]
    )
    latest_volume_ma50 = history["Volume"].rolling(50).mean().iloc[-1]
    latest_volume_below_ma50 = bool(
        require_values(latest["Volume"], latest_volume_ma50)
        and latest["Volume"] < latest_volume_ma50
    )
    latest_volume_ratio_ma50 = None
    if require_values(latest["Volume"], latest_volume_ma50) and latest_volume_ma50:
        latest_volume_ratio_ma50 = float(latest["Volume"] / latest_volume_ma50)

    result = {
        "symbol": normalized,
        "latestClose": None if pd.isna(latest["Close"]) else float(latest["Close"]),
        "latestCloseText": fmt_price(latest["Close"]),
        "latestVolume": None if pd.isna(latest["Volume"]) else float(latest["Volume"]),
        "latestVolumeText": fmt_volume(latest["Volume"]),
        "latestVolumeMA50": None if pd.isna(latest_volume_ma50) else float(latest_volume_ma50),
        "latestVolumeBelowMA50": latest_volume_below_ma50,
        "latestVolumeRatioMA50": (
            None if latest_volume_ratio_ma50 is None else round(latest_volume_ratio_ma50, 4)
        ),
        "latestDate": history["Date"].iloc[-1].strftime("%Y-%m-%d"),
        "dailyChangePct": None if daily_change_pct is None else round(daily_change_pct, 4),
        "dailyChangePctText": fmt_signed_pct(daily_change_pct),
        "fiveDayChangePct": None if five_day_change_pct is None else round(five_day_change_pct, 4),
        "fiveDayChangePctText": fmt_signed_pct(five_day_change_pct),
        "trendSparklineDirection": trend_sparkline["direction"],
        "trendSparklineValues": trend_sparkline["values"],
        "sixMonthHigh": None if pd.isna(six_month_high) else float(six_month_high),
        "sixMonthHighText": fmt_price(six_month_high),
        "sixMonthLow": None if pd.isna(six_month_low) else float(six_month_low),
        "sixMonthLowText": fmt_price(six_month_low),
        "isSixMonthHigh": is_six_month_high,
        "isSixMonthLow": is_six_month_low,
        "trendPassCount": trend_pass_count,
        "trendTotal": trend_total,
        "trendStatus": trend_status,
        "trendTemplateVariantMatch": trend_variant_match,
        "previousRangeBelowMA50": previous_range_check.passed,
        "previousRangeBelowMA50Detail": previous_range_check.detail,
        "advancedTrendPassCount": advanced_trend_pass_count,
        "advancedTrendTotal": advanced_trend_total,
        "advancedTrendStatus": advanced_trend_status,
        "rsScore": None if rs_score is None else round(float(rs_score), 1),
        "rsDetail": rs_detail,
        "sourceNotes": [
            note
            for note in [
                raw_history.attrs.get("source_note", ""),
                raw_benchmark_history.attrs.get("source_note", ""),
            ]
            if note
        ],
        "trendChecks": serialize_checks(trend_checks),
        "advancedTrendChecks": advanced_trend_checks,
        "buyIndicatorGroupsByWindow": buy_indicator_groups_by_window,
        "buyIndicatorWindow": 63,
        "patternRiskChecks": pattern_risk_checks,
        "sellIndicatorGroups": sell_indicator_groups,
        "sellIndicatorGroupsByWindow": sell_indicator_groups_by_window,
        "sellIndicatorWindow": 10,
        "history": serialize_history(history),
        "benchmarkSymbol": DEFAULT_BENCHMARK,
        "benchmarkHistory": serialize_price_history(benchmark_history),
    }
    return set_cached(cache_key, result)


def summary_payload(
    symbol: str,
    force_refresh: bool = False,
    allow_network: bool = True,
    refresh_benchmark: bool = False,
    tiingo_api_key: str | None = None,
) -> dict[str, Any]:
    data = analyze_symbol(
        symbol,
        force_refresh=force_refresh,
        allow_network=allow_network,
        refresh_benchmark=refresh_benchmark,
        tiingo_api_key=tiingo_api_key,
    )
    return {
        "symbol": data["symbol"],
        "latestClose": data["latestClose"],
        "latestCloseText": data["latestCloseText"],
        "latestVolume": data["latestVolume"],
        "latestVolumeText": data["latestVolumeText"],
        "latestVolumeMA50": data["latestVolumeMA50"],
        "latestVolumeBelowMA50": data["latestVolumeBelowMA50"],
        "latestVolumeRatioMA50": data["latestVolumeRatioMA50"],
        "latestDate": data["latestDate"],
        "dailyChangePct": data["dailyChangePct"],
        "dailyChangePctText": data["dailyChangePctText"],
        "trendSparklineDirection": data["trendSparklineDirection"],
        "trendSparklineValues": data["trendSparklineValues"],
        "isSixMonthHigh": data["isSixMonthHigh"],
        "isSixMonthLow": data["isSixMonthLow"],
        "sixMonthHighText": data["sixMonthHighText"],
        "sixMonthLowText": data["sixMonthLowText"],
        "trendPassCount": data["trendPassCount"],
        "trendTotal": data["trendTotal"],
        "trendStatus": data["trendStatus"],
        "trendTemplateVariantMatch": data["trendTemplateVariantMatch"],
        "previousRangeBelowMA50": data["previousRangeBelowMA50"],
        "previousRangeBelowMA50Detail": data["previousRangeBelowMA50Detail"],
    }


app = FastAPI(title="Trend Deck")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def root() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/review")
def review() -> FileResponse:
    return FileResponse(STATIC_DIR / "review.html")


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    return {
        "defaultWatchlist": DEFAULT_WATCHLIST,
        "watchlistGroups": DEFAULT_WATCHLIST_GROUPS,
        "benchmark": DEFAULT_BENCHMARK,
    }


@app.get("/api/watchlist/state")
def get_watchlist_state() -> dict[str, Any]:
    try:
        state = load_watchlist_state()
        return {
            "configured": state is not None,
            "watchlist": state["watchlist"] if state else DEFAULT_WATCHLIST,
            "groups": state["groups"] if state else DEFAULT_WATCHLIST_GROUPS,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.put("/api/watchlist/state")
def put_watchlist_state(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        state = save_watchlist_state(payload)
        return {"configured": True, **state}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/watchlist/summary")
def watchlist_summary(
    symbols: str = Query(..., description="Comma separated stock symbols"),
    refresh: bool = Query(False, description="Force incremental refresh from online sources"),
) -> dict[str, Any]:
    normalized_symbols = [normalize_symbol(symbol) for symbol in symbols.split(",")]
    normalized_symbols = [symbol for symbol in normalized_symbols if symbol]
    if not normalized_symbols:
        raise HTTPException(status_code=400, detail="缺少有效股票代码。")

    benchmark_in_watchlist = DEFAULT_BENCHMARK in normalized_symbols
    refresh_api_keys = {symbol: get_refresh_api_key_for_index(index) for index, symbol in enumerate(normalized_symbols)}
    benchmark_refresh_api_key = get_refresh_api_key_for_index(0)

    if refresh and not benchmark_in_watchlist:
        clear_symbol_memory_cache(DEFAULT_BENCHMARK)
        try:
            load_history(
                DEFAULT_BENCHMARK,
                DEFAULT_HISTORY_PERIOD,
                force_refresh=True,
                allow_network=True,
                tiingo_api_key=benchmark_refresh_api_key,
            )
        except Exception:
            pass

    results: dict[str, dict[str, Any]] = {}
    max_workers = min(2 if refresh else 4, max(1, len(normalized_symbols)))

    def load_item(symbol: str) -> dict[str, Any]:
        try:
            return {
                "symbol": symbol,
                "data": summary_payload(
                    symbol,
                    force_refresh=refresh,
                    allow_network=refresh,
                    refresh_benchmark=False,
                    tiingo_api_key=refresh_api_keys.get(symbol),
                ),
                "error": None,
            }
        except Exception as exc:
            return {"symbol": symbol, "data": None, "error": str(exc)}

    if refresh and benchmark_in_watchlist:
        results[DEFAULT_BENCHMARK] = load_item(DEFAULT_BENCHMARK)

    remaining_symbols = [
        symbol for symbol in normalized_symbols
        if not (refresh and benchmark_in_watchlist and symbol == DEFAULT_BENCHMARK)
    ]

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(load_item, symbol): symbol for symbol in remaining_symbols}
        for future in as_completed(future_map):
            symbol = future_map[future]
            results[symbol] = future.result()

    items = [results[symbol] for symbol in normalized_symbols]
    return {"items": items}


@app.get("/api/symbol/{symbol}")
def symbol_detail(
    symbol: str,
    refresh: bool = Query(False, description="Force incremental refresh from online sources"),
) -> dict[str, Any]:
    normalized = normalize_symbol(symbol)
    if not normalized:
        raise HTTPException(status_code=400, detail="请输入有效的股票代码。")
    try:
        return analyze_symbol(
            normalized,
            force_refresh=refresh,
            allow_network=refresh,
            refresh_benchmark=refresh,
            tiingo_api_key=get_tiingo_api_key(),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/trades")
def get_trades() -> dict[str, Any]:
    try:
        trades = list_all_trades()
        return {
            "symbols": sorted({str(trade["symbol"]) for trade in trades}),
            "trades": trades,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/review/ledger")
def get_review_ledger() -> dict[str, Any]:
    try:
        return load_ibkr_review_ledger()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/trades")
def add_trade(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    normalized = normalize_symbol(str(payload.get("symbol", "")))
    if not normalized:
        raise HTTPException(status_code=400, detail="请输入有效的股票代码。")
    try:
        return create_trade(
            normalized,
            str(payload.get("note", "")),
            str(payload.get("currency", "USD")),
            str(payload.get("direction", "long")),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.put("/api/trades/{trade_id}")
def update_trade(trade_id: int, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return update_trade_note(trade_id, str(payload.get("note", "")))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/trades/{trade_id}/transactions")
def add_trade_transaction(
    trade_id: int,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    try:
        return create_transaction(trade_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.delete("/api/trades/{trade_id}/transactions/{transaction_index}")
def remove_trade_transaction(trade_id: int, transaction_index: int) -> dict[str, Any]:
    try:
        if not delete_transaction(trade_id, transaction_index):
            raise HTTPException(status_code=404, detail="交易记录不存在。")
        return {"deleted": True}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/prompt/{symbol}")
def symbol_prompt(
    symbol: str,
    payload: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    normalized = normalize_symbol(symbol)
    if not normalized:
        raise HTTPException(status_code=400, detail="请输入有效的股票代码。")
    try:
        data = analyze_symbol(
            normalized,
            force_refresh=False,
            allow_network=False,
            refresh_benchmark=False,
            tiingo_api_key=None,
        )
        note = str((payload or {}).get("note", "")).strip()
        holding = (payload or {}).get("holding")
        return {
            "symbol": normalized,
            "prompt": build_prompt_from_analysis(data, note=note, holding=holding if isinstance(holding, dict) else None),
            "templatePath": str(PROMPT_TEMPLATE_PATH),
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
