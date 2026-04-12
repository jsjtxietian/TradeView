from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
import os
from pathlib import Path
import time
import tomllib
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import numpy as np
import pandas as pd
from curl_cffi import requests as curl_requests


CACHE_DIR = Path(".cache")
CACHE_DIR.mkdir(exist_ok=True)
STATIC_DIR = Path("static")
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
    return raw_symbol.strip().upper().replace(" ", "")


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


def period_start(period: str) -> str:
    days = PERIOD_TO_DAYS.get(period, 1095)
    start = pd.Timestamp.utcnow().tz_localize(None) - pd.Timedelta(days=days + 20)
    return start.strftime("%Y-%m-%d")


def history_cache_path(symbol: str, period: str) -> Path:
    safe_symbol = symbol.replace("/", "_").replace("\\", "_")
    return CACHE_DIR / f"{safe_symbol}_{period}_history.csv"


def load_history_cache(symbol: str, period: str) -> pd.DataFrame:
    cache_file = history_cache_path(symbol, period)
    if not cache_file.exists():
        return pd.DataFrame()
    frame = pd.read_csv(cache_file)
    if frame.empty:
        return pd.DataFrame()
    frame["Date"] = pd.to_datetime(frame["Date"]).dt.tz_localize(None)
    frame.attrs["source_note"] = f"{symbol} 使用本地缓存数据，可能不是最新交易日。"
    return frame


def save_history_cache(symbol: str, period: str, frame: pd.DataFrame) -> None:
    history_cache_path(symbol, period).parent.mkdir(exist_ok=True)
    frame.to_csv(history_cache_path(symbol, period), index=False)


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
) -> pd.DataFrame:
    api_key = get_tiingo_api_key()
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
    frame = frame.rename(
        columns={
            "date": "Date",
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        }
    )
    expected = ["Date", "Open", "High", "Low", "Close", "Volume"]
    available = [field for field in expected if field in frame.columns]
    frame = frame[available].copy()
    frame["Date"] = pd.to_datetime(frame["Date"], utc=True).dt.tz_localize(None)
    for field in ["Open", "High", "Low", "Close", "Volume"]:
        if field in frame.columns:
            frame[field] = pd.to_numeric(frame[field], errors="coerce")
    return frame.dropna(subset=["Date", "Close"]).sort_values("Date").reset_index(drop=True)


