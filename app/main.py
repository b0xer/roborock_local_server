"""Application entry point: REST API + MCP endpoint + static web UI on one port."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import build_router
from .auth import LoginFlow
from .config import Settings, get_settings
from .mcp_server import build_mcp
from .robot import RobotError, RobotService
from .security import BearerTokenMiddleware

STATIC_DIR = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None, service: RobotService | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if settings.log_level.upper() != "DEBUG":
        # Stateless MCP logs a session start/stop line for every request.
        logging.getLogger("mcp.server.streamable_http").setLevel(logging.WARNING)
        logging.getLogger("mcp.server.streamable_http_manager").setLevel(logging.WARNING)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    service = service or RobotService(settings)

    mcp = build_mcp(service, allow_raw_commands=settings.allow_raw_commands)
    # host="0.0.0.0" turns off the SDK's localhost-only Host header check so LAN clients can connect.
    # Stateless mode means clients keep working across container restarts.
    mcp_app = mcp.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, host="0.0.0.0")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with mcp.session_manager.run():
            await service.start()
            try:
                yield
            finally:
                await service.stop()

    app = FastAPI(title="Roborock Local Server", version="0.1.0", lifespan=lifespan)
    app.state.service = service

    @app.exception_handler(RobotError)
    async def robot_error_handler(_: Request, err: RobotError) -> JSONResponse:
        return JSONResponse({"detail": str(err)}, status_code=err.status_code)

    app.include_router(build_router(service, LoginFlow(), settings.roborock_email))
    # Register the MCP route directly (not as a Mount) so /mcp works without a trailing-slash redirect.
    app.router.routes.extend(mcp_app.routes)
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")

    app.add_middleware(BearerTokenMiddleware, token=settings.api_token)
    return app


def run() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level=settings.log_level.lower())


if __name__ == "__main__":
    run()
