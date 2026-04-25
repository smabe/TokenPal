"""In-app installer for the [audio] optional-dependencies group.

Output-side only at phase 2: kokoro-onnx + sounddevice. Input-side deps
(openwakeword, faster-whisper, silero-vad) get added when phase 3 lands.

The check uses ``importlib.util.find_spec`` so an absent package raises
ImportError on first real use rather than at the top of any module —
keeps the modularity test (ambient-only never loads sounddevice) honest.
The installer shells out to ``pip`` in the same interpreter so editable
installs and the venv launcher both work without extra glue.
"""

from __future__ import annotations

import importlib.util
import logging
import subprocess
import sys
from dataclasses import dataclass
from typing import Final

log = logging.getLogger(__name__)

# Maps each pip distribution name to the import name used to detect
# presence. Phase 3 will add e.g. ``faster-whisper`` → ``faster_whisper``,
# and pip→import is not a hyphen-replace rule in general (Pillow→PIL,
# python-dateutil→dateutil), so the mapping is explicit. onnxruntime +
# numpy come transitively from kokoro-onnx so we don't double-list them.
AUDIO_DEPS: Final[dict[str, str]] = {
    "kokoro-onnx": "kokoro_onnx",
    "sounddevice": "sounddevice",
}


def missing_deps() -> tuple[str, ...]:
    """Return the pip names of audio deps that aren't importable."""
    return tuple(
        pip_name
        for pip_name, import_name in AUDIO_DEPS.items()
        if importlib.util.find_spec(import_name) is None
    )


def format_warning(*, prefix: str = "missing deps") -> str | None:
    """Build a user-visible warning string when deps are missing, else None.

    Single source of truth for the modal save handler and /voice-io.
    """
    missing = missing_deps()
    if not missing:
        return None
    return f"{prefix}: {', '.join(missing)} — run /voice-io install"


@dataclass(frozen=True)
class InstallResult:
    ok: bool
    message: str


def install(timeout_s: float = 600.0) -> InstallResult:
    """Run ``pip install`` for the missing audio deps.

    Uses the same Python interpreter the buddy is running under so the
    install lands in the active venv without the caller having to know
    where it is. Output is captured so we can summarize cleanly back to
    the user instead of dumping pip's full progress to chat.
    """
    missing = missing_deps()
    if not missing:
        return InstallResult(ok=True, message="audio deps already installed.")

    # --progress-bar off + -q drop pip's spinner / per-line download
    # progress. Without these, capture_output buffers ~5-20MB of text on a
    # slow connection (kokoro-onnx pulls onnxruntime ~200MB).
    cmd = [
        sys.executable, "-m", "pip", "install",
        "--progress-bar", "off", "-q",
        *missing,
    ]
    log.info("Installing audio deps: %s", " ".join(missing))
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return InstallResult(
            ok=False,
            message=f"pip install timed out after {timeout_s:.0f}s.",
        )
    except KeyboardInterrupt:
        return InstallResult(ok=False, message="pip install cancelled.")
    except OSError as e:
        return InstallResult(ok=False, message=f"pip install failed: {e}")

    if proc.returncode != 0:
        # pip's last meaningful line usually has the actual error.
        last = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:]
        tail = last[0] if last else "no output"
        return InstallResult(
            ok=False,
            message=f"pip install failed (exit {proc.returncode}): {tail}",
        )

    still_missing = missing_deps()
    if still_missing:
        return InstallResult(
            ok=False,
            message=(
                f"pip install reported success but still missing: "
                f"{', '.join(still_missing)}. Try restarting the buddy."
            ),
        )
    return InstallResult(
        ok=True,
        message=f"installed: {', '.join(missing)}. Restart to activate.",
    )
