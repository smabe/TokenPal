"""Pluggable authentication middleware for the TokenPal server."""

from __future__ import annotations

import abc

from fastapi import HTTPException, Request


class AbstractAuth(abc.ABC):
    @abc.abstractmethod
    async def authenticate(self, request: Request) -> bool: ...


class NoAuth(AbstractAuth):
    """Allow all requests. V1 default for trusted LAN."""

    async def authenticate(self, request: Request) -> bool:
        return True


class SharedSecretAuth(AbstractAuth):
    """Check X-API-Key header against a pre-shared secret. For v2."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def authenticate(self, request: Request) -> bool:
        return request.headers.get("X-API-Key", "") == self._api_key


async def require_auth(request: Request) -> None:
    """FastAPI dependency — raises 401 if auth fails."""
    auth: AbstractAuth = getattr(request.app.state, "auth_backend", NoAuth())
    if not await auth.authenticate(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
