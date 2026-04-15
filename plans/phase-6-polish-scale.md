# Phase 6: Polish + Scale (bundled)

Scope: ship Phase 6 of the grand plan in one pass, folding the Phase 5 deferred refactors into the touched code.

## User-facing (from grand plan)
1. `/tools describe <name>` — blurb, section, consent category, platforms, `safe`, `requires_confirm`, `rate_limit` if set. Reuse `SECTIONS` catalog, look up class via registry.
2. `/tools list` already works — leave alone, add `/tools describe` branch only.
3. Tool usage stats in `memory.db` — new `tool_calls(ts, tool_name, duration_ms, success)` table. Logged from AgentRunner + slash-command direct-invocations. Read-side API `MemoryStore.tool_usage_counts(since_days)`; no LLM riff yet.
4. Agent result cache — in-memory per-run dict on `AgentSession`, keyed on `(tool_name, json.dumps(args, sort_keys=True))`. Skip for `requires_confirm=True` (user may want to re-confirm) and for tools whose class sets `cacheable: ClassVar[bool] = False`. Cache hit logs as `← (cached) ...`.
5. Research 24h cache — new `research_cache(question_hash, answer, sources_json, created_at)` table in memory.db. Key = `sha256(question.strip().lower())`. TTL 24h. Cached hit renders with `(cached Nh ago)` prefix on the answer bubble.
6. Per-tool `rate_limit` field — `rate_limit: ClassVar[RateLimit | None] = None` on `AbstractAction`, where `RateLimit = dataclass(max_calls: int, window_s: float)`. Enforced in registry-owned `ToolInvoker` wrapper (new module `tokenpal/actions/invoker.py`) using `collections.deque[float]`. Exceed → `ActionResult(success=False, output="rate limit: N calls/Ws exceeded")`. Not a sleep — fail-fast.

## Refactors (bundled, touch same surfaces)
- **BrainMode StrEnum** — replace `_agent_running` + `_research_running` with `self._mode: BrainMode` (`IDLE|AGENT|RESEARCH`). Conversation stays separate since it's an object (`_conversation: ConversationSession | None`), not a bool. Update `_should_suppress_observations()`, `_cmd_agent`, `_cmd_research`, `agent_running`/`research_running` properties (keep properties for callers).
- **AgentBridge / ResearchBridge dataclasses** — the orchestrator currently takes `agent_config`, `agent_log_callback`, `agent_confirm_callback`, `research_config` as loose params. Group: `AgentBridge(config, log_callback, confirm_callback)`, `ResearchBridge(config, log_callback)`. Constructor shrinks; Brain call sites in `app.py` updated.
- **Common base StrEnum** — `StopReason` (agent) and `ResearchStopReason` share 5 of 7 values. Fold into `tokenpal/brain/stop_reason.py` with `AgentStopReason(StrEnum)` and `ResearchStopReason(StrEnum)` both extending a shared `_BaseStopReason` mixin? StrEnum can't be extended. **Decision:** keep them separate but move both into `tokenpal/brain/stop_reason.py` and share the common string values via module constants. 15-min mechanical move.
- **Catalog kind discriminator** — add `kind: Literal["default","local","utility","focus","agent","research"]` to `CatalogEntry` (default `""` for back-compat during migration, then required). Enables `/tools describe` to return the section without a reverse-lookup over `SECTIONS`. Also useful for Phase 2 subsetting heuristic later.

## Order of work
1. Refactors first (no behavior change): catalog kind, StopReason move, Bridge dataclasses, BrainMode. Green tests between each.
2. Feature work: rate_limit field + ToolInvoker → /tools describe → agent cache → memory.db `tool_calls` → research cache.
3. Final: `simplify` pass, test run, commit.

## Done criteria
- [ ] `/tools describe timer` prints full metadata, `/tools describe bogus` returns "Unknown tool".
- [ ] Running `/agent summarize recent activity` twice in one run shows `← (cached)` on the 2nd identical tool call.
- [ ] `/research "what is X"` followed by identical `/research "what is X"` within 24h shows `(cached Nh ago)`.
- [ ] Action with `rate_limit=RateLimit(2, 10)` fails 3rd call within 10s with a rate-limit message.
- [ ] `memory.db` has a `tool_calls` table with rows after any /agent run.
- [ ] `pytest` green, `ruff` clean, `mypy` clean.
- [ ] No raw `_agent_running`/`_research_running` bool assignments outside `_mode` setter helpers.

## Parking lot (defer to Phase 7 or later)
- Buddy riffs on tool-usage patterns (needs personality prompt wiring, separate plan).
- Rate-limit sleep/queue (today's scope = fail-fast only).
- Cross-session agent cache persistence.
- `/research --fresh` flag to bypass cache.
- Catalog `kind` enforced as required (leave default `""` until all entries migrated in this PR).

## Risk
- BrainMode migration crosses orchestrator + app.py + UI status reads. Biggest regression surface. Mitigation: keep `agent_running`/`research_running` @property accessors intact so callers don't move.
- Research cache key on lowercased question can collide across distinct phrasings — acceptable, cache is advisory and 24h.
