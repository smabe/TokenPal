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
from tokenpal.config.schema import CloudLLMConfig, CloudSearchConfig, ResearchConfig
from tokenpal.config.secrets import get_cloud_key, get_tavily_key
from tokenpal.llm.base import AbstractLLMBackend
from tokenpal.llm.cloud_backend import (
    DEEP_MODE_MODELS,
    CloudBackend,
    CloudBackendError,
)

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
        self._cloud_search_config: CloudSearchConfig | None = None
        self._memory: Any = None  # MemoryStore, injected by orchestrator

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
        cloud_mode: str = ""
        if cloud_backend and self._cloud_config:
            if (
                getattr(self._cloud_config, "research_deep", False)
                and cloud_backend.model in DEEP_MODE_MODELS
            ):
                cloud_mode = "deep"
            elif (
                getattr(self._cloud_config, "research_search", False)
                and cloud_backend.model in DEEP_MODE_MODELS
            ):
                cloud_mode = "search"
        # Cloud search layer — independent of cloud_llm (Anthropic synth).
        # Tavily handles search+extract; synth still routes through whatever
        # cloud_llm decides (Haiku by default, local if cloud_llm is off).
        cs_cfg = self._cloud_search_config or CloudSearchConfig()
        tavily_key = get_tavily_key() if cs_cfg.enabled else ""
        if cs_cfg.enabled and not tavily_key:
            log.info("cloud_search: enabled but no tavily key - using local search")

        runner = ResearchRunner(
            llm=self._llm,
            fetch_url=fetch_and_extract,
            log_callback=lambda s: log.info(
                "research%s: %s",
                f" ({cloud_mode})" if cloud_mode else "", s,
            ),
            max_queries=cfg.max_queries,
            max_fetches=cfg.max_fetches,
            token_budget=cfg.token_budget,
            per_search_timeout_s=cfg.per_search_timeout_s,
            per_fetch_timeout_s=cfg.per_fetch_timeout_s,
            synth_thinking=cfg.synth_thinking,
            cloud_backend=cloud_backend,
            cloud_plan=cloud_plan,
            cloud_search=cs_cfg,
            tavily_api_key=tavily_key or "",
        )

        try:
            if cloud_mode == "deep":
                session = await runner.run_deep(question, mode="deep")
            elif cloud_mode == "search":
                session = await runner.run_deep(question, mode="search")
            else:
                session = await runner.run(question)
        except Exception:
            log.exception("research: pipeline crashed")
            return ActionResult(output="research: pipeline crashed", success=False)

        if not session.is_complete or not session.answer:
            reason = session.stopped_reason or ResearchStopReason.CRASHED
            return ActionResult(
                output=f"research: incomplete ({reason})", success=False,
            )

        # Cache the completed session so /refine finds it when the agent
        # tool-path (not /research slash) completed the run. The slash
        # path caches via orchestrator._save_research_cache; tool-invoked
        # runs previously dropped through untracked, and /refine fell
        # back to the latest cached *slash* run — which is rarely what
        # the user meant.
        if self._memory is not None and getattr(self._memory, "enabled", False):
            import json as _json
            payload = _json.dumps([
                {
                    "number": s.number,
                    "url": s.url,
                    "title": s.title,
                    "excerpt": s.excerpt,
                    "backend": s.backend,
                }
                for s in session.sources
            ])
            try:
                self._memory.cache_research_answer(
                    self._memory.research_cache_key(question, mode=cloud_mode),
                    question,
                    session.answer,
                    payload,
                )
            except Exception:
                log.exception("research: cache_research_answer failed (non-fatal)")

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
        backend = CloudBackend(
            api_key=key,
            model=cfg.model,
            timeout_s=cfg.timeout_s,
            deep_timeout_s=getattr(cfg, "deep_timeout_s", 300.0),
        )
    except (ValueError, CloudBackendError) as e:
        log.warning("cloud: backend setup failed (%s) - using local synth", e)
        return None
    log.info("cloud: backend ready (%s)", cfg.model)
    return backend


def _format_result(session: ResearchSession) -> str:
    sources_lines = "\n".join(
        f"[{s.number}] {s.url} - {s.title}" for s in session.sources
    )
    warnings_block = ""
    if session.warnings:
        inner = "\n".join(f"  <warning>{w}</warning>" for w in session.warnings)
        warnings_block = f"<warnings>\n{inner}\n</warnings>\n"
    return (
        f"<tool_result tool=\"research\" status=\"complete\">\n"
        f"{warnings_block}"
        f"<answer>\n{session.answer}\n</answer>\n"
        f"<sources>\n{sources_lines}\n</sources>\n"
        f"</tool_result>"
    )
