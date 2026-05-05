from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
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
STATIC_DIR = Path("static")
PROMPT_TEMPLATE_PATH = Path("prompt_template.md")
DEFAULT_HISTORY_PERIOD = "3y"
DEFAULT_BENCHMARK = "SPY"
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
        return None, "历史数据不足，无法计算 RS 代理分数"

    merged = merged.sort_values("Date").reset_index(drop=True)
    weights = [0.4, 0.2, 0.2, 0.2]
    excess_returns = []
    for days, weight in zip(horizons, weights):
        stock_return = merged["Close_stock"].iloc[-1] / merged["Close_stock"].iloc[-days - 1] - 1
        bench_return = merged["Close_bench"].iloc[-1] / merged["Close_bench"].iloc[-days - 1] - 1
        excess_returns.append((stock_return - bench_return) * weight)

    weighted_excess = float(sum(excess_returns))
    score = float(np.clip(50 + weighted_excess * 100, 1, 99))
    return score, f"相对 {DEFAULT_BENCHMARK} 的 RS 代理分数: {score:.1f}"


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
    return bool(context.rs_score >= 70), context.rs_detail


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


def evaluate_volume_price_health(context: AnalysisContext) -> tuple[bool | None, str]:
    if len(context.stock) < 60:
        return None, "量价健康度至少需要约 60 个交易日数据"

    enriched = context.stock.copy()
    enriched["PrevClose"] = enriched["Close"].shift(1)
    enriched["VolumeMA50"] = enriched["Volume"].rolling(50).mean()
    tail = enriched.tail(30)
    if tail["VolumeMA50"].isna().all():
        return None, "50 日均量数据不足"

    volume_ratio = tail["Volume"] / tail["VolumeMA50"]
    volume_signal = volume_ratio > 1.05
    day_return = tail["Close"] / tail["PrevClose"] - 1
    weighted_move = day_return.abs() * volume_ratio.where(volume_signal, 0)

    up_days = int(((day_return > 0) & volume_signal).sum())
    down_days = int(((day_return < 0) & volume_signal).sum())
    up_score = float(weighted_move.where(day_return > 0, 0).sum())
    down_score = float(weighted_move.where(day_return < 0, 0).sum())

    passed = bool(
        up_days >= 3
        and up_days >= down_days
        and up_score >= max(down_score * 1.25, down_score + 0.02)
    )
    detail = (
        f"上涨: {up_days} 天 / 加权强度 {fmt_pct(up_score)}"
        f"\n下跌: {down_days} 天 / 加权强度 {fmt_pct(down_score)}（基准: 50 日均量）"
    )
    return passed, detail


def evaluate_pullback_depth_limit(context: AnalysisContext) -> tuple[bool | None, str]:
    if len(context.stock) < 63:
        return None, "回调深度至少需要近 3 个月数据"

    lookback = context.stock.tail(min(126, len(context.stock)))
    recent_high = lookback["High"].max()
    latest_close = context.latest["Close"]
    if not require_values(recent_high, latest_close) or recent_high == 0:
        return None, "近期高点数据不足"

    drawdown = 1 - latest_close / recent_high
    passed = bool(drawdown <= 0.35)
    detail = f"距近 6 个月高点回调 {fmt_pct(drawdown)} / 理想上限 35% / 硬上限 50%"
    if not passed and drawdown <= 0.50:
        detail += "，已超过理想区间但尚未跌破硬上限"
    if drawdown > 0.50:
        detail += "，已超过 50% 硬上限"
    return passed, detail


