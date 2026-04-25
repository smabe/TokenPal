"""In-app installer for the [audio] optional-dependencies group.

``missing_deps()`` uses ``importlib.util.find_spec`` so an absent package
raises ImportError on first real use rather than at module top — keeps
the modularity test (ambient-only never loads sounddevice) honest.
``install()`` shells out to ``pip`` in the same interpreter so editable
installs and the venv launcher both work without extra glue.

Model weights live on GitHub releases. ``install_models`` (output) +
``install_input_models`` (wake/VAD) fetch them so /voice-io install is
a single end-to-end action.
"""

from __future__ import annotations

import importlib.util
import logging
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Final

log = logging.getLogger(__name__)

# Maps each pip distribution name to the import name used to detect
# presence. pip→import isn't a hyphen-replace rule in general (Pillow→PIL,
# python-dateutil→dateutil), so the mapping is explicit. onnxruntime +
# numpy come transitively from kokoro-onnx so we don't double-list them.
#
# Output side: TTS playback. Required when speak_ambient_enabled is on.
# Input side: wake-word + ASR. Required when voice_conversation_enabled is
# on. The split is what keeps ambient-only boots from probing
# openwakeword/faster_whisper via find_spec — the modularity test's
# meta_path blocker raises on any spec-lookup of input-side names.
_OUTPUT_DEPS: Final[dict[str, str]] = {
    "kokoro-onnx": "kokoro_onnx",
    "sounddevice": "sounddevice",
}
_INPUT_DEPS: Final[dict[str, str]] = {
    "openwakeword": "openwakeword",
    "faster-whisper": "faster_whisper",
}
# Public union — /voice-io install fetches everything in one shot.
AUDIO_DEPS: Final[dict[str, str]] = {**_OUTPUT_DEPS, **_INPUT_DEPS}


def missing_deps(*, include_input: bool = True) -> tuple[str, ...]:
    """Return the pip names of audio deps that aren't importable.

    ``include_input`` gates the wake-word / ASR deps. Ambient-only callers
    pass False so an unprobed openwakeword wheel doesn't show up as a
    missing dep — and so the modularity test's spec-finder blocker isn't
    tripped by an innocent presence-check.
    """
    deps_map = AUDIO_DEPS if include_input else _OUTPUT_DEPS
    return tuple(
        pip_name
        for pip_name, import_name in deps_map.items()
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


# GitHub releases tag for the Kokoro model files. Pinned so dropping
# kokoro-onnx in the future doesn't move the URL out from under us.
_KOKORO_RELEASE_BASE: Final[str] = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
)


def _kokoro_filenames(quantization: str) -> tuple[str, str]:
    """Resolve (model_filename, voices_filename) for a quantization variant.

    Imported lazily to avoid pulling the kokoro backend module at deps
    import time — keeps deps.py callable in tests that haven't built the
    backends package yet.
    """
    from tokenpal.audio.backends.kokoro import MODEL_FILENAMES, VOICES_FILENAME
    if quantization not in MODEL_FILENAMES:
        raise ValueError(
            f"unknown quantization {quantization!r} — pick from "
            f"{sorted(MODEL_FILENAMES)}",
        )
    return MODEL_FILENAMES[quantization], VOICES_FILENAME


def missing_models(data_dir: Path, quantization: str = "fp16") -> tuple[Path, ...]:
    audio_dir = data_dir / "audio"
    model_name, voices_name = _kokoro_filenames(quantization)
    return tuple(
        audio_dir / name
        for name in (model_name, voices_name)
        if not (audio_dir / name).exists()
    )


