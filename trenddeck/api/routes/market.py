from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

from trenddeck.analysis import (
    analyze_symbol,
    build_watchlist_summary,
)
from trenddeck.config import (
    PROMPT_TEMPLATE_PATH,
)
from trenddeck.market import (
    get_tiingo_api_key,
)
from trenddeck.prompts import (
    build_prompt_from_analysis,
)
from trenddeck.storage import (
    load_configured_watchlist_symbols,
)
from trenddeck.utils import (
    normalize_symbol,
)

router = APIRouter()


@router.get("/api/watchlist/summary")
def watchlist_summary(
    symbols: str = Query(..., description="Comma separated stock symbols"),
    refresh: bool = Query(False, description="Force incremental refresh from online sources"),
) -> dict[str, Any]:
    try:
        return build_watchlist_summary(symbols, refresh=refresh)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/watchlist/refresh")
def refresh_watchlist_cache(
    symbols: str | None = Query(None, description="Optional comma separated stock symbols"),
) -> dict[str, Any]:
    try:
        refresh_symbols = symbols if symbols is not None else load_configured_watchlist_symbols()
        return build_watchlist_summary(refresh_symbols, refresh=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/api/symbol/{symbol}")
def symbol_detail(
    symbol: str,
    refresh: bool = Query(False, description="Force incremental refresh from online sources"),
    compare: str | None = Query(None, description="Optional comparison symbol for the chart"),
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
            comparison_symbol=compare,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/prompt/{symbol}")
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
        holding = (payload or {}).get("holding")
        return {
            "symbol": normalized,
            "prompt": build_prompt_from_analysis(
                data, note=note, holding=holding if isinstance(holding, dict) else None
            ),
            "templatePath": str(PROMPT_TEMPLATE_PATH),
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