def evaluate_vcp_contraction(context: AnalysisContext) -> tuple[bool | None, str]:
    if len(context.stock) < 45:
        return None, "VCP 至少需要约 45 个交易日数据"

    tail = context.stock.tail(45).copy()
    tail["RangePct"] = (tail["High"] - tail["Low"]) / tail["Close"]
    tail["BodyPct"] = (tail["Close"] - tail["Open"]).abs() / tail["Close"]
    tail = tail.replace([np.inf, -np.inf], np.nan)

    segments = [tail.iloc[0:15], tail.iloc[15:30], tail.iloc[30:45]]
    range_avgs = [segment["RangePct"].dropna().mean() for segment in segments]
    if any(pd.isna(value) for value in range_avgs):
        return None, "波动率样本不足"

    small_body_count = int((tail.tail(10)["BodyPct"].dropna() <= 0.012).sum())
    passed = bool(
        range_avgs[0] > range_avgs[1] > range_avgs[2]
        and range_avgs[2] <= range_avgs[0] * 0.7
        and small_body_count >= 2
    )
    detail = (
        "近 45 日平均振幅: "
        + " -> ".join(fmt_pct(value) for value in range_avgs)
        + f" / 最近 10 日小实体 K 线 {small_body_count} 根"
    )
    return passed, detail


def evaluate_pivot_volume_dry_up(context: AnalysisContext) -> tuple[bool | None, str]:
    if len(context.stock) < 60:
        return None, "缩量枢轴至少需要约 60 个交易日数据"

    enriched = context.stock.copy()
    enriched["VolumeMA50"] = enriched["Volume"].rolling(50).mean()
    latest = enriched.iloc[-1]
    if pd.isna(latest["VolumeMA50"]):
        return None, "50 日均量数据不足"

    recent_10 = enriched.tail(10)
    recent_20 = enriched.tail(20)
    recent_avg_volume = recent_10["Volume"].mean()
    recent_min_volume = recent_20["Volume"].min()
    latest_volume = latest["Volume"]
    contraction = (recent_10["High"].max() - recent_10["Low"].min()) / latest["Close"] if latest["Close"] else np.nan

    if not require_values(recent_avg_volume, recent_min_volume, latest_volume, contraction):
        return None, "缩量枢轴数据不足"

    passed = bool(
        recent_avg_volume <= latest["VolumeMA50"] * 0.65
        and latest_volume <= recent_min_volume * 1.05
        and contraction <= 0.08
    )
    detail = (
        f"近 10 日均量 {fmt_volume(recent_avg_volume)} / 50 日均量 {fmt_volume(latest['VolumeMA50'])}"
        f" / 最新量 {fmt_volume(latest_volume)} / 近 10 日振幅 {fmt_pct(contraction)}"
    )
    return passed, detail


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


def evaluate_power_play(context: AnalysisContext) -> tuple[bool | None, str]:
    if len(context.stock) < 90:
        return None, "Power Play 至少需要约 90 个交易日数据"

    frame = context.stock.tail(110).reset_index(drop=True)
    latest_close = frame["Close"].iloc[-1]
    best_candidate: dict[str, Any] | None = None

    for base_len in range(10, 31):
        run_end = len(frame) - base_len
        run = frame.iloc[max(0, run_end - 40) : run_end]
        prior = frame.iloc[max(0, run_end - 80) : max(0, run_end - 40)]
        base = frame.iloc[run_end:]
        if len(run) < 30 or len(base) < 10:
            continue

        run_low = run["Low"].min()
        run_high = run["High"].max()
        base_low = base["Low"].min()
        base_high = base["High"].max()
        if not require_values(run_low, run_high, base_low, base_high, latest_close) or run_low == 0 or base_high == 0:
            continue

        run_gain = float(run_high / run_low - 1)
        base_drawdown = float(1 - base_low / base_high)
        distance_from_base_high = float(1 - latest_close / base_high)
        volume_ratio = np.nan
        if not prior.empty:
            prior_avg_volume = prior["Volume"].mean()
            run_avg_volume = run["Volume"].mean()
            if require_values(prior_avg_volume, run_avg_volume) and prior_avg_volume:
                volume_ratio = float(run_avg_volume / prior_avg_volume)

        low_price_allowance = 0.25 if latest_close < 20 else 0.20
        volume_ok = bool(pd.isna(volume_ratio) or volume_ratio >= 1.25)
        candidate = {
            "base_len": base_len,
            "run_gain": run_gain,
            "base_drawdown": base_drawdown,
            "distance_from_base_high": distance_from_base_high,
            "volume_ratio": volume_ratio,
            "passed": bool(
                run_gain >= 1.0
                and base_drawdown <= low_price_allowance
                and distance_from_base_high <= 0.10
                and volume_ok
            ),
        }
        if best_candidate is None:
            best_candidate = candidate
            continue
        if candidate["passed"] and not best_candidate["passed"]:
            best_candidate = candidate
            continue
        if candidate["passed"] == best_candidate["passed"] and candidate["base_drawdown"] < best_candidate["base_drawdown"]:
            best_candidate = candidate

    if best_candidate is None:
        return None, "Power Play 样本不足"

    volume_text = "-" if pd.isna(best_candidate["volume_ratio"]) else f"{best_candidate['volume_ratio']:.2f}x"
    detail = (
        f"前段 8 周最大推进 {fmt_pct(best_candidate['run_gain'])}"
        f"\n整理 {best_candidate['base_len']} 日回撤 {fmt_pct(best_candidate['base_drawdown'])}"
        f"\n距整理高点 {fmt_pct(best_candidate['distance_from_base_high'])} / 爆发段均量比 {volume_text}"
    )
    return bool(best_candidate["passed"]), detail


