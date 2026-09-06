"""Give the Qt overlay its own macOS app identity.

macOS 26 attributes a menu-bar status item to the LaunchServices coalition
the process runs in and files it under that app's bundle identifier. A
terminal-spawned Python inherits the terminal's coalition and reports
``org.python.python`` — an identity shared by every Python app on the
machine, so one hostile or DENY-flagged Control Center record can park
TokenPal's item off-screen while ``QSystemTrayIcon.isVisible()`` still
says True.

The fix is a minimal app bundle in the data dir whose ``Info.plist``
claims ``com.tokenpal.app``, built around a byte copy of the Python
framework stub, launched through ``open`` so LaunchServices hands it a
fresh coalition. Venv discovery survives via ``Contents/pyvenv.cfg`` plus
a ``site-packages`` symlink, so the bundle runs the same editable install.

Every function is a no-op / False / None off darwin, except
``running_in_bundle`` once the launch marker has been consumed. Nothing
here imports Qt.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import plistlib
import shutil
import signal
import subprocess
import sys
import sysconfig
import tempfile
import time
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Any, Final

log = logging.getLogger(__name__)

BUNDLE_ID: Final = "com.tokenpal.app"
LAUNCH_CWD_ENV: Final = "TOKENPAL_LAUNCH_CWD"
IN_BUNDLE_ENV: Final = "TOKENPAL_IN_BUNDLE"

BUNDLE_NAME: Final = "TokenPal"
_STAMP_NAME: Final = ".tokenpal-bundle-stamp"
_LOG_NAME: Final = "tokenpal-bundle.log"

# Set once the launch marker has been consumed; see consume_launch_env.
_launched_in_bundle = False

# How long to wait for the child to honour SIGINT before force-quitting it.
_QUIT_TIMEOUT_S: Final = 5.0
_QUIT_POLL_S: Final = 0.1


def consume_launch_env() -> str | None:
    """Take the launch hand-off out of the environment, and return the cwd.

    Both variables must be *consumed*, not merely read. ``open`` gives the
    app the launching shell's environment, and the buddy can itself start
    a terminal (``open_app`` allows Terminal, iTerm, Ghostty, Cursor);
    that terminal and every shell in it would otherwise inherit a marker
    claiming to be the bundle, so a later ``tokenpal`` would decline to
    relaunch and quietly go back to the shared ``org.python.python``
    identity — this feature's own bug, made sticky.
    """
    global _launched_in_bundle
    if os.environ.pop(IN_BUNDLE_ENV, ""):
        _launched_in_bundle = True
    return os.environ.pop(LAUNCH_CWD_ENV, None)


def pyobjc_available() -> bool:
    """Whether the ``macos`` extra is installed."""
    if sys.platform != "darwin":
        return False
    try:
        import AppKit  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


def running_in_bundle() -> bool:
    """True when this interpreter is the bundle's own executable.

    The marker is the only check that does not need pyobjc. Without it a
    pyobjc-less install (the ``macos`` extra is optional) would answer
    False *inside* the bundle, relaunch itself, and — since ``open`` is
    deliberately run without ``-n`` — block waiting for the instance it
    already is.
    """
    if _launched_in_bundle or os.environ.get(IN_BUNDLE_ENV):
        return True
    if sys.platform != "darwin":
        return False
    try:
        from AppKit import NSBundle  # noqa: PLC0415
    except ImportError:
        return False
    try:
        return bool(NSBundle.mainBundle().bundleIdentifier() == BUNDLE_ID)
    except Exception:
        log.debug("NSBundle identity probe raised", exc_info=True)
        return False


def framework_stub() -> Path | None:
    """The framework build's ``Python.app`` executable, or None.

    Non-framework interpreters (uv-managed, pyenv without
    ``--enable-framework``) have no stub, which is the signal to stay
    in-process.
    """
    if sys.platform != "darwin":
        return None
    stub = Path(sys.base_prefix) / "Resources/Python.app/Contents/MacOS/Python"
    return stub if stub.exists() else None


def running_instances() -> list[Any]:
    """Live ``NSRunningApplication``s for ``BUNDLE_ID`` (empty off darwin)."""
    if sys.platform != "darwin":
        return []
    try:
        from AppKit import NSRunningApplication  # noqa: PLC0415
    except ImportError:
        return []
    try:
        return list(
            NSRunningApplication.runningApplicationsWithBundleIdentifier_(BUNDLE_ID)
        )
    except Exception:
        log.debug("NSRunningApplication lookup raised", exc_info=True)
        return []


def _package_version() -> str:
    try:
        return pkg_version("tokenpal")
    except PackageNotFoundError:
        return "dev"


def _bundle_stamp(stub: Path) -> dict[str, Any]:
    """Content identity of the bundle we would build right now."""
    return {
        "stub": str(stub),
        "stub_mtime_ns": stub.stat().st_mtime_ns,
        "base_executable": getattr(sys, "_base_executable", sys.executable),
        "purelib": sysconfig.get_paths()["purelib"],
        "python": list(sys.version_info[:2]),
        "version": _package_version(),
    }


def _read_stamp(path: Path) -> dict[str, Any] | None:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _populate_bundle(root: Path, stub: Path, stamp: dict[str, Any]) -> None:
    """Write a complete bundle tree into ``root`` (which must be empty)."""
    contents = root / "Contents"
    macos = contents / "MacOS"
    macos.mkdir(parents=True)

    info = {
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleName": BUNDLE_NAME,
        "CFBundleExecutable": BUNDLE_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": stamp["version"],
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
    }
    with (contents / "Info.plist").open("wb") as fh:
        plistlib.dump(info, fh)

    exe = macos / BUNDLE_NAME
    shutil.copy2(stub, exe)
    exe.chmod(0o755)

    # CPython's getpath reads pyvenv.cfg from the executable's directory
    # and then its parent, so Contents/ is the shallowest place it looks.
    home = Path(stamp["base_executable"]).parent
    (contents / "pyvenv.cfg").write_text(
        f"home = {home}\n"
        "include-system-site-packages = false\n"
        f"version = {sys.version.split()[0]}\n",
        encoding="utf-8",
    )

    major, minor = stamp["python"]
    site_dir = contents / "lib" / f"python{major}.{minor}"
    site_dir.mkdir(parents=True)
    (site_dir / "site-packages").symlink_to(stamp["purelib"])

    (contents / _STAMP_NAME).write_text(json.dumps(stamp), encoding="utf-8")


def bundle_unavailable_reason() -> str | None:
    """Why this host cannot run the buddy in its own bundle, or None.

    pyobjc is required, not optional, on the bundle path: without it we
    can neither recognise a running instance nor deliver the SIGINT that
    quits one, so a relaunch would strand a buddy no terminal can reach.
    """
    if framework_stub() is None:
        return (
            f"no Python.app stub under {sys.base_prefix} "
            "(non-framework interpreter)"
        )
    if not pyobjc_available():
        return "pyobjc not installed (tokenpal[macos] extra)"
    return None


def would_use_bundle(ui_config: dict[str, Any]) -> bool:
    """Whether this config launches the buddy inside ``TokenPal.app``.

    Answers for the *launch*, so it is false inside the bundle already.
    ``--validate`` asks it to name the app that TCC grants will attach
    to; ``main()`` asks it to decide whether to relaunch. One predicate,
    or the advice drifts from the behavior.
    """
    if sys.platform != "darwin" or running_in_bundle():
        return False
    if bundle_unavailable_reason() is not None:
        return False
    from tokenpal.ui.registry import resolve_overlay_name  # noqa: PLC0415

    return resolve_overlay_name(ui_config) == "qt"


def ensure_bundle(data_dir: Path) -> Path:
    """Build or refresh ``<data_dir>/TokenPal.app`` and return its path.

    The tree is built in a sibling temp directory and swapped in with
    ``os.replace``, so an interrupted build can never leave a half-built
    bundle for ``open`` to launch; a swap that fails after the old tree
    has been moved aside puts it back. The stamp is a content-identity
    check (stub, interpreter, purelib, package version), not crash
    recovery.

    Raises ``RuntimeError`` when there is no framework stub to build
    around; lets ``OSError`` propagate.
    """
    stub = framework_stub()
    if stub is None:
        raise RuntimeError(bundle_unavailable_reason())

    bundle = data_dir / f"{BUNDLE_NAME}.app"
    stamp = _bundle_stamp(stub)
    if _read_stamp(bundle / "Contents" / _STAMP_NAME) == stamp:
        return bundle

    data_dir.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=data_dir, prefix=f".{BUNDLE_NAME}.app.tmp-"))
    retired = staging.with_name(staging.name + ".retired")
    try:
        staging.chmod(0o755)
        _populate_bundle(staging, stub, stamp)
        if bundle.exists():
            os.replace(bundle, retired)
            try:
                os.replace(staging, bundle)
            except OSError:
                # The retired tree is the only working bundle left.
                os.replace(retired, bundle)
                raise
        else:
            os.replace(staging, bundle)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if bundle.exists():
            shutil.rmtree(retired, ignore_errors=True)
        elif retired.exists():
            # Both renames failed. Name the survivor, or it is a hidden copy
            # nothing reads and nobody knows to recover from.
            log.warning("kept the previous bundle at %s", retired)
    log.info("Built %s (%s)", bundle, BUNDLE_ID)
    return bundle


def _redirect_target(fd: int, log_path: Path) -> str:
    """Where ``open`` should point one of the child's output streams.

    The launched app is spawned by launchd, not by us, so a ``/dev/fd``
    path is meaningless to it — only a real device or file path works.
    """
    try:
        if os.isatty(fd):
            return os.ttyname(fd)
    except OSError:
        pass
    return str(log_path)


def _instance_pids() -> list[tuple[Any, int]]:
    """Live instances paired with a usable pid.

    ``NSRunningApplication.processIdentifier`` returns -1 for an app with
    no process, and ``os.kill(-1, ...)`` signals every process the user
    owns — the shell that launched us included.
    """
    pairs = []
    for app in running_instances():
        try:
            pid = int(app.processIdentifier())
        except (AttributeError, TypeError, ValueError):
            continue
        if pid > 0:
            pairs.append((app, pid))
    return pairs


def _alive(pid: int) -> bool:
    """Whether ``pid`` still exists.

    Not ``NSRunningApplication.isTerminated`` — its properties are only
    refreshed when the main run loop turns, and this process never turns
    one, so polling it here returns the value cached at lookup time for
    as long as we poll.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _raise_interrupt(signum: int, frame: object) -> None:
    """Route SIGHUP/SIGTERM into the same shutdown path as Ctrl-C."""
    raise KeyboardInterrupt(f"signal {signum}")


