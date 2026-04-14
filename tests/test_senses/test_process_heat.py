"""Tests for the process_heat sense."""

from __future__ import annotations

from unittest.mock import patch

from tokenpal.senses.process_heat.sense import ProcessHeatSense, _friendly_name


class _FakeProc:
    def __init__(self, name: str, cpu: float) -> None:
        self.info = {"name": name, "cpu_percent": cpu}


def _make() -> ProcessHeatSense:
    return ProcessHeatSense({})


def _patch(cpu: float, procs: list[_FakeProc]):
    return patch.multiple(
        "tokenpal.senses.process_heat.sense.psutil",
        cpu_percent=lambda interval=None: cpu,
        process_iter=lambda attrs=None: procs,
    )


def test_friendly_name_strips_helper_suffix() -> None:
    assert _friendly_name("Slack Helper (Renderer)") == "Slack"
    assert _friendly_name("Code Helper (GPU)") == "Code"


def test_friendly_name_passthrough() -> None:
    assert _friendly_name("python3") == "python3"


async def test_below_threshold_silent() -> None:
    sense = _make()
    with _patch(50.0, [_FakeProc("python", 30.0)]):
        r = await sense.poll()
    assert r is None


async def test_requires_sustained_high_cpu() -> None:
    sense = _make()
    # First hot poll — should wait for sustained window before emitting
    with _patch(95.0, [_FakeProc("python", 90.0)]):
        r = await sense.poll()
    assert r is None  # hot_since just started


async def test_fires_after_sustained(monkeypatch) -> None:
    sense = _make()
    # Pretend first hot poll happened 30s ago
    import tokenpal.senses.process_heat.sense as mod
    t = [1000.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: t[0])

    with _patch(95.0, [_FakeProc("python", 90.0)]):
        assert await sense.poll() is None
        t[0] += 30
        r = await sense.poll()

    assert r is not None
    assert "python" in r.summary
    assert r.data["event"] == "hot"


async def test_sensitive_app_name_scrubbed(monkeypatch) -> None:
    sense = _make()
    import tokenpal.senses.process_heat.sense as mod
    t = [1000.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: t[0])

    with _patch(95.0, [_FakeProc("1password", 85.0)]):
        await sense.poll()
        t[0] += 30
        r = await sense.poll()

    assert r is not None
    assert "1password" not in r.summary.lower()
    assert "something's working hard" in r.summary
    assert r.data["top_process"] is None


async def test_electron_family_aggregated(monkeypatch) -> None:
    sense = _make()
    import tokenpal.senses.process_heat.sense as mod
    t = [1000.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: t[0])

    procs = [
        _FakeProc("Slack", 20.0),
        _FakeProc("Slack Helper (Renderer)", 30.0),
        _FakeProc("Slack Helper (GPU)", 25.0),
        _FakeProc("python", 10.0),
    ]
    with _patch(95.0, procs):
        await sense.poll()
        t[0] += 30
        r = await sense.poll()

    assert r is not None
    assert "Slack" in r.summary
    # Aggregated 20+30+25 = 75 > python's 10
    assert r.data["top_process"] == "Slack"


async def test_cool_emits_clear(monkeypatch) -> None:
    sense = _make()
    import tokenpal.senses.process_heat.sense as mod
    t = [1000.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: t[0])

    with _patch(95.0, [_FakeProc("python", 90.0)]):
        await sense.poll()
        t[0] += 30
        await sense.poll()  # fires hot

    with _patch(20.0, [_FakeProc("python", 10.0)]):
        r = await sense.poll()
    assert r is not None
    assert r.data["event"] == "cool"


async def test_kernel_tasks_skipped(monkeypatch) -> None:
    sense = _make()
    import tokenpal.senses.process_heat.sense as mod
    t = [1000.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: t[0])

    procs = [
        _FakeProc("kernel_task", 90.0),  # should be ignored
        _FakeProc("python", 30.0),
    ]
    with _patch(95.0, procs):
        await sense.poll()
        t[0] += 30
        r = await sense.poll()

    assert r is not None
    assert r.data["top_process"] == "python"