def load_history(
    symbol: str,
    period: str = DEFAULT_HISTORY_PERIOD,
    force_refresh: bool = False,
    allow_network: bool = True,
) -> pd.DataFrame:
    cache_key = ("history", symbol, period)
    cached = get_cached(cache_key)
    if cached is not None and not force_refresh:
        return cached.copy()

    disk_cached = load_history_cache(symbol, period)
    if not force_refresh and not disk_cached.empty:
        return set_cached(cache_key, disk_cached.copy()).copy()
    if not allow_network:
        return pd.DataFrame()

    incremental_start = None
    tiingo_had_no_data = False
    if not disk_cached.empty:
        incremental_start = (disk_cached["Date"].max() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    tiingo_error: Exception | None = None
    if get_tiingo_api_key():
        try:
            frame = fetch_history_from_tiingo(symbol, period, start_date=incremental_start)
            if not frame.empty or not disk_cached.empty:
                merged = merge_history_frames(disk_cached, frame)
                merged.attrs["source_note"] = (
                    f"{symbol} 行情已增量更新，价格数据源: Tiingo"
                    if not disk_cached.empty
                    else f"{symbol} 行情数据源: Tiingo"
                )
                save_history_cache(symbol, period, merged)
                return set_cached(cache_key, merged.copy()).copy()
            tiingo_had_no_data = True
        except Exception as exc:
            tiingo_error = exc

    if not disk_cached.empty:
        disk_cached.attrs["source_note"] = f"{symbol} 使用本地缓存数据，当前处于离线或接口失败回退状态。"
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

    peak_idx = int(benchmark_tail["Close"].idxmax())
    if peak_idx >= len(benchmark_tail) - 5:
        return None, f"近 3 个月 {DEFAULT_BENCHMARK} 尚未形成明确回调段"

    bench_segment = benchmark_tail.iloc[peak_idx:].copy()
    bench_peak = bench_segment["Close"].iloc[0]
    bench_low = bench_segment["Close"].min()
    if not require_values(bench_peak, bench_low) or bench_peak == 0:
        return None, f"{DEFAULT_BENCHMARK} 回调段数据不足"

    benchmark_drawdown = 1 - bench_low / bench_peak
    if benchmark_drawdown < 0.08:
        return None, f"近 3 个月 {DEFAULT_BENCHMARK} 最大回撤 {fmt_pct(benchmark_drawdown)}，回调不够明确"

    peak_date = bench_segment["Date"].iloc[0]
    stock_segment = context.stock[context.stock["Date"] >= peak_date].copy()
    if len(stock_segment) < 5:
        return None, "个股与基准对齐后的样本不足"

    stock_peak = stock_segment["Close"].iloc[0]
    stock_low = stock_segment["Close"].min()
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
    detail = f"近 3 个月 {DEFAULT_BENCHMARK} 回撤 {fmt_pct(benchmark_drawdown)} / 个股回撤 {fmt_pct(stock_drawdown)}"
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

    volume_signal = tail["Volume"] > tail["VolumeMA50"] * 1.05
    up_days = int(((tail["Close"] > tail["PrevClose"]) & volume_signal).sum())
    down_days = int(((tail["Close"] < tail["PrevClose"]) & volume_signal).sum())
    passed = up_days >= 3 and up_days >= down_days + 2
    detail = f"近 30 日放量上涨 {up_days} 天 / 放量下跌 {down_days} 天（基准: 50 日均量）"
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
    CheckSpec("trend_10", "量价健康度: 放量上涨日明显多于放量下跌日", evaluate_volume_price_health),
    CheckSpec("trend_11", "回调深度限制: 距近期高点回调不超过 35%", evaluate_pullback_depth_limit),
    CheckSpec("trend_12", "VCP 波动率收缩: 近期波动明显收窄", evaluate_vcp_contraction),
    CheckSpec("trend_13", "枢轴点缩量: 收缩末端成交量极度萎缩", evaluate_pivot_volume_dry_up),
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


def analyze_symbol(
    symbol: str,
    force_refresh: bool = False,
    allow_network: bool = True,
    refresh_benchmark: bool = False,
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
    latest = analysis_context.latest
    trend_pass_count, trend_total, trend_status = summarize_check_group(trend_checks)
    advanced_trend_pass_count, advanced_trend_total, advanced_trend_status = summarize_check_group(advanced_trend_checks)

    result = {
        "symbol": normalized,
        "latestClose": None if pd.isna(latest["Close"]) else float(latest["Close"]),
        "latestCloseText": fmt_price(latest["Close"]),
        "latestVolume": None if pd.isna(latest["Volume"]) else float(latest["Volume"]),
        "latestVolumeText": fmt_volume(latest["Volume"]),
        "latestDate": history["Date"].iloc[-1].strftime("%Y-%m-%d"),
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
        "history": serialize_history(history),
    }
    return set_cached(cache_key, result)


def summary_payload(
    symbol: str,
    force_refresh: bool = False,
    allow_network: bool = True,
    refresh_benchmark: bool = False,
) -> dict[str, Any]:
    data = analyze_symbol(
        symbol,
        force_refresh=force_refresh,
        allow_network=allow_network,
        refresh_benchmark=refresh_benchmark,
    )
    return {
        "symbol": data["symbol"],
        "latestClose": data["latestClose"],
        "latestCloseText": data["latestCloseText"],
        "latestVolume": data["latestVolume"],
        "latestVolumeText": data["latestVolumeText"],
        "latestDate": data["latestDate"],
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

    if refresh and not benchmark_in_watchlist:
        clear_symbol_memory_cache(DEFAULT_BENCHMARK)
        try:
            load_history(
                DEFAULT_BENCHMARK,
                DEFAULT_HISTORY_PERIOD,
                force_refresh=True,
                allow_network=True,
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
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
