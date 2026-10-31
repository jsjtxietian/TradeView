from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException

from trenddeck.alerts import (
    prune_alert_snapshot,
)
from trenddeck.config import (
    DEFAULT_WATCHLIST,
    DEFAULT_WATCHLIST_GROUPS,
)
from trenddeck.storage import (
    WATCHLIST_LOCK,
    load_symbol_notes,
    load_watchlist_state,
    save_symbol_notes,
    save_watchlist_state,
)

router = APIRouter()


@router.get("/api/watchlist/state")
def get_watchlist_state() -> dict[str, Any]:
    try:
        state = load_watchlist_state()
        return {
            "configured": state is not None,
            "watchlist": state["watchlist"] if state else DEFAULT_WATCHLIST,
            "groups": state["groups"] if state else DEFAULT_WATCHLIST_GROUPS,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.put("/api/watchlist/state")
def put_watchlist_state(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        with WATCHLIST_LOCK:
            state = save_watchlist_state(payload)
            prune_alert_snapshot(state["watchlist"])
        return {"configured": True, **state}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/api/notes")
def get_symbol_notes() -> dict[str, Any]:
    try:
        notes = load_symbol_notes()
        return {"configured": notes is not None, "notes": notes or {}}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.put("/api/notes")
def put_symbol_notes(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        notes = save_symbol_notes(payload.get("notes", payload))
        return {"configured": True, "notes": notes}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