def _quit_running_instances() -> None:
    """SIGINT every live instance, force-quitting whatever outlives it.

    SIGINT and not ``terminate()``: an Apple Quit event reaches Qt as
    ``app.quit()`` and bypasses ``_shutdown`` → ``overlay.teardown()``,
    which is the only path that flushes pending UI state to disk.
    """
    instances = _instance_pids()
    for app, pid in instances:
        try:
            os.kill(pid, signal.SIGINT)
        except OSError as e:
            log.debug("SIGINT to pid %s failed: %s", pid, e)

    deadline = time.monotonic() + _QUIT_TIMEOUT_S
    survivors = list(instances)
    while survivors and time.monotonic() < deadline:
        time.sleep(_QUIT_POLL_S)
        survivors = [pair for pair in survivors if _alive(pair[1])]
    for app, pid in survivors:
        log.warning(
            "TokenPal (pid %s) did not exit within %ss — force quitting",
            pid, _QUIT_TIMEOUT_S,
        )
        app.forceTerminate()


def relaunch_in_bundle(argv: list[str], data_dir: Path) -> int:
    """Launch the bundle through LaunchServices and block until it exits.

    Returns the exit status ``main()`` should use. Raises ``RuntimeError``
    when there is no stub to build around or when ``open`` itself fails,
    so the caller can fall back to running in-process.
    """
    if running_instances():
        print("TokenPal is already running")
        return 0

    bundle = ensure_bundle(data_dir)

    log_path = data_dir / "logs" / _LOG_NAME
    stdout_target = _redirect_target(1, log_path)
    stderr_target = _redirect_target(2, log_path)
    if str(log_path) in (stdout_target, stderr_target):
        # open(1) needs a real path -- it cannot be handed our pipe, and
        # /dev/fd/N fails because launchd, not us, spawns the app. Say so,
        # or a piped `--verbose` run looks silently broken.
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.open("w").close()
        log_path.chmod(0o600)
        print(f"TokenPal output is not a terminal — writing it to {log_path}")

    cmd = [
        "open", "-W",
        "--env", f"{IN_BUNDLE_ENV}=1",
        "--env", f"{LAUNCH_CWD_ENV}={os.getcwd()}",
        "--stdout", stdout_target,
        "--stderr", stderr_target,
        str(bundle),
        "--args", "-m", "tokenpal", *argv, "--skip-welcome",
    ]
    log.info("Launching %s via open", bundle)
    proc = subprocess.Popen(cmd)

    # The buddy is launchd-parented now, so the blocking parent is the only
    # thing tying it to this terminal. Without these the window closing
    # (SIGHUP) or a `kill` (SIGTERM) would orphan a buddy the terminal can no
    # longer reach, still writing to a tty it no longer owns.
    previous = {
        sig: signal.signal(sig, _raise_interrupt)
        for sig in (signal.SIGHUP, signal.SIGTERM)
    }
    # subprocess.run() would SIGKILL open inside its own KeyboardInterrupt
    # handling, before we get a chance to hand the signal to the child.
    try:
        proc.wait()
    except KeyboardInterrupt:
        with contextlib.suppress(KeyboardInterrupt):
            _quit_running_instances()
            proc.wait()
        return 0
    finally:
        for sig, handler in previous.items():
            if handler is not None:
                signal.signal(sig, handler)

    # open -W returns 0 even when the app exits non-zero, and 1 when the
    # app dies before open can attach — which is exactly a broken bundle.
    if proc.returncode != 0:
        raise RuntimeError(f"open exited {proc.returncode}")
    return 0
