from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException

from trenddeck.storage import (
    create_trade,
    create_transaction,
    delete_transaction,
    list_all_trades,
    load_ibkr_review_ledger,
    update_trade_note,
)
from trenddeck.utils import (
    normalize_symbol,
)

router = APIRouter()


@router.get("/api/trades")
def get_trades() -> dict[str, Any]:
    try:
        trades = list_all_trades()
        return {
            "symbols": sorted({str(trade["symbol"]) for trade in trades}),
            "trades": trades,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/api/review/ledger")
def get_review_ledger() -> dict[str, Any]:
    try:
        return load_ibkr_review_ledger()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/trades")
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


@router.put("/api/trades/{trade_id}")
def update_trade(trade_id: int, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return update_trade_note(trade_id, str(payload.get("note", "")))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/trades/{trade_id}/transactions")
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


@router.delete("/api/trades/{trade_id}/transactions/{transaction_index}")
def remove_trade_transaction(trade_id: int, transaction_index: int) -> dict[str, Any]:
    try:
        if not delete_transaction(trade_id, transaction_index):
            raise HTTPException(status_code=404, detail="交易记录不存在。")
        return {"deleted": True}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
