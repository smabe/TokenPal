"""Platform shims for reading the current Wi-Fi SSID and default-route iface.

The raw SSID is considered sensitive — callers MUST hash it for storage/logging
and only surface user-labeled aliases to the UI. No function in this module
logs the SSID value.
"""

from __future__ import annotations

import logging
import shutil
import socket
import subprocess

import psutil

from tokenpal.util.platform import current_platform

log = logging.getLogger(__name__)

_CMD_TIMEOUT_S = 2.0


def _run(cmd: list[str]) -> str | None:
    """Run a short platform command, return stdout or None on any failure.

    Never logs stdout (SSID could be inside it).
    """
    if not shutil.which(cmd[0]):
        return None
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_CMD_TIMEOUT_S,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def read_ssid() -> str | None:
    plat = current_platform()
    if plat == "darwin":
        return _read_ssid_darwin()
    if plat == "windows":
        return _read_ssid_windows()
    if plat == "linux":
        return _read_ssid_linux()
    return None


def _read_ssid_darwin() -> str | None:
    raw = _run(["networksetup", "-getairportnetwork", "en0"])
    if not raw:
        return None
    # Output: "Current Wi-Fi Network: <ssid>" — only last colon-split matters.
    if ":" not in raw:
        return None
    _, _, ssid = raw.strip().partition(":")
    ssid = ssid.strip()
    return ssid or None


def _read_ssid_windows() -> str | None:
    raw = _run(["netsh", "wlan", "show", "interfaces"])
    if not raw:
        return None
    for line in raw.splitlines():
        stripped = line.strip()
        # "SSID" alone, not "BSSID". Check prefix + colon.
        if stripped.lower().startswith("ssid") and ":" in stripped:
            key, _, value = stripped.partition(":")
            if key.strip().lower() == "ssid":
                ssid = value.strip()
                return ssid or None
    return None


def _read_ssid_linux() -> str | None:
    raw = _run(["iwgetid", "-r"])
    if not raw:
        return None
    ssid = raw.strip()
    return ssid or None


def is_online() -> bool:
    """Cheap connectivity probe via UDP socket (no packets actually sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.5)
            s.connect(("1.1.1.1", 53))
            return True
    except OSError:
        return False


_VPN_PREFIXES = ("utun", "tun", "tap", "wg", "ppp", "ipsec")


def vpn_active() -> bool:
    """Best-effort VPN detection via interface naming.

    Heuristic — may false-positive on systems that use utun for other reasons
    (iCloud Private Relay creates utun on macOS, for example). Callers should
    treat this as ambient signal, not ground truth.
    """
    try:
        stats = psutil.net_if_stats()
    except Exception:
        return False
    for name, st in stats.items():
        if not st.isup:
            continue
        if any(name.startswith(p) for p in _VPN_PREFIXES):
            return True
    return False
