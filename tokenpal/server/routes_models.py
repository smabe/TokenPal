"""Model management routes — list and pull models via Ollama HTTP API."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from tokenpal.server.models import ModelInfo, PullRequest

router = APIRouter()


@router.get("/models/list")
async def list_models(request: Request) -> list[ModelInfo]:
    """List models available on the server's Ollama instance."""
    client: httpx.AsyncClient = request.app.state.ollama_client
    ollama_url: str = request.app.state.ollama_url

    try:
        resp = await client.get(f"{ollama_url}/api/tags")
        resp.raise_for_status()
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Ollama unreachable")

    models = resp.json().get("models", [])
    return [
        ModelInfo(
            name=m["name"],
            size=m.get("size"),
            modified_at=m.get("modified_at"),
        )
        for m in models
    ]


@router.post("/models/pull", status_code=202)
async def pull_model(req: PullRequest, request: Request) -> JSONResponse:
    """Trigger a model download on the server's Ollama instance."""
    client: httpx.AsyncClient = request.app.state.ollama_client
    ollama_url: str = request.app.state.ollama_url

    try:
        resp = await client.post(
            f"{ollama_url}/api/pull",
            json={"name": req.model, "stream": False},
            timeout=600.0,
        )
        resp.raise_for_status()
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Ollama unreachable")
    except httpx.ReadTimeout:
        raise HTTPException(
            status_code=504,
            detail=f"Model pull timed out. '{req.model}' may be very large.",
        )

    return JSONResponse(
        status_code=200,
        content={"status": "success", "model": req.model},
    )
