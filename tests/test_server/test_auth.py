"""Tests for the pluggable auth middleware."""

from unittest.mock import MagicMock

import pytest

from tokenpal.server.auth import NoAuth, SharedSecretAuth


@pytest.fixture
def mock_request():
    req = MagicMock()
    req.headers = {}
    return req


async def test_no_auth_always_passes(mock_request):
    auth = NoAuth()
    assert await auth.authenticate(mock_request) is True


async def test_shared_secret_rejects_missing_key(mock_request):
    auth = SharedSecretAuth("my-secret-key")
    mock_request.headers = {}
    assert await auth.authenticate(mock_request) is False


async def test_shared_secret_rejects_wrong_key(mock_request):
    auth = SharedSecretAuth("my-secret-key")
    mock_request.headers = {"X-API-Key": "wrong-key"}
    assert await auth.authenticate(mock_request) is False


async def test_shared_secret_accepts_correct_key(mock_request):
    auth = SharedSecretAuth("my-secret-key")
    mock_request.headers = {"X-API-Key": "my-secret-key"}
    assert await auth.authenticate(mock_request) is True
