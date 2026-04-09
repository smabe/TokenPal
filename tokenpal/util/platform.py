"""Platform detection helpers."""

from __future__ import annotations

import platform
from functools import lru_cache


@lru_cache(maxsize=1)
def current_platform() -> str:
    """Return 'windows', 'darwin', or 'linux'."""
    return {"Windows": "windows", "Darwin": "darwin", "Linux": "linux"}.get(
        platform.system(), "linux"
    )


@lru_cache(maxsize=1)
def has_nvidia_gpu() -> bool:
    try:
        import pynvml

        pynvml.nvmlInit()
        return pynvml.nvmlDeviceGetCount() > 0
    except Exception:
        return False


@lru_cache(maxsize=1)
def is_apple_silicon() -> bool:
    return current_platform() == "darwin" and platform.machine() == "arm64"
