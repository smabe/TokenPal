"""Remote SSH orchestrator for LoRA fine-tuning.

Automates the full pipeline: build wheel bundle → push to GPU machine →
install deps → push base model → train → merge adapter →
pull merged safetensors → register with local Ollama.

Uses ssh/scp — no extra Python deps on the local machine.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shlex
import subprocess
import tarfile
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tokenpal.config.schema import FinetuneConfig, RemoteTrainConfig
from tokenpal.tools.voice_profile import VoiceProfile, slugify

log = logging.getLogger(__name__)

ProgressCallback = Callable[[str], None]


# ---------------------------------------------------------------------------
# Bundle building — auto-build wheel + install.sh into a tarball
# ---------------------------------------------------------------------------

_INSTALL_SH = r"""#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="${TOKENPAL_TRAINING_DIR:-$HOME/tokenpal-training}"
PYTHON="${PYTHON:-python3}"
SENTINEL="$INSTALL_DIR/.venv/.install-ok"

# --- Phase 0: WSL mount relocation ---
# pip/venv on DrvFS (/mnt/c/) is 5-10x slower and breaks symlinks.
# Copy to native ext4 before doing anything expensive.
if [[ "$SCRIPT_DIR" == /mnt/* ]]; then
    echo "[1/6] Relocating from Windows mount to native filesystem..."
    mkdir -p "$INSTALL_DIR"
    cp -f "$SCRIPT_DIR"/*.whl "$INSTALL_DIR/" 2>/dev/null || true
    cp -f "$SCRIPT_DIR"/*.json "$INSTALL_DIR/" 2>/dev/null || true
    cp -f "$SCRIPT_DIR/install.sh" "$INSTALL_DIR/"
    cp -f "$SCRIPT_DIR/.source-hash" "$INSTALL_DIR/" 2>/dev/null || true
    cd "$INSTALL_DIR"
    exec bash install.sh
fi

# --- Phase 1: Python check ---
echo "[2/6] Checking Python..."
if ! command -v "$PYTHON" &>/dev/null; then
    echo "ERROR: $PYTHON not found. Install Python 3.12+ or set PYTHON env var."
    exit 1
fi
PY_MINOR=$("$PYTHON" -c "import sys; print(sys.version_info.minor)")
PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
if (( PY_MINOR < 12 )); then
    echo "ERROR: Python 3.12+ required, found $PY_VER"
    exit 1
fi
if (( PY_MINOR > 12 )); then
    echo "WARNING: Python $PY_VER detected. Training deps may not support it yet."
fi

# --- Phase 2: GPU detection ---
echo "[3/6] Detecting GPU..."
if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
    GPU_BACKEND="cuda"
    CUDA_VER=$(nvcc --version 2>/dev/null | grep -oP 'release \K[0-9]+\.[0-9]+' || echo "12.4")
    MAJOR=${CUDA_VER%%.*}
    MINOR=${CUDA_VER##*.}
    if (( MAJOR == 11 )); then TORCH_IDX="cu118"
    elif (( MAJOR == 12 && MINOR <= 1 )); then TORCH_IDX="cu121"
    else TORCH_IDX="cu124"; fi
    TORCH_URL="https://download.pytorch.org/whl/$TORCH_IDX"
elif command -v rocm-smi &>/dev/null || command -v rocminfo &>/dev/null; then
    GPU_BACKEND="rocm"
    TORCH_URL="https://download.pytorch.org/whl/rocm6.2"
else
    if lspci 2>/dev/null | grep -qi "neural\|NPU"; then
        echo "ERROR: Intel NPU detected but not supported for training."
        echo "Use a CUDA or ROCm GPU, or train on a different machine."
    else
        echo "ERROR: No CUDA or ROCm GPU detected."
    fi
    exit 1
fi
echo "  GPU backend: $GPU_BACKEND (index: $TORCH_URL)"

# --- Phase 3: Venv ---
echo "[4/6] Setting up venv..."
VENV="$INSTALL_DIR/.venv"
DESIRED_PY=$("$PYTHON" --version 2>&1)
CURRENT_PY=$("$VENV/bin/python" --version 2>&1 || echo "none")

# Only recreate venv if Python version changed (not for partial installs —
# re-downloading 900MB of PyTorch on every retry is too expensive over WSL)
if [[ "$DESIRED_PY" != "$CURRENT_PY" ]]; then
    [[ -d "$VENV" ]] && echo "  Python changed, recreating venv..." && rm -rf "$VENV"
    "$PYTHON" -m venv "$VENV"
elif [[ ! -d "$VENV" ]]; then
    "$PYTHON" -m venv "$VENV"
fi
"$VENV/bin/pip" install --upgrade pip -q

# --- Phase 4: PyTorch (backend-specific) ---
echo "[5/6] Installing PyTorch ($GPU_BACKEND)..."
# Check if torch is already installed and working
if "$VENV/bin/python" -c "import torch; assert torch.cuda.is_available()" 2>/dev/null; then
    echo "  PyTorch already installed and CUDA working, skipping."
else
    # Download first, then install from cache — avoids SSL flakes during
    # large downloads in WSL2 (known issue with DrvFS network buffers).
    TORCH_CACHE="$INSTALL_DIR/.torch-cache"
    mkdir -p "$TORCH_CACHE"
    for attempt in 1 2 3; do
        "$VENV/bin/pip" download --extra-index-url "$TORCH_URL" \
            torch torchvision --no-deps -d "$TORCH_CACHE" -q && break
        echo "  Retry $attempt/3 (torch download failed)..."
        sleep 5
    done
    "$VENV/bin/pip" install --extra-index-url "$TORCH_URL" \
        --find-links "$TORCH_CACHE" torch torchvision -q
fi

# --- Phase 5: TokenPal + training deps ---
echo "[6/6] Installing tokenpal training bundle..."
WHEEL=$(ls "$INSTALL_DIR"/tokenpal-*.whl 2>/dev/null | head -1)
if [[ -z "$WHEEL" ]]; then
    echo "ERROR: No tokenpal wheel found in $INSTALL_DIR"
    exit 1
fi
"$VENV/bin/pip" install "${WHEEL}[training]" -q

# --- Verify ---
"$VENV/bin/python" -c "
import torch
assert torch.cuda.is_available(), 'GPU not available to PyTorch'
print(f'  GPU OK: {torch.cuda.get_device_name(0)}')
"
echo "  Python: $VENV/bin/python"
touch "$SENTINEL"
echo "Install complete."
"""


def _hash_training_sources() -> str:
    """Hash the training-related source files to detect changes.

    Returns a hex digest. Changes when dataset_prep.py, finetune_voice.py,
    or voice_profile.py are modified.
    """
    tools_dir = Path(__file__).parent
    h = hashlib.sha256()
    for name in sorted(["dataset_prep.py", "finetune_voice.py", "voice_profile.py"]):
        path = tools_dir / name
        if path.exists():
            h.update(path.read_bytes())
    return h.hexdigest()[:16]


def _build_bundle(profile_json_path: Path | None = None) -> Path:
    """Build a training bundle tarball.

    Contains: tokenpal wheel, install.sh, optional profile JSON, source hash.
    Returns the path to the tarball (in a temp directory).
    """
    bundle_dir = Path(tempfile.mkdtemp()) / "tokenpal-training-bundle"
    bundle_dir.mkdir()

    # Build wheel
    project_root = Path(__file__).parent.parent.parent
    result = subprocess.run(
        ["python3", "-m", "build", "--wheel", "--outdir", str(bundle_dir)],
        cwd=str(project_root),
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Wheel build failed:\n{result.stderr[-500:]}")

    # Write install.sh
    install_sh = bundle_dir / "install.sh"
    install_sh.write_text(_INSTALL_SH)
    install_sh.chmod(0o755)

    # Write source hash
    source_hash = _hash_training_sources()
    (bundle_dir / ".source-hash").write_text(source_hash)

    # Include profile JSON if provided
    if profile_json_path and profile_json_path.exists():
        import shutil
        shutil.copy2(profile_json_path, bundle_dir)

    # Create tarball
    tarball = bundle_dir.parent / "tokenpal-training-bundle.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        for item in bundle_dir.iterdir():
            tar.add(item, arcname=item.name)

    return tarball


def _ssh_target(remote: RemoteTrainConfig) -> str:
    """Build the ssh user@host string."""
    if remote.user:
        return f"{remote.user}@{remote.host}"
    return str(remote.host)


def _wsl_wrap(command: str) -> str:
    """Wrap a command to run inside WSL on a Windows host.

    Uses a login shell so /usr/lib/wsl/lib (nvidia) is on PATH.
    Double-quotes the command because the SSH target is PowerShell,
    which doesn't handle single quotes the same way bash does.
    """
    escaped = command.replace('\\', '\\\\').replace('"', '\\"')
    return f'wsl -e bash -lc "{escaped}"'


def _wsl_cmd_dir(remote: RemoteTrainConfig) -> str:
    """Return the working directory for WSL commands.

    Uses ``$HOME/<relative>`` which bash expands to ``/home/<user>/...``,
    avoiding case-sensitivity mismatches with the Windows ``/mnt/c/Users/``
    mount path.
    """
    rel = remote.remote_dir
    if rel.startswith("~/"):
        return f"$HOME/{rel[2:]}"
    if rel == "~":
        return "$HOME"
    return rel


async def _run_ssh(
    remote: RemoteTrainConfig,
    command: str,
    progress: ProgressCallback | None = None,
    timeout: float = 3600,
) -> tuple[int, str, str]:
    """Run a command on the remote machine via SSH.

    Streams stdout lines to the progress callback in real time.
    Returns (returncode, stdout, stderr).
    """
    target = _ssh_target(remote)
    proc = await asyncio.create_subprocess_exec(
        "ssh", "-o", "BatchMode=yes", target, command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    async def _read_stream(
        stream: asyncio.StreamReader,
        buf: list[str],
        is_stdout: bool,
    ) -> None:
        while True:
            line_bytes = await stream.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="replace").rstrip()
            buf.append(line)
            if is_stdout and progress:
                progress(line)

    assert proc.stdout is not None
    assert proc.stderr is not None

    try:
        await asyncio.wait_for(
            asyncio.gather(
                _read_stream(proc.stdout, stdout_lines, True),
                _read_stream(proc.stderr, stderr_lines, False),
            ),
            timeout=timeout,
        )
    except TimeoutError:
        proc.kill()
        return -1, "", "Timed out"

    await proc.wait()
    return (
        proc.returncode or 0,
        "\n".join(stdout_lines),
        "\n".join(stderr_lines),
    )


async def _run_scp(
    remote: RemoteTrainConfig,
    local_path: str,
    remote_path: str,
    *,
    pull: bool = False,
    timeout: float = 1800,
) -> tuple[int, str]:
    """Copy files via SCP. Returns (returncode, stderr)."""
    target = _ssh_target(remote)
    if pull:
        args = ["scp", f"{target}:{remote_path}", local_path]
    else:
        args = ["scp", local_path, f"{target}:{remote_path}"]

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout,
        )
    except TimeoutError:
        proc.kill()
        return -1, "SCP timed out"

    return (
        proc.returncode or 0,
        stderr.decode("utf-8", errors="replace"),
    )


async def _run_wsl_ssh(
    remote: RemoteTrainConfig,
    command: str,
    progress: ProgressCallback | None = None,
    timeout: float = 3600,
) -> tuple[int, str, str]:
    """Run a command inside WSL on a Windows host via SSH."""
    return await _run_ssh(remote, _wsl_wrap(command), progress, timeout)


async def _run_scp_recursive(
    remote: RemoteTrainConfig,
    local_path: str,
    remote_path: str,
    *,
    pull: bool = False,
    timeout: float = 3600,
) -> tuple[int, str]:
    """Copy directories recursively via SCP. Returns (returncode, stderr)."""
    target = _ssh_target(remote)
    if pull:
        args = ["scp", "-r", f"{target}:{remote_path}", local_path]
    else:
        args = ["scp", "-r", local_path, f"{target}:{remote_path}"]

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout,
        )
    except TimeoutError:
        proc.kill()
        return -1, "SCP timed out"

    return (
        proc.returncode or 0,
        stderr.decode("utf-8", errors="replace"),
    )


async def _ensure_base_model(
    remote: RemoteTrainConfig,
    base_model: str,
    model_dir: str,
    venv_py: str,
    _ssh: Any,
    progress: ProgressCallback,
) -> str:
    """Ensure the base model is available on the remote.

    Tries remote download first (huggingface-cli via venv), falls back
    to local download + SCP push.

    Returns the remote path to the model directory.
    """
    # Check if model already exists on remote
    rc, out, _ = await _ssh(remote, f"test -d {model_dir} && echo exists", timeout=15)
    if rc == 0 and "exists" in out:
        progress("Base model already on remote.")
        return model_dir

    progress(f"Downloading base model: {base_model}")

    # Try downloading directly on the remote first (use venv python)
    dl_cmd = (
        f'{venv_py} -c "'
        f"from huggingface_hub import snapshot_download; "
        f"snapshot_download('{base_model}', local_dir='{model_dir}')\""
    )
    rc, _, err = await _ssh(remote, dl_cmd, progress, timeout=1800)
    if rc == 0:
        progress("Base model downloaded on remote.")
        return model_dir

    # Fall back to local download + SCP push
    progress("Remote download failed, downloading locally...")
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise RemoteTrainError(
            "model_push",
            "Install huggingface_hub locally: pip install huggingface_hub",
        ) from exc

    local_model_dir = Path(tempfile.mkdtemp()) / "model"
    snapshot_download(base_model, local_dir=str(local_model_dir))

    progress("Pushing base model to remote (this may take a while)...")
    scp_rdir = remote.remote_dir
    rc, err = await _run_scp_recursive(
        remote, str(local_model_dir), f"{scp_rdir}/model", timeout=3600,
    )
    if rc != 0:
        raise RemoteTrainError("model_push", f"SCP failed: {err[:200]}")

    # For WSL: SCP lands on Windows filesystem, copy to WSL-native path
    if remote.use_wsl:
        rc, win_home, _ = await _run_ssh(
            remote, "echo %USERPROFILE%", timeout=10,
        )
        if rc == 0:
            win_home = win_home.strip().replace("\\", "/")
            if len(win_home) >= 2 and win_home[1] == ":":
                mp = f"/mnt/{win_home[0].lower()}{win_home[2:]}"
            else:
                mp = win_home
            rel = scp_rdir.lstrip("~/")
            wm = f"{mp}/{rel}" if rel else mp
            cp_cmd = f'cp -r "{wm}/model" {model_dir}'
            rc, _, err = await _ssh(remote, cp_cmd, timeout=300)
            if rc != 0:
                raise RemoteTrainError(
                    "model_push",
                    f"Failed to copy model to WSL: {err[:200]}",
                )

    # Clean up local temp
    import shutil
    shutil.rmtree(local_model_dir.parent, ignore_errors=True)

    progress("Base model pushed to remote.")
    return model_dir


class RemoteTrainError(Exception):
    """Raised when a remote training step fails.

    Includes actionable debug info: step name, detail message,
    and optional hints (SSH commands, retry instructions).
    """

    def __init__(
        self,
        step: str,
        detail: str,
        *,
        hint: str = "",
    ) -> None:
        self.step = step
        self.detail = detail
        self.hint = hint
        parts = [f"{step}: {detail}"]
        if hint:
            parts.append(f"\n{hint}")
        super().__init__("\n".join(parts))


async def remote_finetune(
    profile: VoiceProfile,
    config: FinetuneConfig,
    progress: ProgressCallback | None = None,
) -> Path:
    """Run the full remote fine-tuning pipeline.

    Steps: push scripts + profile → ensure base model on remote →
    prep data → train (HF_HUB_OFFLINE=1) → merge adapter →
    pull merged safetensors → register with Ollama.

    Returns the path to the locally saved merged model directory.

    Raises RemoteTrainError on failure.
    """
    remote = config.remote
    if not remote.host:
        raise RemoteTrainError(
            "config", "No remote host configured. Set [finetune.remote] host in config.toml",
        )

    slug = slugify(profile.character)
    q_slug = shlex.quote(slug)
    scp_rdir = remote.remote_dir  # raw path for SCP (no shell quoting)
    cmd_rdir = _wsl_cmd_dir(remote) if remote.use_wsl else scp_rdir
    _ssh = _run_wsl_ssh if remote.use_wsl else _run_ssh
    model_dir = f"{cmd_rdir}/model"  # base model lives here on remote
    venv_py = f"{cmd_rdir}/.venv/bin/python"  # installed by install.sh

    def _progress(msg: str) -> None:
        log.info("Finetune: %s", msg)
        if progress:
            progress(msg)

    # -- Preflight: verify GPU access --
    _progress("Connecting to remote GPU...")
    rc, _out, err = await _ssh(remote, "nvidia-smi", timeout=30)
    if rc != 0:
        raise RemoteTrainError(
            "preflight",
            f"Can't reach {remote.host} or no GPU. {err[:200]}",
        )
    _progress("GPU verified.")

    # -- Preflight: check disk space --
    rc, df_out, _ = await _ssh(
        remote,
        f"df -BG {cmd_rdir} 2>/dev/null | tail -1 | awk '{{print $4}}'",
        timeout=10,
    )
    if rc == 0 and df_out.strip():
        try:
            free_gb = int(df_out.strip().rstrip("G"))
            if free_gb < 25:
                _progress(f"WARNING: Only {free_gb}GB free on remote. Training needs ~25GB.")
        except ValueError:
            pass  # Can't parse, skip check

    # -- Create remote working directory --
    if remote.use_wsl:
        rel = remote.remote_dir
        if rel.startswith("~/"):
            rel = rel[2:]
        mkdir_cmd = f"if not exist %USERPROFILE%\\{rel} mkdir %USERPROFILE%\\{rel}"
    else:
        mkdir_cmd = f"mkdir -p {shlex.quote(scp_rdir)}"
    rc, _, err = await _run_ssh(remote, mkdir_cmd)
    if rc != 0:
        raise RemoteTrainError("mkdir", err[:200])

    # -- Build + push bundle if source code changed or install incomplete --
    local_hash = _hash_training_sources()
    rc, remote_hash_out, _ = await _ssh(
        remote, f"cat {cmd_rdir}/.source-hash 2>/dev/null || echo none", timeout=10,
    )
    remote_hash = remote_hash_out.strip()

    # Also check if install completed (sentinel file exists)
    rc, sentinel_out, _ = await _ssh(
        remote,
        f"test -f {cmd_rdir}/.venv/.install-ok && echo ok || echo missing",
        timeout=10,
    )
    if "missing" in sentinel_out:
        remote_hash = "incomplete"  # force re-push

    import json
    from dataclasses import asdict

    # Write profile JSON to a temp file for inclusion in bundle
    profile_data = asdict(profile)
    profile_data["line_count"] = profile.line_count
    profile_json = Path(tempfile.mkdtemp()) / f"{slug}.json"
    profile_json.write_text(json.dumps(profile_data, ensure_ascii=False, indent=2))

    if local_hash != remote_hash:
        _progress("Building training bundle...")
        try:
            tarball = _build_bundle(profile_json)
        except RuntimeError as exc:
            raise RemoteTrainError("push", str(exc)) from exc

        _progress("Pushing bundle to remote...")
        rc, err = await _run_scp(
            remote, str(tarball), f"{scp_rdir}/bundle.tar.gz",
        )
        if rc != 0:
            raise RemoteTrainError("push", f"SCP failed: {err[:200]}")

        # Extract and install
        if remote.use_wsl:
            # SCP landed on Windows filesystem. Resolve the WSL mount path
            # so we can access it from within WSL. install.sh will self-relocate
            # from /mnt/c/... to ~/tokenpal-training/ automatically.
            rc, win_home, _ = await _run_ssh(
                remote, "echo %USERPROFILE%", timeout=10,
            )
            if rc != 0:
                raise RemoteTrainError("wsl_bridge", "Failed to resolve Windows home dir")
            win_home = win_home.strip().replace("\\", "/")
            if len(win_home) >= 2 and win_home[1] == ":":
                mount_path = f"/mnt/{win_home[0].lower()}{win_home[2:]}"
            else:
                mount_path = win_home
            rel = remote.remote_dir.lstrip("~/")
            win_mount = f"{mount_path}/{rel}" if rel else mount_path

            extract_cmd = (
                f'cd "{win_mount}" && tar xzf bundle.tar.gz && bash install.sh'
            )
        else:
            extract_cmd = (
                f"cd {shlex.quote(scp_rdir)} && tar xzf bundle.tar.gz && "
                f"bash install.sh"
            )
        _progress("Installing training environment...")
        rc, out, err = await _ssh(remote, extract_cmd, progress, timeout=1800)
        if rc != 0:
            raise RemoteTrainError("install", f"Install failed:\n{err[-500:]}")

        _progress("Training environment ready.")
    else:
        _progress("Training code unchanged, skipping bundle push.")
        # Still push the profile JSON (it's per-run data)
        rc, err = await _run_scp(
            remote, str(profile_json), f"{scp_rdir}/{q_slug}.json",
        )
        if rc != 0:
            raise RemoteTrainError("push", f"Profile SCP failed: {err[:200]}")
        # WSL: copy profile to native path
        if remote.use_wsl:
            rc, win_home, _ = await _run_ssh(
                remote, "echo %USERPROFILE%", timeout=10,
            )
            if rc == 0:
                win_home = win_home.strip().replace("\\", "/")
                if len(win_home) >= 2 and win_home[1] == ":":
                    mp = f"/mnt/{win_home[0].lower()}{win_home[2:]}"
                else:
                    mp = win_home
                rel = remote.remote_dir.lstrip("~/")
                wm = f"{mp}/{rel}" if rel else mp
                await _ssh(
                    remote,
                    f'cp "{wm}/{q_slug}.json" {cmd_rdir}/',
                    timeout=15,
                )

    # Clean up local temp files
    profile_json.unlink(missing_ok=True)

    # -- Ensure base model is available on remote --
    _progress("Checking base model on remote...")
    await _ensure_base_model(
        remote, config.base_model, model_dir, venv_py, _ssh, _progress,
    )

    # -- Prepare training data (using installed entry point) --
    _progress(f"Preparing training data ({profile.line_count} lines)...")
    prep_cmd = (
        f"cd {cmd_rdir} && {venv_py} -m tokenpal.tools.finetune_voice prep "
        f"{q_slug}.json -o data"
    )
    rc, out, err = await _ssh(remote, prep_cmd, progress)
    if rc != 0:
        raise RemoteTrainError("prep", f"Dataset prep failed:\n{err[-500:]}")

    # -- LoRA training (in tmux for network resilience) --
    _progress("Starting LoRA training...")
    # Don't shlex.quote — model_dir may contain $HOME which needs shell expansion
    q_model_dir = model_dir
    tmux_session = f"tokenpal-{slug}"
    train_cmd = (
        f"cd {cmd_rdir} && HF_HUB_OFFLINE=1 {venv_py} -m "
        f"tokenpal.tools.finetune_voice train "
        f"--data data/ --output output/ "
        f"--base-model {q_model_dir}"
    )

    # Check for existing checkpoints (resume support)
    rc, ckpt_out, _ = await _ssh(
        remote,
        f"ls -d {cmd_rdir}/output/adapter/checkpoint-* 2>/dev/null | tail -1",
        timeout=10,
    )
    if rc == 0 and ckpt_out.strip():
        _progress(f"Resuming from checkpoint: {ckpt_out.strip().split('/')[-1]}")
        train_cmd += " --resume"

    # Acquire lock to prevent concurrent training
    lock_cmd = (
        "flock -n /tmp/tokenpal-training.lock -c "
        "'echo locked' 2>/dev/null || echo busy"
    )
    rc, lock_out, _ = await _ssh(remote, lock_cmd, timeout=10)
    if "busy" in lock_out:
        raise RemoteTrainError(
            "train",
            "Another training job is already running on this machine. "
            "Wait for it to finish or kill it manually.",
        )

    # Write training command to a script file to avoid quoting issues
    # with tmux + flock + $HOME expansion through WSL SSH.
    # Base64-encode to survive all quoting layers.
    import base64
    script_content = (
        f"#!/bin/bash\nset -eo pipefail\n"
        f"{train_cmd} 2>&1 | tee {cmd_rdir}/train.log\n"
        f"echo EXIT_CODE=$? >> {cmd_rdir}/train.log\n"
    )
    # Resolve $HOME in the script since it will run in a non-login shell
    # via flock — replace $HOME with the expanded path
    rc, home_out, _ = await _ssh(remote, "echo $HOME", timeout=5)
    if rc == 0 and home_out.strip():
        script_content = script_content.replace("$HOME", home_out.strip())

    b64 = base64.b64encode(script_content.encode()).decode()
    write_cmd = (
        f"echo {b64} | base64 -d > {cmd_rdir}/run_train.sh && "
        f"chmod +x {cmd_rdir}/run_train.sh"
    )
    await _ssh(remote, write_cmd, timeout=10)

    # Run in tmux so it survives SSH drops
    tmux_cmd = (
        f"tmux kill-session -t {tmux_session} 2>/dev/null; "
        f"tmux new-session -d -s {tmux_session} "
        f"'flock /tmp/tokenpal-training.lock {cmd_rdir}/run_train.sh'"
    )
    rc, _, err = await _ssh(remote, tmux_cmd, timeout=30)
    if rc != 0:
        raise RemoteTrainError("train", f"Failed to start training: {err[:200]}")

    # Poll for completion
    _progress("Training in progress (SSH-safe, survives disconnects)...")
    poll_interval = 30
    while True:
        await asyncio.sleep(poll_interval)

        # Check if tmux session still exists
        rc, out, _ = await _ssh(
            remote,
            f"tmux has-session -t {tmux_session} 2>/dev/null && echo running || echo done",
            timeout=15,
        )
        if "done" in out:
            break

        # Stream last line of log for progress
        rc, log_tail, _ = await _ssh(
            remote, f"tail -1 {cmd_rdir}/train.log 2>/dev/null", timeout=10,
        )
        if rc == 0 and log_tail.strip():
            _progress(log_tail.strip())

    # Check training result
    rc, log_end, _ = await _ssh(
        remote, f"tail -5 {cmd_rdir}/train.log 2>/dev/null", timeout=10,
    )
    log_text = log_end.strip()
    target = _ssh_target(remote)
    debug_hint = (
        f"To debug:\n"
        f"  ssh {target}\n"
        f"  cd {cmd_rdir} && source .venv/bin/activate\n"
        f"  cat train.log\n"
        f"\nTo retry:  /voice finetune {profile.character}"
    )
    if "EXIT_CODE=0" not in log_text:
        # Check for checkpoints to suggest resume
        rc2, ckpt, _ = await _ssh(
            remote,
            f"ls -d {cmd_rdir}/output/adapter/checkpoint-* 2>/dev/null | tail -1",
            timeout=10,
        )
        ckpt_hint = ""
        if rc2 == 0 and ckpt.strip():
            ckpt_name = ckpt.strip().split("/")[-1]
            ckpt_hint = f"\nCheckpoints saved: output/adapter/{ckpt_name}"

        if "OutOfMemoryError" in log_text or "CUDA out of memory" in log_text:
            raise RemoteTrainError(
                "train",
                "GPU out of memory. Try reducing batch_size in "
                "[finetune] config.",
                hint=f"{ckpt_hint}\n{debug_hint}",
            )
        raise RemoteTrainError(
            "train",
            f"Training failed:\n{log_text[-500:]}",
            hint=f"{ckpt_hint}\n{debug_hint}",
        )

    # -- Merge adapter into base model (safetensors) --
    _progress("Merging adapter into base model...")
    merge_cmd = (
        f"cd {cmd_rdir} && HF_HUB_OFFLINE=1 {venv_py} -m "
        f"tokenpal.tools.finetune_voice merge "
        f"--adapter output/adapter --output output/merged "
        f"--base-model {q_model_dir}"
    )
    rc, out, err = await _ssh(remote, merge_cmd, progress, timeout=3600)
    if rc != 0:
        raise RemoteTrainError(
            "merge",
            f"Merge failed:\n{err[-500:]}",
            hint=debug_hint,
        )

    # Compute remote checksum for integrity verification
    rc, remote_hash_str, _ = await _ssh(
        remote,
        f"find {cmd_rdir}/output/merged -type f -name '*.safetensors' "
        f"-exec sha256sum {{}} + 2>/dev/null | sort | sha256sum | cut -d' ' -f1",
        timeout=30,
    )
    remote_model_hash = remote_hash_str.strip() if rc == 0 else ""

    # -- Download merged model directory --
    _progress("Downloading merged model...")
    local_models_dir = (
        Path(config.output_dir).expanduser() / "models"
    )
    local_model_dir = local_models_dir / f"tokenpal-{slug}"
    local_model_dir.mkdir(parents=True, exist_ok=True)

    # For WSL: merged dir is on WSL-native path, but SCP goes through Windows.
    # Copy it back to the Windows mount first.
    if remote.use_wsl:
        rc, win_home, _ = await _run_ssh(
            remote, "echo %USERPROFILE%", timeout=10,
        )
        win_home = win_home.strip().replace("\\", "/")
        if len(win_home) >= 2 and win_home[1] == ":":
            mp = f"/mnt/{win_home[0].lower()}{win_home[2:]}"
        else:
            mp = win_home
        rel = remote.remote_dir.lstrip("~/")
        win_mount = f"{mp}/{rel}" if rel else mp

        rc, _, err = await _ssh(
            remote,
            f'cp -r {cmd_rdir}/output/merged "{win_mount}/merged"',
            timeout=300,
        )
        if rc != 0:
            raise RemoteTrainError(
                "wsl_bridge",
                f"Failed to copy merged model to Windows: {err[:200]}",
            )
        pull_source = f"{scp_rdir}/merged"
    else:
        pull_source = f"{scp_rdir}/output/merged"

    rc, err = await _run_scp_recursive(
        remote,
        str(local_model_dir),
        pull_source,
        pull=True,
        timeout=3600,
    )
    if rc != 0:
        raise RemoteTrainError("pull", f"Failed to download merged model: {err[:200]}")

    # Report size and verify integrity
    total_size = sum(f.stat().st_size for f in local_model_dir.rglob("*") if f.is_file())
    size_gb = total_size / 1e9
    _progress(f"Downloaded {size_gb:.1f} GB")

    if remote_model_hash:
        h = hashlib.sha256()
        for sf in sorted(local_model_dir.glob("*.safetensors")):
            file_hash = hashlib.sha256(sf.read_bytes()).hexdigest()
            h.update(f"{file_hash}  {sf.name}\n".encode())
        local_model_hash = h.hexdigest()
        if local_model_hash != remote_model_hash:
            log.warning(
                "Model checksum mismatch — file may be corrupted. "
                "Remote: %s, Local: %s",
                remote_model_hash[:12], local_model_hash[:12],
            )
            _progress("WARNING: Model checksum mismatch — may be corrupted.")

    # -- Register with Ollama (FROM safetensors dir) --
    _progress("Registering with Ollama...")
    from tokenpal.tools.dataset_prep import build_system_prompt
    from tokenpal.tools.finetune_voice import register_ollama

    model_name = f"tokenpal-{slug}"
    system_prompt = build_system_prompt(profile)
    if not register_ollama(local_model_dir, model_name, system_prompt):
        raise RemoteTrainError(
            "register", "Ollama registration failed. Is ollama running?",
        )

    # -- Cleanup remote artifacts (keep base model + venv + source hash) --
    _progress("Cleaning up remote files...")
    await _ssh(
        remote,
        f"rm -rf {cmd_rdir}/data {cmd_rdir}/output "
        f"{cmd_rdir}/*.json {cmd_rdir}/*.whl {cmd_rdir}/install.sh "
        f"{cmd_rdir}/bundle.tar.gz",
        timeout=30,
    )
    if remote.use_wsl:
        await _run_ssh(
            remote,
            f'del /Q "{scp_rdir}\\bundle.tar.gz" '
            f'"{scp_rdir}\\*.whl" "{scp_rdir}\\*.json" '
            f'"{scp_rdir}\\install.sh" 2>nul & '
            f'rmdir /S /Q "{scp_rdir}\\merged" 2>nul',
            timeout=30,
        )

    _progress(f"Done! Model: {model_name}")
    return local_model_dir


async def _ensure_wsl(
    remote: RemoteTrainConfig,
    progress: ProgressCallback | None = None,
) -> bool:
    """Ensure WSL + Ubuntu is installed on the Windows host.

    Returns True if WSL is ready. If WSL was just installed,
    tells the user to reboot and returns False.
    """
    _progress = progress or (lambda _msg: None)

    _progress("Checking WSL...")
    rc, out, _ = await _run_ssh(remote, "wsl -e echo wsl-ok", timeout=15)
    if rc == 0 and "wsl-ok" in out:
        _progress("WSL is ready.")
        return True

    # wsl --install requires admin privileges — can't run over SSH
    _progress("Installing WSL (needs admin)...")
    rc, out, err = await _run_ssh(
        remote, "wsl --install -d Ubuntu",
        progress, timeout=300,
    )
    if rc != 0:
        msg = (
            "WSL not installed. Run this in an admin PowerShell "
            f"on {remote.host}:\n  wsl --install -d Ubuntu\n"
            "Then reboot and run /voice finetune-setup again."
        )
        log.error("Finetune setup: %s", msg)
        _progress(msg)
        return False

    _progress(
        "WSL installed! Reboot the Windows machine, "
        "then run /voice finetune-setup again."
    )
    return False


def _setup_fail(msg: str, progress: ProgressCallback) -> bool:
    """Log a setup error and notify the user. Always returns False."""
    log.error("Finetune setup: %s", msg)
    progress(msg)
    return False


async def remote_setup(
    remote: RemoteTrainConfig,
    progress: ProgressCallback | None = None,
) -> bool:
    """One-time setup of the remote training environment.

    Builds a bundle, pushes it, and runs install.sh which handles:
    venv creation, CUDA/ROCm detection, PyTorch + deps installation.

    When use_wsl is True, bootstraps WSL+Ubuntu first.
    Returns True on success.
    """
    if not remote.host:
        log.error("No remote host configured.")
        return False

    def _progress(msg: str) -> None:
        log.info("Finetune setup: %s", msg)
        if progress:
            progress(msg)

    _progress(f"Connecting to {remote.host}...")
    rc, _, err = await _run_ssh(remote, "echo ok", timeout=15)
    if rc != 0:
        return _setup_fail(f"Can't reach {remote.host}: {err[:100]}", _progress)

    if remote.use_wsl:
        wsl_ready = await _ensure_wsl(remote, progress)
        if not wsl_ready:
            return False

    _ssh = _run_wsl_ssh if remote.use_wsl else _run_ssh

    _progress("Checking GPU...")
    rc, out, _ = await _ssh(remote, "nvidia-smi --query-gpu=name --format=csv,noheader")
    if rc != 0:
        return _setup_fail(f"No GPU detected on {remote.host}", _progress)
    _progress(f"GPU: {out.strip()}")

    # Create remote directory
    scp_rdir = remote.remote_dir
    if remote.use_wsl:
        rel = scp_rdir
        if rel.startswith("~/"):
            rel = rel[2:]
        mkdir_cmd = f"if not exist %USERPROFILE%\\{rel} mkdir %USERPROFILE%\\{rel}"
    else:
        mkdir_cmd = f"mkdir -p {shlex.quote(scp_rdir)}"
    rc, _, err = await _run_ssh(remote, mkdir_cmd)
    if rc != 0:
        return _setup_fail(f"Failed to create remote dir: {err[:200]}", _progress)

    if remote.use_wsl:
        _progress("Installing system packages...")
        rc, _, err = await _ssh(
            remote,
            "sudo apt-get update && sudo apt-get install -y python3-venv python3-pip",
            progress, timeout=300,
        )
        if rc != 0:
            return _setup_fail(f"apt install failed: {err[-200:]}", _progress)

    # Build and push bundle
    _progress("Building training bundle...")
    try:
        tarball = _build_bundle()
    except RuntimeError as exc:
        return _setup_fail(f"Bundle build failed: {exc}", _progress)

    _progress("Pushing bundle to remote...")
    rc, err = await _run_scp(remote, str(tarball), f"{scp_rdir}/bundle.tar.gz")
    if rc != 0:
        return _setup_fail(f"SCP failed: {err[:200]}", _progress)

    # Extract and install
    rdir = _wsl_cmd_dir(remote) if remote.use_wsl else scp_rdir
    if remote.use_wsl:
        extract_cmd = f"cd {rdir} && tar xzf bundle.tar.gz && bash install.sh"
    else:
        extract_cmd = (
            f"cd {shlex.quote(scp_rdir)} && tar xzf bundle.tar.gz && "
            f"bash install.sh"
        )
    _progress("Installing training environment (this may take a while)...")
    rc, out, err = await _ssh(remote, extract_cmd, progress, timeout=1800)
    if rc != 0:
        return _setup_fail(f"Install failed:\n{err[-500:]}", _progress)

    venv_python = f"{rdir}/.venv/bin/python"
    _progress(f"Setup complete! Remote python: {venv_python}")
    return True
