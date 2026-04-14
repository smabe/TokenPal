"""Network state sense — online/offline, SSID change, VPN transitions.

Privacy: the raw SSID never leaves this module. It is hashed (sha256[:16])
before any storage, log, or summary. Users opt into human-readable labels via
`[network_state] ssid_labels = { "<hash>" = "<label>" }` in config.toml.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any, ClassVar

from tokenpal.senses.base import AbstractSense, SenseReading
from tokenpal.senses.network_state.platform_impl import (
    is_online,
    read_ssid,
    vpn_active,
)
from tokenpal.senses.registry import register_sense

log = logging.getLogger(__name__)


def hash_ssid(ssid: str) -> str:
    return hashlib.sha256(ssid.encode("utf-8")).hexdigest()[:16]


def get_current_ssid_hash() -> str | None:
    """Read the current wifi SSID and return its hash.

    Public helper for callers (like /wifi label) that need the hash without
    ever touching the raw SSID. Returns None when there is no wifi connection
    or the platform shim is unavailable.
    """
    from tokenpal.senses.network_state.platform_impl import read_ssid
    raw = read_ssid()
    return hash_ssid(raw) if raw else None


def _label_for(hashed: str, labels: dict[str, str]) -> str:
    alias = labels.get(hashed)
    if alias:
        return alias
    return f"unknown wifi ({hashed[:6]}\u2026)"


@register_sense
class NetworkStateSense(AbstractSense):
    sense_name: ClassVar[str] = "network_state"
    platforms: ClassVar[tuple[str, ...]] = ("windows", "darwin", "linux")
    priority: ClassVar[int] = 100
    poll_interval_s: ClassVar[float] = 15.0
    reading_ttl_s: ClassVar[float] = 300.0

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        labels_cfg = config.get("ssid_labels") or {}
        self._labels: dict[str, str] = {
            str(k): str(v) for k, v in labels_cfg.items()
        }
        self._prev: dict[str, Any] | None = None

    async def setup(self) -> None:
        pass

    async def poll(self) -> SenseReading | None:
        # Platform shims do blocking I/O (subprocess, socket). Offload to keep
        # the brain loop responsive — worst case otherwise is ~2.5s per poll.
        online, vpn = await asyncio.gather(
            asyncio.to_thread(is_online),
            asyncio.to_thread(vpn_active),
        )
        ssid_raw = await asyncio.to_thread(read_ssid) if online else None
        ssid_hash = hash_ssid(ssid_raw) if ssid_raw else None

        curr = {"online": online, "ssid_hash": ssid_hash, "vpn": vpn}
        prev = self._prev
        self._prev = curr

        if prev is None:
            return None

        transitions: list[str] = []
        changed_from_bits: list[str] = []

        if online != prev["online"]:
            transitions.append("back online" if online else "lost network connection")
            changed_from_bits.append("offline" if online else "online")

        if ssid_hash != prev["ssid_hash"]:
            prev_hash = prev["ssid_hash"]
            if prev_hash is None and ssid_hash is not None:
                transitions.append(
                    f"wifi joined: {_label_for(ssid_hash, self._labels)}"
                )
            elif ssid_hash is None:
                transitions.append("wifi dropped")
                changed_from_bits.append(_label_for(prev_hash, self._labels))
            else:
                transitions.append(
                    f"switched wifi to {_label_for(ssid_hash, self._labels)}"
                )
                changed_from_bits.append(_label_for(prev_hash, self._labels))

        if vpn != prev["vpn"]:
            transitions.append("VPN up" if vpn else "VPN down")
            changed_from_bits.append("VPN down" if vpn else "VPN up")

        if not transitions:
            return None

        return self._reading(
            data=curr,
            summary=", ".join(transitions),
            confidence=3.0 if not online else 2.0,
            changed_from=", ".join(changed_from_bits),
        )

    async def teardown(self) -> None:
        pass
