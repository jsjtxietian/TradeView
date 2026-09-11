from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import numpy as np
import pandas as pd

from trenddeck.alerts import (
    evaluate_watchlist_alerts,
)
from trenddeck.config import (
    DEFAULT_BENCHMARK,
    DEFAULT_HISTORY_PERIOD,
)
from trenddeck.indicators import (
    BASE_TREND_SPECS,
    BUY_LOOKBACK_WINDOWS,
    SELL_LOOKBACK_WINDOWS,
    TREND_TEMPLATE_VARIANT_EXCLUDED_NAMES,
    AnalysisContext,
    CheckResult,
    add_indicators,
    build_buy_indicator_groups,
    build_checks,
    build_sell_indicator_groups,
    build_trend_sparkline,
    compute_rs_proxy,
    evaluate_latest_range_below_ma50,
    flatten_buy_indicator_groups,
    serialize_checks,
    summarize_check_group,
)
from trenddeck.market import (
    clear_symbol_memory_cache,
    get_cached,
    get_refresh_api_key_for_index,
    load_history,
    set_cached,
)
from trenddeck.utils import (
    fmt_price,
    fmt_signed_pct,
    fmt_volume,
    normalize_symbol,
    normalize_symbol_list,
    require_values,
)


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


def build_summary_fields(
    normalized: str,
    history: pd.DataFrame,
    analysis_context: AnalysisContext,
) -> tuple[dict[str, Any], list[CheckResult]]:
    trend_checks = build_checks(BASE_TREND_SPECS, analysis_context)
    previous_range_check = CheckResult(
        "前一日振幅低于 50 日均振幅",
        *evaluate_latest_range_below_ma50(analysis_context),
    )
    latest = analysis_context.latest
    prev_close = history["Close"].iloc[-2] if len(history) >= 2 else np.nan
    daily_change_pct = None
    if require_values(latest["Close"], prev_close) and prev_close != 0:
        daily_change_pct = float(latest["Close"] / prev_close - 1)
    five_day_change_pct = None
    if len(history) >= 6:
        five_day_base_close = history["Close"].iloc[-6]
        if require_values(latest["Close"], five_day_base_close) and five_day_base_close != 0:
            five_day_change_pct = float(latest["Close"] / five_day_base_close - 1)
    latest_ma50 = latest.get("MA50", np.nan)
    previous = history.iloc[-2] if len(history) >= 2 else None
    previous_ma50 = previous.get("MA50", np.nan) if previous is not None else np.nan
    crossed_above_ma50 = bool(
        previous is not None
        and require_values(previous["Close"], previous_ma50, latest["Close"], latest_ma50)
        and previous["Close"] <= previous_ma50
        and latest["Close"] > latest_ma50
    )
    crossed_below_ma50 = bool(
        previous is not None
        and require_values(previous["Close"], previous_ma50, latest["Close"], latest_ma50)
        and previous["Close"] >= previous_ma50
        and latest["Close"] < latest_ma50
    )
    trend_sparkline = build_trend_sparkline(history)
    recent_window = history.tail(min(126, len(history)))
    six_month_high = recent_window["Close"].max() if not recent_window.empty else np.nan
    six_month_low = recent_window["Close"].min() if not recent_window.empty else np.nan
    is_six_month_high = bool(
        require_values(latest["Close"], six_month_high) and latest["Close"] >= six_month_high
    )
    is_six_month_low = bool(
        require_values(latest["Close"], six_month_low) and latest["Close"] <= six_month_low
    )
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
    latest_volume_ma50 = history["Volume"].rolling(50).mean().iloc[-1]
    latest_volume_below_ma50 = bool(
        require_values(latest["Volume"], latest_volume_ma50)
        and latest["Volume"] < latest_volume_ma50
    )
    latest_volume_ratio_ma50 = None
    if require_values(latest["Volume"], latest_volume_ma50) and latest_volume_ma50:
        latest_volume_ratio_ma50 = float(latest["Volume"] / latest_volume_ma50)

    return {
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
        "latestMA50": None if pd.isna(latest_ma50) else float(latest_ma50),
        "latestMA50Text": fmt_price(latest_ma50),
        "crossedAboveMA50": crossed_above_ma50,
        "crossedBelowMA50": crossed_below_ma50,
        "trendSparklineDirection": trend_sparkline["direction"],
        "trendSparklineValues": trend_sparkline["values"],
        "sixMonthHigh": None if pd.isna(six_month_high) else float(six_month_high),
        "isSixMonthHigh": is_six_month_high,
        "sixMonthLow": None if pd.isna(six_month_low) else float(six_month_low),
        "isSixMonthLow": is_six_month_low,
        "sixMonthHighText": fmt_price(six_month_high),
        "sixMonthLowText": fmt_price(six_month_low),
        "trendPassCount": trend_pass_count,
        "trendTotal": trend_total,
        "trendStatus": trend_status,
        "trendTemplateVariantMatch": trend_variant_match,
        "previousRangeBelowMA50": previous_range_check.passed,
        "previousRangeBelowMA50Detail": previous_range_check.detail,
    }, trend_checks


