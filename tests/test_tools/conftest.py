"""Shared fixtures for tests/test_tools/.

Auto-applied to every test in this subdirectory via pytest's conftest
resolution — individual tests don't need to opt in.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _fast_poll_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the remote training poll loop instant during tests.

    Production uses POLL_INTERVAL_SECONDS = 30 so the loop doesn't hammer
    the remote over SSH. In tests, the loop is mocked and returns "done"
    immediately — we just need `asyncio.sleep(0)` to yield to the event
    loop and let the next iteration check the mock. Saves ~30s per test
    that reaches the poll loop.
    """
    import tokenpal.tools.remote_train as rt
    monkeypatch.setattr(rt, "POLL_INTERVAL_SECONDS", 0)