def evaluate_vcp_contraction_ladder(context: AnalysisContext) -> tuple[bool | None, str]:
    if len(context.stock) < 70:
        return None, "VCP 递减结构至少需要约 70 个交易日数据"

    frame = context.stock.tail(55).copy().reset_index(drop=True)
    frame["VolumeMA50"] = context.stock["Volume"].rolling(50).mean().tail(55).reset_index(drop=True)
    segments = [frame.iloc[index : index + 11] for index in range(0, len(frame), 11)]
    if len(segments) < 5:
        return None, "VCP 分段样本不足"

    range_series: list[float] = []
    for segment in segments:
        avg_close = segment["Close"].mean()
        segment_range = np.nan if not avg_close else (segment["High"].max() - segment["Low"].min()) / avg_close
        range_series.append(float(segment_range) if pd.notna(segment_range) else np.nan)
    if any(pd.isna(value) for value in range_series):
        return None, "VCP 振幅样本不足"

    contraction_steps = sum(1 for left, right in zip(range_series, range_series[1:]) if right < left)
    strong_steps = sum(1 for left, right in zip(range_series, range_series[1:]) if right <= left * 0.7)
    last_avg_volume = segments[-1]["Volume"].mean()
    last_volume_ma50 = segments[-1]["VolumeMA50"].iloc[-1]
    if not require_values(last_avg_volume, last_volume_ma50) or last_volume_ma50 == 0:
        return None, "VCP 均量数据不足"

    passed = bool(
        contraction_steps >= 3
        and strong_steps >= 1
        and range_series[-1] <= range_series[0] * 0.6
        and last_avg_volume <= last_volume_ma50 * 0.75
    )
    detail = (
        "近 55 日五段振幅 "
        + " -> ".join(fmt_pct(value) for value in range_series)
        + f"\n收缩步数 {contraction_steps} / 强收缩 {strong_steps} 次"
        + f"\n末段均量 {fmt_volume(last_avg_volume)} / 50 日均量 {fmt_volume(last_volume_ma50)}"
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
    elif len(first_four) >= 4:
        passed = bool(up4 >= 3)
    else:
        passed = bool((day_change > 0).sum() > (day_change < 0).sum())

    detail = (
        f"突破日 {breakout['date'].strftime('%Y-%m-%d')}"
        f"\n后续 4 日上涨 {up4}/{len(first_four)}"
        f"\n后续 8 日上涨 {up8}/{len(first_eight)}"
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
    good_count = int((close_location >= 0.55).sum())
    bad_count = int((close_location <= 0.45).sum())
    passed = bool(good_count > bad_count)
    detail = f"近 10 日好收盘 {good_count} 天\n近 10 日弱收盘 {bad_count} 天"
    return passed, detail


def evaluate_no_three_lower_lows(context: AnalysisContext) -> tuple[bool | None, str]:
    if len(context.stock) < 25:
        return None, "三连阴破位至少需要约 25 个交易日数据"

    tail = context.stock.tail(6).reset_index(drop=True)
    baseline_volume = context.stock["Volume"].tail(20).mean()
    if not require_values(baseline_volume):
        return None, "量能基准不足"

    warning_detail = ""
    danger_found = False
    for start in range(0, len(tail) - 3):
        segment = tail.iloc[start : start + 4]
        lower_lows = bool((segment["Low"].diff().iloc[1:] < 0).all())
        lower_closes = int((segment["Close"].diff().iloc[1:] < 0).sum())
        avg_volume = segment["Volume"].iloc[1:].mean()
        volume_expanding = bool(require_values(avg_volume) and avg_volume >= baseline_volume * 1.1)
        if lower_lows and lower_closes >= 2 and volume_expanding:
            danger_found = True
            warning_detail = (
                f"{segment['Date'].iloc[0].strftime('%Y-%m-%d')} -> {segment['Date'].iloc[-1].strftime('%Y-%m-%d')}"
                f"\n连续更低低点，3 日均量 {fmt_volume(avg_volume)} 高于近 20 日常态"
            )
            break

    if not danger_found:
        warning_detail = f"近 6 日未见连续 3 天更低低点放量扩散（近 20 日均量 {fmt_volume(baseline_volume)}）"
    return (not danger_found), warning_detail


def evaluate_no_high_volume_ma_break(context: AnalysisContext) -> tuple[bool | None, str]:
    if len(context.stock) < 50:
        return None, "放量破均线至少需要约 50 个交易日数据"

    frame = context.stock.copy()
    frame["VolumeMA20"] = frame["Volume"].rolling(20).mean()
    frame["VolumeMA50"] = frame["Volume"].rolling(50).mean()
    latest = frame.iloc[-1]
    if pd.isna(latest["VolumeMA20"]) or pd.isna(latest["VolumeMA50"]):
        return None, "均量数据不足"

    break_ma20 = bool(
        require_values(latest["Close"], latest["MA20"], latest["Volume"], latest["VolumeMA20"])
        and latest["Close"] < latest["MA20"]
        and latest["Volume"] >= latest["VolumeMA20"] * 1.25
    )
    break_ma50 = bool(
        require_values(latest["Close"], latest["MA50"], latest["Volume"], latest["VolumeMA50"])
        and latest["Close"] < latest["MA50"]
        and latest["Volume"] >= latest["VolumeMA50"] * 1.25
    )
    passed = bool(not break_ma20 and not break_ma50)
    detail = (
        f"现价 {fmt_price(latest['Close'])} / MA20 {fmt_price(latest['MA20'])} / MA50 {fmt_price(latest['MA50'])}"
        f"\n最新量 {fmt_volume(latest['Volume'])} / 20 日均量 {fmt_volume(latest['VolumeMA20'])} / 50 日均量 {fmt_volume(latest['VolumeMA50'])}"
    )
    if break_ma50:
        detail += "\n已触发放量跌破 MA50 风险"
    elif break_ma20:
        detail += "\n已触发放量跌破 MA20 风险"
    return passed, detail


def evaluate_no_churning(context: AnalysisContext) -> tuple[bool | None, str]:
    if len(context.stock) < 60:
        return None, "放量滞涨至少需要约 60 个交易日数据"

    frame = context.stock.copy()
    frame["PrevClose"] = frame["Close"].shift(1)
    frame["VolumeMA50"] = frame["Volume"].rolling(50).mean()
    tail = frame.tail(10).copy()
    full_range = tail["High"] - tail["Low"]
    close_location = (tail["Close"] - tail["Low"]) / full_range.where(full_range > 0, np.nan)
    day_return = tail["Close"] / tail["PrevClose"] - 1
    volume_ratio = tail["Volume"] / tail["VolumeMA50"]
    churning_mask = (
        (volume_ratio >= 1.5)
        & (day_return.abs() <= 0.01)
        & (close_location <= 0.6)
    )
    count = int(churning_mask.sum())
    passed = bool(count == 0)
    detail = f"近 10 日疑似放量滞涨 {count} 天\n最大量比 {volume_ratio.replace([np.inf, -np.inf], np.nan).max():.2f}x"
    return passed, detail


def evaluate_no_climax_run(context: AnalysisContext) -> tuple[bool | None, str]:
    if len(context.stock) < 20:
        return None, "高潮加速至少需要约 20 个交易日数据"

    best_window: dict[str, Any] | None = None
    for window in range(7, 16):
        tail = context.stock.tail(window).copy()
        if len(tail) < window:
            continue
        up_days = int((tail["Close"].diff() > 0).sum())
        move = float(tail["Close"].iloc[-1] / tail["Close"].iloc[0] - 1) if tail["Close"].iloc[0] else np.nan
        if pd.isna(move):
            continue
        up_ratio = up_days / max(window - 1, 1)
        candidate = {"window": window, "up_ratio": up_ratio, "move": move}
        if best_window is None or candidate["move"] > best_window["move"]:
            best_window = candidate

    if best_window is None:
        return None, "高潮加速样本不足"

    danger = bool(best_window["up_ratio"] >= 0.70 and best_window["move"] >= 0.25)
    detail = (
        f"近 {best_window['window']} 日上涨天数占比 {best_window['up_ratio']:.0%}"
        f"\n近 {best_window['window']} 日累计涨幅 {fmt_signed_pct(best_window['move'])}"
    )
    return (not danger), detail


BASE_TREND_SPECS = [
    CheckSpec("trend_1", "当前股价高于 150 日和 200 日均线", evaluate_price_above_long_mas),
    CheckSpec("trend_2", "150 日均线高于 200 日均线", evaluate_ma150_above_ma200),
    CheckSpec("trend_3", "200 日均线至少连续 1 个月上升", evaluate_ma200_uptrend),
    CheckSpec("trend_4", "50 日均线高于 150 日和 200 日均线", evaluate_ma50_above_long_mas),
    CheckSpec("trend_5", "当前股价高于 50 日均线", evaluate_price_above_ma50),
    CheckSpec("trend_6", "当前股价较 52 周低点至少高出 30%", evaluate_above_52w_low),
    CheckSpec("trend_7", "当前股价距离 52 周高点不超过 25%", evaluate_near_52w_high),
    CheckSpec("trend_8", "RS 代理分数不低于 70", evaluate_rs_proxy_threshold),
]

ADVANCED_TREND_SPECS = [
    CheckSpec(
        "trend_9",
        "大盘回调测试: 回撤小于大盘或形成更高低点",
        evaluate_market_pullback_resilience,
    ),
    CheckSpec("trend_10", "30个交易日内放量上涨日明显多于放量下跌日", evaluate_volume_price_health),
    CheckSpec("trend_11", "回调深度限制: 距近期高点回调不超过 35%", evaluate_pullback_depth_limit),
    CheckSpec("trend_12", "VCP 波动率收缩: 近期波动明显收窄", evaluate_vcp_contraction),
    CheckSpec("trend_13", "枢轴点缩量: 收缩末端成交量极度萎缩", evaluate_pivot_volume_dry_up),
]

PATTERN_RISK_SPECS = [
    CheckSpec("pattern_1", "MVP 动量量价共振", evaluate_mvp_burst),
    CheckSpec("pattern_2", "Power Play 高位紧凑旗形", evaluate_power_play),
    CheckSpec("pattern_3", "VCP 收缩递减结构", evaluate_vcp_contraction_ladder),
    CheckSpec("pattern_4", "突破后跟进买盘占优", evaluate_follow_through_count),
    CheckSpec("pattern_5", "近期好收盘天数占优", evaluate_good_closes),
    CheckSpec("pattern_6", "未出现三连阴破位", evaluate_no_three_lower_lows),
    CheckSpec("pattern_7", "未出现放量破 20/50 日线", evaluate_no_high_volume_ma_break),
    CheckSpec("pattern_8", "近 10 日未见明显放量滞涨", evaluate_no_churning),
    CheckSpec("pattern_9", "近 7-15 日未进入高潮式加速", evaluate_no_climax_run),
]


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


def find_check_detail(checks: list[dict[str, Any]], target_name: str) -> tuple[str, str] | None:
    for item in checks:
        name = strip_check_name_prefix(item.get("name", ""))
        if name != target_name:
            continue
        passed = item.get("passed")
        status = "通过" if passed is True else "未通过" if passed is False else "待确认"
        detail = str(item.get("detail", "")).strip().replace("\n", "；")
        return status, detail
    return None


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
            "### 相对强度",
            f"- {rs_detail}",
        ])

    summary_lines.append("### 吸筹/派发线索")
    if range_position is not None:
        summary_lines.append(f"- 近 20 日区间位置：约处在区间的 {range_position * 100:.0f}% 位置")
    summary_lines.append(f"- 近 20 日疑似吸筹日 {accumulation_days} 天；疑似派发日 {distribution_days} 天（定义：涨/跌且成交量高于前一日）")
    volume_price_health = find_check_detail(data.get("advancedTrendChecks", []), "30个交易日内放量上涨日明显多于放量下跌日")
    if volume_price_health:
        status, detail = volume_price_health
        summary_lines.append(f"- 量价健康度：{status}；{detail}")
    pivot_dry_up = find_check_detail(data.get("advancedTrendChecks", []), "收缩末端成交量极度萎缩")
    if pivot_dry_up:
        status, detail = pivot_dry_up
        summary_lines.append(f"- 枢轴缩量线索：{status}；{detail}")

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

    summary_lines.append("### 扩展观察")
    summary_lines.extend(build_check_summary_lines(data.get("advancedTrendChecks", [])))
    summary_lines.append("### 形态 / 风控检查")
    summary_lines.extend(build_check_summary_lines(data.get("patternRiskChecks", [])))
    return "\n".join(summary_lines)


