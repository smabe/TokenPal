"""LLMInitiatedRoller - M3 of the idle-tool-rolls feature (issue #33).

Layered on top of the deterministic M1+M2 path. During a freeform tick, if
M3 is enabled, the LLM is offered a curated 9-tool flavor catalog and may
choose to call one. One tool call max per fire; second turn (the riff) is
the existing `_generate_tool_riff` path so personality + filter pipeline
are reused.

Cross-path cooldown is asymmetric on purpose. A deterministic fire of
`moon_phase` blocks M3 `moon_phase` for the rule's cooldown window via
the shared `FireTracker.last_by_tool`, but an M3 fire of
`word_of_the_day` does NOT block deterministic `coffee_break` (which
reads `last_by_rule`, not `last_by_tool`). M3 is the conservative path.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Mapping
from typing import Any

from tokenpal.actions.base import AbstractAction
from tokenpal.actions.invoker import ToolInvoker
from tokenpal.brain.idle_rules import IdleToolContext
from tokenpal.brain.idle_tools import FireTracker, IdleFireResult
from tokenpal.config.schema import IdleToolsConfig
from tokenpal.llm.base import AbstractLLMBackend

log = logging.getLogger(__name__)

# Curated subset. The LLM never sees the full action registry.
M3_CATALOG: tuple[str, ...] = (
    "word_of_the_day",
    "joke_of_the_day",
    "on_this_day",
    "moon_phase",
    "random_fact",
    "trivia_question",
    "weather_forecast_week",
    "sunrise_sunset",
    "memory_query",
)

# Subset of M3_CATALOG that requires web_fetches consent.
M3_NEEDS_WEB: frozenset[str] = frozenset({
    "word_of_the_day",
    "joke_of_the_day",
    "on_this_day",
    "random_fact",
    "trivia_question",
    "weather_forecast_week",
})

# Per-tool cool-off window. Mirrors the tightest deterministic rule cooldown
# for the same tool, so a deterministic fire of moon_phase blocks M3
# moon_phase for 24h.
PER_TOOL_COOLOFF_S: Mapping[str, float] = {
    "word_of_the_day": 12 * 3600,
    "joke_of_the_day": 12 * 3600,
    "on_this_day": 18 * 3600,
    "moon_phase": 24 * 3600,
    "random_fact": 1 * 3600,
    "trivia_question": 2 * 3600,
    "weather_forecast_week": 6 * 3600,
    "sunrise_sunset": 24 * 3600,
    "memory_query": 3 * 3600,
}

# Circuit-breaker threshold: same tool picked N times in a row triggers a
# per-tool cool-off. Lands in M3.3 telemetry tuning; the constants live here
# for visibility.
CONSECUTIVE_PICK_LIMIT: int = 3
CIRCUIT_COOLOFF_S: float = 2 * 3600

# memory_query has a required `metric` enum. If the LLM omits it, fall back
# to the lowest-privacy probe (matches the deterministic floor in
# tokenpal/brain/idle_tools.py:_MEMORY_RECALL_METRICS).
MEMORY_QUERY_DEFAULT_METRIC: str = "session_count_today"
MEMORY_QUERY_VALID_METRICS: frozenset[str] = frozenset({
    "time_in_app", "switches_per_hour", "streaks", "session_count_today",
})

_PICKER_RULES: str = (
    "You are TokenPal's idle thought-tool picker.\n"
    "Most ticks you should NOT call any tool - only call one when the\n"
    "current moment genuinely benefits from a fresh fact.\n"
    "\n"
    "Rules:\n"
    "1) Pick at most ONE tool.\n"
    "2) Do NOT pick a tool just because it's available.\n"
    "3) Do NOT call a tool whose output you used in the last 30 minutes.\n"
    "4) If unsure, return no tool call.\n"
)


class LLMInitiatedRoller:
    """Asks the LLM to optionally pick one flavor tool during a freeform tick."""

    def __init__(
        self,
        config: IdleToolsConfig,
        actions: Mapping[str, AbstractAction],
        llm: AbstractLLMBackend,
        tracker: FireTracker,
        invoker: ToolInvoker | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._config = config
        self._actions = actions
        self._llm = llm
        self._tracker = tracker
        self._invoker = invoker or ToolInvoker()
        self._rng = rng or random.Random()
        self._last_skip_reason: str | None = None

    async def maybe_fire(self, ctx: IdleToolContext) -> IdleFireResult | None:
        """Decide, ask LLM, optionally invoke one tool. Returns None on decline.

        Skip reasons log once at DEBUG until the reason changes, so a long
        cooldown doesn't tile the log with countdown lines.
        """
        if not self._config.llm_initiated_enabled:
            self._log_skip("disabled", "m3 skip: config.llm_initiated_enabled=False")
            return None

        now = time.monotonic()
        tr = self._tracker

        # M3-specific cooldown (separate from deterministic global cooldown).
        if (
            tr.m3_last_fire is not None
            and now - tr.m3_last_fire < self._config.llm_initiated_cooldown_s
        ):
            remaining = self._config.llm_initiated_cooldown_s - (now - tr.m3_last_fire)
            self._log_skip("cooldown", "m3 skip: m3 cooldown, %.0fs remaining", remaining)
            return None

        # M3-specific rolling-hour cap.
        cutoff = now - 3600.0
        while tr.m3_recent_fires and tr.m3_recent_fires[0] < cutoff:
            tr.m3_recent_fires.popleft()
        if len(tr.m3_recent_fires) >= self._config.llm_initiated_max_per_hour:
            self._log_skip(
                "m3_ratecap",
                "m3 skip: rate cap (%d m3 fires/h, max=%d)",
                len(tr.m3_recent_fires), self._config.llm_initiated_max_per_hour,
            )
            return None

        # Shared global cap with deterministic - M3 fires count toward
        # IdleToolsConfig.max_per_hour so noise stays bounded overall.
        while tr.recent_fires and tr.recent_fires[0] < cutoff:
            tr.recent_fires.popleft()
        if len(tr.recent_fires) >= self._config.max_per_hour:
            self._log_skip("shared_ratecap", "m3 skip: shared global rate cap reached")
            return None

        tool_specs = self._build_tool_specs(now, ctx)
        if not tool_specs:
            self._log_skip(
                "no_tools", "m3 skip: no tools eligible after consent + cool-off filter",
            )
            return None

        prompt = self._build_picker_prompt(ctx)
        try:
            resp = await self._llm.generate_with_tools(
                messages=[{"role": "user", "content": prompt}],
                tools=tool_specs,
            )
        except Exception:
            log.debug("m3 skip: generate_with_tools crashed", exc_info=True)
            return None

        if not resp.tool_calls:
            log.debug("m3 decline: model returned no tool_calls")
            return None

        tc = resp.tool_calls[0]
        if tc.name not in M3_CATALOG:
            log.debug("m3 skip: out-of-catalog tool %r", tc.name)
            return None
        action = self._actions.get(tc.name)
        if action is None:
            log.debug("m3 skip: action %r not loaded", tc.name)
            return None

        args = self._sanitize_args(tc.name, tc.arguments)
        start = time.monotonic()
        try:
            result = await self._invoker.invoke(action, args)
        except Exception:
            log.debug("m3 skip: invoke %r crashed", tc.name, exc_info=True)
            return None
        latency_ms = (time.monotonic() - start) * 1000.0

        if not result.success or not result.output:
            log.debug("m3 skip: %r returned no usable output", tc.name)
            return None

        self._record_fire(tc.name, now)
        self._last_skip_reason = None

        return IdleFireResult(
            rule_name=f"llm_initiated:{tc.name}",
            tool_name=tc.name,
            tool_output=result.output,
            framing="React to this fresh detail in one in-character line.",
            latency_ms=latency_ms,
            success=True,
        )

    def _log_skip(self, key: str, msg: str, *args: object) -> None:
        if self._last_skip_reason == key:
            return
        self._last_skip_reason = key
        log.debug(msg, *args)

    def _build_tool_specs(
        self, now: float, ctx: IdleToolContext,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for name in M3_CATALOG:
            if name in M3_NEEDS_WEB and not ctx.consent_web_fetches:
                continue
            action = self._actions.get(name)
            if action is None:
                continue
            last = self._tracker.last_by_tool.get(name)
            cooloff = PER_TOOL_COOLOFF_S.get(name, 3600.0)
            if last is not None and (now - last) < cooloff:
                continue
            # Circuit breaker: if M3 picked the same tool N times in a row,
            # block it for CIRCUIT_COOLOFF_S even if its per-tool window is
            # otherwise expired.
            if (
                self._tracker.consecutive_same_tool.get(name, 0)
                >= CONSECUTIVE_PICK_LIMIT
            ):
                if last is not None and (now - last) < CIRCUIT_COOLOFF_S:
                    continue
                self._tracker.consecutive_same_tool[name] = 0
            out.append(action.to_tool_spec())
        return out

    def _build_picker_prompt(self, ctx: IdleToolContext) -> str:
        # Static rules first for prompt-cache stability; volatile context last.
        return (
            f"{_PICKER_RULES}\n"
            f"[Mood] {ctx.mood}   "
            f"[Time-of-day] {ctx.now.strftime('%H:%M')}\n"
            f"[Current moment] "
            f"{', '.join(sorted(ctx.active_readings)) or 'quiet'}\n"
        )

    def _sanitize_args(
        self, tool_name: str, arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if tool_name == "memory_query":
            metric = arguments.get("metric")
            if metric not in MEMORY_QUERY_VALID_METRICS:
                return {"metric": MEMORY_QUERY_DEFAULT_METRIC}
        return arguments

    def _record_fire(self, tool_name: str, now: float) -> None:
        tr = self._tracker
        # Per-tool history for cross-path cooldown + circuit breaker.
        tr.last_by_tool[tool_name] = now
        tr.last_any = now
        tr.recent_fires.append(now)
        tr.m3_last_fire = now
        tr.m3_recent_fires.append(now)
        # Circuit-breaker streak: increment if same as previous M3 pick,
        # else reset all other tools to 0 and start a fresh streak.
        for name in M3_CATALOG:
            if name == tool_name:
                tr.consecutive_same_tool[name] = (
                    tr.consecutive_same_tool.get(name, 0) + 1
                )
            else:
                tr.consecutive_same_tool[name] = 0


