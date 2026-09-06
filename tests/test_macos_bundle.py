"""The macOS app bundle that gives TokenPal its own menu-bar identity.

Nothing here launches anything: ``open`` is always a fake ``Popen``, the
framework stub is a fake file under ``tmp_path``, and AppKit is a stub
module. Launching the real bundle from a test would create a status item
and rewrite the machine's Control Center registry.
"""

from __future__ import annotations

import json
import os
import plistlib
import signal
import sys
from pathlib import Path
from typing import Any

import pytest

from tokenpal.ui.qt import macos_bundle


@pytest.fixture(autouse=True)
def _no_ambient_launch_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    """The marker and the latch it sets are process-wide; no test may inherit
    either from the environment or from another test."""
    monkeypatch.delenv(macos_bundle.IN_BUNDLE_ENV, raising=False)
    monkeypatch.delenv(macos_bundle.LAUNCH_CWD_ENV, raising=False)
    monkeypatch.setattr(macos_bundle, "_launched_in_bundle", False)


@pytest.fixture
def stub(tmp_path: Path) -> Path:
    """A stand-in for Python.app's executable, plus the base_prefix and
    purelib the bundle is built around."""
    exe = tmp_path / "base/Resources/Python.app/Contents/MacOS/Python"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"#not-really-a-mach-o")
    return exe


