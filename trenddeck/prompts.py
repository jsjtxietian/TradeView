from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from trenddeck.config import (
    PROMPT_TEMPLATE_PATH,
)
from trenddeck.utils import (
    coerce_float,
    fmt_pct,
    fmt_price,
    fmt_signed_pct,
    fmt_signed_price,
    fmt_volume,
    require_values,
    strip_check_name_prefix,
)


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
