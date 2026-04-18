"""IdleToolRoller — picks and invokes a contextual tool during quiet stretches.

This is the third emission path in the brain loop, parallel to
`_generate_comment` (observation) and `_generate_freeform_comment`
(unprompted). It runs only when the commentary gate chose silence, so it
cannot inflate the comment rate — it only fills gaps that would otherwise
be dead air.

Flow, per tick:

    roller.maybe_fire(ctx)
        → enabled check
        → global cooldown + max_per_hour rate cap
        → filter M1_RULES by predicate + consent + per-rule cooldown + config toggle
        → weighted random pick
        → invoke tool (warm cache for daily evergreens, else live call)
        → return IdleFireResult | None

`IdleFireResult` is purely data — Brain is the one that turns it into a
riff prompt + emits the final in-character line. Separation of concerns:
rolling + invoking here, LLM generation + filtering over in Brain.
"""

from __future__ import annotations

import logging
import random
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tokenpal.actions.base import AbstractAction
from tokenpal.actions.invoker import ToolInvoker
from tokenpal.brain.idle_rules import (
    M1_RULES,
    IdleToolContext,
    IdleToolRule,
)
from tokenpal.config.schema import IdleToolsConfig

log = logging.getLogger(__name__)

# Tools cheap enough (and stable enough across a day) to pre-warm on
# session start, avoiding a blocking HTTP call on the idle hot path.
_DAILY_EVERGREEN_TOOLS: frozenset[str] = frozenset({
    "word_of_the_day",
    "joke_of_the_day",
    "on_this_day",
    "moon_phase",
    "sunrise_sunset",
})

# Memory-recall probes — the offline floor picks one at random per fire.
_MEMORY_RECALL_METRICS: tuple[str, ...] = (
    "time_in_app",
    "switches_per_hour",
    "streaks",
    "session_count_today",
)


@dataclass
class IdleFireResult:
    """What the roller hands back when it decides to fire."""

    rule_name: str
    tool_name: str
    tool_output: str
    framing: str
    latency_ms: float
    success: bool
    running_bit: bool = False
    bit_decay_s: float = 0.0
    # Opener announcement framing (running-bit rules only). Empty means the
    # bit is registered silently; orchestrator skips the one-line emit.
    opener_framing: str = ""
    # Chain-rule outputs keyed by tool name, excluding the primary tool.
    # Non-empty only for rules that declare extra_tool_names.
    extra_outputs: dict[str, str] = field(default_factory=dict)


@dataclass
class _CacheEntry:
    output: str
    success: bool
    fetched_at: float               # wall clock (time.time())


