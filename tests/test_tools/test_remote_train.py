"""Tests for the remote SSH training orchestrator."""

from __future__ import annotations  # noqa: I001

import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from tokenpal.config.schema import FinetuneConfig, RemoteTrainConfig
from tokenpal.tools.voice_profile import VoiceProfile

from tokenpal.tools.remote_train import (
    RemoteTrainError,
    _INSTALL_SH,
    _ensure_base_model,
    _hash_training_sources,
    _ssh_target,
    _wsl_cmd_dir,
    _wsl_wrap,
    remote_finetune,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_remote(**overrides: object) -> RemoteTrainConfig:
    defaults = dict(
        host="gpu-box",
        user="testuser",
        remote_dir="~/training",
        python="python3",
    )
    defaults.update(overrides)
    return RemoteTrainConfig(**defaults)  # type: ignore[arg-type]


def _make_config(**remote_overrides: object) -> FinetuneConfig:
    config = FinetuneConfig()
    config.remote = _make_remote(**remote_overrides)
    return config


def _make_profile() -> VoiceProfile:
    return VoiceProfile(
        character="Mordecai",
        source="regularshow",
        created="2026-01-01",
        lines=[f"Dude, line {i}." for i in range(100)],
        persona="A blue jay who says dude.",
    )


class _MockSSH:
    """Configurable SSH mock that routes by command substring."""

    def __init__(self, routes: dict[str, tuple[int, str, str]] | None = None):
        self.routes = routes or {}
        self.calls: list[str] = []

    async def __call__(
        self, remote, cmd, progress=None, timeout=3600,
    ):
        self.calls.append(cmd)
        for pattern, response in self.routes.items():
            if pattern in cmd:
                return response
        return (0, "", "")


class _MockSCP:
    """Configurable SCP mock."""

    def __init__(self, rc: int = 0, err: str = ""):
        self.rc = rc
        self.err = err
        self.calls: list[tuple[str, str]] = []

    async def __call__(
        self, remote, local, remote_path, *, pull=False, timeout=1800,
    ):
        self.calls.append((local, remote_path))
        return (self.rc, self.err)


# Preflight probe mock output helpers. The probe is routed by the unique
# substring "import torch" (appears only in `_preflight_remote_state`).
# Tests that should pass through preflight cleanly spread `**_preflight_clean()`.
def _preflight_clean() -> dict[str, tuple[int, str, str]]:
    """All-clean preflight: no lock, no tmux session, venv functional."""
    return {"import torch": (0, "lock_file=0\nlock=free\ntmux=dead\nvenv=ok\n", "")}


def _preflight_state(
    *,
    lock_file: bool = False,
    lock_held: bool = False,
    tmux_alive: bool = False,
    venv_ok: bool = True,
) -> dict[str, tuple[int, str, str]]:
    """Build a preflight probe response with specific state fields."""
    lines = [
        f"lock_file={'1' if lock_file else '0'}",
        f"lock={'held' if lock_held else 'free'}",
        f"tmux={'alive' if tmux_alive else 'dead'}",
        f"venv={'ok' if venv_ok else 'broken'}",
    ]
    return {"import torch": (0, "\n".join(lines) + "\n", "")}


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------


def test_ssh_target_with_user():
    assert _ssh_target(_make_remote()) == "testuser@gpu-box"


def test_ssh_target_without_user():
    assert _ssh_target(_make_remote(user="")) == "gpu-box"


def test_wsl_wrap_escapes_quotes():
    result = _wsl_wrap('echo "hello"')
    assert 'wsl -e bash -lc' in result
    assert '\\"hello\\"' in result


def test_wsl_cmd_dir_expands_tilde():
    remote = _make_remote(remote_dir="~/tokenpal-training")
    assert _wsl_cmd_dir(remote) == "$HOME/tokenpal-training"


def test_wsl_cmd_dir_bare_tilde():
    remote = _make_remote(remote_dir="~")
    assert _wsl_cmd_dir(remote) == "$HOME"


def test_wsl_cmd_dir_absolute():
    remote = _make_remote(remote_dir="/opt/training")
    assert _wsl_cmd_dir(remote) == "/opt/training"


def test_hash_training_sources_is_deterministic():
    h1 = _hash_training_sources()
    h2 = _hash_training_sources()
    assert h1 == h2
    assert len(h1) == 16  # truncated hex digest


def test_hash_training_sources_is_hex():
    h = _hash_training_sources()
    int(h, 16)  # raises ValueError if not valid hex


# ---------------------------------------------------------------------------
# install.sh content checks
# ---------------------------------------------------------------------------


def test_install_sh_has_shebang():
    assert _INSTALL_SH.strip().startswith("#!/usr/bin/env bash")


def test_install_sh_has_strict_mode():
    assert "set -euo pipefail" in _INSTALL_SH


def test_install_sh_detects_wsl_mount():
    assert '/mnt/*' in _INSTALL_SH


def test_install_sh_checks_python_version():
    assert "PY_MINOR" in _INSTALL_SH
    assert "Python 3.12+" in _INSTALL_SH


def test_install_sh_detects_cuda_and_rocm():
    assert "nvidia-smi" in _INSTALL_SH
    assert "rocm-smi" in _INSTALL_SH


def test_install_sh_detects_intel_npu():
    assert "Intel NPU" in _INSTALL_SH


def test_install_sh_installs_pytorch_first():
    # PyTorch must be installed before the wheel (which depends on it)
    torch_idx = _INSTALL_SH.index("Installing PyTorch")
    wheel_idx = _INSTALL_SH.index("Installing tokenpal")
    assert torch_idx < wheel_idx


def test_install_sh_verifies_cuda():
    assert "torch.cuda.is_available()" in _INSTALL_SH


def test_install_sh_no_sentinel_file():
    """The .install-ok sentinel was retired when preflight adopted a real
    venv integrity check (import torch). Guard against accidental
    re-introduction — any new 'partial install recovery' mechanism should
    go through `_preflight_remote_state` instead."""
    assert ".install-ok" not in _INSTALL_SH
    assert "SENTINEL" not in _INSTALL_SH


# ---------------------------------------------------------------------------
# _build_bundle tests
# ---------------------------------------------------------------------------


def test_build_bundle_produces_tarball(tmp_path):
    """_build_bundle creates a valid tarball with install.sh and source hash."""
    from tokenpal.tools.remote_train import _build_bundle

    # Mock subprocess.run to avoid actually building the wheel
    fake_wheel = tmp_path / "tokenpal-0.1.0-py3-none-any.whl"
    fake_wheel.write_text("fake wheel")

    def mock_build(args, **kwargs):
        # Simulate `python -m build --wheel --outdir <dir>`
        outdir = Path(args[-1]) if "--outdir" in args else tmp_path
        # Copy fake wheel to outdir
        (outdir / fake_wheel.name).write_text("fake wheel")

        class FakeResult:
            returncode = 0
            stderr = ""
        return FakeResult()

    with patch("tokenpal.tools.remote_train.subprocess.run", mock_build):
        tarball = _build_bundle()

    assert tarball.exists()
    assert tarball.name == "tokenpal-training-bundle.tar.gz"

    # Verify tarball contents
    with tarfile.open(tarball, "r:gz") as tar:
        names = tar.getnames()
        assert "install.sh" in names
        assert ".source-hash" in names
        assert any(n.endswith(".whl") for n in names)


def test_build_bundle_includes_profile_json(tmp_path):
    """Profile JSON is included in the bundle when provided."""
    profile_json = tmp_path / "mordecai.json"
    profile_json.write_text('{"character": "Mordecai"}')

    def mock_build(args, **kwargs):
        outdir = args[args.index("--outdir") + 1]
        (Path(outdir) / "tokenpal-0.1.0-py3-none-any.whl").write_text("fake")

        class FakeResult:
            returncode = 0
            stderr = ""
        return FakeResult()

    from tokenpal.tools.remote_train import _build_bundle

    with patch("tokenpal.tools.remote_train.subprocess.run", mock_build):
        tarball = _build_bundle(profile_json_path=profile_json)

    with tarfile.open(tarball, "r:gz") as tar:
        assert "mordecai.json" in tar.getnames()


def test_build_bundle_raises_on_wheel_failure():
    """_build_bundle raises RuntimeError when wheel build fails."""
    from tokenpal.tools.remote_train import _build_bundle

    def mock_fail(args, **kwargs):
        class FakeResult:
            returncode = 1
            stderr = "error: no setup.py"
        return FakeResult()

    with patch("tokenpal.tools.remote_train.subprocess.run", mock_fail):
        with pytest.raises(RuntimeError, match="Wheel build failed"):
            _build_bundle()


# ---------------------------------------------------------------------------
# RemoteTrainError tests
# ---------------------------------------------------------------------------


def test_remote_train_error_basic():
    err = RemoteTrainError("train", "something broke")
    assert err.step == "train"
    assert err.detail == "something broke"
    assert "train: something broke" in str(err)


def test_remote_train_error_with_hint():
    err = RemoteTrainError("train", "OOM", hint="ssh gpu-box\ncd ~/training")
    assert "OOM" in str(err)
    assert "ssh gpu-box" in str(err)
    assert err.hint == "ssh gpu-box\ncd ~/training"


# ---------------------------------------------------------------------------
# remote_finetune pipeline tests
# ---------------------------------------------------------------------------


async def test_remote_finetune_no_host():
    config = FinetuneConfig()
    config.remote = RemoteTrainConfig()
    with pytest.raises(RemoteTrainError, match="No remote host"):
        await remote_finetune(_make_profile(), config)


async def test_remote_finetune_preflight_fail():
    config = _make_config()
    ssh = _MockSSH({"nvidia-smi": (1, "", "Connection refused")})

    with patch("tokenpal.tools.remote_train._run_ssh", ssh):
        with pytest.raises(RemoteTrainError, match="preflight"):
            await remote_finetune(_make_profile(), config)


async def test_remote_finetune_calls_progress():
    """Verify progress callback fires during preflight."""
    config = _make_config()
    progress_msgs: list[str] = []

    ssh = _MockSSH({
        "nvidia-smi": (0, "GPU OK", ""),
        "mkdir": (0, "", ""),
        "df -BG": (0, "50G", ""),
        ".source-hash": (0, "none", ""),
    })
    scp = _MockSCP(rc=1, err="SCP failed")

    with (
        patch("tokenpal.tools.remote_train._run_ssh", ssh),
        patch("tokenpal.tools.remote_train._run_scp", scp),
        patch(
            "tokenpal.tools.remote_train._build_bundle",
            return_value=Path("/tmp/fake.tar.gz"),
        ),
    ):
        with pytest.raises(RemoteTrainError, match="push"):
            await remote_finetune(
                _make_profile(), config, lambda msg: progress_msgs.append(msg),
            )

    assert any("GPU" in msg for msg in progress_msgs)


async def test_source_hash_match_skips_bundle_push():
    """When source hash matches remote, bundle push is skipped."""
    config = _make_config()
    local_hash = _hash_training_sources()
    progress_msgs: list[str] = []

    ssh = _MockSSH({
        **_preflight_clean(),
        "nvidia-smi": (0, "GPU OK", ""),
        "mkdir": (0, "", ""),
        "df -BG": (0, "50G", ""),
        ".source-hash": (0, local_hash, ""),  # hash matches!
        "test -d": (1, "", ""),  # base model not found → triggers model push
    })
    scp = _MockSCP(rc=0)

    with (
        patch("tokenpal.tools.remote_train._run_ssh", ssh),
        patch("tokenpal.tools.remote_train._run_scp", scp),
        patch(
            "tokenpal.tools.remote_train._ensure_base_model",
            side_effect=RemoteTrainError("model_push", "test stop"),
        ),
    ):
        with pytest.raises(RemoteTrainError, match="model_push"):
            await remote_finetune(
                _make_profile(), config, lambda msg: progress_msgs.append(msg),
            )

    assert any("skipping bundle push" in msg.lower() for msg in progress_msgs)
    # _build_bundle should NOT have been called (no import needed)


async def test_install_failure_raises():
    """install.sh failure raises RemoteTrainError('install')."""
    config = _make_config()

    ssh = _MockSSH({
        "nvidia-smi": (0, "GPU OK", ""),
        "mkdir": (0, "", ""),
        "df -BG": (0, "50G", ""),
        ".source-hash": (0, "none", ""),  # hash mismatch → push bundle
        "install.sh": (1, "", "pip install failed"),  # install fails
    })
    scp = _MockSCP(rc=0)

    with (
        patch("tokenpal.tools.remote_train._run_ssh", ssh),
        patch("tokenpal.tools.remote_train._run_scp", scp),
        patch(
            "tokenpal.tools.remote_train._build_bundle",
            return_value=Path("/tmp/fake.tar.gz"),
        ),
    ):
        with pytest.raises(RemoteTrainError, match="install"):
            await remote_finetune(_make_profile(), config)


async def test_concurrent_training_blocked():
    """Lock busy → RemoteTrainError about concurrent training."""
    config = _make_config()

    ssh = _MockSSH({
        **_preflight_clean(),
        "nvidia-smi": (0, "GPU OK", ""),
        "mkdir": (0, "", ""),
        "df -BG": (0, "50G", ""),
        ".source-hash": (0, _hash_training_sources(), ""),
        "test -d": (0, "exists", ""),  # base model exists
        "finetune_voice prep": (0, "", ""),  # prep succeeds
        "checkpoint": (1, "", ""),  # no checkpoints
        "flock": (0, "busy", ""),  # lock is busy!
    })
    scp = _MockSCP(rc=0)

    with (
        patch("tokenpal.tools.remote_train._run_ssh", ssh),
        patch("tokenpal.tools.remote_train._run_scp", scp),
        patch("tokenpal.tools.remote_train._ensure_base_model"),
    ):
        with pytest.raises(RemoteTrainError, match="Another training job"):
            await remote_finetune(_make_profile(), config)


# ---------------------------------------------------------------------------
# Preflight remote-state tests (commit 1 of pipeline-hardening)
# ---------------------------------------------------------------------------


async def test_preflight_live_training_raises():
    """Live training detected (lock held + tmux alive) → error with attach hint."""
    config = _make_config()

    ssh = _MockSSH({
        **_preflight_state(lock_file=True, lock_held=True, tmux_alive=True, venv_ok=True),
        "nvidia-smi": (0, "GPU OK", ""),
        "df -BG": (0, "50G", ""),
    })

    with patch("tokenpal.tools.remote_train._run_ssh", ssh):
        with pytest.raises(RemoteTrainError) as exc_info:
            await remote_finetune(_make_profile(), config)

    err = exc_info.value
    assert err.step == "preflight"
    assert "already running" in err.detail.lower()
    assert "tmux attach" in err.hint
    assert "tokenpal-mordecai" in err.hint


async def test_preflight_stale_flock_auto_removed(caplog):
    """Lock held but no tmux session → stale → rm -f issued, WARN logged."""
    import logging
    caplog.set_level(logging.WARNING, logger="tokenpal.tools.remote_train")
    config = _make_config()
    progress_msgs: list[str] = []

    ssh = _MockSSH({
        **_preflight_state(lock_file=True, lock_held=True, tmux_alive=False, venv_ok=True),
        "nvidia-smi": (0, "GPU OK", ""),
        "df -BG": (0, "50G", ""),
        ".source-hash": (0, _hash_training_sources(), ""),
        "test -d": (0, "exists", ""),
        "finetune_voice prep": (0, "", ""),
        "checkpoint": (1, "", ""),
        # flock re-check later returns "locked" (free) — stale was cleaned up
        "flock -n /tmp/tokenpal-training.lock -c": (0, "locked", ""),
        # tmux new-session succeeds, has-session returns "done" immediately
        "tmux new-session": (0, "", ""),
        "tmux has-session": (0, "done", ""),
        "EXIT_CODE=0": (0, "EXIT_CODE=0", ""),
    })

    with (
        patch("tokenpal.tools.remote_train._run_ssh", ssh),
        patch("tokenpal.tools.remote_train._ensure_base_model"),
    ):
        # Raise to stop before we go deeper — we only care about preflight behavior
        with patch(
            "tokenpal.tools.remote_train._run_scp",
            _MockSCP(rc=1, err="stop here"),
        ):
            with pytest.raises(RemoteTrainError):
                await remote_finetune(
                    _make_profile(),
                    config,
                    lambda msg: progress_msgs.append(msg),
                )

    # Preflight should have issued the rm, emitted progress + warning log
    assert any("rm -f /tmp/tokenpal-training.lock" in call for call in ssh.calls), (
        f"expected rm -f call, got: {ssh.calls}"
    )
    assert any("stale training lock" in msg.lower() for msg in progress_msgs)
    assert any("stale flock" in rec.message.lower() for rec in caplog.records)


async def test_preflight_orphan_tmux_session_killed():
    """Tmux alive but no lock → orphan from crashed run → kill-session issued."""
    config = _make_config()
    progress_msgs: list[str] = []

    ssh = _MockSSH({
        **_preflight_state(lock_file=False, lock_held=False, tmux_alive=True, venv_ok=True),
        "nvidia-smi": (0, "GPU OK", ""),
        "df -BG": (0, "50G", ""),
    })

    with (
        patch("tokenpal.tools.remote_train._run_ssh", ssh),
        # Stop early — we only care that the kill-session was issued
        patch(
            "tokenpal.tools.remote_train._run_scp",
            _MockSCP(rc=1, err="stop here"),
        ),
        patch(
            "tokenpal.tools.remote_train._build_bundle",
            return_value=Path("/tmp/fake.tar.gz"),
        ),
    ):
        with pytest.raises(RemoteTrainError):
            await remote_finetune(
                _make_profile(),
                config,
                lambda msg: progress_msgs.append(msg),
            )

    # Expect a tmux kill-session targeting tokenpal-mordecai (not the default
    # silent `2>/dev/null` inline kill that happens later at new-session time)
    preflight_kill = [
        c for c in ssh.calls
        if "tmux kill-session -t tokenpal-mordecai" in c and "2>/dev/null" not in c
    ]
    assert preflight_kill, f"expected explicit preflight kill-session, got: {ssh.calls}"
    assert any("orphan tmux session" in msg.lower() for msg in progress_msgs)


async def test_preflight_broken_venv_forces_reinstall():
    """Sentinel present but torch import fails → force bundle push + reinstall.

    This replaces the old `test -f .install-ok` grep. Catches partial pip installs
    where the sentinel was touched but the venv is broken (e.g. WSL SSL flake).
    """
    config = _make_config()
    progress_msgs: list[str] = []
    local_hash = _hash_training_sources()

    # venv_ok=False simulates a torch import failure on a venv that looks installed
    ssh = _MockSSH({
        **_preflight_state(venv_ok=False),
        "nvidia-smi": (0, "GPU OK", ""),
        "mkdir": (0, "", ""),
        "df -BG": (0, "50G", ""),
        ".source-hash": (0, local_hash, ""),  # hash matches — WOULD skip push...
    })
    # ...but broken venv forces incomplete → push + install. Make install fail
    # to stop the test before we get deeper into the pipeline.
    ssh.routes["install.sh"] = (1, "", "reinstall triggered")

    with (
        patch("tokenpal.tools.remote_train._run_ssh", ssh),
        patch("tokenpal.tools.remote_train._run_scp", _MockSCP(rc=0)),
        patch(
            "tokenpal.tools.remote_train._build_bundle",
            return_value=Path("/tmp/fake.tar.gz"),
        ),
    ):
        with pytest.raises(RemoteTrainError, match="install"):
            await remote_finetune(
                _make_profile(),
                config,
                lambda msg: progress_msgs.append(msg),
            )

    # Bundle push must have happened — install.sh was called even though hash matched
    assert any("install.sh" in call for call in ssh.calls)
    # The skip-bundle-push path must NOT have been taken
    assert not any("skipping bundle push" in msg.lower() for msg in progress_msgs)


async def test_preflight_all_clean_proceeds():
    """All-clean state → no cleanup, proceeds straight to normal flow."""
    config = _make_config()
    progress_msgs: list[str] = []

    ssh = _MockSSH({
        **_preflight_clean(),
        "nvidia-smi": (0, "GPU OK", ""),
        "df -BG": (0, "50G", ""),
        "mkdir": (0, "", ""),
    })

    with (
        patch("tokenpal.tools.remote_train._run_ssh", ssh),
        # Stop early — we only care that preflight didn't issue any cleanup commands
        patch(
            "tokenpal.tools.remote_train._run_scp",
            _MockSCP(rc=1, err="stop here"),
        ),
        patch(
            "tokenpal.tools.remote_train._build_bundle",
            return_value=Path("/tmp/fake.tar.gz"),
        ),
    ):
        with pytest.raises(RemoteTrainError):
            await remote_finetune(
                _make_profile(),
                config,
                lambda msg: progress_msgs.append(msg),
            )

    # No stale-lock cleanup commands should have fired
    assert not any("rm -f /tmp/tokenpal-training.lock" in c for c in ssh.calls)
    assert not any(
        "tmux kill-session" in c and "2>/dev/null" not in c for c in ssh.calls
    )
    assert not any("stale training lock" in msg.lower() for msg in progress_msgs)
    assert not any("orphan tmux session" in msg.lower() for msg in progress_msgs)


async def test_training_oom_includes_hint():
    """OOM error includes actionable hint with debug commands."""
    config = _make_config()

    call_count = 0

    async def smart_ssh(remote, cmd, progress=None, timeout=3600):
        nonlocal call_count
        call_count += 1
        if "nvidia-smi" in cmd:
            return (0, "GPU OK", "")
        if "mkdir" in cmd:
            return (0, "", "")
        if "df -BG" in cmd:
            return (0, "50G", "")
        if ".source-hash" in cmd:
            return (0, _hash_training_sources(), "")
        if "finetune_voice prep" in cmd:
            return (0, "", "")
        if "checkpoint-" in cmd and "ls -d" in cmd:
            return (0, "output/adapter/checkpoint-100", "")  # has checkpoint
        if "flock -n" in cmd:
            return (0, "locked", "")  # lock available
        if "tmux new-session" in cmd:
            return (0, "", "")  # tmux starts
        if "tmux has-session" in cmd:
            return (0, "done", "")  # training done
        if "tail" in cmd and "train.log" in cmd:
            return (0, "CUDA out of memory\nEXIT_CODE=1", "")
        if "checkpoint" in cmd:
            return (0, "output/adapter/checkpoint-100", "")
        return (0, "", "")

    scp = _MockSCP(rc=0)

    with (
        patch("tokenpal.tools.remote_train._run_ssh", smart_ssh),
        patch("tokenpal.tools.remote_train._run_scp", scp),
        patch("tokenpal.tools.remote_train._ensure_base_model"),
    ):
        with pytest.raises(RemoteTrainError, match="out of memory") as exc_info:
            await remote_finetune(_make_profile(), config)

    assert exc_info.value.hint
    assert "ssh" in exc_info.value.hint.lower()
    assert "checkpoint" in exc_info.value.hint.lower()


async def test_disk_space_warning(capsys):
    """Low disk space produces a warning in progress messages."""
    config = _make_config()
    progress_msgs: list[str] = []

    ssh = _MockSSH({
        "nvidia-smi": (0, "GPU OK", ""),
        "mkdir": (0, "", ""),
        "df -BG": (0, "10G", ""),  # only 10GB free!
        ".source-hash": (0, "none", ""),
    })
    scp = _MockSCP(rc=1, err="fail")

    with (
        patch("tokenpal.tools.remote_train._run_ssh", ssh),
        patch("tokenpal.tools.remote_train._run_scp", scp),
        patch(
            "tokenpal.tools.remote_train._build_bundle",
            return_value=Path("/tmp/fake.tar.gz"),
        ),
    ):
        with pytest.raises(RemoteTrainError):
            await remote_finetune(
                _make_profile(), config, lambda msg: progress_msgs.append(msg),
            )

    assert any("10GB free" in msg for msg in progress_msgs)


async def test_checkpoint_resume_detected():
    """Existing checkpoint triggers --resume in training command."""
    config = _make_config()
    ssh_calls: list[str] = []

    async def tracking_ssh(remote, cmd, progress=None, timeout=3600):
        ssh_calls.append(cmd)
        if "nvidia-smi" in cmd:
            return (0, "GPU OK", "")
        if "mkdir" in cmd:
            return (0, "", "")
        if "df -BG" in cmd:
            return (0, "50G", "")
        if ".source-hash" in cmd:
            return (0, _hash_training_sources(), "")
        if "finetune_voice prep" in cmd:
            return (0, "", "")
        if "ls -d" in cmd and "checkpoint" in cmd:
            return (0, "output/adapter/checkpoint-50", "")
        if "flock -n" in cmd:
            return (0, "locked", "")
        if "tmux new-session" in cmd:
            return (0, "", "")
        if "tmux has-session" in cmd:
            return (0, "done", "")
        if "tail" in cmd and "train.log" in cmd:
            return (0, "EXIT_CODE=0", "")
        if "finetune_voice merge" in cmd:
            return (1, "", "merge fail")  # stop here
        return (0, "", "")

    scp = _MockSCP(rc=0)

    with (
        patch("tokenpal.tools.remote_train._run_ssh", tracking_ssh),
        patch("tokenpal.tools.remote_train._run_scp", scp),
        patch("tokenpal.tools.remote_train._ensure_base_model"),
    ):
        with pytest.raises(RemoteTrainError, match="merge"):
            await remote_finetune(_make_profile(), config)

    # The training script should contain --resume (written via base64)
    script_calls = [c for c in ssh_calls if "base64" in c]
    assert script_calls
    # Decode the base64 payload to verify --resume is in the script
    import base64
    b64_data = script_calls[0].split("echo ")[1].split(" |")[0]
    script_content = base64.b64decode(b64_data).decode()
    assert "--resume" in script_content


async def test_merge_failure_includes_debug_hint():
    """Merge failure includes SSH debug commands."""
    config = _make_config()

    async def smart_ssh(remote, cmd, progress=None, timeout=3600):
        if "nvidia-smi" in cmd:
            return (0, "GPU OK", "")
        if "mkdir" in cmd:
            return (0, "", "")
        if "df -BG" in cmd:
            return (0, "50G", "")
        if ".source-hash" in cmd:
            return (0, _hash_training_sources(), "")
        if "finetune_voice prep" in cmd:
            return (0, "", "")
        if "ls -d" in cmd and "checkpoint" in cmd:
            return (1, "", "")  # no checkpoints
        if "flock -n" in cmd:
            return (0, "locked", "")
        if "tmux new-session" in cmd:
            return (0, "", "")
        if "tmux has-session" in cmd:
            return (0, "done", "")
        if "tail" in cmd and "train.log" in cmd:
            return (0, "EXIT_CODE=0", "")
        if "finetune_voice merge" in cmd:
            return (1, "", "CUDA error during merge")
        return (0, "", "")

    with (
        patch("tokenpal.tools.remote_train._run_ssh", smart_ssh),
        patch("tokenpal.tools.remote_train._run_scp", _MockSCP(rc=0)),
        patch("tokenpal.tools.remote_train._ensure_base_model"),
    ):
        with pytest.raises(RemoteTrainError, match="merge") as exc_info:
            await remote_finetune(_make_profile(), config)

    assert exc_info.value.hint
    assert "ssh" in exc_info.value.hint.lower()
    assert "debug" in exc_info.value.hint.lower()


# ---------------------------------------------------------------------------
# Base model + pull integrity tests (commit 3 of pipeline-hardening)
# ---------------------------------------------------------------------------


async def test_ensure_base_model_valid_skips_download():
    """Config.json + nonzero weight shard → skip download entirely."""
    remote = _make_remote()
    ssh = _MockSSH({
        # The new check command contains "BASE_MODEL_OK" as its echo argument
        "BASE_MODEL_OK": (0, "BASE_MODEL_OK\n", ""),
    })
    progress_msgs: list[str] = []

    result = await _ensure_base_model(
        remote,
        base_model="google/gemma-2-2b-it",
        model_dir="/home/user/tokenpal-training/model",
        venv_py="/home/user/tokenpal-training/.venv/bin/python",
        _ssh=ssh,
        progress=lambda msg: progress_msgs.append(msg),
    )

    assert result == "/home/user/tokenpal-training/model"
    # Download MUST NOT have been triggered
    assert not any("snapshot_download" in call for call in ssh.calls)
    # Progress should confirm verification, not download
    assert any("already on remote" in msg.lower() for msg in progress_msgs)
    assert any("weights verified" in msg.lower() for msg in progress_msgs)


async def test_ensure_base_model_config_without_weights_redownloads():
    """Config.json present but weight shards missing → forces re-download.

    This is the exact failure mode commit 3 targets: a prior HF download that
    wrote config.json successfully but died before (or mid-) weight shards.
    Old check grepped for model_type in config.json → passed. New check
    additionally requires at least one nonzero .safetensors or .bin file,
    so this state correctly triggers re-download.
    """
    remote = _make_remote()
    # Check command returns empty (BASE_MODEL_OK never echoed — check failed)
    # Then snapshot_download is attempted and succeeds.
    ssh = _MockSSH({
        "BASE_MODEL_OK": (1, "", "no weights found"),
        "snapshot_download": (0, "downloaded", ""),
    })
    progress_msgs: list[str] = []

    result = await _ensure_base_model(
        remote,
        base_model="google/gemma-2-2b-it",
        model_dir="/home/user/tokenpal-training/model",
        venv_py="/home/user/tokenpal-training/.venv/bin/python",
        _ssh=ssh,
        progress=lambda msg: progress_msgs.append(msg),
    )

    assert result == "/home/user/tokenpal-training/model"
    # snapshot_download MUST have been triggered
    assert any("snapshot_download" in call for call in ssh.calls)
    # Progress should indicate download, not skip
    assert any("downloading" in msg.lower() for msg in progress_msgs)
    assert not any("already on remote" in msg.lower() for msg in progress_msgs)


async def test_pull_failure_includes_recovery_hint(tmp_path):
    """rsync failure during pull → error with rm -rf recovery hint."""
    config = _make_config()
    config.output_dir = str(tmp_path)

    async def smart_ssh(remote, cmd, progress=None, timeout=3600):
        if "import torch" in cmd:
            return (0, "lock_file=0\nlock=free\ntmux=dead\nvenv=ok\n", "")
        if "nvidia-smi" in cmd:
            return (0, "GPU OK", "")
        if "df -BG" in cmd:
            return (0, "50G", "")
        if "mkdir" in cmd:
            return (0, "", "")
        if ".source-hash" in cmd:
            return (0, _hash_training_sources(), "")
        if "finetune_voice prep" in cmd:
            return (0, "", "")
        if "checkpoint-" in cmd and "ls -d" in cmd:
            return (1, "", "")  # no checkpoint
        if "flock -n" in cmd and "-c true" not in cmd:
            return (0, "locked", "")  # lock free (not preflight probe)
        if "echo $HOME" in cmd:
            return (0, "/home/user", "")
        if "rocminfo" in cmd:
            return (0, "", "")
        if "base64 -d" in cmd:  # script write
            return (0, "", "")
        if "tmux new-session" in cmd:
            return (0, "", "")
        if "tmux has-session" in cmd:
            return (0, "done", "")
        if "tail" in cmd and "train.log" in cmd:
            return (0, "training complete\nEXIT_CODE=0", "")
        if "finetune_voice merge" in cmd:
            return (0, "merged", "")
        if "sha256sum" in cmd:
            return (0, "abc123remotehash" + "0" * 48, "")
        return (0, "", "")

    # _run_rsync fails → pull error path
    async def failing_rsync(remote, local, remote_path, pull=False, progress=None, timeout=3600):
        return (1, "rsync: connection reset by peer")

    with (
        patch("tokenpal.tools.remote_train._run_ssh", smart_ssh),
        patch("tokenpal.tools.remote_train._run_scp", _MockSCP(rc=0)),
        patch("tokenpal.tools.remote_train._run_rsync", failing_rsync),
        patch("tokenpal.tools.remote_train._ensure_base_model"),
    ):
        with pytest.raises(RemoteTrainError, match="pull") as exc_info:
            await remote_finetune(_make_profile(), config)

    err = exc_info.value
    assert err.hint, "pull failure error should include recovery hint"
    assert "rm -rf" in err.hint
    assert "voice finetune" in err.hint.lower()
    assert "mordecai" in err.hint.lower()


async def test_pull_checksum_mismatch_raises_hard_error(tmp_path):
    """sha256 mismatch after successful pull → hard error (was silent warning).

    Pre-commit-3: a corrupted local model got a WARNING log and was then
    registered with Ollama and used for inference. Post commit 3: the
    mismatch is a blocking error with a recovery hint.
    """
    config = _make_config()
    config.output_dir = str(tmp_path)

    async def smart_ssh(remote, cmd, progress=None, timeout=3600):
        if "import torch" in cmd:
            return (0, "lock_file=0\nlock=free\ntmux=dead\nvenv=ok\n", "")
        if "nvidia-smi" in cmd:
            return (0, "GPU OK", "")
        if "df -BG" in cmd:
            return (0, "50G", "")
        if "mkdir" in cmd:
            return (0, "", "")
        if ".source-hash" in cmd:
            return (0, _hash_training_sources(), "")
        if "finetune_voice prep" in cmd:
            return (0, "", "")
        if "checkpoint-" in cmd and "ls -d" in cmd:
            return (1, "", "")
        if "flock -n" in cmd and "-c true" not in cmd:
            return (0, "locked", "")
        if "echo $HOME" in cmd:
            return (0, "/home/user", "")
        if "rocminfo" in cmd:
            return (0, "", "")
        if "base64 -d" in cmd:
            return (0, "", "")
        if "tmux new-session" in cmd:
            return (0, "", "")
        if "tmux has-session" in cmd:
            return (0, "done", "")
        if "tail" in cmd and "train.log" in cmd:
            return (0, "EXIT_CODE=0", "")
        if "finetune_voice merge" in cmd:
            return (0, "merged", "")
        if "sha256sum" in cmd:
            # Deliberately return a remote hash that won't match the local one
            return (0, "f" * 64, "")
        return (0, "", "")

    # Rsync "succeeds" but creates no files — local hash will be the empty digest,
    # which won't match the remote hash of "f" * 64 → triggers mismatch branch.
    async def succeeding_rsync_noop(
        remote, local, remote_path, pull=False, progress=None, timeout=3600,
    ):
        return (0, "")

    with (
        patch("tokenpal.tools.remote_train._run_ssh", smart_ssh),
        patch("tokenpal.tools.remote_train._run_scp", _MockSCP(rc=0)),
        patch("tokenpal.tools.remote_train._run_rsync", succeeding_rsync_noop),
        patch("tokenpal.tools.remote_train._ensure_base_model"),
    ):
        with pytest.raises(RemoteTrainError, match="pull") as exc_info:
            await remote_finetune(_make_profile(), config)

    err = exc_info.value
    assert "checksum mismatch" in err.detail.lower()
    assert "corrupted" in err.detail.lower()
    assert err.hint, "checksum mismatch error should include recovery hint"
    assert "rm -rf" in err.hint
    assert "voice finetune" in err.hint.lower()


# ---------------------------------------------------------------------------
# Error surfacing tests (commit 4 of pipeline-hardening)
# ---------------------------------------------------------------------------


def test_looks_like_hf_auth_error_detects_common_patterns():
    """The heuristic catches the common HF auth failure shapes."""
    from tokenpal.tools.remote_train import _looks_like_hf_auth_error

    # Positive cases: common HF auth error messages
    assert _looks_like_hf_auth_error("HTTPError: 401 Client Error")
    assert _looks_like_hf_auth_error("403 Forbidden")
    assert _looks_like_hf_auth_error("GatedRepoError: Cannot access gated repo")
    assert _looks_like_hf_auth_error("Access to model google/gemma-2-2b-it is restricted")
    assert _looks_like_hf_auth_error("Invalid credentials in Authorization header")
    assert _looks_like_hf_auth_error("Token is not valid")
    assert _looks_like_hf_auth_error("Repository Not Found for url")
    assert _looks_like_hf_auth_error("You must be authenticated to access this resource")

    # Negative cases: other errors should not trip the heuristic
    assert not _looks_like_hf_auth_error("Connection timed out")
    assert not _looks_like_hf_auth_error("Disk full")
    assert not _looks_like_hf_auth_error("SSL: CERTIFICATE_VERIFY_FAILED")
    assert not _looks_like_hf_auth_error("")
    assert not _looks_like_hf_auth_error("Download succeeded")


async def test_ensure_base_model_surfaces_hf_auth_error():
    """Remote HF download fails with 401/403 → RemoteTrainError('auth') with hint."""
    remote = _make_remote()
    ssh = _MockSSH({
        "BASE_MODEL_OK": (1, "", ""),  # model not present
        "snapshot_download": (1, "", "HTTPError: 401 Client Error: Unauthorized"),
    })

    with pytest.raises(RemoteTrainError) as exc_info:
        await _ensure_base_model(
            remote,
            base_model="google/gemma-2-2b-it",
            model_dir="/home/user/tokenpal-training/model",
            venv_py="/home/user/tokenpal-training/.venv/bin/python",
            _ssh=ssh,
            progress=lambda _: None,
        )

    err = exc_info.value
    assert err.step == "auth"
    assert "HuggingFace auth failed" in err.detail
    assert "gemma-2-2b-it" in err.detail
    assert err.hint
    assert "HF_TOKEN" in err.hint
    assert "huggingface.co/google/gemma-2-2b-it" in err.hint
    # Should NOT have attempted local download — auth will fail there too
    # (we can't assert that from this test directly since we don't mock
    # huggingface_hub.snapshot_download, but the raise short-circuits it)


async def test_ensure_base_model_nonauth_error_falls_through_to_local():
    """Non-auth remote failure (e.g. network) falls through to local download path.

    Guard against the new auth check accidentally swallowing transient failures
    that WOULD succeed on the local fallback.
    """
    remote = _make_remote()
    ssh = _MockSSH({
        "BASE_MODEL_OK": (1, "", ""),
        "snapshot_download": (1, "", "Connection reset by peer"),
    })

    # Patch local snapshot_download + the push transport (rsync for non-WSL).
    async def succeeding_rsync(
        remote, local, remote_path, pull=False, progress=None, timeout=3600,
    ):
        return (0, "")

    with (
        patch("huggingface_hub.snapshot_download") as mock_local_dl,
        patch("tokenpal.tools.remote_train._run_scp", _MockSCP(rc=0)),
        patch("tokenpal.tools.remote_train._run_rsync", succeeding_rsync),
    ):
        mock_local_dl.return_value = None
        result = await _ensure_base_model(
            remote,
            base_model="google/gemma-2-2b-it",
            model_dir="/home/user/tokenpal-training/model",
            venv_py="/home/user/tokenpal-training/.venv/bin/python",
            _ssh=ssh,
            progress=lambda _: None,
        )

    # Should have reached and called local snapshot_download
    assert mock_local_dl.called
    assert result == "/home/user/tokenpal-training/model"


async def test_ollama_register_failure_includes_recovery_hint(tmp_path):
    """Register returns False → error with safetensors path + manual recovery."""
    config = _make_config()
    config.output_dir = str(tmp_path)

    async def smart_ssh(remote, cmd, progress=None, timeout=3600):
        if "import torch" in cmd:
            return (0, "lock_file=0\nlock=free\ntmux=dead\nvenv=ok\n", "")
        if "nvidia-smi" in cmd:
            return (0, "GPU OK", "")
        if "df -BG" in cmd:
            return (0, "50G", "")
        if "mkdir" in cmd:
            return (0, "", "")
        if ".source-hash" in cmd:
            return (0, _hash_training_sources(), "")
        if "finetune_voice prep" in cmd:
            return (0, "", "")
        if "checkpoint-" in cmd and "ls -d" in cmd:
            return (1, "", "")
        if "flock -n" in cmd and "-c true" not in cmd:
            return (0, "locked", "")
        if "echo $HOME" in cmd:
            return (0, "/home/user", "")
        if "rocminfo" in cmd:
            return (0, "", "")
        if "base64 -d" in cmd:
            return (0, "", "")
        if "tmux new-session" in cmd:
            return (0, "", "")
        if "tmux has-session" in cmd:
            return (0, "done", "")
        if "tail" in cmd and "train.log" in cmd:
            return (0, "EXIT_CODE=0", "")
        if "finetune_voice merge" in cmd:
            return (0, "merged", "")
        if "sha256sum" in cmd:
            return (0, "", "")  # empty hash → skip verification
        return (0, "", "")

    async def succeeding_rsync(remote, local, remote_path, pull=False, progress=None, timeout=3600):
        return (0, "")

    with (
        patch("tokenpal.tools.remote_train._run_ssh", smart_ssh),
        patch("tokenpal.tools.remote_train._run_scp", _MockSCP(rc=0)),
        patch("tokenpal.tools.remote_train._run_rsync", succeeding_rsync),
        patch("tokenpal.tools.remote_train._ensure_base_model"),
        # register_ollama returns False → triggers the error branch
        patch("tokenpal.tools.finetune_voice.register_ollama", return_value=False),
    ):
        with pytest.raises(RemoteTrainError, match="register") as exc_info:
            await remote_finetune(_make_profile(), config)

    err = exc_info.value
    assert err.step == "register"
    assert err.hint, "register failure should include recovery hint"
    assert "tokenpal-mordecai" in err.hint
    # Should tell the user where their model is so they don't think it's lost
    assert str(tmp_path) in err.hint or "models/tokenpal-mordecai" in err.hint
    assert "ollama" in err.hint.lower()
