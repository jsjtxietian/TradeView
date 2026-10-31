"""ASGI assembly. Routes and MCP transport share the same domain services."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from trenddeck.api.mcp import MCPAccessGuard, create_mcp_server
from trenddeck.api.routes import alerts, market, pages, setups, trades, watchlist
from trenddeck.config import STATIC_DIR


def create_app() -> FastAPI:
    mcp = create_mcp_server()
    transport = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="Trend Deck", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    for router in [
        setups.router,
        pages.router,
        watchlist.router,
        alerts.router,
        market.router,
        trades.router,
    ]:
        app.include_router(router)
    # Mount last: MCP owns /mcp without redirecting or shadowing web routes.
    app.mount("/", MCPAccessGuard(transport), name="mcp")
    return app


app = create_app()
