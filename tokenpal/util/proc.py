"""Run a child process with a timeout, capturing its output."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence


async def run_capture(argv: Sequence[str], *, timeout_s: float) -> tuple[int, bytes, bytes]:
    """Run ``argv`` to completion and return ``(returncode, stdout, stderr)``.

    Raises ``TimeoutError`` once the child has been killed *and reaped* — a kill
    without the wait leaves a zombie until the loop next reaps children. Spawn
    failures (a missing binary, a non-executable one) propagate as ``OSError``.
    """
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode or 0, stdout, stderr
