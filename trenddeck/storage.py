from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import numpy as np

from trenddeck.config import (
    DEFAULT_WATCHLIST,
    LEDGER_FILE,
    NOTES_FILE,
    TRADE_DIR,
    TRADE_FILE,
    WATCHLIST_FILE,
)
from trenddeck.utils import (
    normalize_symbol,
    normalize_symbol_list,
)


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


def normalize_symbol_notes(payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError("股票笔记文件格式无效。")
    notes: dict[str, dict[str, Any]] = {}
    for raw_symbol, raw_note in payload.items():
        symbol = normalize_symbol(str(raw_symbol))
        if not symbol:
            continue
        if isinstance(raw_note, str):
            text = raw_note.strip()
            if text:
                notes[symbol] = {"text": text, "isHolding": False, "costBasis": None, "shares": None}
            continue
        if not isinstance(raw_note, dict) or isinstance(raw_note, list):
            continue
        text = str(raw_note.get("text", "")).strip()
        cost_basis = parse_optional_positive_float(raw_note.get("costBasis"))
        shares = parse_optional_positive_float(raw_note.get("shares"))
        is_holding = bool(raw_note.get("isHolding")) or cost_basis is not None or shares is not None
        if text or is_holding or cost_basis is not None or shares is not None:
            notes[symbol] = {
                "text": text,
                "isHolding": is_holding,
                "costBasis": cost_basis,
                "shares": shares,
            }
    return dict(sorted(notes.items()))


def parse_optional_positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(parsed) or parsed <= 0:
        return None
    return round(parsed, 4)


def load_symbol_notes() -> dict[str, dict[str, Any]] | None:
    if not NOTES_FILE.exists():
        return None
    try:
        payload = json.loads(NOTES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"股票笔记文件读取失败: {exc}") from exc
    return normalize_symbol_notes(payload)


def save_symbol_notes(payload: Any) -> dict[str, dict[str, Any]]:
    normalized = normalize_symbol_notes(payload)
    TRADE_DIR.mkdir(exist_ok=True)
    temp_path = NOTES_FILE.with_suffix(".json.tmp")
    temp_path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(NOTES_FILE)
    return normalized


def load_configured_watchlist_symbols() -> list[str]:
    state = load_watchlist_state()
    if state:
        symbols = normalize_symbol_list(state["watchlist"])
        if symbols:
            return symbols
    return DEFAULT_WATCHLIST
