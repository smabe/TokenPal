"""Remote SSH orchestrator for LoRA fine-tuning.

Automates the full pipeline: build wheel bundle → push to GPU machine →
install deps → push base model → train → merge adapter →
pull merged safetensors → register with local Ollama.

Uses ssh/scp — no extra Python deps on the local machine.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tokenpal.config.schema import FinetuneConfig, RemoteTrainConfig
from tokenpal.tools.voice_profile import VoiceProfile, slugify

log = logging.getLogger(__name__)

ProgressCallback = Callable[[str], None]

# How often the training-completion poll loop wakes up to check tmux state.
# Module-level constant so tests can monkeypatch it to 0 for fast runs —
# a conftest fixture in tests/test_tools/ patches it automatically.
POLL_INTERVAL_SECONDS = 30

# UTF-8 BOM — PowerShell 5.1 reads .ps1 files as Windows-1252 without it.
_UTF8_BOM = b"\xef\xbb\xbf"


# ---------------------------------------------------------------------------
# Bundle building — auto-build wheel + install.sh into a tarball
# ---------------------------------------------------------------------------

_INSTALL_SH = r"""#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="${TOKENPAL_TRAINING_DIR:-$HOME/tokenpal-training}"
PYTHON="${PYTHON:-python3}"

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
elif command -v rocm-smi &>/dev/null || command -v rocminfo &>/dev/null || ls /opt/rocm*/bin/rocminfo &>/dev/null; then
    GPU_BACKEND="rocm"
    # Determine ROCm version for correct PyTorch index URL
    ROCM_VER=""
    if [[ -f /opt/rocm/.info/version ]]; then
        ROCM_VER=$(cat /opt/rocm/.info/version | grep -oP '^\d+\.\d+' || true)
    fi
    if [[ -z "$ROCM_VER" ]]; then
        # Check versioned install directories (e.g. /opt/rocm-7.2.0)
        ROCM_DIR=$(ls -d /opt/rocm-[0-9]* 2>/dev/null | sort -V | tail -1 || true)
        if [[ -n "$ROCM_DIR" ]]; then
            ROCM_VER=$(basename "$ROCM_DIR" | grep -oP '\d+\.\d+' || true)
        fi
    fi
    if [[ -z "$ROCM_VER" ]]; then
        # Try rocminfo (needs HSA_ENABLE_DXG_DETECTION=1 on WSL)
        ROCM_VER=$(HSA_ENABLE_DXG_DETECTION=1 rocminfo --version 2>/dev/null | grep -oP '\d+\.\d+' || true)
    fi
    ROCM_MAJOR=${ROCM_VER%%.*}
    if [[ -n "$ROCM_MAJOR" ]] && (( ROCM_MAJOR >= 7 )); then
        TORCH_URL="https://download.pytorch.org/whl/rocm7.2"
        echo "  ROCm $ROCM_VER detected, using rocm7.2 PyTorch index"
    else
        TORCH_URL="https://download.pytorch.org/whl/rocm6.2"
        echo "  ROCm ${ROCM_VER:-unknown} detected, using rocm6.2 PyTorch index"
    fi
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

# ROCm: export HSA env vars for GPU access and RDNA 4 compat
if [[ "$GPU_BACKEND" == "rocm" ]]; then
    export HSA_ENABLE_DXG_DETECTION=1
    GFX_ARCH=$(rocminfo 2>/dev/null \
        | grep -oP 'gfx\d+' | grep -v 'gfx0' | head -1 || true)
    if [[ "$GFX_ARCH" == gfx12* ]]; then
        export HSA_OVERRIDE_GFX_VERSION=11.0.0
        echo "  RDNA 4 ($GFX_ARCH) detected, setting HSA_OVERRIDE_GFX_VERSION=11.0.0"
    fi
fi

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
# Force-refresh tokenpal code (even if version unchanged), then resolve
# training extras without re-downloading PyTorch or other large deps.
"$VENV/bin/pip" install --force-reinstall --no-deps --no-cache-dir "${WHEEL}" -q
"$VENV/bin/pip" install "${WHEEL}[training]" -q

# --- Verify ---
"$VENV/bin/python" -c "
import torch
assert torch.cuda.is_available(), 'GPU not available to PyTorch'
print(f'  GPU OK: {torch.cuda.get_device_name(0)}')
"
echo "  Python: $VENV/bin/python"
echo "Install complete."
"""


# Native Windows installer — parallel to _INSTALL_SH for hosts where
# platform="windows" is configured (or auto-detected). Targets CUDA only
# (ROCm on Windows via HIP SDK is out of scope; see plans/shipped/remote-
# pipeline-windows.md non-goals). Uses the 'py' launcher from python.org,
# not Microsoft Store Python or Anaconda.
#
# Phases mirror _INSTALL_SH where possible:
#   1. PowerShell version + execution policy self-check
#   2. Python 3.12+ check via `py -3.12`
#   3. nvidia-smi GPU detection (Windows + CUDA only, no ROCm path)
#   4. venv create/reuse under %USERPROFILE%\tokenpal-training\.venv
#   5. PyTorch CUDA install (cu124 wheel, skip if already working)
#   6. tokenpal wheel install + training extras
#   7. Verification (torch.cuda.is_available())
_INSTALL_PS1 = r"""#Requires -Version 5.1
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
if ($env:TOKENPAL_TRAINING_DIR) {
    $InstallDir = $env:TOKENPAL_TRAINING_DIR
} else {
    $InstallDir = "$env:USERPROFILE\tokenpal-training"
}

Write-Host "Install dir: $InstallDir"

# --- Phase 1: Python launcher check ---
Write-Host "[1/6] Checking Python..."
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: Python launcher 'py' not found." -ForegroundColor Red
    Write-Host "  Install Python 3.12+ from python.org/downloads/" -ForegroundColor Yellow
    Write-Host "  (installer includes 'py' launcher by default)" -ForegroundColor Yellow
    exit 1
}
$pyVersion = py -3.12 --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Python 3.12 not installed via the py launcher." -ForegroundColor Red
    Write-Host "  Run: winget install Python.Python.3.12" -ForegroundColor Yellow
    Write-Host "  Or download from: https://www.python.org/downloads/" -ForegroundColor Yellow
    exit 1
}
Write-Host "  Found: $pyVersion"

# --- Phase 2: GPU detection (CUDA only on Windows) ---
Write-Host "[2/6] Detecting GPU..."
if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: nvidia-smi not found in PATH." -ForegroundColor Red
    Write-Host "  Install NVIDIA drivers from nvidia.com/download" -ForegroundColor Yellow
    Write-Host "  (CUDA only on Windows — ROCm/HIP out of scope)" -ForegroundColor Yellow
    exit 1
}
$gpuName = nvidia-smi --query-gpu=name --format=csv,noheader 2>&1 | Select-Object -First 1
Write-Host "  GPU: $gpuName"
# Detect CUDA version from nvidia-smi to pick the right PyTorch index.
# nvidia-smi reports the max CUDA version the driver supports (e.g. 13.2).
# Map to the closest PyTorch wheel index (cu126, cu128, cu130).
$nvOut = nvidia-smi 2>&1 | Out-String
if ($nvOut -match 'CUDA Version:\s+(\d+)\.(\d+)') {
    $major = [int]$Matches[1]; $minor = [int]$Matches[2]
    if ($major -ge 13) { $cuTag = "cu130" }
    elseif ($major -eq 12 -and $minor -ge 8) { $cuTag = "cu128" }
    elseif ($major -eq 12 -and $minor -ge 6) { $cuTag = "cu126" }
    else { $cuTag = "cu126" }  # oldest supported
} else {
    $cuTag = "cu126"  # safe fallback
}
$TorchUrl = "https://download.pytorch.org/whl/$cuTag"
Write-Host "  PyTorch index: $TorchUrl"

