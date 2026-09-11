from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import numpy as np

from trenddeck.config import (
    ALERT_TIMEZONE,
    ALERTS_FILE,
    ALERTS_SNAPSHOT_FILE,
    TRADE_DIR,
)
from trenddeck.utils import (
    fmt_signed_pct,
    normalize_symbol,
    normalize_symbol_list,
)


def normalize_alert_item(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    symbol = normalize_symbol(str(payload.get("symbol", "")))
    message = str(payload.get("message", "")).strip()
    created_at = str(payload.get("createdAt", "")).strip()
    if not symbol or not message:
        return None
    try:
        parsed_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        parsed_at = datetime.now(timezone.utc)
        created_at = parsed_at.isoformat(timespec="seconds").replace("+00:00", "Z")
    time_label = str(payload.get("timeLabel", "")).strip()
    if not time_label:
        time_label = parsed_at.strftime("%m/%d %H:%M")
    return {
        "symbol": symbol,
        "message": message,
        "createdAt": created_at,
        "timeLabel": time_label,
    }


def load_alert_log() -> list[dict[str, Any]]:
    if not ALERTS_FILE.exists():
        return []
    try:
        payload = json.loads(ALERTS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"提醒日志读取失败: {exc}") from exc
    if not isinstance(payload, list):
        raise ValueError("提醒日志格式无效。")
    alerts = [alert for item in payload if (alert := normalize_alert_item(item))]
    return sorted(alerts, key=lambda item: str(item["createdAt"]))


def write_alert_log(alerts: list[dict[str, Any]]) -> None:
    TRADE_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = ALERTS_FILE.with_suffix(".json.tmp")
    temp_path.write_text(
        json.dumps(alerts, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(ALERTS_FILE)


def alert_dedupe_key(alert: dict[str, Any]) -> tuple[str, str, str]:
    created_at = str(alert.get("createdAt", ""))
    try:
        parsed_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if parsed_at.tzinfo is None:
            parsed_at = parsed_at.replace(tzinfo=timezone.utc)
        day = parsed_at.astimezone(ALERT_TIMEZONE).date().isoformat()
    except ValueError:
        day = created_at[:10]
    return str(alert.get("symbol", "")), str(alert.get("message", "")), day


def append_alert_log(payload: Any) -> list[dict[str, Any]]:
    raw_alerts = payload if isinstance(payload, list) else []
    incoming = [alert for item in raw_alerts if (alert := normalize_alert_item(item))]
    if not incoming:
        return []
    alerts = load_alert_log()
    seen = {alert_dedupe_key(item) for item in alerts}
    appended = []
    for alert in incoming:
        key = alert_dedupe_key(alert)
        if key in seen:
            continue
        seen.add(key)
        alerts.append(alert)
        appended.append(alert)
    if appended:
        write_alert_log(sorted(alerts, key=lambda item: str(item["createdAt"])))
    return appended


def load_alert_snapshot() -> dict[str, dict[str, Any]]:
    if not ALERTS_SNAPSHOT_FILE.exists():
        return {}
    try:
        payload = json.loads(ALERTS_SNAPSHOT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"提醒快照读取失败: {exc}") from exc
    raw_symbols = payload.get("symbols", {}) if isinstance(payload, dict) else {}
    if not isinstance(raw_symbols, dict):
        raise ValueError("提醒快照格式无效。")
    return {
        symbol: item
        for raw_symbol, item in raw_symbols.items()
        if (symbol := normalize_symbol(str(raw_symbol))) and isinstance(item, dict)
    }


def write_alert_snapshot(snapshot: dict[str, dict[str, Any]]) -> None:
    TRADE_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = ALERTS_SNAPSHOT_FILE.with_suffix(".json.tmp")
    temp_path.write_text(
        json.dumps({"version": 1, "symbols": snapshot}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(ALERTS_SNAPSHOT_FILE)


def prune_alert_snapshot(symbols: list[str]) -> None:
    if not ALERTS_SNAPSHOT_FILE.exists():
        return
    allowed = set(normalize_symbol_list(symbols))
    snapshot = load_alert_snapshot()
    pruned = {symbol: item for symbol, item in snapshot.items() if symbol in allowed}
    if pruned != snapshot:
        write_alert_snapshot(pruned)


def is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and np.isfinite(value)


def build_alert_snapshot_item(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "latestDate": str(data.get("latestDate", "")),
        "latestClose": data.get("latestClose"),
        "dailyChangePct": data.get("dailyChangePct"),
        "fiveDayChangePct": data.get("fiveDayChangePct"),
        "latestVolumeRatioMA50": data.get("latestVolumeRatioMA50"),
        "trendPassCount": data.get("trendPassCount"),
        "trendTotal": data.get("trendTotal"),
        "isSixMonthHigh": bool(data.get("isSixMonthHigh")),
        "isSixMonthLow": bool(data.get("isSixMonthLow")),
        "sixMonthHighText": str(data.get("sixMonthHighText") or "-"),
        "sixMonthLowText": str(data.get("sixMonthLowText") or "-"),
        "crossedAboveMA50": bool(data.get("crossedAboveMA50")),
        "crossedBelowMA50": bool(data.get("crossedBelowMA50")),
        "latestMA50Text": str(data.get("latestMA50Text") or "-"),
    }


def create_server_alert(symbol: str, message: str) -> dict[str, Any]:
    created_at = datetime.now(timezone.utc)
    return {
        "symbol": symbol,
        "message": message,
        "createdAt": created_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "timeLabel": created_at.astimezone(ALERT_TIMEZONE).strftime("%m/%d %H:%M"),
    }


def evaluate_watchlist_alerts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    snapshot = load_alert_snapshot()
    next_snapshot = dict(snapshot)
    fresh_alerts: list[dict[str, Any]] = []

    for item in items:
        data = item.get("data") if isinstance(item, dict) else None
        if not isinstance(data, dict):
            continue
        symbol = normalize_symbol(str(data.get("symbol") or item.get("symbol") or ""))
        if not symbol:
            continue
        current = build_alert_snapshot_item(data)
        previous = snapshot.get(symbol)
        next_snapshot[symbol] = current
        if not previous:
            continue

        current_date = str(current.get("latestDate", ""))
        is_new_session = bool(current_date and current_date != str(previous.get("latestDate", "")))
        was_template = (
            previous.get("trendTotal", 0) > 0
            and previous.get("trendPassCount") == previous.get("trendTotal")
        )
        is_template = (
            current.get("trendTotal", 0) > 0
            and current.get("trendPassCount") == current.get("trendTotal")
        )
        if not was_template and is_template:
            fresh_alerts.append(create_server_alert(symbol, "刚刚满足趋势模板。"))
        elif was_template and not is_template:
            fresh_alerts.append(create_server_alert(symbol, "已不再满足趋势模板。"))

        if not previous.get("isSixMonthHigh") and current.get("isSixMonthHigh"):
            fresh_alerts.append(
                create_server_alert(symbol, f"创近 6 个月新高（{current['sixMonthHighText']}）。")
            )
        if not previous.get("isSixMonthLow") and current.get("isSixMonthLow"):
            fresh_alerts.append(
                create_server_alert(symbol, f"创近 6 个月新低（{current['sixMonthLowText']}）。")
            )

        if is_new_session:
            daily_change = current.get("dailyChangePct")
            if is_finite_number(daily_change) and abs(daily_change) >= 0.05:
                direction = "上涨" if daily_change > 0 else "下跌"
                fresh_alerts.append(
                    create_server_alert(symbol, f"较前一交易日{direction} {fmt_signed_pct(daily_change)}。")
                )

            five_day_change = current.get("fiveDayChangePct")
            if is_finite_number(five_day_change) and abs(five_day_change) >= 0.08:
                direction = "上涨" if five_day_change > 0 else "下跌"
                fresh_alerts.append(
                    create_server_alert(
                        symbol,
                        f"近 5 个交易日累计{direction} {fmt_signed_pct(five_day_change)}。",
                    )
                )

            volume_ratio = current.get("latestVolumeRatioMA50")
            if is_finite_number(volume_ratio):
                if volume_ratio >= 1.5:
                    fresh_alerts.append(
                        create_server_alert(
                            symbol,
                            f"当日成交量放大至 50 日均量的 {volume_ratio * 100:.0f}%。",
                        )
                    )
                elif volume_ratio <= 0.5:
                    fresh_alerts.append(
                        create_server_alert(
                            symbol,
                            f"当日成交量缩至 50 日均量的 {volume_ratio * 100:.0f}%。",
                        )
                    )

            if current.get("crossedAboveMA50"):
                fresh_alerts.append(
                    create_server_alert(symbol, f"收盘价上穿 50 日均线（MA50 {current['latestMA50Text']}）。")
                )
            elif current.get("crossedBelowMA50"):
                fresh_alerts.append(
                    create_server_alert(symbol, f"收盘价下穿 50 日均线（MA50 {current['latestMA50Text']}）。")
                )

    appended = append_alert_log(fresh_alerts)
    write_alert_snapshot(next_snapshot)
    return appended


def query_alert_log(symbol: str | None = None, limit: int | None = 50) -> list[dict[str, Any]]:
    alerts = load_alert_log()
    normalized_symbol = normalize_symbol(symbol or "") if symbol else ""
    if normalized_symbol:
        alerts = [alert for alert in alerts if alert["symbol"] == normalized_symbol]
    latest_first = sorted(alerts, key=lambda item: str(item["createdAt"]), reverse=True)
    if limit is not None and limit > 0:
        return latest_first[:limit]
    return latest_first
