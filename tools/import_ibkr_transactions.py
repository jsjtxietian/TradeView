from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


TRADE_SECTION = "Transaction History"
BUY_TYPES = {"买", "Buy"}
SELL_TYPES = {"卖", "Sell"}
QUANTITY_EPSILON = 1e-8


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert IBKR stock transactions and append reviewed JSON trades.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    convert = commands.add_parser("convert", help="Convert an IBKR CSV to standalone JSON")
    convert.add_argument("csv_path", type=Path)
    convert.add_argument(
        "--output",
        type=Path,
        default=Path("ibkr-trades.json"),
        help="Standalone JSON output (default: ibkr-trades.json)",
    )
    convert.add_argument(
        "--start-date",
        default=None,
        help="Only include cycles active on/after YYYY-MM-DD, while retaining earlier opening trades",
    )

    append = commands.add_parser("append", help="Append standalone JSON to the app trade store")
    append.add_argument("json_path", type=Path)
    append.add_argument(
        "--output",
        type=Path,
        default=Path(".trade/trades.json"),
        help="Trade store to update (default: .trade/trades.json)",
    )
    return parser.parse_args()


def read_stock_transactions(csv_path: Path) -> list[dict[str, Any]]:
    header: list[str] | None = None
    transactions: list[dict[str, Any]] = []
    with csv_path.open(encoding="utf-8-sig", newline="") as csv_file:
        for row_index, row in enumerate(csv.reader(csv_file), start=1):
            if row[:2] == [TRADE_SECTION, "Header"]:
                header = row[2:]
                continue
            if row[:2] != [TRADE_SECTION, "Data"] or header is None:
                continue
            raw = dict(zip(header, row[2:]))
            trade_type = str(raw.get("交易类型", "")).strip()
            symbol = str(raw.get("代码", "")).strip().upper()
            if trade_type not in BUY_TYPES | SELL_TYPES or not symbol or symbol == "-":
                continue
            currency = str(raw.get("Price Currency", "")).strip().upper()
            try:
                quantity = abs(float(raw["数量"]))
                price = float(raw["价格"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"第 {row_index} 行的数量或价格无效。") from exc
            if quantity <= 0 or price <= 0 or not currency:
                raise ValueError(f"第 {row_index} 行的数量、价格或币种无效。")
            transactions.append({
                "source_index": row_index,
                "symbol": symbol,
                "currency": currency,
                "date": str(raw.get("日期", "")).strip(),
                "quantity": round(quantity, 4),
                "price": round(price, 4),
                "side": "buy" if trade_type in BUY_TYPES else "sell",
            })
    return transactions


def split_closed_trades(
    transactions: list[dict[str, Any]],
    start_date: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for transaction in transactions:
        grouped[transaction["symbol"]].append(transaction)

    trades: list[dict[str, Any]] = []
    skipped: dict[str, str] = {}
    if start_date:
        try:
            datetime.strptime(start_date, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("--start-date 必须是 YYYY-MM-DD 格式。") from exc
    for symbol, symbol_transactions in sorted(grouped.items()):
        currencies = {item["currency"] for item in symbol_transactions}
        if len(currencies) != 1:
            skipped[symbol] = f"同一股票包含多个成交币种: {', '.join(sorted(currencies))}"
            continue
        ordered = sorted(
            symbol_transactions,
            key=lambda item: (
                item["date"],
                0 if item["side"] == "buy" else 1,
                item["source_index"],
            ),
        )
        position = 0.0
        current: list[dict[str, Any]] = []
        ignored_unmatched_sales = False
        for item in ordered:
            if item["side"] == "sell" and item["quantity"] > position + QUANTITY_EPSILON:
                position = 0.0
                current = []
                ignored_unmatched_sales = True
                continue
            action = "sell"
            if item["side"] == "buy":
                action = "buy" if position <= QUANTITY_EPSILON else "add"
                position += item["quantity"]
            else:
                position -= item["quantity"]
            current.append({
                "date": item["date"],
                "quantity": item["quantity"],
                "price": item["price"],
                "action": action,
                "note": "",
            })
            if abs(position) <= QUANTITY_EPSILON:
                if not start_date or any(
                    transaction["date"] >= start_date
                    for transaction in current
                ):
                    trades.append({
                        "symbol": symbol,
                        "currency": item["currency"],
                        "note": "",
                        "transactions": current,
                    })
                current = []
                position = 0.0
        if current and (not start_date or any(item["date"] >= start_date for item in current)):
            skipped[symbol] = "筛选区间内有交易，但截至报表结束仍未清仓"
        elif ignored_unmatched_sales and not any(
            trade["symbol"] == symbol for trade in trades
        ):
            skipped[symbol] = "存在早于完整建仓记录的卖出，且未找到符合条件的完整清仓轮次"
    return trades, skipped


def load_json_array(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path} 顶层必须是数组。")
    return payload


def write_json(path: Path, payload: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def trade_signature(trade: dict[str, Any]) -> str:
    transactions = sorted(
        trade.get("transactions", []),
        key=lambda item: (
            str(item.get("date", "")),
            {"buy": 0, "add": 1, "sell": 2}.get(str(item.get("action", "")), 9),
            float(item.get("price", 0)),
            float(item.get("quantity", 0)),
            str(item.get("note", "")),
        ),
    )
    comparable = {
        "symbol": trade.get("symbol"),
        "currency": str(trade.get("currency", "USD")).upper(),
        "transactions": transactions,
    }
    return json.dumps(comparable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def append_trades(
    source_path: Path,
    output_path: Path,
) -> tuple[list[dict[str, Any]], int, int]:
    source = load_json_array(source_path)
    existing = load_json_array(output_path) if output_path.exists() else []
    signatures = {trade_signature(trade) for trade in existing}
    next_id = max((int(trade.get("id", 0)) for trade in existing), default=0) + 1
    added = 0
    duplicates = 0
    for raw_trade in source:
        symbol = str(raw_trade.get("symbol", "")).strip().upper()
        currency = str(raw_trade.get("currency", "")).strip().upper()
        transactions = raw_trade.get("transactions")
        if not symbol or not currency or not isinstance(transactions, list):
            raise ValueError("待追加 JSON 中存在缺少 symbol、currency 或 transactions 的交易。")
        trade = {
            "symbol": symbol,
            "currency": currency,
            "note": str(raw_trade.get("note", "")).strip(),
            "transactions": transactions,
        }
        signature = trade_signature(trade)
        if signature in signatures:
            duplicates += 1
            continue
        existing.append({"id": next_id, **trade})
        signatures.add(signature)
        next_id += 1
        added += 1
    for index, trade in enumerate(existing, start=1):
        trade["id"] = index
    write_json(output_path, existing)
    return existing, added, duplicates


def main() -> None:
    args = parse_args()
    if args.command == "convert":
        transactions = read_stock_transactions(args.csv_path)
        converted, skipped = split_closed_trades(transactions, args.start_date)
        standalone = [
            {"id": index, **trade}
            for index, trade in enumerate(converted, start=1)
        ]
        write_json(args.output, standalone)
        print(f"读取股票买卖记录: {len(transactions)}")
        print(f"生成完整清仓交易: {len(converted)}")
        print(f"输出: {args.output}")
        if skipped:
            print("跳过:")
            for symbol, reason in skipped.items():
                print(f"  {symbol}: {reason}")
        return

    output, added, duplicates = append_trades(args.json_path, args.output)
    print(f"从 {args.json_path} 新增: {added}")
    print(f"已存在并跳过: {duplicates}")
    print(f"{args.output} 交易总数: {len(output)}")


if __name__ == "__main__":
    main()