# --- Phase 3: Venv setup ---
Write-Host "[3/6] Setting up venv..."
$VenvDir = "$InstallDir\.venv"
$VenvPython = "$VenvDir\Scripts\python.exe"
$VenvPip = "$VenvDir\Scripts\pip.exe"

if (-not (Test-Path $InstallDir)) {
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
}

# Recreate venv only if Python version changed (saves 900MB torch re-download)
$desiredPy = py -3.12 --version 2>&1
$currentPy = "none"
if (Test-Path $VenvPython) {
    $currentPy = & $VenvPython --version 2>&1
}
if ($desiredPy -ne $currentPy) {
    if (Test-Path $VenvDir) {
        Write-Host "  Python changed ($currentPy -> $desiredPy), recreating venv..."
        Remove-Item -Recurse -Force $VenvDir
    }
    py -3.12 -m venv $VenvDir
}
& $VenvPip install --upgrade pip -q

# --- Phase 4: TokenPal wheel + training extras ---
Write-Host "[4/6] Installing tokenpal training bundle..."
$wheelPattern = "$InstallDir\tokenpal-*.whl"
$Wheel = Get-ChildItem $wheelPattern -ErrorAction SilentlyContinue |
    Select-Object -First 1
if (-not $Wheel) {
    Write-Host "ERROR: No tokenpal wheel found in $InstallDir" -ForegroundColor Red
    Write-Host "  Bundle push must complete before install.ps1 runs." -ForegroundColor Yellow
    exit 1
}
# Force-refresh tokenpal code, then resolve training extras.
# Training extras are installed BEFORE CUDA torch (Phase 5) because
# transitive deps (trl, accelerate, etc.) pull in CPU-only torch from
# PyPI regardless of --extra-index-url. Installing CUDA torch last
# with --force-reinstall guarantees it wins.
& $VenvPip install --force-reinstall --no-deps --no-cache-dir $Wheel.FullName -q
& $VenvPip install "$($Wheel.FullName)[training]" -q

# --- Phase 5: PyTorch (CUDA) ---
Write-Host "[5/6] Installing PyTorch (CUDA)..."
# Check if CUDA torch is already working (idempotent reinstall guard).
$torchOk = $false
try {
    $check = & $VenvPython -c "import torch; print(1 if torch.cuda.is_available() else 0)" 2>&1
    if ($LASTEXITCODE -eq 0 -and $check -eq "1") {
        $torchOk = $true
    }
} catch {
    $torchOk = $false
}

if ($torchOk) {
    Write-Host "  PyTorch CUDA already working, skipping."
} else {
    # On Windows, default `pip install torch` gives CPU-only. Must use
    # --index-url with the detected CUDA tag. Force-reinstall in case
    # a CPU-only transitive dep snuck in during Phase 4.
    & $VenvPip install --force-reinstall --index-url $TorchUrl torch torchvision -q
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: PyTorch CUDA install failed." -ForegroundColor Red
        Write-Host "  Common cause: Defender blocking DLLs." -ForegroundColor Yellow
        Write-Host "  Add exclusion for $VenvDir and retry." -ForegroundColor Yellow
        exit 1
    }
    # Torch may pull in triton, which ships broken Windows binaries
    # (AttrsDescriptor import error). Training uses eager attention and
    # never calls torch.compile, so triton is dead weight. Remove it.
    try { & $VenvPip uninstall triton -y -q 2>&1 | Out-Null } catch {}
}

# --- Phase 6: Verification ---
Write-Host "[6/6] Verifying..."
# Inline -c instead of a here-string — here-strings break when Python
# f-string braces ({torch.cuda...}) look like PS script blocks. Single
# line with semicolons is bulletproof across PS versions and encodings.
& $VenvPython -c "import torch; assert torch.cuda.is_available(), 'GPU not available to PyTorch'; print('  GPU OK: ' + torch.cuda.get_device_name(0))"
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: PyTorch CUDA verification failed." -ForegroundColor Red
    exit 1
}
Write-Host "  Python: $VenvPython"
Write-Host "Install complete."
"""


# Native Windows training runner — parameterized at launch time. Lives in
# the bundle alongside install.ps1 so we don't have to build+push it per run.
# Invoked by the Windows training launch path as:
#   powershell -ExecutionPolicy Bypass -File run_train.ps1
#     -VenvPy "C:\...\python.exe" -ModelDir "C:\...\model"
#     -CmdRdir "C:\..." [-Resume]
# Streams training output to both stdout (so the _ssh progress callback
# picks it up in real-time) AND train.log (for post-mortem inspection).
# Blocks until training completes — the Windows path has no tmux, so
# this script runs inside the SSH session and exits with the training
# exit code.
_TRAIN_PS1 = r"""#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$VenvPy,
    [Parameter(Mandatory)][string]$ModelDir,
    [Parameter(Mandatory)][string]$CmdRdir,
    [switch]$Resume
)

# Don't abort on Python warnings — we want the exit code to reflect
# training's own success/failure, not stderr noise.
$ErrorActionPreference = "Continue"
$env:HF_HUB_OFFLINE = "1"
# Disable torch.compile/inductor — triton doesn't ship usable Windows
# binaries and the import alone crashes with AttrsDescriptor errors.
# Training uses eager attention anyway, so inductor is never needed.
$env:TORCHDYNAMO_DISABLE = "1"

Set-Location $CmdRdir

$trainArgs = @(
    '-m', 'tokenpal.tools.finetune_voice', 'train',
    '--data', 'data/',
    '--output', 'output/',
    '--base-model', $ModelDir
)
if ($Resume) { $trainArgs += '--resume' }

# Tee-Object splits the pipe to both the file and stdout. $LASTEXITCODE
# after a pipeline captures the LAST command's exit (Tee-Object's), so
# we grab the training process exit via the $? shortcut + manual capture.
& $VenvPy @trainArgs 2>&1 | Tee-Object -FilePath 'train.log'
$exitCode = $LASTEXITCODE