@pytest.fixture
def fake_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stub: Path,
) -> Path:
    site = tmp_path / "venv/lib/python9.9/site-packages"
    site.mkdir(parents=True)
    monkeypatch.setattr(macos_bundle.sys, "platform", "darwin")
    monkeypatch.setattr(macos_bundle.sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(
        macos_bundle.sys, "_base_executable", str(tmp_path / "base/bin/python9.9"),
    )
    monkeypatch.setattr(
        macos_bundle.sysconfig, "get_paths", lambda: {"purelib": str(site)},
    )
    return site


def _data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


# --- identity probes -------------------------------------------------


def test_framework_stub_is_none_off_darwin(
    monkeypatch: pytest.MonkeyPatch, fake_env: Path,
) -> None:
    monkeypatch.setattr(macos_bundle.sys, "platform", "linux")
    assert macos_bundle.framework_stub() is None


def test_framework_stub_finds_the_python_app_executable(fake_env: Path) -> None:
    found = macos_bundle.framework_stub()
    assert found is not None
    assert found.name == "Python"


def test_framework_stub_is_none_without_a_framework_build(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(macos_bundle.sys, "platform", "darwin")
    monkeypatch.setattr(macos_bundle.sys, "base_prefix", str(tmp_path / "nope"))
    assert macos_bundle.framework_stub() is None


def test_running_in_bundle_false_off_darwin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole relaunch path is darwin-only; the other three target
    machines must never even import AppKit."""
    monkeypatch.setattr(macos_bundle.sys, "platform", "win32")
    assert macos_bundle.running_in_bundle() is False


def test_running_in_bundle_compares_the_main_bundle_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(macos_bundle.sys, "platform", "darwin")

    for reported, expected in ((macos_bundle.BUNDLE_ID, True), ("org.python.python", False)):
        class _Bundle:
            @staticmethod
            def bundleIdentifier() -> str:  # noqa: N802
                return reported

        class _NSBundle:
            @staticmethod
            def mainBundle() -> Any:  # noqa: N802
                return _Bundle

        monkeypatch.setitem(
            sys.modules, "AppKit", type("appkit", (), {"NSBundle": _NSBundle}),
        )
        assert macos_bundle.running_in_bundle() is expected


def test_running_instances_empty_off_darwin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(macos_bundle.sys, "platform", "linux")
    assert macos_bundle.running_instances() == []


# --- ensure_bundle ---------------------------------------------------


def test_ensure_bundle_builds_the_tree_and_a_readable_info_plist(
    fake_env: Path, tmp_path: Path,
) -> None:
    data_dir = _data_dir(tmp_path)
    bundle = macos_bundle.ensure_bundle(data_dir)

    assert bundle == data_dir / "TokenPal.app"
    exe = bundle / "Contents/MacOS/TokenPal"
    assert exe.read_bytes() == b"#not-really-a-mach-o"
    assert exe.stat().st_mode & 0o111

    info = plistlib.loads((bundle / "Contents/Info.plist").read_bytes())
    assert info["CFBundleIdentifier"] == macos_bundle.BUNDLE_ID
    assert info["CFBundleName"] == "TokenPal"
    assert info["CFBundleExecutable"] == "TokenPal"
    assert info["CFBundlePackageType"] == "APPL"
    assert info["LSUIElement"] is True
    assert info["NSHighResolutionCapable"] is True
    assert info["CFBundleShortVersionString"]

    cfg = (bundle / "Contents/pyvenv.cfg").read_text()
    assert f"home = {tmp_path / 'base/bin'}" in cfg
    assert "include-system-site-packages = false" in cfg

    major, minor = sys.version_info[:2]
    site = bundle / f"Contents/lib/python{major}.{minor}/site-packages"
    assert site.is_symlink()
    assert site.readlink() == fake_env


def test_ensure_bundle_is_a_no_op_when_the_stamp_matches(
    fake_env: Path, tmp_path: Path,
) -> None:
    data_dir = _data_dir(tmp_path)
    bundle = macos_bundle.ensure_bundle(data_dir)
    exe = bundle / "Contents/MacOS/TokenPal"
    marker = exe.stat().st_mtime_ns

    assert macos_bundle.ensure_bundle(data_dir) == bundle
    assert exe.stat().st_mtime_ns == marker


def test_ensure_bundle_rebuilds_when_the_stub_changes(
    fake_env: Path, tmp_path: Path, stub: Path,
) -> None:
    """A Homebrew Python upgrade replaces the stub; the copy inside the
    bundle must follow or `open` launches a stale interpreter."""
    data_dir = _data_dir(tmp_path)
    bundle = macos_bundle.ensure_bundle(data_dir)

    stub.write_bytes(b"#a-newer-mach-o")
    bundle = macos_bundle.ensure_bundle(data_dir)
    assert (bundle / "Contents/MacOS/TokenPal").read_bytes() == b"#a-newer-mach-o"


def test_ensure_bundle_recovers_from_a_build_that_died_midway(
    fake_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed build must never leave a half-built TokenPal.app behind for
    `open` to launch: the tree is staged elsewhere and swapped in whole."""
    data_dir = _data_dir(tmp_path)

    real_copy2 = macos_bundle.shutil.copy2

    def _boom(*_args: object, **_kw: object) -> None:
        raise OSError("disk went away")

    monkeypatch.setattr(macos_bundle.shutil, "copy2", _boom)
    with pytest.raises(OSError):
        macos_bundle.ensure_bundle(data_dir)
    assert not (data_dir / "TokenPal.app").exists()
    assert list(data_dir.iterdir()) == []

    monkeypatch.setattr(macos_bundle.shutil, "copy2", real_copy2)
    bundle = macos_bundle.ensure_bundle(data_dir)
    assert (bundle / "Contents/MacOS/TokenPal").exists()


def test_ensure_bundle_replaces_a_stampless_bundle(
    fake_env: Path, tmp_path: Path,
) -> None:
    data_dir = _data_dir(tmp_path)
    bundle = macos_bundle.ensure_bundle(data_dir)
    (bundle / "Contents/.tokenpal-bundle-stamp").unlink()
    (bundle / "Contents/MacOS/TokenPal").unlink()

    bundle = macos_bundle.ensure_bundle(data_dir)
    assert (bundle / "Contents/MacOS/TokenPal").exists()
    stamp = json.loads((bundle / "Contents/.tokenpal-bundle-stamp").read_text())
    assert stamp["stub"].endswith("Python.app/Contents/MacOS/Python")


def test_ensure_bundle_raises_without_a_framework_stub(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(macos_bundle.sys, "platform", "darwin")
    monkeypatch.setattr(macos_bundle.sys, "base_prefix", str(tmp_path / "nope"))
    with pytest.raises(RuntimeError, match="non-framework interpreter"):
        macos_bundle.ensure_bundle(_data_dir(tmp_path))


# --- relaunch_in_bundle ----------------------------------------------


class _FakeProc:
    """Stands in for the ``open`` child process."""

    def __init__(self, rc: int = 0, raise_on_wait: BaseException | None = None) -> None:
        self.returncode = rc
        self._raise = raise_on_wait
        self.waits = 0

    def wait(self) -> int:
        self.waits += 1
        if self._raise is not None and self.waits == 1:
            raise self._raise
        return self.returncode


class _FakeApp:
    def __init__(self, pid: int = 4242, terminates: bool = True) -> None:
        self.pid = pid
        self._terminates = terminates
        self.force_quits = 0

    def processIdentifier(self) -> int:  # noqa: N802
        return self.pid

    def isTerminated(self) -> bool:  # noqa: N802
        # The real property is cached until the main run loop turns, which is
        # why nothing under test may consult it. Blow up if anything does.
        raise AssertionError("isTerminated() is not a liveness check here")

    def forceTerminate(self) -> None:  # noqa: N802
        self.force_quits += 1


class _FakeKill:
    """Stands in for os.kill: records signals, answers liveness probes.

    ``sig == 0`` is the existence probe; every pid in ``dead`` raises
    ProcessLookupError for it, as a departed process would.
    """

    def __init__(self, dead: set[int] | None = None) -> None:
        self.signals: list[tuple[int, int]] = []
        self.dead = dead or set()

    def __call__(self, pid: int, sig: int) -> None:
        if sig == 0:
            if pid in self.dead:
                raise ProcessLookupError(pid)
            return
        self.signals.append((pid, sig))


class _FakeOpen:
    """Captures the argv `open` would have been given, and never runs it."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.proc = _FakeProc()

    def __call__(self, cmd: list[str]) -> _FakeProc:
        self.calls.append(cmd)
        return self.proc


@pytest.fixture
def fake_open(monkeypatch: pytest.MonkeyPatch) -> _FakeOpen:
    faked = _FakeOpen()
    monkeypatch.setattr(macos_bundle.subprocess, "Popen", faked)
    return faked


def test_relaunch_composes_the_locked_open_command(
    fake_env: Path, tmp_path: Path, fake_open: _FakeOpen,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = _data_dir(tmp_path)
    monkeypatch.setattr(macos_bundle, "running_instances", list)
    monkeypatch.setattr(macos_bundle.os, "getcwd", lambda: "/repo/windoze")
    monkeypatch.setattr(macos_bundle.os, "isatty", lambda _fd: False)

    assert macos_bundle.relaunch_in_bundle(["--verbose"], data_dir) == 0

    (cmd,) = fake_open.calls
    log = str(data_dir / "logs/tokenpal-bundle.log")
    assert cmd == [
        "open", "-W",
        "--env", "TOKENPAL_IN_BUNDLE=1",
        "--env", "TOKENPAL_LAUNCH_CWD=/repo/windoze",
        "--stdout", log,
        "--stderr", log,
        str(data_dir / "TokenPal.app"),
        "--args", "-m", "tokenpal", "--verbose", "--skip-welcome",
    ]


def test_relaunch_truncates_the_log_it_redirects_into(
    fake_env: Path, tmp_path: Path, fake_open: _FakeOpen,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only non-tty launches land in the file; it must not grow across runs."""
    data_dir = _data_dir(tmp_path)
    log = data_dir / "logs/tokenpal-bundle.log"
    log.parent.mkdir(parents=True)
    log.write_text("stale output from the last headless run")

    monkeypatch.setattr(macos_bundle, "running_instances", list)
    monkeypatch.setattr(macos_bundle.os, "isatty", lambda _fd: False)
    macos_bundle.relaunch_in_bundle([], data_dir)
    assert log.read_text() == ""


def test_relaunch_uses_the_tty_when_stdout_is_a_terminal(
    fake_env: Path, tmp_path: Path, fake_open: _FakeOpen,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`./run.sh --verbose` must keep printing into the terminal it was
    started from — `open` needs a real device path, not /dev/fd/1."""
    data_dir = _data_dir(tmp_path)
    monkeypatch.setattr(macos_bundle, "running_instances", list)
    monkeypatch.setattr(macos_bundle.os, "isatty", lambda fd: fd == 1)
    monkeypatch.setattr(macos_bundle.os, "ttyname", lambda _fd: "/dev/ttys009")

    macos_bundle.relaunch_in_bundle([], data_dir)
    (cmd,) = fake_open.calls
    assert cmd[cmd.index("--stdout") + 1] == "/dev/ttys009"
    assert cmd[cmd.index("--stderr") + 1].endswith("tokenpal-bundle.log")


def test_relaunch_declines_to_start_a_second_buddy(
    fake_env: Path, tmp_path: Path, fake_open: _FakeOpen,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """The already-running check runs before any build, so a rebuild can
    never swap the tree out from under a live app."""
    data_dir = _data_dir(tmp_path)
    monkeypatch.setattr(macos_bundle, "running_instances", lambda: [_FakeApp()])

    assert macos_bundle.relaunch_in_bundle([], data_dir) == 0
    assert fake_open.calls == []
    assert not (data_dir / "TokenPal.app").exists()
    assert "already running" in capsys.readouterr().out


def test_relaunch_raises_when_open_fails(
    fake_env: Path, tmp_path: Path, fake_open: _FakeOpen,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`open -W` returns 1 when the app dies before it can attach — the
    broken-bundle case, which must reach the in-process fallback."""
    data_dir = _data_dir(tmp_path)
    monkeypatch.setattr(macos_bundle, "running_instances", list)
    monkeypatch.setattr(macos_bundle.os, "isatty", lambda _fd: False)
    fake_open.proc = _FakeProc(rc=1)

    with pytest.raises(RuntimeError, match="open exited 1"):
        macos_bundle.relaunch_in_bundle([], data_dir)


def test_ctrl_c_sends_sigint_to_the_running_buddy(
    fake_env: Path, tmp_path: Path, fake_open: _FakeOpen,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SIGINT, not terminate(): only the signal handler runs teardown() and
    flushes pending UI state to disk."""
    data_dir = _data_dir(tmp_path)
    app = _FakeApp(pid=777, terminates=True)
    instances = iter([[], [app]])
    monkeypatch.setattr(macos_bundle, "running_instances", lambda: next(instances))
    monkeypatch.setattr(macos_bundle.os, "isatty", lambda _fd: False)
    fake_open.proc = _FakeProc(raise_on_wait=KeyboardInterrupt())

    kill = _FakeKill(dead={777})
    monkeypatch.setattr(macos_bundle.os, "kill", kill)

    assert macos_bundle.relaunch_in_bundle([], data_dir) == 0
    assert kill.signals == [(777, signal.SIGINT)]
    assert app.force_quits == 0
    # open is waited on again so the parent doesn't outlive its child.
    assert fake_open.proc.waits == 2


def test_ctrl_c_force_quits_a_buddy_that_ignores_sigint(
    fake_env: Path, tmp_path: Path, fake_open: _FakeOpen,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = _data_dir(tmp_path)
    app = _FakeApp(pid=778, terminates=False)
    instances = iter([[], [app]])
    monkeypatch.setattr(macos_bundle, "running_instances", lambda: next(instances))
    monkeypatch.setattr(macos_bundle.os, "isatty", lambda _fd: False)
    # A real timeout, short enough not to stall the suite: the poll loop has
    # to actually run, or the force-quit passes for the wrong reason.
    monkeypatch.setattr(macos_bundle, "_QUIT_TIMEOUT_S", 0.2)
    monkeypatch.setattr(macos_bundle, "_QUIT_POLL_S", 0.05)
    kill = _FakeKill()  # 778 never dies
    monkeypatch.setattr(macos_bundle.os, "kill", kill)
    fake_open.proc = _FakeProc(rc=1, raise_on_wait=KeyboardInterrupt())

    # A non-zero rc after a deliberate Ctrl-C must not raise: the caller
    # would fall back to starting the buddy in-process, right after the
    # user asked for it to stop.
    assert macos_bundle.relaunch_in_bundle([], data_dir) == 0
    assert app.force_quits == 1


def test_ctrl_c_never_signals_a_pidless_instance(
    fake_env: Path, tmp_path: Path, fake_open: _FakeOpen,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NSRunningApplication reports -1 for an app with no live process, and
    os.kill(-1, SIGINT) signals every process the user owns — the launching
    shell included."""
    data_dir = _data_dir(tmp_path)
    app = _FakeApp(pid=-1)
    instances = iter([[], [app]])
    monkeypatch.setattr(macos_bundle, "running_instances", lambda: next(instances))
    monkeypatch.setattr(macos_bundle.os, "isatty", lambda _fd: False)
    fake_open.proc = _FakeProc(raise_on_wait=KeyboardInterrupt())
    kill = _FakeKill()
    monkeypatch.setattr(macos_bundle.os, "kill", kill)

    assert macos_bundle.relaunch_in_bundle([], data_dir) == 0
    assert kill.signals == []
    assert app.force_quits == 0


def test_running_in_bundle_trusts_the_env_marker_without_pyobjc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without this the child of a pyobjc-less install answers False inside
    the bundle, relaunches, and blocks on the instance it already is."""
    monkeypatch.setattr(macos_bundle.sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "AppKit", None)
    monkeypatch.delenv(macos_bundle.IN_BUNDLE_ENV, raising=False)
    assert macos_bundle.running_in_bundle() is False

    monkeypatch.setenv(macos_bundle.IN_BUNDLE_ENV, "1")
    assert macos_bundle.running_in_bundle() is True


def test_relaunch_marks_the_child_as_in_bundle(
    fake_env: Path, tmp_path: Path, fake_open: _FakeOpen,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = _data_dir(tmp_path)
    monkeypatch.setattr(macos_bundle, "running_instances", list)
    monkeypatch.setattr(macos_bundle.os, "isatty", lambda _fd: False)

    macos_bundle.relaunch_in_bundle([], data_dir)

    (cmd,) = fake_open.calls
    assert "--env" in cmd
    assert f"{macos_bundle.IN_BUNDLE_ENV}=1" in cmd


def test_redirect_log_is_owner_only(
    fake_env: Path, tmp_path: Path, fake_open: _FakeOpen,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It carries the child's whole stdout+stderr, --verbose DEBUG included,
    and every other TokenPal log is 0600."""
    data_dir = _data_dir(tmp_path)
    monkeypatch.setattr(macos_bundle, "running_instances", list)
    monkeypatch.setattr(macos_bundle.os, "isatty", lambda _fd: False)

    macos_bundle.relaunch_in_bundle([], data_dir)

    log_file = data_dir / "logs" / "tokenpal-bundle.log"
    assert log_file.stat().st_mode & 0o777 == 0o600


def test_a_failed_swap_keeps_the_bundle_that_already_worked(
    fake_env: Path, tmp_path: Path, stub: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rebuild that dies between the two renames must leave the old bundle
    in place, not delete the only working one."""
    data_dir = _data_dir(tmp_path)
    bundle = macos_bundle.ensure_bundle(data_dir)
    marker = bundle / "Contents" / "MacOS" / "TokenPal"
    original = marker.read_bytes()

    stub.write_bytes(b"#a-newer-mach-o")

    real_replace = macos_bundle.os.replace
    calls: list[int] = []

    def flaky(src: object, dst: object) -> None:
        calls.append(1)
        if len(calls) == 2:  # staging -> bundle
            raise OSError("swap failed")
        real_replace(src, dst)

    monkeypatch.setattr(macos_bundle.os, "replace", flaky)
    with pytest.raises(OSError, match="swap failed"):
        macos_bundle.ensure_bundle(data_dir)

    assert bundle.exists()
    assert marker.read_bytes() == original


# --- the predicate main() and --validate must agree on -----------------


def test_would_use_bundle_is_false_off_darwin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(macos_bundle.sys, "platform", "linux")
    assert macos_bundle.would_use_bundle({"overlay": "qt"}) is False


def test_would_use_bundle_is_false_inside_the_bundle(
    fake_env: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It answers for the launch, so the child never re-enters the gate."""
    monkeypatch.setenv(macos_bundle.IN_BUNDLE_ENV, "1")
    assert macos_bundle.would_use_bundle({"overlay": "qt"}) is False


def test_would_use_bundle_is_false_without_a_framework_stub(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """uv-managed and non-framework pyenv builds stay in-process."""
    monkeypatch.setattr(macos_bundle.sys, "platform", "darwin")
    monkeypatch.delenv(macos_bundle.IN_BUNDLE_ENV, raising=False)
    monkeypatch.setattr(macos_bundle.sys, "base_prefix", str(tmp_path / "nowhere"))
    assert macos_bundle.would_use_bundle({"overlay": "qt"}) is False


def test_would_use_bundle_follows_the_resolved_overlay(
    fake_env: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--overlay textual` must not relaunch, and --validate must not then
    tell the user to grant permissions to TokenPal."""
    from tokenpal.ui.registry import discover_overlays

    monkeypatch.delenv(macos_bundle.IN_BUNDLE_ENV, raising=False)
    discover_overlays()

    assert macos_bundle.would_use_bundle({"overlay": "textual"}) is False
    assert macos_bundle.would_use_bundle({"overlay": "console"}) is False
    assert macos_bundle.would_use_bundle({"overlay": "qt"}) is True


def test_the_launch_marker_is_consumed_not_merely_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The buddy can open a terminal (open_app allows Terminal/iTerm/Ghostty).
    A marker left in the environment would tell every `tokenpal` launched from
    that terminal it is already the bundle, so it would never relaunch and
    would go back to the shared org.python.python identity."""
    monkeypatch.setenv(macos_bundle.IN_BUNDLE_ENV, "1")
    monkeypatch.setenv(macos_bundle.LAUNCH_CWD_ENV, "/repo/windoze")

    assert macos_bundle.consume_launch_env() == "/repo/windoze"

    assert macos_bundle.IN_BUNDLE_ENV not in os.environ
    assert macos_bundle.LAUNCH_CWD_ENV not in os.environ
    # ...and this process still knows what it is.
    assert macos_bundle.running_in_bundle() is True


def test_consume_launch_env_is_quiet_when_not_relaunched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert macos_bundle.consume_launch_env() is None
    monkeypatch.setattr(macos_bundle.sys, "platform", "linux")
    assert macos_bundle.running_in_bundle() is False


def test_a_host_without_pyobjc_does_not_relaunch(
    fake_env: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without pyobjc we can neither see a running instance nor SIGINT one,
    so relaunching would strand a buddy no terminal can reach."""
    from tokenpal.ui.registry import discover_overlays

    discover_overlays()
    monkeypatch.setattr(macos_bundle, "pyobjc_available", lambda: False)

    assert macos_bundle.bundle_unavailable_reason() == (
        "pyobjc not installed (tokenpal[macos] extra)"
    )
    assert macos_bundle.would_use_bundle({"overlay": "qt"}) is False


def test_a_non_framework_interpreter_names_its_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The population most likely to hit the parked-status-item bug is the one
    that must find an explanation in the log."""
    monkeypatch.setattr(macos_bundle.sys, "platform", "darwin")
    monkeypatch.setattr(macos_bundle.sys, "base_prefix", str(tmp_path / "nowhere"))
    reason = macos_bundle.bundle_unavailable_reason()
    assert reason is not None
    assert "non-framework interpreter" in reason


def test_a_failed_restore_still_leaves_the_old_bundle_on_disk(
    fake_env: Path, tmp_path: Path, stub: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whatever makes the swap fail usually makes the restore fail too; the
    retired tree is the last copy and must not be swept with the staging one."""
    data_dir = _data_dir(tmp_path)
    macos_bundle.ensure_bundle(data_dir)
    stub.write_bytes(b"#a-newer-mach-o")

    real_replace = macos_bundle.os.replace
    calls: list[int] = []

    def flaky(src: object, dst: object) -> None:
        calls.append(1)
        if len(calls) >= 2:  # both the swap and the restore
            raise OSError("rename failed")
        real_replace(src, dst)

    monkeypatch.setattr(macos_bundle.os, "replace", flaky)
    with pytest.raises(OSError, match="rename failed"):
        macos_bundle.ensure_bundle(data_dir)

    retired = list(data_dir.glob(".TokenPal.app.tmp-*.retired"))
    assert retired, "the last working bundle must survive somewhere"
    assert (retired[0] / "Contents/MacOS/TokenPal").exists()


def test_terminal_close_quits_the_buddy_like_ctrl_c(
    fake_env: Path, tmp_path: Path, fake_open: _FakeOpen,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The buddy is launchd-parented now, so nothing but the blocking parent
    ties it to the terminal: SIGHUP must reach the same shutdown path."""
    data_dir = _data_dir(tmp_path)
    app = _FakeApp(pid=999)
    instances = iter([[], [app]])
    monkeypatch.setattr(macos_bundle, "running_instances", lambda: next(instances))
    monkeypatch.setattr(macos_bundle.os, "isatty", lambda _fd: False)
    kill = _FakeKill(dead={999})
    monkeypatch.setattr(macos_bundle.os, "kill", kill)

    installed: dict[int, object] = {}

    def fake_signal(sig: int, handler: object) -> object:
        installed[sig] = handler
        return signal.SIG_DFL

    monkeypatch.setattr(macos_bundle.signal, "signal", fake_signal)

    def wait_then_hangup() -> None:
        if signal.SIGHUP in installed:
            installed[signal.SIGHUP](signal.SIGHUP, None)  # type: ignore[operator]

    fake_open.proc = _FakeProc()
    monkeypatch.setattr(fake_open.proc, "wait", wait_then_hangup)

    assert macos_bundle.relaunch_in_bundle([], data_dir) == 0
    assert signal.SIGHUP in installed
    assert signal.SIGTERM in installed
    assert kill.signals == [(999, signal.SIGINT)]
