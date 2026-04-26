"""Shared fixtures for sense tests."""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture
def enabled_config() -> dict[str, Any]:
    return {"enabled": True}