Add-Content -Path 'train.log' -Value "EXIT_CODE=$exitCode"
exit $exitCode
"""


def _ps_quote(s: str) -> str:
    """Return a PowerShell single-quoted string literal for `s`.

    PowerShell single-quoted strings are literal (no variable expansion),
    but embedded single quotes must be doubled: `it's` → `'it''s'`.
    Use for any path or user-provided value embedded in a PowerShell
    command string, so paths with apostrophes don't break the command.
    """
    return "'" + s.replace("'", "''") + "'"


def _to_windows_path(rel_path: str) -> str:
    """Convert a Unix-style path to a Windows cmd.exe / PowerShell path.

    Examples:
        ~/tokenpal-training → %USERPROFILE%\\tokenpal-training
        ~ → %USERPROFILE%
        /c/Users/foo → \\c\\Users\\foo (slashes normalized)
        C:\\already\\win → C:\\already\\win (unchanged)

    Used at 3 sites where `remote_train.py` builds Windows paths from
    `remote.remote_dir` (which is Unix-style in the default config).
    """
    if rel_path == "~":
        return "%USERPROFILE%"
    if rel_path.startswith("~/"):
        rest = rel_path[2:].replace("/", "\\")
        return f"%USERPROFILE%\\{rest}"
    return rel_path.replace("/", "\\")


def _build_checkpoint_check_cmd(platform: str, cmd_rdir: str) -> str:
    """Return a shell command that prints the latest checkpoint directory
    name if one exists, or empty output otherwise.

    Linux: `ls -d ... | tail -1` (unchanged from original)
    Windows: PowerShell `Get-ChildItem ... | Select -Last 1 -Expand Name`
    """
    if platform == "windows":
        ckpt_dir = _ps_quote(f"{cmd_rdir}\\output\\adapter")
        return (
            f'powershell -Command "Get-ChildItem {ckpt_dir} -Directory '
            f"-ErrorAction SilentlyContinue "
            f'| Where-Object {{ $_.Name -like \'checkpoint-*\' }} '
            f'| Select-Object -Last 1 -ExpandProperty Name"'
        )
    return f"ls -d {cmd_rdir}/output/adapter/checkpoint-* 2>/dev/null | tail -1"


def _build_merge_cmd(
    platform: str, venv_py: str, cmd_rdir: str, raw_model_dir: str,
) -> str:
    """Return the command to run `tokenpal.tools.finetune_voice merge` on
    the remote. Differs per platform because env var syntax and invocation
    shell differ: bash uses `HF_HUB_OFFLINE=1 python`, PowerShell uses
    `$env:HF_HUB_OFFLINE = '1'; & python`.
    """
    if platform == "windows":
        return (
            f'powershell -ExecutionPolicy Bypass -Command '
            f'"$env:HF_HUB_OFFLINE = \'1\'; '
            f"Set-Location {_ps_quote(cmd_rdir)}; "
            f"& {_ps_quote(venv_py)} -m tokenpal.tools.finetune_voice merge "
            f"--adapter output/adapter --output output/merged "
            f'--base-model {_ps_quote(raw_model_dir)}"'
        )
    return (
        f"cd {cmd_rdir} && HF_HUB_OFFLINE=1 {venv_py} -m "
        f"tokenpal.tools.finetune_voice merge "
        f"--adapter output/adapter --output output/merged "
        f"--base-model {raw_model_dir}"
    )


def _build_remote_sha256_cmd(platform: str, cmd_rdir: str) -> str:
    """Return a command that emits a single hex digest representing the
    combined hash of all .safetensors files under the merged output dir.
    Matches what the local Python sha256 computation expects to compare against.

    Linux: `find ... -exec sha256sum {} + | sort | sha256sum | cut -d' ' -f1`
    Windows: PowerShell pipeline emitting the same shape via Get-FileHash.
    """
    if platform == "windows":
        # Compute per-file SHA-256 hashes, sort deterministically, then
        # hash the concatenated "HASH  name" lines to match the Linux
        # format produced by `sha256sum file | sort | sha256sum`.
        #
        # Avoids \" escaping inside powershell -Command "..." — cmd.exe
        # (the SSH default shell on Windows) misparses \" as a string
        # terminator instead of passing it through. Uses single-quoted
        # strings and string concatenation instead.
        merged_dir = _ps_quote(f"{cmd_rdir}\\output\\merged")
        return (
            f'powershell -Command "'
            f"$files = Get-ChildItem {merged_dir} "
            f"-Recurse -Filter '*.safetensors' -ErrorAction SilentlyContinue "
            f'| Sort-Object Name; '
            f'$combined = ($files | ForEach-Object '
            f"{{ $h = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower(); "
            f"($h + '  ' + $_.Name) }}) -join [string][char]10; "
            f"$bytes = [Text.Encoding]::UTF8.GetBytes($combined + [string][char]10); "
            f'$sha = [Security.Cryptography.SHA256]::Create(); '
            f'$hash = $sha.ComputeHash($bytes); '
            f"($hash | ForEach-Object {{ $_.ToString('x2') }}) -join ''\""
        )
    return (
        f"find {cmd_rdir}/output/merged -type f -name '*.safetensors' "
        f"-exec sha256sum {{}} + 2>/dev/null | sort | sha256sum | cut -d' ' -f1"
    )


def _build_cleanup_cmd(platform: str, cmd_rdir: str) -> str:
    """Return the command to clean up transient files after a successful run.
    Removes data/, output/, profile JSONs, the wheel, both install scripts,
    and the bundle tarball. Keeps the base model + venv + source hash.
    """
    if platform == "windows":
        # cmd.exe equivalents. `del /Q` is quiet, `rmdir /S /Q` is recursive.
        # 2>nul suppresses "file not found" noise.
        return (
            f'del /Q "{cmd_rdir}\\*.json" "{cmd_rdir}\\*.whl" '
            f'"{cmd_rdir}\\install.sh" "{cmd_rdir}\\install.ps1" '
            f'"{cmd_rdir}\\run_train.ps1" "{cmd_rdir}\\bundle.tar.gz" 2>nul & '
            f'rmdir /S /Q "{cmd_rdir}\\data" 2>nul & '
            f'rmdir /S /Q "{cmd_rdir}\\output" 2>nul'
        )
    return (
        f"rm -rf {cmd_rdir}/data {cmd_rdir}/output "
        f"{cmd_rdir}/*.json {cmd_rdir}/*.whl {cmd_rdir}/install.sh "
        f"{cmd_rdir}/bundle.tar.gz"
    )


def _build_windows_base_model_check(model_dir: str) -> str:
    """Build a PowerShell command that emits 'BASE_MODEL_OK' iff the model
    directory at `model_dir` has config.json with a model_type field AND at
    least one nonzero weight shard (.safetensors or .bin).

    Parallel to the bash `check_cmd` inside `_ensure_base_model` — caught
    interrupted HF downloads where config.json landed but weight shards didn't.
    Uses Windows-native PowerShell primitives (Test-Path, Select-String,
    Get-ChildItem) instead of POSIX `test -f`, `grep -q`, `find -size`.

    Returned string is a single semicolon-separated PowerShell one-liner so it
    survives SSH command transport without heredoc quoting.
    """
    # Wrapped in `powershell -Command "..."` because SSH on Windows sends
    # commands to cmd.exe, which can't parse raw PowerShell syntax.
    # Uses single-quoted strings for paths and patterns to avoid \" escaping
    # issues when cmd.exe passes the command to PowerShell.
    config_path = _ps_quote(f"{model_dir}\\config.json")
    model_dir_q = _ps_quote(model_dir)
    return (
        f'powershell -Command "'
        f"if ((Test-Path {config_path} -PathType Leaf)"
        f" -and (Select-String -Pattern 'model_type' -Path {config_path} -Quiet)"
        f" -and (Get-ChildItem {model_dir_q} -Include *.safetensors,*.bin -Recurse"
        f" -ErrorAction SilentlyContinue | Where-Object {{ $_.Length -gt 0 }}"
        f" | Select-Object -First 1)) {{ Write-Output 'BASE_MODEL_OK' }}\""
    )


async def _ensure_base_model_windows(
    remote: RemoteTrainConfig,
    base_model: str,
    model_dir: str,
    venv_py: str,
    _ssh: Any,
    progress: ProgressCallback,
) -> str:
    """Windows-native equivalent of `_ensure_base_model`.

    Differences from the Linux version:
      - Uses PowerShell `Test-Path` + `Select-String` + `Get-ChildItem`
        instead of POSIX `test -f` + `grep -q` + `find -size +0c`
      - HF_TOKEN from `$env:HF_TOKEN` (persistent via `setx` in user env)
        instead of `source ~/.bashrc`
      - Windows venv python path: `.venv\\Scripts\\python.exe` not
        `.venv/bin/python`
      - Local fallback (download locally, push to remote) is NOT implemented
        for the Windows path in commit 3 — a pure-remote download is the
        only supported flow. If the remote can't reach HF, the user must
        fix HF_TOKEN or network on the remote. The Linux fallback worked
        because macOS controllers could scp into a Linux host; pushing a
        multi-GB model to a Windows host over plain SCP is slow and
        brittle enough that we deliberately punt.

    Returns the remote model directory path on success.
    """
    # Check if model already exists and has intact config + weight shards
    check_cmd = _build_windows_base_model_check(model_dir)
    rc, out, _ = await _ssh(remote, check_cmd, timeout=15)
    if rc == 0 and "BASE_MODEL_OK" in out:
        progress("Base model already on remote (config + weights verified).")
        return model_dir

    progress(f"Downloading base model: {base_model}")

    # Remote download via venv python. Pull HF_TOKEN from the user env
    # (set via `setx HF_TOKEN ...` or a one-shot in the same PS session).
    # The $env expansion happens on the remote PowerShell.
    dl_cmd = (
        f"$env:HF_TOKEN = [System.Environment]::GetEnvironmentVariable('HF_TOKEN', 'User'); "
        f"& {_ps_quote(venv_py)} -c "
        f'"from huggingface_hub import snapshot_download; '
        f"snapshot_download('{base_model}', local_dir='{model_dir}')\""
    )
    rc, _, err = await _ssh(remote, dl_cmd, progress, timeout=1800)
    if rc == 0:
        progress("Base model downloaded on remote.")
        return model_dir

    # Detect auth failures and surface with actionable hint. No local
    # fallback on the Windows path — see docstring above.
    if _looks_like_hf_auth_error(err):
        target = _ssh_target(remote)
        raise RemoteTrainError(
            "auth",
            f"HuggingFace auth failed for {base_model}.",
            hint=(
                f"The remote couldn't access {base_model}.\n"
                f"Fix on the Windows remote:\n"
                f"  ssh {target}\n"
                f"  # Set the token persistently:\n"
                f"  setx HF_TOKEN hf_your_token_here\n"
                f"  # Then restart your SSH session (persistent env needs a new shell)\n"
                f"  # Also accept the license at: https://huggingface.co/{base_model}"
            ),
        )

    raise RemoteTrainError(
        "model_push",
        f"Remote download failed: {err[:200]}",
        hint=(
            "Native Windows path has no local fallback for base model push. "
            "Fix HF_TOKEN or network on the remote and retry."
        ),
    )


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

    Contains: tokenpal wheel, install.sh, install.ps1, optional profile JSON,
    source hash. Both install scripts are always bundled so a single bundle
    works on Linux/WSL (install.sh) or native Windows (install.ps1) remotes.
    Returns the path to the tarball (in a temp directory).
    """
    bundle_dir = Path(tempfile.mkdtemp()) / "tokenpal-training-bundle"
    bundle_dir.mkdir()

    # Build wheel
    project_root = Path(__file__).parent.parent.parent
    result = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(bundle_dir)],
        cwd=str(project_root),
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Wheel build failed:\n{result.stderr[-500:]}")

    # Write install.sh
    install_sh = bundle_dir / "install.sh"
    install_sh.write_text(_INSTALL_SH)
    install_sh.chmod(0o755)

    # Write install.ps1 (native Windows installer). Two encoding details:
    #   1. CRLF line endings — PowerShell expects them on Windows.
    #   2. UTF-8 BOM prefix — without the BOM, PowerShell 5.1 reads the file
    #      as Windows-1252. UTF-8 byte \x94 (part of the em dash U+2014)
    #      maps to a right double quote in 1252, prematurely closing any
    #      double-quoted string and cascading parser errors.
    install_ps1 = bundle_dir / "install.ps1"
    install_ps1.write_bytes(
        _UTF8_BOM + _INSTALL_PS1.replace("\n", "\r\n").encode("utf-8")
    )
    # No chmod on .ps1 — PowerShell execution policy controls this, not file perms

    # Write run_train.ps1 (native Windows training runner, parameterized).
    # Same CRLF + BOM requirements as install.ps1.
    run_train_ps1 = bundle_dir / "run_train.ps1"
    run_train_ps1.write_bytes(
        _UTF8_BOM + _TRAIN_PS1.replace("\n", "\r\n").encode("utf-8")
    )

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


