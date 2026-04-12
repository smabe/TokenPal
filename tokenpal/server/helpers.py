"""Shared helpers for server routes."""

from __future__ import annotations

from fastapi.responses import JSONResponse


def ollama_unavailable(hint: str = "Is Ollama running? Start with: ollama serve") -> JSONResponse:
    """Standard 502 response when Ollama is unreachable."""
    return JSONResponse(status_code=502, content={"error": "Ollama unreachable", "hint": hint})
