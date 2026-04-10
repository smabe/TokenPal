"""Remote SSH orchestrator for LoRA fine-tuning.

Automates the full pipeline: push voice profile to a GPU machine,
run training remotely, pull GGUF back, register with local Ollama.
Uses ssh/scp — no extra Python deps on the local machine.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
from collections.abc import Callable
from pathlib import Path

from tokenpal.config.schema import FinetuneConfig, RemoteTrainConfig
from tokenpal.tools.voice_profile import VoiceProfile, slugify

log = logging.getLogger(__name__)

ProgressCallback = Callable[[str], None]


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


class RemoteTrainError(Exception):
    """Raised when a remote training step fails."""

    def __init__(self, step: str, detail: str) -> None:
        self.step = step
        self.detail = detail
        super().__init__(f"{step}: {detail}")


async def remote_finetune(
    profile: VoiceProfile,
    config: FinetuneConfig,
    progress: ProgressCallback | None = None,
) -> Path:
    """Run the full remote fine-tuning pipeline.

    Returns the path to the locally registered GGUF file.

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
    py = remote.python  # may contain $HOME — don't shell-quote
    _ssh = _run_wsl_ssh if remote.use_wsl else _run_ssh

    def _progress(msg: str) -> None:
        log.info("Finetune: %s", msg)
        if progress:
            progress(msg)

    _progress("Connecting to remote GPU...")
    rc, _out, err = await _ssh(remote, "nvidia-smi", timeout=30)
    if rc != 0:
        raise RemoteTrainError(
            "preflight",
            f"Can't reach {remote.host} or no GPU. {err[:200]}",
        )
    _progress("GPU verified.")

    if remote.use_wsl:
        # PowerShell: expand ~ via %USERPROFILE%, no single-quote quoting
        rel = remote.remote_dir
        if rel.startswith("~/"):
            rel = rel[2:]
        mkdir_cmd = f"if not exist %USERPROFILE%\\{rel} mkdir %USERPROFILE%\\{rel}"
    else:
        mkdir_cmd = f"mkdir -p {shlex.quote(scp_rdir)}"
    rc, _, err = await _run_ssh(remote, mkdir_cmd)
    if rc != 0:
        raise RemoteTrainError("mkdir", err[:200])

    _progress("Pushing training scripts...")
    tools_dir = Path(__file__).parent

    import json
    import tempfile
    from dataclasses import asdict

    profile_data = asdict(profile)
    profile_data["line_count"] = profile.line_count
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False,
    ) as f:
        json.dump(profile_data, f, ensure_ascii=False, indent=2)
        local_profile = f.name

    try:
        scripts = [
            (str(tools_dir / "dataset_prep.py"), f"{scp_rdir}/dataset_prep.py"),
            (str(tools_dir / "finetune_voice.py"), f"{scp_rdir}/finetune_voice.py"),
            (str(tools_dir / "voice_profile.py"), f"{scp_rdir}/voice_profile.py"),
            (local_profile, f"{scp_rdir}/{q_slug}.json"),
        ]

        results = await asyncio.gather(*(
            _run_scp(remote, local, remote_path)
            for local, remote_path in scripts
        ))
        for (rc, err), (local, _) in zip(results, scripts):
            if rc != 0:
                raise RemoteTrainError("push", f"SCP failed for {local}: {err[:200]}")
    finally:
        Path(local_profile).unlink(missing_ok=True)

    # When using WSL, SCP lands on the Windows filesystem but commands run in
    # WSL-native paths. Resolve the Windows mount path for cross-filesystem copies.
    win_mount = ""
    if remote.use_wsl:
        rc, win_home, _ = await _run_ssh(remote, "echo %USERPROFILE%", timeout=10)
        if rc != 0:
            raise RemoteTrainError("copy", "Failed to resolve Windows home dir")
        win_home = win_home.strip().replace("\\", "/")
        if len(win_home) >= 2 and win_home[1] == ":":
            mount_path = f"/mnt/{win_home[0].lower()}{win_home[2:]}"
        else:
            mount_path = win_home
        rel = remote.remote_dir.lstrip("~/")
        win_mount = f"{mount_path}/{rel}" if rel else mount_path

        copy_cmd = (
            f"mkdir -p {cmd_rdir} && "
            f'cp "{win_mount}/"*.py "{win_mount}/"*.json {cmd_rdir}/'
        )
        rc, _, err = await _ssh(remote, copy_cmd, timeout=30)
        if rc != 0:
            raise RemoteTrainError("copy", f"Failed to copy files into WSL: {err[:200]}")

    _progress(f"Preparing training data ({profile.line_count} lines)...")
    prep_cmd = (
        f"cd {cmd_rdir} && {py} -c "
        f"\"from dataset_prep import prepare_dataset; "
        f"from pathlib import Path; "
        f"prepare_dataset(Path('{slug}.json'), Path('data'))\""
    )
    rc, out, err = await _ssh(remote, prep_cmd, progress)
    if rc != 0:
        raise RemoteTrainError("prep", f"Dataset prep failed:\n{err[-500:]}")

    _progress("Starting LoRA training...")
    q_base = shlex.quote(config.base_model)
    train_cmd = (
        f"cd {cmd_rdir} && {py} -m finetune_voice train "
        f"--data data/ --output output/ "
        f"--base-model {q_base}"
    )
    rc, out, err = await _ssh(
        remote, train_cmd, progress, timeout=7200,
    )
    if rc != 0:
        if "OutOfMemoryError" in err or "CUDA out of memory" in err:
            raise RemoteTrainError(
                "train",
                "GPU out of memory. Try reducing batch_size in "
                "[finetune] config.",
            )
        raise RemoteTrainError("train", f"Training failed:\n{err[-500:]}")

    _progress("Exporting GGUF...")
    q_quant = shlex.quote(config.quantization)
    q_base = shlex.quote(config.base_model)
    export_cmd = (
        f"cd {cmd_rdir} && {py} -m finetune_voice export "
        f"--adapter output/adapter --output {q_slug}.gguf "
        f"--base-model {q_base} --quantization {q_quant}"
    )
    rc, out, err = await _ssh(remote, export_cmd, progress, timeout=3600)
    if rc != 0:
        raise RemoteTrainError("export", f"GGUF export failed:\n{err[-500:]}")

    if remote.use_wsl:
        rc, _, err = await _ssh(
            remote, f'cp {cmd_rdir}/{q_slug}.gguf "{win_mount}/"', timeout=120,
        )
        if rc != 0:
            raise RemoteTrainError("copy", f"Failed to copy GGUF to Windows: {err[:200]}")

    _progress("Downloading model...")
    local_models_dir = (
        Path(config.output_dir).expanduser() / "models"
    )
    local_models_dir.mkdir(parents=True, exist_ok=True)
    local_gguf = local_models_dir / f"{slug}.gguf"

    rc, err = await _run_scp(
        remote,
        str(local_gguf),
        f"{scp_rdir}/{q_slug}.gguf",
        pull=True,
        timeout=1800,
    )
    if rc != 0:
        raise RemoteTrainError("pull", f"Failed to download GGUF: {err[:200]}")

    size_gb = local_gguf.stat().st_size / 1e9
    _progress(f"Downloaded {size_gb:.1f} GB")

    _progress("Registering with Ollama...")
    from tokenpal.tools.dataset_prep import build_system_prompt
    from tokenpal.tools.finetune_voice import register_ollama

    model_name = f"tokenpal-{slug}"
    system_prompt = build_system_prompt(profile)
    if not register_ollama(local_gguf, model_name, system_prompt):
        raise RemoteTrainError(
            "register", "Ollama registration failed. Is ollama running?",
        )

    _progress("Cleaning up remote files...")
    await _ssh(
        remote,
        f"rm -rf {cmd_rdir}/data {cmd_rdir}/output",
        timeout=30,
    )

    _progress(f"Done! Model: {model_name}")
    return local_gguf


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

    Creates a venv and installs training dependencies.
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

    rdir = _wsl_cmd_dir(remote) if remote.use_wsl else remote.remote_dir
    await _ssh(remote, f"mkdir -p {rdir}")

    py = remote.python
    _progress("Checking Python...")
    rc, out, _ = await _ssh(remote, f"{shlex.quote(py)} --version")
    if rc != 0:
        return _setup_fail(f"Python not found at {py}", _progress)
    _progress(f"Python: {out.strip()}")

    if remote.use_wsl:
        _progress("Installing system packages...")
        rc, _, err = await _ssh(
            remote,
            "sudo apt-get update && sudo apt-get install -y python3-venv python3-pip",
            progress, timeout=300,
        )
        if rc != 0:
            return _setup_fail(f"apt install failed: {err[-200:]}", _progress)

    _progress("Setting up virtual environment...")
    venv_path = f"{rdir}/.venv"
    setup_cmd = (
        f"test -d {venv_path} || {shlex.quote(py)} -m venv {venv_path} && "
        f"{venv_path}/bin/pip install --upgrade pip"
    )
    rc, _, err = await _ssh(remote, setup_cmd, progress, timeout=120)
    if rc != 0:
        return _setup_fail(f"Venv setup failed: {err[:200]}", _progress)

    _progress("Installing training dependencies (this may take a while)...")
    install_cmd = (
        f"{venv_path}/bin/pip install "
        f"--trusted-host pypi.org --trusted-host files.pythonhosted.org "
        f"unsloth trl transformers datasets bitsandbytes accelerate peft"
    )
    rc, _, err = await _ssh(remote, install_cmd, progress, timeout=1800)
    if rc != 0:
        return _setup_fail(f"Install failed: {err[-300:]}", _progress)

    _progress("Verifying CUDA...")
    verify_cmd = f'{venv_path}/bin/python -c "import torch; print(torch.cuda.is_available())"'
    rc, out, _ = await _ssh(remote, verify_cmd)
    if rc != 0 or "True" not in out:
        return _setup_fail(f"CUDA verification failed on {remote.host}", _progress)

    venv_python = f"{venv_path}/bin/python"
    _progress(f"Setup complete! Remote python: {venv_python}")
    _progress(
        f"Update config.toml: [finetune.remote] python = \"{venv_python}\""
    )
    return True
