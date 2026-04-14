"""Tests for the network_state sense — focus on SSID privacy + transitions."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import tokenpal.senses.network_state.sense as ns_sense
from tokenpal.senses.network_state.sense import (
    NetworkStateSense,
    _label_for,
    hash_ssid,
)


def _patch(online: bool, ssid: str | None, vpn: bool):
    return patch.multiple(
        ns_sense,
        is_online=lambda: online,
        read_ssid=lambda: ssid,
        vpn_active=lambda: vpn,
    )


def test_hash_is_deterministic_and_truncated() -> None:
    h = hash_ssid("home wifi")
    assert len(h) == 16
    assert h == hash_ssid("home wifi")
    assert h != hash_ssid("coffee shop")


def test_label_for_falls_back_to_hash_prefix() -> None:
    h = hash_ssid("unknown")
    label = _label_for(h, {})
    assert h[:6] in label
    assert "unknown wifi" in label


def test_label_for_uses_config_map() -> None:
    h = hash_ssid("home")
    label = _label_for(h, {h: "home"})
    assert label == "home"


async def test_first_poll_silent() -> None:
    sense = NetworkStateSense({})
    with _patch(online=True, ssid="home", vpn=False):
        r = await sense.poll()
    assert r is None


async def test_ssid_change_emits_label() -> None:
    home_hash = hash_ssid("home")
    work_hash = hash_ssid("work")
    sense = NetworkStateSense({"ssid_labels": {home_hash: "home", work_hash: "work"}})
    with _patch(online=True, ssid="home", vpn=False):
        await sense.poll()
    with _patch(online=True, ssid="work", vpn=False):
        r = await sense.poll()
    assert r is not None
    assert "work" in r.summary
    assert "home" in r.changed_from


async def test_raw_ssid_never_in_reading() -> None:
    sense = NetworkStateSense({})  # no labels → generic "unknown wifi (xxx…)"
    with _patch(online=True, ssid="SuperSecretSSID", vpn=False):
        await sense.poll()
    with _patch(online=True, ssid="OtherSecretSSID", vpn=False):
        r = await sense.poll()
    assert r is not None
    assert "SuperSecretSSID" not in r.summary
    assert "OtherSecretSSID" not in r.summary
    assert "SuperSecretSSID" not in r.changed_from
    assert r.data.get("ssid_hash") == hash_ssid("OtherSecretSSID")
    assert "SuperSecretSSID" not in str(r.data)


async def test_offline_transition() -> None:
    sense = NetworkStateSense({})
    with _patch(online=True, ssid="home", vpn=False):
        await sense.poll()
    with _patch(online=False, ssid=None, vpn=False):
        r = await sense.poll()
    assert r is not None
    assert "lost" in r.summary or "offline" in r.summary or "dropped" in r.summary
    assert r.data["online"] is False


async def test_vpn_transition() -> None:
    sense = NetworkStateSense({})
    with _patch(online=True, ssid="home", vpn=False):
        await sense.poll()
    with _patch(online=True, ssid="home", vpn=True):
        r = await sense.poll()
    assert r is not None
    assert "VPN" in r.summary


async def test_no_transitions_returns_none() -> None:
    sense = NetworkStateSense({})
    with _patch(online=True, ssid="home", vpn=False):
        await sense.poll()
    with _patch(online=True, ssid="home", vpn=False):
        r = await sense.poll()
    assert r is None


def test_no_raw_ssid_logged_in_modules() -> None:
    """Lint-test: neither module may pass raw ssid (or subprocess stdout) into a log call."""
    import re

    from tokenpal.senses.network_state import platform_impl

    for module in (ns_sense, platform_impl):
        source = Path(module.__file__).read_text()
        for match in re.finditer(r"log\.\w+\([^\n]+", source):
            call = match.group(0)
            assert "ssid_raw" not in call, f"Raw SSID in log call: {call}"
            assert "ssid" not in call or "hash" in call, (
                f"Possible raw SSID leak in log call: {call}"
            )