def _download(url: str, dest: Path, timeout_s: float) -> None:
    """Stream ``url`` to ``dest`` atomically.

    Writes to ``dest.tmp`` first and renames on success so a Ctrl+C mid-flight
    leaves no half-file the warmup() check would otherwise treat as present.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with urllib.request.urlopen(url, timeout=timeout_s) as r, tmp.open("wb") as f:
        # 1MB chunks — small enough that Ctrl+C interrupts reasonably fast.
        while chunk := r.read(1 << 20):
            f.write(chunk)
    tmp.replace(dest)


def install_models(
    data_dir: Path,
    quantization: str = "fp16",
    timeout_s: float = 600.0,
) -> InstallResult:
    """Fetch missing Kokoro model files into ``<data_dir>/audio/``."""
    missing = missing_models(data_dir, quantization)
    if not missing:
        return InstallResult(ok=True, message="audio models already present.")

    fetched: list[str] = []
    for path in missing:
        url = f"{_KOKORO_RELEASE_BASE}/{path.name}"
        log.info("Fetching %s -> %s", url, path)
        try:
            _download(url, path, timeout_s)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return InstallResult(
                ok=False,
                message=f"download {path.name} failed: {e}",
            )
        except KeyboardInterrupt:
            return InstallResult(ok=False, message="model download cancelled.")
        fetched.append(path.name)
    return InstallResult(
        ok=True,
        message=f"downloaded: {', '.join(fetched)} ({quantization}).",
    )


def install_all(
    data_dir: Path,
    quantization: str = "fp16",
    timeout_s: float = 600.0,
    *,
    include_input: bool = True,
) -> InstallResult:
    """Run wheels then output models then input models.

    Stops at the first failure so the message points at the actual
    blocker instead of a downstream ImportError. ``include_input``
    gates the wake/VAD model fetch — typed-only (audio off) callers
    pass False; ambient-only doesn't need them either, but /voice-io
    install routinely runs from a half-configured state so we default
    to True and let it succeed even if voice mode flips on later.
    """
    deps_result = install(timeout_s=timeout_s)
    if not deps_result.ok:
        return deps_result
    models_result = install_models(data_dir, quantization, timeout_s=timeout_s)
    if not models_result.ok:
        return models_result
    if include_input:
        input_result = install_input_models(data_dir, timeout_s=timeout_s)
        if not input_result.ok:
            return input_result
        message = (
            f"{deps_result.message} {models_result.message} "
            f"{input_result.message}"
        )
    else:
        message = f"{deps_result.message} {models_result.message}"
    return InstallResult(ok=True, message=message.strip())


# OpenWakeWord v0.5.1 release ships every input-side onnx we need —
# silero VAD, the shared mel + embedding, and the stock wakeword. Pinning
# all four to one release tag is atomic: bump the constant to upgrade
# everything, no drift between independently-versioned files.
_OWW_RELEASE_BASE: Final[str] = (
    "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1"
)
_SILERO_VAD_URL: Final[str] = f"{_OWW_RELEASE_BASE}/silero_vad.onnx"
_OWW_MODEL_URLS: Final[dict[str, str]] = {
    name: f"{_OWW_RELEASE_BASE}/{name}"
    for name in (
        "hey_jarvis_v0.1.onnx",
        "melspectrogram.onnx",
        "embedding_model.onnx",
    )
}


def missing_input_models(data_dir: Path) -> tuple[Path, ...]:
    """Return the input-side model files that aren't on disk yet.

    Whisper weights are excluded — faster-whisper auto-downloads them on
    first transcribe, and the file lives under download_root with a
    nontrivial directory layout we don't want to predict here.
    """
    audio_dir = data_dir / "audio"
    expected = [
        audio_dir / "vad" / "silero_vad.onnx",
        *(audio_dir / "wakeword" / name for name in _OWW_MODEL_URLS),
    ]
    return tuple(p for p in expected if not p.exists())


def install_input_models(
    data_dir: Path, timeout_s: float = 600.0,
) -> InstallResult:
    """Fetch Silero VAD + the OpenWakeWord model trio into <data_dir>/audio.

    Downloads run in parallel — they're network-bound and independent.
    On any failure we still return a clean error; partial successes
    leave .tmp atomicity intact for retry.
    """
    from concurrent.futures import ThreadPoolExecutor

    audio_dir = data_dir / "audio"
    targets: list[tuple[Path, str]] = []
    if not (audio_dir / "vad" / "silero_vad.onnx").exists():
        targets.append((audio_dir / "vad" / "silero_vad.onnx", _SILERO_VAD_URL))
    for name, url in _OWW_MODEL_URLS.items():
        path = audio_dir / "wakeword" / name
        if not path.exists():
            targets.append((path, url))

    if not targets:
        return InstallResult(ok=True, message="input models already present.")

    def _fetch(target: tuple[Path, str]) -> tuple[Path, Exception | None]:
        path, url = target
        log.info("Fetching %s -> %s", url, path)
        try:
            _download(url, path, timeout_s)
            return path, None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return path, e

    try:
        with ThreadPoolExecutor(max_workers=len(targets)) as pool:
            results = list(pool.map(_fetch, targets))
    except KeyboardInterrupt:
        return InstallResult(ok=False, message="input download cancelled.")

    for path, err in results:
        if err is not None:
            return InstallResult(
                ok=False, message=f"download {path.name} failed: {err}",
            )
    return InstallResult(
        ok=True,
        message=f"downloaded: {', '.join(p.name for p, _ in results)}.",
    )
