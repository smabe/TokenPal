"""TokenPal Server — FastAPI app for inference proxy and training orchestration."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from tokenpal.server import __version__
from tokenpal.server.auth import NoAuth, SharedSecretAuth
from tokenpal.server.job_store import JsonFileJobStore
from tokenpal.server.routes_inference import router as inference_router
from tokenpal.server.routes_models import router as models_router
from tokenpal.server.routes_server import router as server_router
from tokenpal.server.routes_training import router as training_router

log = logging.getLogger(__name__)


class TokenPalServerError(Exception):
    """Base error with structured JSON response."""

    def __init__(self, message: str, hint: str = "", status_code: int = 500) -> None:
        self.message = message
        self.hint = hint
        self.status_code = status_code
        super().__init__(message)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup: shared httpx client, job store, Ollama health check."""
    ollama_url = getattr(app.state, "ollama_url", "http://localhost:11434")

    app.state.ollama_client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=120.0, write=10.0, pool=5.0),
    )

    # Job store with crash recovery
    jobs_dir = Path.home() / ".tokenpal-server" / "jobs"
    app.state.job_store = JsonFileJobStore(jobs_dir)
    app.state.job_store.recover_stale_jobs()

    # Quick Ollama health check (non-fatal)
    try:
        resp = await app.state.ollama_client.get(f"{ollama_url}/")
        app.state.ollama_healthy = resp.status_code == 200
        if app.state.ollama_healthy:
            log.info("Ollama reachable at %s", ollama_url)
        else:
            log.warning("Ollama returned %d at %s", resp.status_code, ollama_url)
    except httpx.HTTPError:
        app.state.ollama_healthy = False
        log.warning("Ollama not reachable at %s — start with: ollama serve", ollama_url)

    yield

    await app.state.ollama_client.aclose()


def create_app(
    ollama_url: str = "http://localhost:11434",
    host: str = "127.0.0.1",
) -> FastAPI:
    """Create the FastAPI application."""
    app = FastAPI(
        title="TokenPal Server",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.ollama_url = ollama_url

    # Routes
    app.include_router(inference_router)
    app.include_router(server_router, prefix="/api/v1")
    app.include_router(models_router, prefix="/api/v1")
    app.include_router(training_router, prefix="/api/v1")

    # Error handlers
    @app.exception_handler(TokenPalServerError)
    async def _handle_server_error(
        request: Request, exc: TokenPalServerError,
    ) -> JSONResponse:
        body: dict[str, str | None] = {"error": exc.message}
        if exc.hint:
            body["hint"] = exc.hint
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.exception_handler(ValueError)
    async def _handle_value_error(
        request: Request, exc: ValueError,
    ) -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    # Log warning for LAN-exposed server with no auth
    if host == "0.0.0.0":
        log.warning(
            "Server bound to all interfaces with no authentication. "
            "Any device on your network can access this server."
        )

    return app


def main() -> None:
    """Entry point for ``tokenpal-server``."""
    try:
        import uvicorn
    except ImportError:
        print(
            "ERROR: Server dependencies not installed.\n"
            "Install with: pip install 'tokenpal[server]'\n"
            "Or: pip install fastapi uvicorn"
        )
        raise SystemExit(1)

    import argparse

    from tokenpal.config.loader import load_config

    parser = argparse.ArgumentParser(description="TokenPal Server")
    parser.add_argument("--config", type=Path, help="Config file path")
    parser.add_argument("--host", help="Bind host (overrides config)")
    parser.add_argument("--port", type=int, help="Bind port (overrides config)")
    args = parser.parse_args()

    config = load_config(config_path=args.config)
    host = args.host or config.server.host
    port = args.port or config.server.port

    auth_backend: NoAuth | SharedSecretAuth
    if config.server.auth_backend == "shared_secret" and config.server.api_key:
        auth_backend = SharedSecretAuth(config.server.api_key)
    else:
        auth_backend = NoAuth()

    app = create_app(ollama_url=config.server.ollama_url, host=host)
    app.state.auth_backend = auth_backend

    uvicorn.run(app, host=host, port=port, log_level="info")
