from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from trenddeck.setups import queries as setup_queries
from trenddeck.setups.model import MODEL_VERSION

router = APIRouter()


@router.get("/api/setups/rankings")
def setup_rankings(
    as_of: str | None = None,
    symbols: list[str] | None = Query(None),
    universe: str = "watchlist",
    actionable_only: bool = False,
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
    model_version: str = MODEL_VERSION,
    include_details: bool = False,
    setup_type: str = "base",
) -> dict[str, Any]:
    try:
        return setup_queries.get_setup_rankings(
            as_of,
            symbols,
            universe,
            actionable_only,
            limit,
            offset,
            model_version,
            include_details,
            setup_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/setups/report/{symbol}")
def setup_report(
    symbol: str,
    as_of: str | None = None,
    model_version: str = MODEL_VERSION,
    include_details: bool = False,
    setup_type: str = "base",
    include_chart: bool = False,
) -> dict[str, Any]:
    try:
        return setup_queries.get_setup_report(
            symbol, as_of, model_version, include_details, setup_type, include_chart
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/setups/backtest")
def setup_backtest(
    start: str,
    end: str,
    symbols: list[str] | None = Query(None),
    universe: str = "watchlist",
    horizon: int = Query(20, ge=5, le=60),
    step: int = Query(20, ge=5, le=60),
    top_n: int = Query(10, ge=1, le=30),
    cost_bps: float = Query(10, ge=0, le=100),
    holdout_start: str | None = None,
    model_version: str = MODEL_VERSION,
    include_event_records: bool = False,
) -> dict[str, Any]:
    try:
        return setup_queries.get_setup_backtest(
            start,
            end,
            symbols,
            universe,
            horizon,
            step,
            top_n,
            cost_bps,
            holdout_start,
            model_version,
            include_event_records,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
