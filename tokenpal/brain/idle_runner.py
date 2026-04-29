"""IdleToolRunner — owns the idle-tool emission path called at brain post-pass."""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from tokenpal.brain.idle_tools import IdleFireResult, build_context
from tokenpal.brain.personality import SENSITIVE_APPS
from tokenpal.config.consent import Category, has_consent

if TYPE_CHECKING:
    from tokenpal.brain.orchestrator import Brain

log = logging.getLogger(__name__)


class IdleToolRunner:
    def __init__(self, brain: Brain) -> None:
        self._brain = brain

    def is_eligible(self) -> bool:
        """Hard gates only. Idle rolls self-govern via per-rule + global
        cooldowns and rate cap — they do NOT inherit the observation-path
        forced-silence window. That window exists to stop near-dup LLM
        spam; an idle roll injects fresh tool output and is the right
        recovery from dead air, not something to suppress further.
        """
        b = self._brain
        if not b._idle_tools_config.enabled:
            return False
        if b._paused:
            return False
        if b._in_conversation:
            return False
        if b._any_long_task():
            return False
        return True

    async def maybe_run(self, snapshot: str, emitted: bool) -> None:
        if emitted or not self.is_eligible():
            return
        if not await self._maybe_fire_llm_initiated(snapshot):
            await self._maybe_fire_deterministic(snapshot)

    def build_context(self) -> Any:
        # Personalization signals — MemoryStore caches pattern_callbacks
        # for the session and both new helpers are single-query reads,
        # so pulling these every tick is cheap. Default-safe when memory
        # is None (tests, disabled config).
        b = self._brain
        daily_streak_days = 0
        install_age_days = 0
        pattern_callbacks: tuple[str, ...] = ()
        if b._memory is not None:
            try:
                daily_streak_days = b._memory.get_daily_streak_days()
                install_age_days = b._memory.get_install_age_days()
                pattern_callbacks = tuple(
                    b._memory.get_pattern_callbacks(sensitive_apps=SENSITIVE_APPS),
                )
            except Exception:
                log.debug(
                    "Personalization signal fetch failed; using defaults",
                    exc_info=True,
                )
        return build_context(
            now=datetime.now(),
            session_minutes=int(
                (time.monotonic() - b._session_started_at) / 60
            ),
            first_session_of_day=b._first_session_of_day,
            active_readings=b._context.active_readings(),
            mood=str(b._personality.mood),
            time_since_last_comment_s=time.monotonic() - b._last_comment_time,
            consent_web_fetches=has_consent(Category.WEB_FETCHES),
            daily_streak_days=daily_streak_days,
            install_age_days=install_age_days,
            pattern_callbacks=pattern_callbacks,
        )

    async def _maybe_fire_deterministic(self, snapshot: str) -> None:
        """Roll the idle-tool die; on hit, riff the result in-character."""
        b = self._brain
        if b._personality.check_sensitive_app(snapshot):
            return
        ctx = self.build_context()
        try:
            result = await b._idle_tools.maybe_fire(ctx)
        except Exception:
            log.exception("Idle tool roll crashed")
            return
        if result is None:
            return
        if result.running_bit:
            self._register_running_bit(result)
            if not result.opener_framing:
                # Silent registration — bit rides along future prompts without
                # announcing itself. Still counts as a fire for telemetry.
                self.record_fire(result, emitted=True)
                return
        await self._riff(snapshot, result)

    async def _maybe_fire_llm_initiated(self, snapshot: str) -> bool:
        """M3 (issue #33): let the LLM optionally pick a flavor tool.

        Returns True iff a tool was picked, invoked, and riffed - so the
        caller can skip the deterministic roll for this tick. Hard gates
        live here (env, mood, sensitive app); per-tool cooldowns + circuit
        breaker live in LLMInitiatedRoller.
        """
        b = self._brain
        if not b._idle_tools_config.llm_initiated_enabled:
            return False
        if os.environ.get("TOKENPAL_M3") != "1":
            return False
        if b._personality.check_sensitive_app(snapshot):
            return False
        if b._personality.mood_role in {"sleepy", "concerned"}:
            return False
        ctx = self.build_context()
        try:
            result = await b._idle_tools_m3.maybe_fire(ctx)
        except Exception:
            log.exception("M3 idle tool roll crashed")
            return False
        if result is None:
            return False
        await self._riff(snapshot, result)
        return True

    def _register_running_bit(self, fire: IdleFireResult) -> None:
        """Install the fired rule as a running bit on the personality engine."""
        try:
            framing = fire.framing.format(output=fire.tool_output)
        except (KeyError, IndexError):
            framing = fire.framing
        self._brain._personality.add_running_bit(
            tag=fire.rule_name,
            framing=framing,
            decay_s=fire.bit_decay_s,
            payload={"output": fire.tool_output},
        )

    async def _riff(self, snapshot: str, fire: IdleFireResult) -> None:
        """Compose an in-character line that weaves the tool output in."""
        b = self._brain
        # Running-bit opener uses opener_framing; one-shot rules use framing.
        framing = fire.opener_framing if fire.running_bit else fire.framing
        detail_block = fire.tool_output
        if fire.extra_outputs:
            detail_lines = [fire.tool_output]
            for tool_name, extra in fire.extra_outputs.items():
                detail_lines.append(f"({tool_name}) {extra}")
            detail_block = "\n".join(detail_lines)
        prompt = (
            f"{b._personality.build_freeform_prompt()}\n\n"
            f"[Current moment:]\n{snapshot}\n\n"
            f"[Fresh detail to weave in, in-character:]\n{detail_block}\n\n"
            f"[How to frame it:]\n{framing}\n"
        )
        try:
            if b._status_callback:
                b._status_callback("thinking...")
            response = await b._llm.generate(
                prompt,
                target_latency_s=b._budgets.idle_tool,
                min_tokens=b._min_tokens.idle_tool,
            )
            b._push_status()
        except Exception:
            log.exception("Idle-tool riff generation failed")
            b._push_status()
            self.record_fire(fire, emitted=False)
            return

        filtered = b._personality.filter_response(response.text)
        filter_reason = b._personality.last_filter_reason.value
        if filtered and b._is_near_duplicate(filtered):
            log.info(
                "TokenPal (idle-tool %s suppressed near-duplicate): %s",
                fire.rule_name, filtered,
            )
            # Skip _handle_suppressed_output — the observation-path silence
            # window would starve freeform + drift nudges for 2 minutes on
            # one bad framing. The rule's own cooldown is enough.
            filter_reason = "near_duplicate"
            filtered = ""

        if not filtered:
            log.debug(
                "Idle-tool riff filtered out (%s): %r",
                filter_reason or "empty",
                response.text[:80] if response.text else "",
            )
            self.record_fire(
                fire, emitted=False, filter_reason=filter_reason or "empty",
            )
            return

        log.info(
            "TokenPal (idle-tool %s -> %s): %s (%.0fms)",
            fire.rule_name, fire.tool_name, filtered, response.latency_ms,
        )
        b._emit_comment(filtered)
        b._recent_outputs.append(filtered)
        self.record_fire(fire, emitted=True)

    def record_fire(
        self,
        fire: IdleFireResult,
        *,
        emitted: bool,
        filter_reason: str = "",
    ) -> None:
        """Write a telemetry row so memory_query can surface idle-tool stats.

        filter_reason is "" on a successful emit. On a swallow it's one of
        the filter_response reasons (drifted, anchor_regurgitation,
        cross_franchise, too_short, silent_marker, near_duplicate, empty)
        so we can tune framing without tailing logs.
        """
        b = self._brain
        if b._memory is None:
            return
        source = (
            "llm_initiated"
            if fire.rule_name.startswith("llm_initiated:")
            else "deterministic"
        )
        data: dict[str, Any] = {
            "tool": fire.tool_name,
            "emitted": emitted,
            "tool_success": fire.success,
            "running_bit": fire.running_bit,
            "latency_ms": int(fire.latency_ms),
            "source": source,
        }
        if filter_reason:
            data["filter_reason"] = filter_reason
        try:
            b._memory.record_observation(
                sense_name="idle_tools",
                event_type="idle_tool_fire",
                summary=fire.rule_name,
                data=data,
            )
        except Exception:
            log.debug("idle_tool_fire telemetry write failed", exc_info=True)
