from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import time
import tomllib
from typing import Any, Iterable

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import numpy as np
import pandas as pd
import yfinance as yf
from curl_cffi import requests as curl_requests
from yfinance.exceptions import YFRateLimitError


CACHE_DIR = Path(".cache")
CACHE_DIR.mkdir(exist_ok=True)
STATIC_DIR = Path("static")
DEFAULT_HISTORY_PERIOD = "3y"
DEFAULT_BENCHMARK = "SPY"
DEFAULT_WATCHLIST = ["AAPL", "NVDA", "MSFT", "TSLA"]
PERIOD_TO_DAYS = {"1y": 365, "2y": 730, "3y": 1095, "5y": 1825}
MEMORY_CACHE_TTL = 1800
EARNINGS_LIMIT = 12

TREND_RULES = [
    "当前股价高于 150 日和 200 日均线",
    "150 日均线高于 200 日均线",
    "200 日均线至少连续 1 个月上升",
    "50 日均线高于 150 日和 200 日均线",
    "当前股价高于 50 日均线",
    "当前股价较 52 周低点至少高出 30%",
    "当前股价距离 52 周高点不超过 25%",
    "RS 代理分数不低于 70",
]

CODE33_RULES = [
    "最近 3 个季度 EPS 同比增速持续加速",
    "最近 3 个季度营收同比增速持续加速",
    "最近 3 个季度净利率持续抬升",
]


@dataclass
class CheckResult:
    name: str
    passed: bool | None
    detail: str


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