def analyze_symbol(
    symbol: str,
    force_refresh: bool = False,
    allow_network: bool = True,
    refresh_benchmark: bool = False,
    tiingo_api_key: str | None = None,
    comparison_symbol: str | None = None,
) -> dict[str, Any]:
    normalized = normalize_symbol(symbol)
    if not normalized:
        raise ValueError("请输入有效的股票代码。")

    comparison_normalized = normalize_symbol(comparison_symbol or DEFAULT_BENCHMARK) or DEFAULT_BENCHMARK
    cache_key = ("analysis", normalized, comparison_normalized)
    cached = get_cached(cache_key)
    if cached is not None and not force_refresh:
        return cached

    if force_refresh:
        clear_symbol_memory_cache(normalized)
        if refresh_benchmark:
            clear_symbol_memory_cache(DEFAULT_BENCHMARK)
            if comparison_normalized != DEFAULT_BENCHMARK:
                clear_symbol_memory_cache(comparison_normalized)

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

    raw_benchmark_history = raw_history if normalized == DEFAULT_BENCHMARK else load_history(
        DEFAULT_BENCHMARK,
        DEFAULT_HISTORY_PERIOD,
        force_refresh=force_refresh and refresh_benchmark,
        allow_network=allow_network,
        tiingo_api_key=tiingo_api_key,
    )
    raw_comparison_history = (
        raw_history
        if comparison_normalized == normalized
        else raw_benchmark_history
        if comparison_normalized == DEFAULT_BENCHMARK
        else load_history(
            comparison_normalized,
            DEFAULT_HISTORY_PERIOD,
            force_refresh=force_refresh and refresh_benchmark,
            allow_network=allow_network,
            tiingo_api_key=tiingo_api_key,
        )
    )
    result = analyze_frames(normalized, raw_history, raw_benchmark_history, comparison_normalized, raw_comparison_history)
    return set_cached(cache_key, result)


def analyze_frames(
    normalized: str,
    raw_history: pd.DataFrame,
    raw_benchmark_history: pd.DataFrame,
    comparison_normalized: str = DEFAULT_BENCHMARK,
    raw_comparison_history: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Pure analysis shared by the web API and cache-only MCP queries."""
    if raw_history.empty:
        raise ValueError(f"{normalized} has no cached price history.")
    if raw_comparison_history is None:
        raw_comparison_history = raw_benchmark_history
    history = add_indicators(raw_history)
    rs_score = None
    rs_detail = f"本地未缓存 {DEFAULT_BENCHMARK}，相对 SPY 表现分暂不可用。点击“拉新”后可补齐。"
    benchmark_history = pd.DataFrame()
    if not raw_benchmark_history.empty:
        benchmark_history = add_indicators(raw_benchmark_history)
        rs_score, rs_detail = compute_rs_proxy(history, benchmark_history)
    comparison_history = (
        add_indicators(raw_comparison_history)
        if not raw_comparison_history.empty
        else pd.DataFrame()
    )
    analysis_context = AnalysisContext(
        stock=history,
        benchmark=benchmark_history,
        latest=history.iloc[-1],
        rs_score=rs_score,
        rs_detail=rs_detail,
    )
    summary_fields, trend_checks = build_summary_fields(normalized, history, analysis_context)
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
    advanced_trend_pass_count, advanced_trend_total, advanced_trend_status = summarize_check_group(
        [
            CheckResult(item["name"], item["passed"], item["detail"])
            for item in advanced_trend_checks
        ]
    )
    result = {
        **summary_fields,
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
                raw_comparison_history.attrs.get("source_note", ""),
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
        "benchmarkSymbol": comparison_normalized,
        "benchmarkHistory": serialize_price_history(comparison_history),
    }
    return result


def summary_payload(
    symbol: str,
    force_refresh: bool = False,
    allow_network: bool = True,
    refresh_benchmark: bool = False,
    tiingo_api_key: str | None = None,
) -> dict[str, Any]:
    normalized = normalize_symbol(symbol)
    if not normalized:
        raise ValueError("请输入有效的股票代码。")

    cache_key = ("summary", normalized, DEFAULT_BENCHMARK)
    cached = get_cached(cache_key)
    if cached is not None and not force_refresh:
        return cached

    if force_refresh:
        clear_symbol_memory_cache(normalized)

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
    benchmark_history = pd.DataFrame()
    raw_benchmark_history = raw_history if normalized == DEFAULT_BENCHMARK else load_history(
        DEFAULT_BENCHMARK,
        DEFAULT_HISTORY_PERIOD,
        force_refresh=force_refresh and refresh_benchmark,
        allow_network=allow_network,
        tiingo_api_key=tiingo_api_key,
    )
    rs_score = None
    rs_detail = f"本地未缓存 {DEFAULT_BENCHMARK}，相对 SPY 表现分暂不可用。点击“拉新”后可补齐。"
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
    data, _ = build_summary_fields(normalized, history, analysis_context)
    return set_cached(cache_key, data)


def build_watchlist_summary(symbols: list[str] | str, refresh: bool = False) -> dict[str, Any]:
    normalized_symbols = normalize_symbol_list(symbols)
    if not normalized_symbols:
        raise ValueError("缺少有效股票代码。")

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
    appended_alerts = evaluate_watchlist_alerts(items) if refresh else []
    return {"items": items, "appendedAlerts": appended_alerts}
