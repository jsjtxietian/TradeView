from __future__ import annotations

import os
import time
import tomllib
from pathlib import Path
from typing import Any

import pandas as pd
from curl_cffi import requests as curl_requests

from trenddeck.config import (
    DEFAULT_HISTORY_PERIOD,
    LEGACY_PRICE_MODE,
    MEMORY_CACHE_TTL,
    PERIOD_TO_DAYS,
    PREFERRED_PRICE_MODE,
    PRICE_MODE_COLUMN,
    PROJECT_ROOT,
    REFRESH_COOLDOWN_SECONDS,
    STOCK_DIR,
    TIINGO_REFRESH_BATCH_SIZE,
)

_memory_cache: dict[tuple[Any, ...], tuple[float, Any]] = {}


class MarketDataRateLimitError(ValueError):
    """The upstream market-data provider rejected the request due to rate limits."""


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


def load_local_secrets() -> dict[str, Any]:
    secrets_path = PROJECT_ROOT / ".streamlit" / "secrets.toml"
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
    return STOCK_DIR / f"{safe_symbol}_{period}_history.csv"


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
    if response.status_code == 429:
        retry_after = str(response.headers.get("Retry-After", "")).strip()
        retry_hint = f"，Retry-After: {retry_after}" if retry_after else ""
        raise MarketDataRateLimitError(f"Tiingo API 限流: HTTP 429{retry_hint}。")
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
    require_refresh_success: bool = False,
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
    if (
        force_refresh
        and not disk_cached.empty
        and not legacy_cache_needs_rebuild
        and is_refresh_cooldown_active(symbol, period)
    ):
        disk_cached.attrs["source_note"] = f"{symbol} 刚刚已拉新过，短时间内直接复用本地缓存。"
        return set_cached(cache_key, disk_cached.copy()).copy()

    incremental_start = None
    tiingo_had_no_data = False
    if not disk_cached.empty and not legacy_cache_needs_rebuild:
        incremental_start = (disk_cached["Date"].max() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    tiingo_error: Exception | None = None
    tiingo_errors: list[Exception] = []
    api_keys = get_tiingo_api_key_candidates(tiingo_api_key)
    if require_refresh_success and not api_keys:
        raise ValueError(f"{symbol} 无法刷新：服务器未配置 Tiingo API key。")
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
                tiingo_errors.append(exc)

    if require_refresh_success:
        if tiingo_errors:
            details = "; ".join(str(error) for error in tiingo_errors)
            raise ValueError(
                f"{symbol} Tiingo 刷新失败（已尝试 {len(tiingo_errors)} 个 API key）：{details}"
            )
        if tiingo_had_no_data:
            raise ValueError(f"{symbol} 在 Tiingo 中没有返回可用行情，代码可能无效。")
        raise ValueError(f"{symbol} 行情刷新未成功，旧缓存未作为最新数据返回。")

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