def flatten_history_columns(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if isinstance(frame.columns, pd.MultiIndex):
        if symbol in frame.columns.get_level_values(-1):
            frame = frame.xs(symbol, axis=1, level=-1)
        else:
            frame.columns = frame.columns.get_level_values(0)
    frame = frame.rename_axis(index="Date").reset_index()
    frame["Date"] = pd.to_datetime(frame["Date"]).dt.tz_localize(None)
    return frame.sort_values("Date").reset_index(drop=True)


def history_cache_path(symbol: str, period: str) -> Path:
    safe_symbol = symbol.replace("/", "_").replace("\\", "_")
    return CACHE_DIR / f"{safe_symbol}_{period}_history.csv"


def frame_cache_path(symbol: str, cache_name: str) -> Path:
    safe_symbol = symbol.replace("/", "_").replace("\\", "_")
    return CACHE_DIR / f"{safe_symbol}_{cache_name}.csv"


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


def load_income_statement_cache(symbol: str) -> pd.DataFrame:
    cache_file = frame_cache_path(symbol, "income_statement")
    if not cache_file.exists():
        return pd.DataFrame()
    frame = pd.read_csv(cache_file)
    if frame.empty:
        return pd.DataFrame()
    frame["Date"] = pd.to_datetime(frame["Date"]).dt.tz_localize(None)
    frame = frame.set_index("Date").sort_index()
    frame.attrs["source_note"] = f"{symbol} 财报使用本地缓存数据。"
    return frame


def save_income_statement_cache(symbol: str, frame: pd.DataFrame) -> None:
    cache_file = frame_cache_path(symbol, "income_statement")
    cache_file.parent.mkdir(exist_ok=True)
    payload = frame.reset_index().rename(columns={"index": "Date"})
    payload.to_csv(cache_file, index=False)


def load_earnings_dates_cache(symbol: str) -> pd.DataFrame:
    cache_file = frame_cache_path(symbol, "earnings_dates")
    if not cache_file.exists():
        return pd.DataFrame()
    frame = pd.read_csv(cache_file)
    if frame.empty:
        return pd.DataFrame()
    frame["Date"] = pd.to_datetime(frame["Date"]).dt.tz_localize(None)
    frame = frame.set_index("Date").sort_index()
    frame.attrs["source_note"] = f"{symbol} 财报日期使用本地缓存数据。"
    return frame


def save_earnings_dates_cache(symbol: str, frame: pd.DataFrame) -> None:
    cache_file = frame_cache_path(symbol, "earnings_dates")
    cache_file.parent.mkdir(exist_ok=True)
    payload = frame.reset_index().rename(columns={"index": "Date"})
    payload.to_csv(cache_file, index=False)


def merge_history_frames(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    if existing.empty:
        return incoming.copy()
    if incoming.empty:
        return existing.copy()
    merged = pd.concat([existing, incoming], ignore_index=True)
    merged["Date"] = pd.to_datetime(merged["Date"]).dt.tz_localize(None)
    merged = merged.sort_values("Date").drop_duplicates(subset=["Date"], keep="last").reset_index(drop=True)
    return merged


def merge_indexed_frames(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    if existing.empty:
        return incoming.copy()
    if incoming.empty:
        return existing.copy()
    merged = pd.concat([existing, incoming])
    merged.index = pd.to_datetime(merged.index).tz_localize(None)
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
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


def fetch_history_once(
    symbol: str,
    period: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    ticker = yf.Ticker(symbol, session=get_session())
    frame = ticker.history(
        period=period,
        start=start_date,
        end=end_date,
        interval="1d",
        auto_adjust=False,
        actions=False,
    )
    if frame.empty:
        return pd.DataFrame()
    return flatten_history_columns(frame, symbol)


def fetch_income_statement_yahoo(symbol: str) -> pd.DataFrame:
    ticker = yf.Ticker(symbol, session=get_session())
    frame = ticker.quarterly_income_stmt
    if frame is None or frame.empty:
        return pd.DataFrame()
    frame = frame.T.copy()
    frame.index = pd.to_datetime(frame.index).tz_localize(None)
    return frame.sort_index()


def fetch_earnings_dates_yahoo(symbol: str) -> pd.DataFrame:
    ticker = yf.Ticker(symbol, session=get_session())
    frame = ticker.get_earnings_dates(limit=EARNINGS_LIMIT)
    if frame is None or frame.empty:
        return pd.DataFrame()
    frame = frame.copy()
    frame.index = pd.to_datetime(frame.index).tz_localize(None)
    return frame.sort_index()


def load_history(symbol: str, period: str = DEFAULT_HISTORY_PERIOD, force_refresh: bool = False) -> pd.DataFrame:
    cache_key = ("history", symbol, period)
    cached = get_cached(cache_key)
    if cached is not None and not force_refresh:
        return cached.copy()

    disk_cached = load_history_cache(symbol, period)
    if not force_refresh and not disk_cached.empty:
        return set_cached(cache_key, disk_cached.copy()).copy()

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

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            period_arg = None if incremental_start else period
            frame = fetch_history_once(
                symbol,
                period=period_arg,
                start_date=incremental_start,
                end_date=pd.Timestamp.utcnow().tz_localize(None).strftime("%Y-%m-%d") if incremental_start else None,
            )
            if not frame.empty or not disk_cached.empty:
                merged = merge_history_frames(disk_cached, frame)
                merged.attrs["source_note"] = (
                    f"{symbol} 使用本地缓存并已增量更新。"
                    if not disk_cached.empty
                    else f"{symbol} 行情数据源: Yahoo Finance"
                )
                save_history_cache(symbol, period, merged)
                return set_cached(cache_key, merged.copy()).copy()
        except YFRateLimitError as exc:
            last_error = exc
            time.sleep(2.5 * (attempt + 1))
            continue
        except Exception as exc:
            last_error = exc
        time.sleep(1.2 * (attempt + 1))

    if not disk_cached.empty:
        disk_cached.attrs["source_note"] = f"{symbol} 使用本地缓存数据，当前处于离线或接口失败回退状态。"
        return set_cached(cache_key, disk_cached.copy()).copy()

    if tiingo_error is not None:
        if isinstance(last_error, YFRateLimitError):
            raise ValueError(f"{symbol} Tiingo 失败: {tiingo_error}；Yahoo Finance 也被限流了。")
        if last_error is not None:
            raise ValueError(f"{symbol} Tiingo 失败: {tiingo_error}；Yahoo Finance 失败: {last_error}")
        raise ValueError(f"{symbol} Tiingo 失败: {tiingo_error}")
    if tiingo_had_no_data and isinstance(last_error, YFRateLimitError):
        raise ValueError(
            f"{symbol} 在 Tiingo 中没有返回可用行情，代码可能无效；"
            " 同时 Yahoo Finance 又临时限流了。"
            " 如果你想查苹果，请确认输入的是 AAPL，不是 APPL。"
        )
    if tiingo_had_no_data:
        raise ValueError(f"{symbol} 在 Tiingo 中没有返回可用行情，代码可能无效。")
    if isinstance(last_error, YFRateLimitError):
        raise ValueError(
            f"{symbol} 被 Yahoo Finance 临时限流，当前拿不到最新行情。"
            " 如果你之前查过这只股票，系统会自动回退到本地缓存；"
            " 如果是第一次查询，请等几分钟后重试。"
        )
    if last_error is not None:
        raise ValueError(f"{symbol} 行情抓取失败: {last_error}")
    raise ValueError(f"{symbol} 未返回任何价格数据，可能是代码无效或 Yahoo 当前限流。")


def load_income_statement_yahoo(symbol: str, force_refresh: bool = False) -> pd.DataFrame:
    cache_key = ("income_statement", symbol)
    cached = get_cached(cache_key)
    if cached is not None and not force_refresh:
        return cached.copy()

    disk_cached = load_income_statement_cache(symbol)
    if not force_refresh and not disk_cached.empty:
        return set_cached(cache_key, disk_cached.copy()).copy()

    try:
        frame = fetch_income_statement_yahoo(symbol)
        if not frame.empty or not disk_cached.empty:
            merged = merge_indexed_frames(disk_cached, frame)
            merged.attrs["source_note"] = (
                f"{symbol} 财报已增量更新。"
                if not disk_cached.empty
                else f"{symbol} 财报数据源: Yahoo Finance"
            )
            save_income_statement_cache(symbol, merged)
            return set_cached(cache_key, merged.copy()).copy()
    except Exception:
        pass

    if not disk_cached.empty:
        disk_cached.attrs["source_note"] = f"{symbol} 财报使用本地缓存数据，当前处于离线或接口失败回退状态。"
        return set_cached(cache_key, disk_cached.copy()).copy()
    return pd.DataFrame()


def load_earnings_dates_yahoo(symbol: str, force_refresh: bool = False) -> pd.DataFrame:
    cache_key = ("earnings_dates", symbol)
    cached = get_cached(cache_key)
    if cached is not None and not force_refresh:
        return cached.copy()

    disk_cached = load_earnings_dates_cache(symbol)
    if not force_refresh and not disk_cached.empty:
        return set_cached(cache_key, disk_cached.copy()).copy()

    try:
        frame = fetch_earnings_dates_yahoo(symbol)
    except Exception:
        frame = pd.DataFrame()

    if not frame.empty or not disk_cached.empty:
        merged = merge_indexed_frames(disk_cached, frame)
        merged.attrs["source_note"] = (
            f"{symbol} 财报日期已增量更新。"
            if not disk_cached.empty
            else f"{symbol} 财报日期数据源: Yahoo Finance"
        )
        save_earnings_dates_cache(symbol, merged)
        return set_cached(cache_key, merged.copy()).copy()

    if not disk_cached.empty:
        disk_cached.attrs["source_note"] = f"{symbol} 财报日期使用本地缓存数据，当前处于离线或接口失败回退状态。"
        return set_cached(cache_key, disk_cached.copy()).copy()
    return pd.DataFrame()


def add_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    enriched = frame.copy()
    for window in (20, 50, 150, 200):
        enriched[f"MA{window}"] = enriched["Close"].rolling(window).mean()
    enriched["PctFrom52WLow"] = enriched["Close"] / enriched["Low"].rolling(252).min() - 1
    enriched["PctFrom52WHigh"] = 1 - enriched["Close"] / enriched["High"].rolling(252).max()
    return enriched


def safe_pct_change(newer: float, older: float) -> float | None:
    if pd.isna(newer) or pd.isna(older) or older == 0:
        return None
    return (newer - older) / abs(older)


def extract_first_match(frame: pd.DataFrame, candidates: Iterable[str]) -> pd.Series | None:
    lowered = {str(column).lower(): column for column in frame.columns}
    for candidate in candidates:
        if candidate.lower() in lowered:
            return pd.to_numeric(frame[lowered[candidate.lower()]], errors="coerce")
    return None


def compute_eps_series(earnings_dates: pd.DataFrame, income_statement: pd.DataFrame) -> pd.Series:
    if not earnings_dates.empty and "Reported EPS" in earnings_dates.columns:
        eps = pd.to_numeric(earnings_dates["Reported EPS"], errors="coerce").dropna()
        if len(eps) >= 7:
            return eps

    if income_statement.empty:
        return pd.Series(dtype=float)

    eps = extract_first_match(
        income_statement,
        ["Diluted EPS", "Basic EPS", "Normalized Diluted EPS", "Reported EPS"],
    )
    return eps.dropna() if eps is not None else pd.Series(dtype=float)


def acceleration_details(series: pd.Series, label: str) -> tuple[bool | None, str, list[float]]:
    clean = series.dropna()
    if len(clean) < 7:
        return None, f"{label} 数据不足，至少需要 7 个季度", []

    yoy = []
    for idx in range(4, len(clean)):
        growth = safe_pct_change(clean.iloc[idx], clean.iloc[idx - 4])
        yoy.append(growth)
    yoy_series = pd.Series(yoy, index=clean.index[4:]).dropna()
    if len(yoy_series) < 3:
        return None, f"{label} 同比数据不足", []

    recent = yoy_series.iloc[-3:].tolist()
    passed = recent[0] < recent[1] < recent[2]
    detail = f"{label} 最近三季同比增速: " + " -> ".join(fmt_pct(value) for value in recent)
    return passed, detail, recent


def margin_details(income_statement: pd.DataFrame) -> tuple[bool | None, str, list[float]]:
    if income_statement.empty:
        return None, "利润率数据不可用", []

    revenue = extract_first_match(income_statement, ["Total Revenue", "Revenue", "Operating Revenue"])
    net_income = extract_first_match(
        income_statement,
        ["Net Income", "Net Income Common Stockholders", "NetIncome"],
    )
    if revenue is None or net_income is None:
        return None, "无法从财报中提取营收或净利润", []

    margin = (net_income / revenue).replace([np.inf, -np.inf], np.nan).dropna()
    if len(margin) < 3:
        return None, "利润率季度数据不足", []

    recent = margin.iloc[-3:].tolist()
    passed = recent[0] < recent[1] < recent[2]
    detail = "最近三季净利率: " + " -> ".join(fmt_pct(value) for value in recent)
    return passed, detail, recent


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


def build_trend_checks(stock: pd.DataFrame, rs_score: float | None, rs_detail: str) -> list[CheckResult]:
    latest = stock.iloc[-1]
    ma200_month_ago = stock["MA200"].iloc[-22] if len(stock) >= 222 else np.nan
    low_52w = stock["Low"].tail(252).min() if len(stock) >= 252 else np.nan
    high_52w = stock["High"].tail(252).max() if len(stock) >= 252 else np.nan

    return [
        CheckResult(
            TREND_RULES[0],
            bool(latest["Close"] > latest["MA150"] and latest["Close"] > latest["MA200"]),
            f"现价 {fmt_price(latest['Close'])} / MA150 {fmt_price(latest['MA150'])} / MA200 {fmt_price(latest['MA200'])}",
        ),
        CheckResult(
            TREND_RULES[1],
            bool(latest["MA150"] > latest["MA200"]),
            f"MA150 {fmt_price(latest['MA150'])} / MA200 {fmt_price(latest['MA200'])}",
        ),
        CheckResult(
            TREND_RULES[2],
            bool(pd.notna(latest["MA200"]) and pd.notna(ma200_month_ago) and latest["MA200"] > ma200_month_ago),
            f"当前 MA200 {fmt_price(latest['MA200'])} / 约 1 个月前 {fmt_price(ma200_month_ago)}",
        ),
        CheckResult(
            TREND_RULES[3],
            bool(latest["MA50"] > latest["MA150"] and latest["MA50"] > latest["MA200"]),
            f"MA50 {fmt_price(latest['MA50'])} / MA150 {fmt_price(latest['MA150'])} / MA200 {fmt_price(latest['MA200'])}",
        ),
        CheckResult(
            TREND_RULES[4],
            bool(latest["Close"] > latest["MA50"]),
            f"现价 {fmt_price(latest['Close'])} / MA50 {fmt_price(latest['MA50'])}",
        ),
        CheckResult(
            TREND_RULES[5],
            bool(pd.notna(low_52w) and latest["Close"] >= low_52w * 1.3),
            f"现价 {fmt_price(latest['Close'])} / 52 周低点 {fmt_price(low_52w)}",
        ),
        CheckResult(
            TREND_RULES[6],
            bool(pd.notna(high_52w) and latest["Close"] >= high_52w * 0.75),
            f"现价 {fmt_price(latest['Close'])} / 52 周高点 {fmt_price(high_52w)}",
        ),
        CheckResult(TREND_RULES[7], bool(rs_score is not None and rs_score >= 70), rs_detail),
    ]


def build_code33_checks(
    symbol: str,
    force_refresh: bool = False,
) -> tuple[list[CheckResult], dict[str, list[float]], list[str], list[str]]:
    warnings: list[str] = []
    source_notes: list[str] = []
    income_statement = load_income_statement_yahoo(symbol, force_refresh=force_refresh)
    earnings_dates = load_earnings_dates_yahoo(symbol, force_refresh=force_refresh)

    for note in [income_statement.attrs.get("source_note", ""), earnings_dates.attrs.get("source_note", "")]:
        if note:
            source_notes.append(note)

    eps_series = compute_eps_series(earnings_dates, income_statement)
    revenue = (
        extract_first_match(income_statement, ["Total Revenue", "Revenue", "Operating Revenue"])
        if not income_statement.empty
        else None
    )
    eps_passed, eps_detail, eps_recent = acceleration_details(eps_series, "EPS")
    revenue_passed, revenue_detail, revenue_recent = acceleration_details(
        revenue.dropna() if revenue is not None else pd.Series(dtype=float),
        "营收",
    )
    margin_passed, margin_detail, margin_recent = margin_details(income_statement)

    if income_statement.empty:
        warnings.append("Yahoo 财报接口当前没有返回季度利润表，Code 33 结果可能缺失。")
    if earnings_dates.empty and eps_series.empty:
        warnings.append("EPS 实际值未取到，EPS 加速判断可能不可用。")

    checks = [
        CheckResult(CODE33_RULES[0], eps_passed, eps_detail),
        CheckResult(CODE33_RULES[1], revenue_passed, revenue_detail),
        CheckResult(CODE33_RULES[2], margin_passed, margin_detail),
    ]
    for index, result in enumerate(checks):
        if [eps_passed, revenue_passed, margin_passed][index] is None:
            result.detail = f"{result.detail}。"

    return (
        checks,
        {"eps_yoy": eps_recent, "revenue_yoy": revenue_recent, "margin": margin_recent},
        warnings,
        source_notes,
    )


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


def analyze_symbol(symbol: str, force_refresh: bool = False) -> dict[str, Any]:
    normalized = normalize_symbol(symbol)
    if not normalized:
        raise ValueError("请输入有效的股票代码。")

    cache_key = ("analysis", normalized)
    cached = get_cached(cache_key)
    if cached is not None and not force_refresh:
        return cached

    if force_refresh:
        clear_symbol_memory_cache(normalized)
        clear_symbol_memory_cache(DEFAULT_BENCHMARK)

    raw_history = load_history(normalized, DEFAULT_HISTORY_PERIOD, force_refresh=force_refresh)
    raw_benchmark_history = load_history(DEFAULT_BENCHMARK, DEFAULT_HISTORY_PERIOD, force_refresh=force_refresh)
    history = add_indicators(raw_history)
    benchmark_history = add_indicators(raw_benchmark_history)
    rs_score, rs_detail = compute_rs_proxy(history, benchmark_history)
    trend_checks = build_trend_checks(history, rs_score, rs_detail)
    code33_checks, raw_code33, warnings, financial_notes = build_code33_checks(
        normalized,
        force_refresh=force_refresh,
    )

    latest = history.iloc[-1]
    trend_pass_count, trend_total, trend_status = summarize_check_group(trend_checks)
    code33_pass_count, code33_total, code33_status = summarize_check_group(code33_checks)

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
        "code33PassCount": code33_pass_count,
        "code33Total": code33_total,
        "code33Status": code33_status,
        "rsScore": None if rs_score is None else round(float(rs_score), 1),
        "rsDetail": rs_detail,
        "sourceNotes": [
            note
            for note in [
                raw_history.attrs.get("source_note", ""),
                raw_benchmark_history.attrs.get("source_note", ""),
                *financial_notes,
            ]
            if note
        ],
        "warnings": warnings,
        "trendChecks": serialize_checks(trend_checks),
        "code33Checks": serialize_checks(code33_checks),
        "rawCode33": raw_code33,
        "history": serialize_history(history),
    }
    return set_cached(cache_key, result)


def summary_payload(symbol: str, force_refresh: bool = False) -> dict[str, Any]:
    data = analyze_symbol(symbol, force_refresh=force_refresh)
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
        "code33PassCount": data["code33PassCount"],
        "code33Total": data["code33Total"],
        "code33Status": data["code33Status"],
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
        "chartWindows": ["1M", "6M", "1Y", "2Y", "ALL"],
        "benchmark": DEFAULT_BENCHMARK,
        "cacheMode": "默认优先读取本地缓存；点击“拉新”后增量更新价格与财报，并回写本地缓存。",
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

    items = []
    for symbol in normalized_symbols:
        try:
            items.append({"symbol": symbol, "data": summary_payload(symbol, force_refresh=refresh), "error": None})
        except Exception as exc:
            items.append({"symbol": symbol, "data": None, "error": str(exc)})
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
        return analyze_symbol(normalized, force_refresh=refresh)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
