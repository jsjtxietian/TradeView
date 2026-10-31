from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import FileResponse

from trenddeck.config import (
    DEFAULT_BENCHMARK,
    DEFAULT_WATCHLIST,
    DEFAULT_WATCHLIST_GROUPS,
    STATIC_DIR,
)

router = APIRouter()


@router.get("/")
def root() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/review")
def review() -> FileResponse:
    return FileResponse(STATIC_DIR / "review.html")


@router.get("/api/config")
def get_config() -> dict[str, Any]:
    return {
        "defaultWatchlist": DEFAULT_WATCHLIST,
        "watchlistGroups": DEFAULT_WATCHLIST_GROUPS,
        "benchmark": DEFAULT_BENCHMARK,
    }
