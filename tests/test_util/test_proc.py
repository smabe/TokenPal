"""Tests for the shared subprocess runner."""

from __future__ import annotations

import sys

import pytest

from tokenpal.util.proc import run_capture


async def test_captures_streams_and_returncode() -> None:
    rc, stdout, stderr = await run_capture(
        [sys.executable, "-c", "import sys; sys.stdout.write('out'); sys.stderr.write('err')"],
        timeout_s=10,
    )
    assert (rc, stdout, stderr) == (0, b"out", b"err")


async def test_non_zero_returncode_is_reported_not_raised() -> None:
    rc, _, _ = await run_capture([sys.executable, "-c", "raise SystemExit(3)"], timeout_s=10)
    assert rc == 3


async def test_missing_binary_raises_oserror() -> None:
    with pytest.raises(OSError):
        await run_capture(["tokenpal-no-such-binary-xyz"], timeout_s=10)


async def test_timeout_kills_and_reaps_before_raising() -> None:
    """A kill without the wait leaves a zombie until the loop next reaps."""
    with pytest.raises(TimeoutError):
        await run_capture([sys.executable, "-c", "import time; time.sleep(30)"], timeout_s=0.2)
