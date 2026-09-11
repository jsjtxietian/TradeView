from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from trenddeck.alerts import (
    append_alert_log,
    prune_alert_snapshot,
    query_alert_log,
)
from trenddeck.analysis import (
    analyze_symbol,
    build_watchlist_summary,
)
from trenddeck.config import (
    ALERTS_FILE,
    DEFAULT_BENCHMARK,
    DEFAULT_WATCHLIST,
    DEFAULT_WATCHLIST_GROUPS,
    PROMPT_TEMPLATE_PATH,
    STATIC_DIR,
)
from trenddeck.market import (
    get_tiingo_api_key,
)
from trenddeck.mcp_server import MCPAccessGuard, create_mcp_server
from trenddeck.prompts import (
    build_prompt_from_analysis,
)
from trenddeck.storage import (
    create_trade,
    create_transaction,
    delete_transaction,
    list_all_trades,
    load_configured_watchlist_symbols,
    load_ibkr_review_ledger,
    load_symbol_notes,
    load_watchlist_state,
    save_symbol_notes,
    save_watchlist_state,
    update_trade_note,
)
from trenddeck.utils import (
    normalize_symbol,
)

mcp = create_mcp_server()
mcp_http_app = mcp.streamable_http_app()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="Trend Deck", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def root() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/review")
def review() -> FileResponse:
    return FileResponse(STATIC_DIR / "review.html")


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    return {
        "defaultWatchlist": DEFAULT_WATCHLIST,
        "watchlistGroups": DEFAULT_WATCHLIST_GROUPS,
        "benchmark": DEFAULT_BENCHMARK,
    }


@app.get("/api/watchlist/state")
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


@app.put("/api/watchlist/state")
def put_watchlist_state(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        state = save_watchlist_state(payload)
        prune_alert_snapshot(state["watchlist"])
        return {"configured": True, **state}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/notes")
def get_symbol_notes() -> dict[str, Any]:
    try:
        notes = load_symbol_notes()
        return {"configured": notes is not None, "notes": notes or {}}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.put("/api/notes")
def put_symbol_notes(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        notes = save_symbol_notes(payload.get("notes", payload))
        return {"configured": True, "notes": notes}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/alerts")
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


@app.post("/api/alerts/append")
def append_alerts(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        appended = append_alert_log(payload.get("alerts", []))
        return {"appended": appended, "alerts": query_alert_log(limit=50)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/watchlist/summary")
def watchlist_summary(
    symbols: str = Query(..., description="Comma separated stock symbols"),
    refresh: bool = Query(False, description="Force incremental refresh from online sources"),
) -> dict[str, Any]:
    try:
        return build_watchlist_summary(symbols, refresh=refresh)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/watchlist/refresh")
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


@app.get("/api/symbol/{symbol}")
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


@app.get("/api/trades")
def get_trades() -> dict[str, Any]:
    try:
        trades = list_all_trades()
        return {
            "symbols": sorted({str(trade["symbol"]) for trade in trades}),
            "trades": trades,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/review/ledger")
def get_review_ledger() -> dict[str, Any]:
    try:
        return load_ibkr_review_ledger()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/trades")
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


@app.put("/api/trades/{trade_id}")
def update_trade(trade_id: int, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return update_trade_note(trade_id, str(payload.get("note", "")))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/trades/{trade_id}/transactions")
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


@app.delete("/api/trades/{trade_id}/transactions/{transaction_index}")
def remove_trade_transaction(trade_id: int, transaction_index: int) -> dict[str, Any]:
    try:
        if not delete_transaction(trade_id, transaction_index):
            raise HTTPException(status_code=404, detail="交易记录不存在。")
        return {"deleted": True}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/prompt/{symbol}")
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
            "prompt": build_prompt_from_analysis(data, note=note, holding=holding if isinstance(holding, dict) else None),
            "templatePath": str(PROMPT_TEMPLATE_PATH),
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# Mount last so existing web/API routes keep their behavior. The child owns
# /mcp, avoiding a /mcp -> /mcp/ redirect during client initialization.
app.mount("/", MCPAccessGuard(mcp_http_app), name="mcp")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