class IdleToolRoller:
    """Rolls a weighted die across contextual rules during quiet stretches."""

    # Evergreen cache TTL. 6 hours covers a typical workday from one warm fetch.
    _DAILY_CACHE_TTL_S: float = 6 * 3600

    def __init__(
        self,
        config: IdleToolsConfig,
        actions: Mapping[str, AbstractAction],
        rules: tuple[IdleToolRule, ...] = M1_RULES,
        rng: random.Random | None = None,
        invoker: ToolInvoker | None = None,
    ) -> None:
        self._config = config
        self._actions = actions
        self._rules = rules
        self._rng = rng or random.Random()
        self._invoker = invoker or ToolInvoker()

        # Per-rule last-fire monotonic timestamps. Initialized lazy on first
        # maybe_fire — we do not want to stamp "just fired" on startup. A
        # missing key means "never fired", so cooldown checks should let the
        # rule through on the first call regardless of process uptime.
        self._last_fire_by_rule: dict[str, float] = {}
        self._last_fire_any: float | None = None

        # Sliding window of fire times (monotonic) for max_per_hour.
        self._recent_fires: deque[float] = deque()

        # Evergreen warm cache.
        self._daily_cache: dict[str, _CacheEntry] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def warm_daily_cache(self) -> None:
        """Pre-fetch daily evergreen tools so the hot path never blocks.

        Safe to call multiple times — caller typically runs this once at
        session start and then relies on TTL refresh inside maybe_fire.
        """
        for tool_name in _DAILY_EVERGREEN_TOOLS:
            action = self._actions.get(tool_name)
            if action is None:
                continue
            await self._refresh_cache(tool_name, action)

    async def maybe_fire(self, ctx: IdleToolContext) -> IdleFireResult | None:
        """Decide whether to fire, pick a rule, invoke its tool, return result.

        Returns None if disabled, rate-capped, cooled-down, or no rule's
        predicate passes. The caller (Brain) owns LLM generation + filtering.
        """
        if not self._config.enabled:
            return None

        now = time.monotonic()

        # Global cooldown.
        if (
            self._last_fire_any is not None
            and now - self._last_fire_any < self._config.global_cooldown_s
        ):
            return None

        # Rolling-hour rate cap.
        cutoff = now - 3600.0
        while self._recent_fires and self._recent_fires[0] < cutoff:
            self._recent_fires.popleft()
        if len(self._recent_fires) >= self._config.max_per_hour:
            return None

        candidates = list(self._candidates(now, ctx))
        if not candidates:
            return None

        rule = self._weighted_pick(candidates)
        result = await self._invoke(rule, ctx)
        if result is None:
            return None

        # Record fire state regardless of tool success — a flaky API
        # shouldn't let us hammer it twice a second.
        self._last_fire_by_rule[rule.name] = now
        self._last_fire_any = now
        self._recent_fires.append(now)
        return result

    async def force_fire(
        self, rule_name: str, ctx: IdleToolContext,
    ) -> IdleFireResult | None:
        """Bypass predicates + cooldowns. Used by `/idle_tools roll`.

        Still records the fire in cooldown state so a manual roll doesn't
        trigger another automatic one 30 seconds later.
        """
        rule = next((r for r in self._rules if r.name == rule_name), None)
        if rule is None:
            return None
        result = await self._invoke(rule, ctx)
        if result is None:
            return None
        now = time.monotonic()
        self._last_fire_by_rule[rule.name] = now
        self._last_fire_any = now
        self._recent_fires.append(now)
        return result

    # ------------------------------------------------------------------
    # Introspection (used by /idle_tools list)
    # ------------------------------------------------------------------

    def rule_status(
        self, ctx: IdleToolContext,
    ) -> list[tuple[IdleToolRule, bool, str]]:
        """Return (rule, enabled_by_config, reason_if_not_eligible) per rule.

        reason_if_not_eligible is "" when the rule would pass right now.
        Helpful for a /idle_tools list view that explains why a rule isn't
        firing.
        """
        now = time.monotonic()
        result: list[tuple[IdleToolRule, bool, str]] = []
        for rule in self._rules:
            enabled = self._config.rules.get(rule.name, rule.enabled_default)
            reason = self._ineligibility_reason(rule, ctx, now, enabled)
            result.append((rule, enabled, reason))
        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _candidates(
        self, now: float, ctx: IdleToolContext,
    ) -> list[IdleToolRule]:
        out: list[IdleToolRule] = []
        for rule in self._rules:
            if not self._config.rules.get(rule.name, rule.enabled_default):
                continue
            if rule.needs_web_fetches and not ctx.consent_web_fetches:
                continue
            last = self._last_fire_by_rule.get(rule.name)
            if last is not None and now - last < rule.cooldown_s:
                continue
            if rule.tool_name not in self._actions:
                continue
            try:
                if not rule.predicate(ctx):
                    continue
            except Exception:
                # A broken predicate should not poison the whole roll.
                log.debug("Predicate raised for rule %r", rule.name, exc_info=True)
                continue
            out.append(rule)
        return out

    def _weighted_pick(self, rules: list[IdleToolRule]) -> IdleToolRule:
        weights = [max(r.weight, 0.0001) for r in rules]
        return self._rng.choices(rules, weights=weights, k=1)[0]

    async def _invoke(
        self, rule: IdleToolRule, ctx: IdleToolContext,
    ) -> IdleFireResult | None:
        primary = await self._invoke_single(rule.tool_name, rule, ctx)
        if primary is None:
            return None
        primary_output, primary_latency_ms = primary

        extras: dict[str, str] = {}
        for extra_name in rule.extra_tool_names:
            pair = await self._invoke_single(extra_name, rule, ctx)
            if pair is None:
                # Graceful degradation — a single failed chain tool doesn't
                # poison the whole monologue. The riff just has less to chew on.
                log.debug(
                    "Chain tool %r for rule %r failed; continuing without it",
                    extra_name, rule.name,
                )
                continue
            extras[extra_name] = pair[0]

        return IdleFireResult(
            rule_name=rule.name,
            tool_name=rule.tool_name,
            tool_output=primary_output,
            framing=rule.framing,
            latency_ms=primary_latency_ms,
            success=True,
            running_bit=rule.running_bit,
            bit_decay_s=rule.bit_decay_s,
            opener_framing=rule.opener_framing,
            extra_outputs=extras,
        )

    async def _invoke_single(
        self, tool_name: str, rule: IdleToolRule, ctx: IdleToolContext,
    ) -> tuple[str, float] | None:
        """Invoke one tool (primary or chain). Returns (output, latency_ms)."""
        action = self._actions.get(tool_name)
        if action is None:
            log.debug("Tool %r for rule %r not loaded", tool_name, rule.name)
            return None

        # Evergreen cache hit — shared across primary + chain tools.
        if tool_name in _DAILY_EVERGREEN_TOOLS:
            cached = self._daily_cache.get(tool_name)
            if cached and (time.time() - cached.fetched_at) < self._DAILY_CACHE_TTL_S:
                return cached.output, 0.0
            await self._refresh_cache(tool_name, action)
            cached = self._daily_cache.get(tool_name)
            if cached is None:
                return None
            return cached.output, 0.0

        arguments = self._build_arguments_for_tool(tool_name, rule, ctx)
        start = time.monotonic()
        try:
            result = await self._invoker.invoke(action, arguments)
        except Exception:
            log.debug("Idle tool invocation crashed: %r", tool_name, exc_info=True)
            return None
        latency_ms = (time.monotonic() - start) * 1000.0

        if not result.success or not result.output:
            log.debug("Idle tool %r returned no usable output", tool_name)
            return None

        return result.output, latency_ms

    def _build_arguments_for_tool(
        self, tool_name: str, rule: IdleToolRule, ctx: IdleToolContext,
    ) -> dict[str, Any]:
        """Derive tool arguments from rule + tool_name + context. Keep small."""
        if tool_name == "memory_query":
            return {"metric": self._rng.choice(_MEMORY_RECALL_METRICS)}
        return {}

    async def _refresh_cache(
        self, tool_name: str, action: AbstractAction,
    ) -> None:
        try:
            result = await self._invoker.invoke(action, {})
        except Exception:
            log.debug("Warm cache fetch failed for %r", tool_name, exc_info=True)
            return
        if not result.success or not result.output:
            log.debug("Warm cache fetch empty for %r: %r", tool_name, result.output)
            return
        self._daily_cache[tool_name] = _CacheEntry(
            output=result.output,
            success=result.success,
            fetched_at=time.time(),
        )

    def _ineligibility_reason(
        self, rule: IdleToolRule, ctx: IdleToolContext,
        now: float, enabled: bool,
    ) -> str:
        if not enabled:
            return "disabled in config"
        if rule.needs_web_fetches and not ctx.consent_web_fetches:
            return "web_fetches consent missing"
        last = self._last_fire_by_rule.get(rule.name)
        if last is not None:
            remaining = rule.cooldown_s - (now - last)
            if remaining > 0:
                return f"cooldown: {int(remaining)}s left"
        if rule.tool_name not in self._actions:
            return f"tool '{rule.tool_name}' not loaded"
        try:
            if not rule.predicate(ctx):
                return "predicate not met"
        except Exception:
            return "predicate raised"
        return ""


def build_context(
    *,
    now: datetime,
    session_minutes: int,
    first_session_of_day: bool,
    active_readings: Mapping[str, Any],
    mood: str,
    time_since_last_comment_s: float,
    consent_web_fetches: bool,
) -> IdleToolContext:
    """Convenience constructor used by Brain; derives weather_summary."""
    weather = active_readings.get("weather")
    weather_summary = ""
    if weather is not None:
        weather_summary = str(getattr(weather, "summary", "") or "")
    return IdleToolContext(
        now=now,
        session_minutes=session_minutes,
        first_session_of_day=first_session_of_day,
        active_readings=active_readings,
        mood=mood,
        weather_summary=weather_summary,
        time_since_last_comment_s=time_since_last_comment_s,
        consent_web_fetches=consent_web_fetches,
    )
