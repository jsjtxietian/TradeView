from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

from trenddeck.alerts import (
    append_alert_log,
    query_alert_log,
)
from trenddeck.config import (
    ALERTS_FILE,
)

router = APIRouter()


@router.get("/api/alerts")
def get_alerts(
    symbol: str | None = Query(None, description="Optional stock symbol filter"),
    all: bool = Query(False, description="Return full alert history"),
    limit: int = Query(50, ge=1, le=500, description="Maximum number of recent alerts"),
) -> dict[str, Any]:
    try:
        alerts = query_alert_log(symbol=symbol, limit=None if all else limit)
        return {"configured": ALERTS_FILE.exists(), "alerts": alerts}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/alerts/append")
def append_alerts(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        appended = append_alert_log(payload.get("alerts", []))
        return {"appended": appended, "alerts": query_alert_log(limit=50)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
