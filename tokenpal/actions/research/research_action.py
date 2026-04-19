"""research — inline multi-step research as an LLM-callable tool.

Wraps the existing ResearchRunner so the conversation model can trigger
a full plan->search->fetch->synthesize pipeline as a single tool call.
The synthesized answer with citations is returned in the tool result.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from tokenpal.actions.base import AbstractAction, ActionResult, RateLimit
from tokenpal.actions.registry import register_action
from tokenpal.actions.research.fetch_url import fetch_and_extract
from tokenpal.brain.research import ResearchRunner, ResearchSession
from tokenpal.brain.stop_reason import ResearchStopReason
from tokenpal.config.consent import Category, has_consent
from tokenpal.config.schema import CloudLLMConfig, ResearchConfig
from tokenpal.config.secrets import get_cloud_key
from tokenpal.llm.base import AbstractLLMBackend
from tokenpal.llm.cloud_backend import CloudBackend, CloudBackendError

log = logging.getLogger(__name__)


@register_action
class ResearchAction(AbstractAction):
    action_name = "research"
    description = (
        "Deep research for comparison, recommendation, or 'best of' "
        "questions. Plans multiple search queries, reads several pages, "
        "and returns a synthesized answer with numbered citations. "
        "Always use this for questions like 'best X', 'which X should I "
        "buy', 'compare X vs Y', or anything that needs weighing "
        "multiple sources."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The research question to investigate.",
            },
        },
        "required": ["question"],
    }
    platforms: ClassVar[tuple[str, ...]] = ("windows", "darwin", "linux")
    safe: ClassVar[bool] = True
    requires_confirm: ClassVar[bool] = False
    rate_limit: ClassVar[RateLimit | None] = RateLimit(max_calls=2, window_s=120.0)

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._llm: AbstractLLMBackend | None = None
        self._research_config: ResearchConfig | None = None
        self._cloud_config: CloudLLMConfig | None = None

    async def execute(self, **kwargs: Any) -> ActionResult:
        question = (kwargs.get("question") or "").strip()
        if not question:
            return ActionResult(output="research: empty question", success=False)
        if not has_consent(Category.RESEARCH_MODE):
            return ActionResult(
                output="research: research_mode consent not granted. Run /consent.",
                success=False,
            )
        if not has_consent(Category.WEB_FETCHES):
            return ActionResult(
                output="research: web_fetches consent not granted. Run /consent.",
                success=False,
            )
        if self._llm is None:
            return ActionResult(
                output="research: LLM backend not wired up", success=False,
            )

        cfg = self._research_config or ResearchConfig()
        cloud_backend = _build_cloud_backend(self._cloud_config)
        cloud_plan = bool(
            cloud_backend and self._cloud_config
            and getattr(self._cloud_config, "research_plan", False)
        )
        runner = ResearchRunner(
            llm=self._llm,
            fetch_url=fetch_and_extract,
            log_callback=lambda s: log.info("research: %s", s),
            max_queries=cfg.max_queries,
            max_fetches=cfg.max_fetches,
            token_budget=cfg.token_budget,
            per_search_timeout_s=cfg.per_search_timeout_s,
            per_fetch_timeout_s=cfg.per_fetch_timeout_s,
            synth_thinking=cfg.synth_thinking,
            cloud_backend=cloud_backend,
            cloud_plan=cloud_plan,
        )

        try:
            session = await runner.run(question)
        except Exception:
            log.exception("research: pipeline crashed")
            return ActionResult(output="research: pipeline crashed", success=False)

        if not session.is_complete or not session.answer:
            reason = session.stopped_reason or ResearchStopReason.CRASHED
            return ActionResult(
                output=f"research: incomplete ({reason})", success=False,
            )

        display_urls = [
            (f"[{s.number}] {s.title}" if s.title else f"[{s.number}] {s.url}", s.url)
            for s in session.sources
            if s.url
        ]
        return ActionResult(
            output=_format_result(session),
            success=True,
            display_urls=display_urls or None,
        )


def _build_cloud_backend(cfg: CloudLLMConfig | None) -> CloudBackend | None:
    """Construct an Anthropic-backed synth backend when /cloud is enabled and
    a key is on disk. Logs the reason on every None-return so a silent local
    fallback is always traceable.
    """
    if cfg is None:
        log.info("cloud: config not injected (None) - using local synth")
        return None
    if not cfg.enabled:
        log.info("cloud: disabled in config - using local synth")
        return None
    if not cfg.research_synth:
        log.info("cloud: research_synth flag off - using local synth")
        return None
    key = get_cloud_key()
    if not key:
        log.info("cloud: enabled but no API key stored - using local synth")
        return None
    try:
        backend = CloudBackend(api_key=key, model=cfg.model, timeout_s=cfg.timeout_s)
    except (ValueError, CloudBackendError) as e:
        log.warning("cloud: backend setup failed (%s) - using local synth", e)
        return None
    log.info("cloud: backend ready (%s)", cfg.model)
    return backend


def _format_result(session: ResearchSession) -> str:
    sources_lines = "\n".join(
        f"[{s.number}] {s.url} - {s.title}" for s in session.sources
    )
    return (
        f"<tool_result tool=\"research\" status=\"complete\">\n"
        f"<answer>\n{session.answer}\n</answer>\n"
        f"<sources>\n{sources_lines}\n</sources>\n"
        f"</tool_result>"
    )