async def _drain_stream(
    stream: asyncio.StreamReader,
    buf: list[str] | None = None,
    callback: ProgressCallback | None = None,
    line_filter: Callable[[str], bool] | None = None,
) -> None:
    """Read lines from an async stream, optionally buffering and/or calling back."""
    while True:
        raw = await stream.readline()
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace").rstrip()
        if buf is not None:
            buf.append(line)
        if callback and (line_filter is None or line_filter(line)):
            callback(line.strip())


async def _resolve_wsl_mount(remote: RemoteTrainConfig) -> str:
    """Resolve the Windows remote_dir as a WSL /mnt/ mount path.

    SCPs to a Windows SSH host land at %USERPROFILE%\\<remote_dir>.
    This resolves that to the WSL-accessible /mnt/c/Users/... path.
    """
    rc, win_home, _ = await _run_ssh(remote, "echo %USERPROFILE%", timeout=10)
    if rc != 0:
        raise RemoteTrainError("wsl_bridge", "Failed to resolve Windows home dir")
    win_home = win_home.strip().replace("\\", "/")
    if len(win_home) >= 2 and win_home[1] == ":":
        mount_path = f"/mnt/{win_home[0].lower()}{win_home[2:]}"
    else:
        mount_path = win_home
    rel = remote.remote_dir.lstrip("~/")
    return f"{mount_path}/{rel}" if rel else mount_path


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
    ssh_args = ["ssh", "-o", "BatchMode=yes", "-p", str(remote.port), target, command]
    proc = await asyncio.create_subprocess_exec(
        *ssh_args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    assert proc.stdout is not None
    assert proc.stderr is not None

    try:
        await asyncio.wait_for(
            asyncio.gather(
                _drain_stream(proc.stdout, stdout_lines, progress),
                _drain_stream(proc.stderr, stderr_lines),
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
    recursive: bool = False,
    timeout: float = 1800,
) -> tuple[int, str]:
    """Copy files via SCP. Returns (returncode, stderr)."""
    target = _ssh_target(remote)
    scp_args = ["scp", "-P", str(remote.port)]
    if recursive:
        scp_args.append("-r")
    if pull:
        scp_args.extend([f"{target}:{remote_path}", local_path])
    else:
        scp_args.extend([local_path, f"{target}:{remote_path}"])
    proc = await asyncio.create_subprocess_exec(
        *scp_args,
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


async def _run_rsync(
    remote: RemoteTrainConfig,
    local_path: str,
    remote_path: str,
    *,
    pull: bool = False,
    progress: ProgressCallback | None = None,
    timeout: float = 3600,
) -> tuple[int, str]:
    """Copy files/dirs via rsync with --progress and --partial (resume support).

    Streams progress lines to the callback. Returns (returncode, stderr).
    No compression (-z omitted) — safetensors are dense binary, incompressible.
    """
    target = _ssh_target(remote)
    ssh_cmd = f"ssh -o BatchMode=yes -p {remote.port}"
    # Trailing slash on source = copy contents, not the directory itself
    if pull:
        args = [
            "rsync", "-a", "--partial", "--progress",
            "-e", ssh_cmd,
            f"{target}:{remote_path}/",
            f"{local_path}/",
        ]
    else:
        args = [
            "rsync", "-a", "--partial", "--progress",
            "-e", ssh_cmd,
            f"{local_path}/",
            f"{target}:{remote_path}/",
        ]

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stderr_lines: list[str] = []

    async def _read_rsync_progress(stream: asyncio.StreamReader) -> None:
        """Read rsync output in chunks — --progress uses \\r for updates."""
        buf = b""
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            buf += chunk
            # Split on both \r and \n to catch intra-line progress updates
            while b"\r" in buf or b"\n" in buf:
                idx_r = buf.find(b"\r")
                idx_n = buf.find(b"\n")
                if idx_r == -1:
                    idx = idx_n
                elif idx_n == -1:
                    idx = idx_r
                else:
                    idx = min(idx_r, idx_n)
                line = buf[:idx].decode("utf-8", errors="replace").strip()
                buf = buf[idx + 1:]
                if progress and line and "%" in line:
                    progress(line)

    assert proc.stdout is not None
    assert proc.stderr is not None

    try:
        await asyncio.wait_for(
            asyncio.gather(
                _read_rsync_progress(proc.stdout),
                _drain_stream(proc.stderr, stderr_lines),
            ),
            timeout=timeout,
        )
    except TimeoutError:
        proc.kill()
        return -1, "rsync timed out"

    await proc.wait()
    return (
        proc.returncode or 0,
        "\n".join(stderr_lines),
    )


def _looks_like_hf_auth_error(text: str) -> bool:
    """Heuristic: does this error text look like an HF auth/gate failure?

    HuggingFace returns 401/403 HTTP errors with varying prose depending on
    the specific failure (missing token, expired token, ungated model not
    yet accepted). This catches the common cases without coupling to a
    specific HF exception class (which may not be importable from here).
    """
    if not text:
        return False
    lower = text.lower()
    markers = (
        "401", "403",
        "unauthorized", "forbidden",
        "gated", "gatedrepoerror",
        "invalid credentials",
        "token is not valid", "invalid token",
        "repository not found",  # private repo without token
        "access to model",  # "access to model X is restricted"
        "must be authenticated",
    )
    return any(m in lower for m in markers)


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
    # Check if model already exists AND has a valid config.json AND at least
    # one nonzero weight shard. Previously only checked config.json for
    # "model_type", which let interrupted HF downloads slip through — HF writes
    # config.json first, then streams weight shards. If the download died after
    # config but before weights, the old check passed but training OOM'd trying
    # to load missing weights. `find -size +0c` rejects empty/truncated shards.
    check_cmd = (
        f"test -f {model_dir}/config.json"
        f" && grep -q '\"model_type\"' {model_dir}/config.json"
        rf" && find {model_dir} -maxdepth 1 \( -name '*.safetensors' -o -name '*.bin' \)"
        f" -size +0c 2>/dev/null | grep -q ."
        f" && echo BASE_MODEL_OK"
    )
    rc, out, _ = await _ssh(remote, check_cmd, timeout=15)
    if rc == 0 and "BASE_MODEL_OK" in out:
        progress("Base model already on remote (config + weights verified).")
        return model_dir

    progress(f"Downloading base model: {base_model}")

    # Try downloading directly on the remote first (use venv python).
    # Source .bashrc for HF_TOKEN (may not be in the login shell env).
    dl_cmd = (
        f"source ~/.bashrc 2>/dev/null; "
        f'{venv_py} -c "'
        f"from huggingface_hub import snapshot_download; "
        f"snapshot_download('{base_model}', local_dir='{model_dir}')\""
    )
    rc, _, err = await _ssh(remote, dl_cmd, progress, timeout=1800)
    if rc == 0:
        progress("Base model downloaded on remote.")
        return model_dir

    # Detect auth failures before falling back — a gated/401/403 model will
    # fail the same way locally, so falling through just wastes time and
    # produces a confusing cascade of opaque HTTP errors at the caller.
    if _looks_like_hf_auth_error(err):
        target = _ssh_target(remote)
        raise RemoteTrainError(
            "auth",
            f"HuggingFace auth failed for {base_model}.",
            hint=(
                f"The remote couldn't access {base_model} — likely an expired "
                f"or missing HF_TOKEN, or you haven't accepted the model's "
                f"license on huggingface.co.\n"
                f"Fix on the remote:\n"
                f"  ssh {target}\n"
                f"  # Set or update the token in ~/.bashrc:\n"
                f"  echo 'export HF_TOKEN=hf_your_token_here' >> ~/.bashrc\n"
                f"  # Also accept the license at: https://huggingface.co/{base_model}"
            ),
        )

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
    try:
        snapshot_download(base_model, local_dir=str(local_model_dir))
    except Exception as exc:
        # Catch HF-specific auth errors (GatedRepoError, HTTP 401/403) and
        # surface them with an actionable hint. Other snapshot_download
        # failures (network, disk full) propagate as-is.
        if _looks_like_hf_auth_error(str(exc)) or type(exc).__name__ in (
            "GatedRepoError", "RepositoryNotFoundError", "HfHubHTTPError",
        ):
            raise RemoteTrainError(
                "auth",
                f"HuggingFace auth failed for {base_model} (local fallback).",
                hint=(
                    f"Neither the remote nor your local machine could access "
                    f"{base_model}.\n"
                    f"Most likely: expired/missing HF_TOKEN, or you need to "
                    f"accept the model license at "
                    f"https://huggingface.co/{base_model}\n"
                    f"Local fix:\n"
                    f"  export HF_TOKEN=hf_your_token_here"
                ),
            ) from exc
        raise

    progress("Pushing base model to remote (this may take a while)...")
    scp_rdir = remote.remote_dir
    if remote.use_wsl:
        rc, err = await _run_scp(
            remote, str(local_model_dir), f"{scp_rdir}/model",
            recursive=True, timeout=3600,
        )
    else:
        rc, err = await _run_rsync(
            remote, str(local_model_dir), f"{scp_rdir}/model",
            progress=progress, timeout=3600,
        )
    if rc != 0:
        raise RemoteTrainError("model_push", f"SCP failed: {err[:200]}")

    # For WSL: SCP lands on Windows filesystem, copy to WSL-native path
    if remote.use_wsl:
        wm = await _resolve_wsl_mount(remote)
        cp_cmd = f'cp -r "{wm}/model" {model_dir}'
        rc, _, err = await _ssh(remote, cp_cmd, timeout=300)
        if rc != 0:
            raise RemoteTrainError(
                "model_push",
                f"Failed to copy model to WSL: {err[:200]}",
            )

    # Clean up local temp
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


@dataclass
class RemoteState:
    """Snapshot of remote-side state before a training run begins.

    Gathered in a single SSH round-trip by `_preflight_remote_state`.
    `remote_finetune` uses this to decide: proceed, raise, or auto-recover.
    """

    lock_file_exists: bool      # /tmp/tokenpal-training.lock exists on disk?
    lock_held: bool             # something is actively holding the flock?
    tmux_session_alive: bool    # tokenpal-<slug> tmux session exists?
    venv_functional: bool       # .venv/bin/python -c "import torch" succeeds?


async def _detect_remote_platform(
    _ssh: Any,
    remote: RemoteTrainConfig,
) -> str:
    """Probe the remote to determine whether it's Linux or native Windows.

    Used when RemoteTrainConfig.platform == "auto" — runs a single SSH
    command that works on both platforms:
        uname -s 2>/dev/null || ver

    `uname -s` exists on Linux/WSL/macOS and returns "Linux" / "Darwin".
    On Windows cmd/PowerShell it fails, and `ver` takes over, returning
    "Microsoft Windows [Version X.Y.Z]".

    Returns "linux" or "windows". Falls back to "linux" on unrecognized
    output — safer default because all pre-commit-1 code assumed Linux.

    Note: when use_wsl=true, the command is wrapped in a WSL bash call
    and `uname -s` returns "Linux" from inside WSL, so this correctly
    returns "linux" even on a Windows host with use_wsl=true.
    """
    _rc, out, _err = await _ssh(
        remote, "uname -s 2>/dev/null || ver", timeout=10,
    )
    lower = out.lower()
    if "microsoft windows" in lower or lower.strip().startswith("windows"):
        return "windows"
    # Everything else (Linux, Darwin, unknown) routes to the Linux path
    return "linux"


async def _preflight_remote_state(
    _ssh: Any,
    remote: RemoteTrainConfig,
    slug: str,
    cmd_rdir: str,
    platform: str,
) -> RemoteState:
    """Check remote side for stale state that would break a fresh training run.

    One SSH round-trip gathers:
      - lock file presence and held-status (flock advisory lock probe)
      - tmux session liveness for tokenpal-<slug>
      - venv integrity (can we `import torch`?)

    Output parsed from key=value lines. Unknown keys default to safe values
    (lock_held=False, tmux_alive=False, venv_functional=False) — a mocked or
    empty response in tests falls through to "fresh install needed."

    On Windows: no flock or tmux (non-goals), only the venv check matters.
    Lock/tmux fields are always False on Windows.
    """
    if platform == "windows":
        # Windows has no flock or tmux. Only check venv integrity.
        # The venv python is at .venv\Scripts\python.exe on Windows.
        venv_py = f"{cmd_rdir}\\.venv\\Scripts\\python.exe"
        probe = (
            f'echo lock_file=0 & echo lock=free & echo tmux=dead & '
            f'"{venv_py}" -c "import torch" >nul 2>&1 '
            f'&& echo venv=ok || echo venv=broken'
        )
    else:
        probe = (
            "if [ -f /tmp/tokenpal-training.lock ]; then "
            "  echo lock_file=1; "
            "  if flock -n /tmp/tokenpal-training.lock -c true 2>/dev/null; then "
            "    echo lock=free; "
            "  else "
            "    echo lock=held; "
            "  fi; "
            "else "
            "  echo lock_file=0; "
            "  echo lock=free; "
            "fi; "
            f"tmux has-session -t tokenpal-{slug} 2>/dev/null"
            " && echo tmux=alive || echo tmux=dead; "
            f"{cmd_rdir}/.venv/bin/python -c 'import torch' 2>/dev/null"
            " && echo venv=ok || echo venv=broken"
        )
    _rc, out, _err = await _ssh(remote, probe, timeout=15)

    fields: dict[str, str] = {}
    for line in out.splitlines():
        line = line.strip()
        if "=" in line:
            key, _, value = line.partition("=")
            fields[key] = value

    return RemoteState(
        lock_file_exists=fields.get("lock_file") == "1",
        lock_held=fields.get("lock") == "held",
        tmux_session_alive=fields.get("tmux") == "alive",
        venv_functional=fields.get("venv") == "ok",
    )


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

    # Invalid combo: native Windows path cannot run through WSL bash.
    # Users migrating from the WSL pipeline must explicitly set use_wsl=false.
    if remote.platform == "windows" and remote.use_wsl:
        raise RemoteTrainError(
            "config",
            "platform='windows' is incompatible with use_wsl=true — "
            "native Windows means cmd.exe + PowerShell, not WSL bash.",
            hint=(
                "Set use_wsl=false in [finetune.remote]. The existing WSL "
                "pipeline stays available by leaving platform='auto' or "
                "'linux' and keeping use_wsl=true."
            ),
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

    # -- Resolve remote platform FIRST (before any platform-specific SSH) --
    # Explicit config wins; "auto" triggers an SSH probe that works on both
    # cmd.exe and bash (`uname -s 2>/dev/null || ver`). Must happen before
    # the GPU check since the GPU probe is platform-specific.
    if remote.platform == "auto":
        platform = await _detect_remote_platform(_ssh, remote)
        _progress(f"Detected remote platform: {platform}")
    else:
        platform = remote.platform
        _progress(f"Using configured platform: {platform}")

    # Reassign path shapes for Windows. cmd.exe expects backslash separators
    # and resolved absolute paths — %USERPROFILE% only expands in a shell,
    # and SCP doesn't go through a shell. So we resolve %USERPROFILE% once
    # via an SSH call, then use the absolute path everywhere (SSH + SCP).
    if platform == "windows":
        _rc, win_home_out, _err = await _ssh(
            remote, "echo %USERPROFILE%", timeout=10,
        )
        win_home = win_home_out.strip()
        if not win_home or win_home == "%USERPROFILE%":
            raise RemoteTrainError(
                "config",
                "Could not resolve %USERPROFILE% on Windows remote. "
                "Is the SSH shell cmd.exe?",
            )
        # Rewrite remote_dir using the absolute home path (SCP-compatible).
        if scp_rdir.startswith("~/"):
            rel = scp_rdir[2:].replace("/", "\\")
            scp_rdir = f"{win_home}\\{rel}"
        elif scp_rdir == "~":
            scp_rdir = win_home
        else:
            scp_rdir = scp_rdir.replace("/", "\\")
        cmd_rdir = scp_rdir  # same path for both SSH and SCP on Windows
        model_dir = f"{cmd_rdir}\\model"
        venv_py = f"{cmd_rdir}\\.venv\\Scripts\\python.exe"

    # -- Preflight: verify GPU access --
    _progress("Connecting to remote GPU...")
    if platform == "windows":
        # cmd.exe doesn't grok bash redirects / grep / HSA env vars. Windows
        # is CUDA-only per the plan non-goals — just invoke nvidia-smi and
        # check the exit code.
        gpu_probe = "nvidia-smi"
    else:
        # Bash probe: nvidia-smi first, fall through to rocminfo for AMD.
        gpu_probe = (
            "nvidia-smi 2>/dev/null || HSA_ENABLE_DXG_DETECTION=1 "
            "rocminfo 2>/dev/null | grep -q 'Device Type:.*GPU'"
        )
    rc, _out, err = await _ssh(remote, gpu_probe, timeout=30)
    if rc != 0:
        raise RemoteTrainError(
            "preflight",
            f"Can't reach {remote.host} or no GPU. {err[:200]}",
        )
    _progress("GPU verified.")

    # -- Preflight: check disk space --
    if platform == "windows":
        # PowerShell `Get-PSDrive` on the drive letter containing cmd_rdir.
        # Output is integer GB free; 0 on parse failure (check silently skipped).
        disk_cmd = (
            f'powershell -Command "'
            f'$d = (Get-Item {_ps_quote(cmd_rdir)}).PSDrive; '
            f'[int]($d.Free / 1GB)"'
        )
    else:
        disk_cmd = f"df -BG {cmd_rdir} 2>/dev/null | tail -1 | awk '{{print $4}}'"
    rc, df_out, _ = await _ssh(remote, disk_cmd, timeout=10)
    if rc == 0 and df_out.strip():
        try:
            free_gb = int(df_out.strip().rstrip("G"))
            if free_gb < 25:
                _progress(f"WARNING: Only {free_gb}GB free on remote. Training needs ~25GB.")
        except ValueError:
            pass  # Can't parse, skip check

    # -- Preflight: check remote state for stale locks, running training, broken venv --
    state = await _preflight_remote_state(_ssh, remote, slug, cmd_rdir, platform)

    if state.lock_held and state.tmux_session_alive:
        # Live training already running for this slug. Attach-and-stream is
        # deferred to remote-train-phase-extraction.md — for now, surface a
        # clear error with a tmux attach hint so the user can watch it manually.
        target = _ssh_target(remote)
        raise RemoteTrainError(
            "preflight",
            f"Training is already running for {profile.character} on {remote.host}.",
            hint=(
                f"A tmux session 'tokenpal-{slug}' is active and holding the training lock.\n"
                f"To watch its progress:\n"
                f"  ssh {target} 'tmux attach -t tokenpal-{slug}'\n"
                f"Detach with Ctrl-b d. Wait for it to finish before re-running finetune."
            ),
        )

    if state.lock_held and not state.tmux_session_alive:
        # Lock is held but no training session exists — the previous holder is
        # a zombie/hang/external process. Removing the file gives the next flock
        # call a fresh inode; the kernel lock on the old inode is orphaned.
        _progress("WARNING: stale training lock detected (no active tmux session). Auto-removing.")
        log.warning(
            "Removing stale flock file /tmp/tokenpal-training.lock (no matching tmux session)"
        )
        await _ssh(remote, "rm -f /tmp/tokenpal-training.lock", timeout=5)

    if state.tmux_session_alive and not state.lock_held:
        # Orphan tmux session without training lock — probably a stale session
        # from a crashed previous run (shouldn't happen with tokenpal's own code,
        # but defensive cleanup avoids the 'tmux kill-session 2>/dev/null' silent-swallow
        # later when new-session runs).
        _progress(f"Cleaning up orphan tmux session tokenpal-{slug} from previous run.")
        log.info("Killing orphan tmux session tokenpal-%s (no lock held)", slug)
        await _ssh(remote, f"tmux kill-session -t tokenpal-{slug}", timeout=5)

    # -- Create remote working directory --
    if platform == "windows":
        # Native Windows via cmd.exe (OpenSSH's default shell on Windows).
        # cmd handles %USERPROFILE% expansion; no WSL bridging needed.
        mkdir_cmd = f'if not exist "{scp_rdir}" mkdir "{scp_rdir}"'
    elif remote.use_wsl:
        win_path = _to_windows_path(remote.remote_dir)
        mkdir_cmd = f'if not exist "{win_path}" mkdir "{win_path}"'
    else:
        mkdir_cmd = f"mkdir -p {shlex.quote(scp_rdir)}"
    rc, _, err = await _run_ssh(remote, mkdir_cmd)
    if rc != 0:
        raise RemoteTrainError("mkdir", err[:200])

    # -- Build + push bundle if source code changed or install incomplete --
    local_hash = _hash_training_sources()
    if platform == "windows":
        hash_cmd = f'type "{cmd_rdir}\\.source-hash" 2>nul || echo none'
    else:
        hash_cmd = f"cat {cmd_rdir}/.source-hash 2>/dev/null || echo none"
    rc, remote_hash_out, _ = await _ssh(remote, hash_cmd, timeout=10)
    remote_hash = remote_hash_out.strip()

    # Venv integrity check from preflight — stricter than the old `test -f .install-ok`
    # because it actually verifies torch is importable. Catches partial pip installs
    # where the sentinel was touched but the venv is broken (WSL SSL flake, pip bomb).
    if not state.venv_functional:
        remote_hash = "incomplete"  # force re-push + reinstall

    # Write profile JSON to a temp file for inclusion in bundle
    profile_data = asdict(profile)
    profile_data["line_count"] = profile.line_count
    profile_json = Path(tempfile.mkdtemp()) / f"{slug}.json"
    profile_json.write_text(
        json.dumps(profile_data, ensure_ascii=False, indent=2), encoding="utf-8",
    )

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
        if platform == "windows":
            # Windows 10+ ships bsdtar; extract then invoke install.ps1.
            # PowerShell is spawned from cmd.exe with -ExecutionPolicy Bypass
            # to sidestep the default Restricted policy without persistent changes.
            extract_cmd = (
                f'cd /d "{cmd_rdir}" && tar xzf bundle.tar.gz && '
                f'powershell -ExecutionPolicy Bypass -File install.ps1'
            )
        elif remote.use_wsl:
            # SCP landed on Windows filesystem. install.sh will self-relocate
            # from /mnt/c/... to ~/tokenpal-training/ automatically.
            win_mount = await _resolve_wsl_mount(remote)
            extract_cmd = (
                f'cd "{win_mount}" && tar xzf bundle.tar.gz && bash install.sh'
            )
        else:
            extract_cmd = (
                f"cd {cmd_rdir} && tar xzf bundle.tar.gz && "
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
            wm = await _resolve_wsl_mount(remote)
            await _ssh(
                remote,
                f'cp "{wm}/{q_slug}.json" {cmd_rdir}/',
                timeout=15,
            )

    # Clean up local temp files
    profile_json.unlink(missing_ok=True)

    # -- Ensure base model is available on remote --
    _progress("Checking base model on remote...")
    if platform == "windows":
        await _ensure_base_model_windows(
            remote, config.base_model, model_dir, venv_py, _ssh, _progress,
        )
    else:
        await _ensure_base_model(
            remote, config.base_model, model_dir, venv_py, _ssh, _progress,
        )

    # -- Prepare training data (using installed entry point) --
    _progress(f"Preparing training data ({profile.line_count} lines)...")
    if platform == "windows":
        prep_cmd = (
            f'cd /d "{cmd_rdir}" && "{venv_py}" -m tokenpal.tools.finetune_voice prep '
            f"{q_slug}.json -o data"
        )
    else:
        prep_cmd = (
            f"cd {cmd_rdir} && {venv_py} -m tokenpal.tools.finetune_voice prep "
            f"{q_slug}.json -o data"
        )
    rc, out, err = await _ssh(remote, prep_cmd, progress)
    if rc != 0:
        raise RemoteTrainError("prep", f"Dataset prep failed:\n{err[-500:]}")

    # -- LoRA training --
    _progress("Starting LoRA training...")
    raw_model_dir = model_dir  # not quoted — may contain $HOME for shell expansion
    tmux_session = f"tokenpal-{slug}"
    train_cmd = (
        f"cd {cmd_rdir} && HF_HUB_OFFLINE=1 {venv_py} -m "
        f"tokenpal.tools.finetune_voice train "
        f"--data data/ --output output/ "
        f"--base-model {raw_model_dir}"
    )

    # Check for existing checkpoints (resume support)
    ckpt_check_cmd = _build_checkpoint_check_cmd(platform, cmd_rdir)
    rc, ckpt_out, _ = await _ssh(remote, ckpt_check_cmd, timeout=10)
    has_checkpoint = rc == 0 and bool(ckpt_out.strip())
    if has_checkpoint:
        ckpt_name = ckpt_out.strip().split("/")[-1]
        _progress(f"Resuming from checkpoint: {ckpt_name}")
        train_cmd += " --resume"

    # Acquire lock to prevent concurrent training (Linux/WSL only — Windows
    # has no flock equivalent and concurrent-training detection is
    # intentionally skipped there. Worst case on Windows: two concurrent
    # trainings stomp each other, but that requires deliberate user action.)
    if platform != "windows":
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
    else:
        _progress("Note: concurrent-training detection skipped (Windows).")

    if platform == "windows":
        # Native Windows: synchronous SSH call into the pre-bundled
        # run_train.ps1 script. No tmux, no flock, no base64 encoding —
        # just parameterize the script and let PowerShell run it.
        #
        # SSH session MUST stay open for the training duration (~10 min);
        # SSH-survivable training is a documented non-goal for this MVP.
        # Progress streams via the _ssh callback in real time (Tee-Object
        # in the .ps1 pipes to both stdout and train.log).
        resume_arg = " -Resume" if has_checkpoint else ""
        windows_train_cmd = (
            f'powershell -ExecutionPolicy Bypass -File '
            f'"{cmd_rdir}\\run_train.ps1" '
            f'-VenvPy "{venv_py}" '
            f'-ModelDir "{raw_model_dir}" '
            f'-CmdRdir "{cmd_rdir}"'
            f'{resume_arg}'
        )
        _progress(
            "Training in progress (~10 min). Keep this SSH session open — "
            "the Windows path has no tmux to survive disconnects."
        )
        rc, _out, err = await _ssh(remote, windows_train_cmd, progress, timeout=3600)
        if rc != 0:
            target = _ssh_target(remote)
            raise RemoteTrainError(
                "train",
                f"Training failed (exit {rc}): {err[-500:] if err else ''}",
                hint=(
                    f"SSH session dropped? Training on Windows doesn't survive "
                    f"SSH disconnects (non-goal in the MVP).\n"
                    f"To retry:\n"
                    f"  ssh -p {remote.port} {target}  # keep this open\n"
                    f"  /voice finetune {profile.character}"
                ),
            )
    else:
        # Linux/WSL path: write a bash script, launch in tmux under flock,
        # poll for completion. Unchanged from the pre-Windows implementation.
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

        # ROCm/WSL needs these env vars for GPU access and RDNA 4 compat
        if remote.gpu_backend != "cuda":
            rc, gfx_out, _ = await _ssh(
                remote,
                "HSA_ENABLE_DXG_DETECTION=1 rocminfo 2>/dev/null"
                " | grep -oP 'gfx\\d+' | grep -v gfx0 | head -1",
                timeout=15,
            )
            gfx_arch = gfx_out.strip() if rc == 0 else ""
            if gfx_arch:
                env_lines = "export HSA_ENABLE_DXG_DETECTION=1\n"
                if gfx_arch.startswith("gfx12"):
                    env_lines += "export HSA_OVERRIDE_GFX_VERSION=11.0.0\n"
                script_content = script_content.replace(
                    "#!/bin/bash\n", f"#!/bin/bash\n{env_lines}"
                )

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
        while True:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

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

    # Check training result (common to both platforms — read from train.log)
    tail_cmd = (
        f'powershell -Command "Get-Content \'{cmd_rdir}\\train.log\' -Tail 5"'
        if platform == "windows"
        else f"tail -5 {cmd_rdir}/train.log 2>/dev/null"
    )
    rc, log_end, _ = await _ssh(remote, tail_cmd, timeout=10)
    log_text = log_end.strip()
    target = _ssh_target(remote)
    if platform == "windows":
        debug_hint = (
            f"To debug:\n"
            f"  ssh -p {remote.port} {target}\n"
            f"  cd /d \"{cmd_rdir}\"\n"
            f"  .venv\\Scripts\\activate\n"
            f"  type train.log\n"
            f"\nTo retry:  /voice finetune {profile.character}"
        )
    else:
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
            remote, _build_checkpoint_check_cmd(platform, cmd_rdir), timeout=10,
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
    merge_cmd = _build_merge_cmd(platform, venv_py, cmd_rdir, raw_model_dir)
    rc, out, err = await _ssh(remote, merge_cmd, progress, timeout=3600)
    if rc != 0:
        raise RemoteTrainError(
            "merge",
            f"Merge failed:\n{err[-500:]}",
            hint=debug_hint,
        )

    # Compute remote checksum for integrity verification
    rc, remote_hash_str, _ = await _ssh(
        remote, _build_remote_sha256_cmd(platform, cmd_rdir), timeout=60,
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
        win_mount = await _resolve_wsl_mount(remote)
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

    if remote.use_wsl or platform == "windows":
        # Windows (native or WSL-via-SSH) has no rsync — use SCP.
        # Forward-slash the remote path: backslash `C:\Users\...` makes
        # SCP interpret the `:` after the drive letter as a second host
        # delimiter. Windows OpenSSH accepts forward slashes.
        #
        # SCP -r copies the remote dir AS a subdirectory of the local
        # target (scp -r host:merged/ parent/ → parent/merged/). Pull
        # into the parent, then atomically rename on success — keeps any
        # existing local_model_dir intact until the transfer completes.
        scp_pull = pull_source.replace("\\", "/")
        rc, err = await _run_scp(
            remote, str(local_model_dir.parent), scp_pull,
            pull=True, recursive=True, timeout=3600,
        )
        if rc == 0:
            scp_landed = local_model_dir.parent / Path(pull_source).name
            if scp_landed.exists() and scp_landed != local_model_dir:
                if local_model_dir.exists():
                    shutil.rmtree(local_model_dir)
                scp_landed.rename(local_model_dir)
    else:
        rc, err = await _run_rsync(
            remote, str(local_model_dir), pull_source,
            pull=True, progress=progress, timeout=3600,
        )
    if rc != 0:
        raise RemoteTrainError(
            "pull",
            f"Failed to download merged model: {err[:200]}",
            hint=(
                f"rsync/scp transfer failed. To retry from a clean slate:\n"
                f"  rm -rf {local_model_dir}\n"
                f"  /voice finetune {profile.character}"
            ),
        )

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
            # Was a warning that let corrupted local models through to Ollama
            # registration. Escalated to hard error: a checksum mismatch after
            # a nominally-successful pull means the transfer left bad bytes on
            # disk (interrupted rsync that didn't retransmit partial shards,
            # truncated SCP, disk full on the local side). Registering a
            # corrupt model would silently serve garbage — fail loud instead.
            raise RemoteTrainError(
                "pull",
                (
                    f"Model checksum mismatch after download — "
                    f"local file is corrupted.\n"
                    f"  Remote: {remote_model_hash[:12]}\n"
                    f"  Local:  {local_model_hash[:12]}"
                ),
                hint=(
                    f"The transfer completed but the local hash doesn't match "
                    f"the remote hash — likely an interrupted rsync that didn't "
                    f"retransmit partial shards.\n"
                    f"Force a clean retry:\n"
                    f"  rm -rf {local_model_dir}\n"
                    f"  /voice finetune {profile.character}"
                ),
            )

    # -- Register with Ollama (FROM safetensors dir) --
    _progress("Registering with Ollama...")
    from tokenpal.tools.dataset_prep import build_system_prompt
    from tokenpal.tools.finetune_voice import register_ollama

    model_name = f"tokenpal-{slug}"
    system_prompt = build_system_prompt(profile)
    if not register_ollama(local_model_dir, model_name, system_prompt):
        # The safetensors are sitting safely at local_model_dir — don't let
        # the user think their training effort was lost. Give them the path
        # and the manual re-register command so they can recover without
        # rerunning the full pipeline.
        raise RemoteTrainError(
            "register",
            "Ollama registration failed. Is ollama running?",
            hint=(
                f"Your merged model is saved at:\n"
                f"  {local_model_dir}\n"
                f"Training did NOT need to be redone. Once ollama is running, "
                f"you can register it manually without re-running finetune:\n"
                f"  ollama create {model_name} -f <(echo 'FROM {local_model_dir}')\n"
                f"Or retry just the registration step:\n"
                f"  ollama serve  # in another terminal\n"
                f"  /voice finetune {profile.character}  # will skip to register"
            ),
        )

    # -- Cleanup remote artifacts (keep base model + venv + source hash) --
    _progress("Cleaning up remote files...")
    await _ssh(remote, _build_cleanup_cmd(platform, cmd_rdir), timeout=30)
    if remote.use_wsl:
        # WSL leaves a second copy on the Windows filesystem bridge — clean
        # that too. Native Windows path doesn't have this second copy.
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
        win_path = _to_windows_path(scp_rdir)
        mkdir_cmd = f'if not exist "{win_path}" mkdir "{win_path}"'
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
        # SCP landed on Windows filesystem. install.sh will self-relocate.
        try:
            win_mount = await _resolve_wsl_mount(remote)
        except RemoteTrainError:
            return _setup_fail("Failed to resolve Windows home dir", _progress)
        extract_cmd = (
            f'cd "{win_mount}" && tar xzf bundle.tar.gz && bash install.sh'
        )
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