def build_prompt_from_analysis(data: dict[str, Any], note: str = "") -> str:
    template = read_prompt_template()
    note_text = note.strip()
    note_block = note_text if note_text else "（无）"
    replacements = {
        "{{symbol}}": str(data.get("symbol", "")).strip(),
        "{{latest_date}}": str(data.get("latestDate", "")).strip(),
        "{{technical_summary}}": build_technical_summary(data),
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
    rs_detail = f"本地未缓存 {DEFAULT_BENCHMARK}，RS 代理分数暂不可用。点击“拉新”后可补齐。"
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
    advanced_trend_checks = build_checks(ADVANCED_TREND_SPECS, analysis_context)
    pattern_risk_checks = build_checks(PATTERN_RISK_SPECS, analysis_context)
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
    advanced_trend_pass_count, advanced_trend_total, advanced_trend_status = summarize_check_group(advanced_trend_checks)

    result = {
        "symbol": normalized,
        "latestClose": None if pd.isna(latest["Close"]) else float(latest["Close"]),
        "latestCloseText": fmt_price(latest["Close"]),
        "latestVolume": None if pd.isna(latest["Volume"]) else float(latest["Volume"]),
        "latestVolumeText": fmt_volume(latest["Volume"]),
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
        "advancedTrendChecks": serialize_checks(advanced_trend_checks),
        "patternRiskChecks": serialize_checks(pattern_risk_checks),
        "history": serialize_history(history),
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
    }


app = FastAPI(title="Trend Deck")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def root() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    return {
        "defaultWatchlist": DEFAULT_WATCHLIST,
        "watchlistGroups": DEFAULT_WATCHLIST_GROUPS,
        "benchmark": DEFAULT_BENCHMARK,
    }


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
        return {
            "symbol": normalized,
            "prompt": build_prompt_from_analysis(data, note=note),
            "templatePath": str(PROMPT_TEMPLATE_PATH),
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
