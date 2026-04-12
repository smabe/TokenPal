"""Server info endpoint — version, health, GPU status."""

from __future__ import annotations

import os

from fastapi import APIRouter, Request

from tokenpal.server import __version__
from tokenpal.server.models import ServerInfo

router = APIRouter()


@router.get("/server/info")
async def server_info(request: Request) -> ServerInfo:
    """Return server version, Ollama health, and active training job.

    Never exposes secrets (HF_TOKEN value, api_key, etc.).
    """
    job_store = getattr(request.app.state, "job_store", None)
    active_job = job_store.get_active() if job_store else None

    return ServerInfo(
        server_version=__version__,
        api_version=1,
        ollama_healthy=getattr(request.app.state, "ollama_healthy", False),
        ollama_url=getattr(request.app.state, "ollama_url", "http://localhost:11434"),
        active_training_job=active_job.job_id if active_job else None,
        hf_token_set=bool(os.environ.get("HF_TOKEN")),
    )
