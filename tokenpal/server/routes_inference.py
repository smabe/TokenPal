"""Inference proxy — byte-forwards /v1/* to local Ollama."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from tokenpal.server.helpers import ollama_unavailable

router = APIRouter()

# Native-root paths on llama-server (non-OpenAI-compat) that TokenPal clients
# legitimately probe — currently just /props for context-length autosizing.
# Ollama has equivalents under /api/*, which we deliberately do NOT forward
# because Ollama's native API isn't part of the supported client contract.
_NATIVE_PASSTHROUGH = {"props"}


@router.api_route("/v1/{path:path}", methods=["GET", "POST", "DELETE"])
async def proxy_ollama(path: str, request: Request) -> Response:
    """Forward /v1/* requests to the local Ollama instance.

    Raw byte-forwarding — no JSON deserialization. This keeps the proxy
    schema-agnostic and SSE-streaming-ready for a future upgrade.
    """
    client: httpx.AsyncClient = request.app.state.ollama_client
    ollama_url: str = request.app.state.ollama_url
    url = f"{ollama_url}/v1/{path}"

    if request.query_params:
        url += f"?{request.query_params}"

    try:
        if request.method == "GET":
            resp = await client.get(url)
        else:
            body = await request.body()
            resp = await client.request(
                method=request.method,
                url=url,
                content=body,
                headers={"Content-Type": "application/json"},
            )
    except httpx.ConnectError:
        request.app.state.ollama_healthy = False
        return ollama_unavailable()
    except httpx.ReadTimeout:
        return JSONResponse(status_code=504, content={
            "error": "Ollama timed out",
            "hint": "Model may be loading. Try again in a few seconds.",
        })

    request.app.state.ollama_healthy = True
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers={"Content-Type": resp.headers.get("content-type", "application/json")},
    )


@router.get("/props")
async def proxy_props(request: Request) -> Response:
    """Forward GET /props to the upstream llama-server native root.

    Only enabled against llamacpp upstreams — Ollama doesn't expose /props
    and will 404, which matches the client's graceful None return path.
    Clients use this for context-length autosizing (see plans/gpu-scaling.md).
    """
    if "props" not in _NATIVE_PASSTHROUGH:  # pragma: no cover - defense in depth
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    client: httpx.AsyncClient = request.app.state.ollama_client
    ollama_url: str = request.app.state.ollama_url
    try:
        resp = await client.get(f"{ollama_url}/props")
    except httpx.ConnectError:
        request.app.state.ollama_healthy = False
        return ollama_unavailable()
    except httpx.ReadTimeout:
        return JSONResponse(status_code=504, content={"error": "upstream timed out"})
    request.app.state.ollama_healthy = True
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers={"Content-Type": resp.headers.get("content-type", "application/json")},
    )
