# Cloud-native web search for /research (Sonnet+ only)

## Goal
Add an opt-in "deep mode" to `/research` and the `research` LLM tool that replaces our local DuckDuckGo + newspaper4k + trafilatura pipeline with Anthropic's server-side `web_search_20260209` + `web_fetch_20260209` tools when the user has the cloud backend set to Sonnet 4.6 or Opus 4.7. For Haiku-tier users and the default `/research` behavior, nothing changes.

## Non-goals
- **Not the default.** Deep mode stays opt-in forever. The local pipeline works for 90% of queries and costs a third as much; don't make every research call cost 2-3× to cover the 10% that need it.
- **No Haiku support.** `web_search_20260209`'s dynamic filtering requires Sonnet 4.6+. Haiku users see the flag disabled with an explanation.
- **No hybrid.** Don't mix local-sourced URLs with Anthropic-sourced ones in a single synth - keeps the provenance clean and the `contains_sensitive_content_term` filter invariant honest (local filter applies to local sources only; Anthropic sources go through Anthropic's content policies instead).
- **No caching of Anthropic-sourced results to the research cache.** Different provenance, different trust model, separate cache bucket or no cache at all (evaluate during implementation).
- **No new slash command.** This lives on top of `/research` and the `research` tool as a toggle, not a parallel command. One command per mental model; the toggle is the lever.
- **Not the `/refine` path.** Refine always reuses local-sourced cached sources. Deep mode is a fresh research call with different sourcing.

## Why Sonnet+ (not Haiku)
- `web_search_20260209` / `web_fetch_20260209` (the dynamic-filtering tool versions that actually make this worthwhile) are **Opus 4.7 / Opus 4.6 / Sonnet 4.6 only**. Haiku 4.5 falls back to `web_search_20250305` which loads full results into context - token cost explodes.
- Deep mode needs adaptive thinking to be worthwhile (decide when to search vs. fetch vs. synthesize). Haiku doesn't support thinking params.

## When to actually use deep mode
Local pipeline chokes on:
- Pure React SPAs (rtings.com is the canonical offender - no extractor can recover content without a JS render step)
- Forbes / bot-blocked sites (403s on our aiohttp fetcher)
- Cloudflare-aggressive sites
- Paywalled content with preview only

Deep mode handles all of these. If the user regularly researches high-end product reviews or specific walled-garden sites, deep mode is the right answer. For Wikipedia / Reddit / most blog content / long-tail review sites, local is fine.

## Cost model
| Scenario | Local default | Deep mode (Sonnet) |
|---|---|---|
| Typical /research (3 queries, 5 fetches, synth) | ~$0.05 (synth only) | ~$0.12-0.18 |
| Search-tool billing | $0 (DDG free) | ~$10/1k searches × 2-4 searches = $0.02-0.04 |
| Fetch-tool billing | $0 | bundled with search |
| Agentic-loop sampling overhead | none | ~2-5× normal synth input tokens (model reads results, decides to fetch, reads fetched pages, synthesizes) |
| Bad /research (source extractor fails, thin pool, dropped picks) | ~$0.05 spent, poor answer | Deep mode probably would have worked - quality upside is real |

Rough rule: deep mode costs 2-3× normal Sonnet /research. If your current synth-only run is $0.05, deep is $0.12-0.15. Still cheap in absolute terms - a 20-run week is $2.50 vs $1.

## Files to touch
- `tokenpal/config/schema.py` - add `research_deep: bool = False` to `CloudLLMConfig`. Default off. Separate from `research_synth` / `research_plan` so users can mix (e.g., deep mode disables without affecting the plan/synth split).
- `tokenpal/config/cloud_writer.py` - new `set_cloud_deep(enabled: bool)` function, mirrors `set_cloud_plan`.
- `tokenpal/llm/cloud_backend.py` - new method `research_deep(question: str) -> ResearchDeepResult`. Calls `client.messages.create` with `tools=[{"type": "web_search_20260209", "name": "web_search"}, {"type": "web_fetch_20260209", "name": "web_fetch"}]`, `thinking: {"type": "adaptive"}`, and a prompt that asks for the same `SYNTH_SCHEMA` output. Handle `pause_turn` stop_reason for agentic-loop continuation (re-send with assistant content per the skill docs). Cap total server-tool iterations via `max_continuations=3` to bound worst-case cost. Returns `ResearchDeepResult(synth_result, cited_urls, tokens_used, iterations)`.
- `tokenpal/brain/research.py` - new `ResearchRunner.run_deep(question) -> ResearchSession` method. Bypasses `_plan`, `_search_all`, `_read`, and `_synthesize` entirely. Builds sources list **from the model's output** - Sonnet cites URLs inline in its JSON response, we parse them back out into `Source` objects for the existing `display_urls` renderer. Returns a ResearchSession with the same shape as `run()`.
- `tokenpal/brain/orchestrator.py` - in `_handle_research`, branch on `cfg.cloud_llm.research_deep`: if set AND model is Sonnet+, call `runner.run_deep()` instead of `runner.run()`. Log line clearly labels the path so the user can see which ran (`"> research (deep): <q>"` vs `"> research: <q>"`).
- `tokenpal/actions/research/research_action.py` - same branch in the tool-path `execute()`. Respect the flag uniformly.
- `tokenpal/app.py` - `/cloud` subcommand: add `/cloud deep [on|off]` paralleling `/cloud plan`. Update the `/cloud` bare status line to show the deep flag.
- `tokenpal/ui/cloud_modal.py` - add a fourth checkbox: "Use deep web search (Sonnet+ only)". When current model is `claude-haiku-4-5` OR cloud is disabled, render the checkbox disabled with a tooltip-style label. When user flips it on with Haiku selected, block the Save and surface the constraint in a help label.
- `tokenpal/app.py::_apply_cloud_modal_result` - persist the new flag via cloud_writer + live flip on the config object.
- `docs/research-architecture.md` - new subsection "Deep mode (cloud-native web search)" covering when to enable, cost model, and the Source-object-from-model-output parsing approach.
- `CLAUDE.md` - one-line note under `/research` about the deep flag.
- Tests:
  - `tests/test_cloud_backend.py` - mock `messages.create` with `tools=[web_search_*]`, verify the tool set is sent correctly, verify `pause_turn` continuation handling, verify `max_continuations` cap.
  - `tests/test_research.py` - `run_deep` builds a ResearchSession from mocked cloud output, display_urls populate correctly, cloud_plan / synth flags are ignored when deep is on (deep subsumes both).
  - `tests/test_cloud_command.py` - `/cloud deep on/off` persists and surfaces status.
  - `tests/test_cloud_modal.py` - checkbox disables for Haiku, flag persists through apply.

## Tricky bits
- **`pause_turn` continuation.** When the server-side tool loop hits its default 10-iteration cap, the response stops with `stop_reason: "pause_turn"`. We re-send the conversation (original user message + the full assistant response content) and the server picks up where it left off. Skill docs explicitly warn NOT to add a "please continue" user message - the API detects the trailing `server_tool_use` block and resumes automatically. Easy to get wrong. Cap our side at 3 continuations to prevent runaway.
- **Parsing URLs out of the model's response.** Sonnet cites sources inline in its synth JSON (`"citation": <url>` won't match our schema since citations are integers). Options:
  - Keep `SYNTH_SCHEMA` integer citations, but have Sonnet emit a parallel `sources` array in the JSON: `"sources": [{"number": 1, "url": "..."}, ...]`. Extend `SYNTH_SCHEMA` with an optional `sources` field for the deep path only.
  - Parse URLs from the text response post-hoc and number them.
  - Prefer option A - explicit structured output, no regex fragility.
- **Citation validation against model-reported sources.** Our `_validate_picks` substring-matches pick names against source excerpts. In deep mode we don't have excerpts - the model read them server-side and summarized. Options:
  - Skip validation in deep mode (trust the model more; Sonnet+ with thinking is less hallucination-prone on grounded synth than Qwen3 was).
  - Require the model to emit source excerpts alongside URLs (extra output tokens, fights the dynamic-filtering cost savings).
  - Pick validation skip. Document clearly.
- **Filter boundary.** The local path runs `contains_sensitive_content_term` on article excerpts before they reach synth - keeps sensitive-brand text out of the prompt cache. Deep mode doesn't see those excerpts. Tradeoff: deep mode relies on Anthropic's content policies instead of our local filter. Document this in docs + CLAUDE.md as a caveat.
- **Cache key for deep-mode results.** Same question can run deep or local with different answers. Cache key needs a `deep` suffix or we'll serve a local-cached answer to a deep-mode follow-up. Proposal: `question_hash = sha256(f"{deep}:{question}")`.
- **Link rendering.** `display_urls` in `ActionResult` is already the right contract - deep mode just populates it from the model-reported sources instead of the search pipeline. Chat-log rendering code is untouched.

## Failure modes to anticipate
- **Deep mode + Haiku = 400.** Gate at config load time and at slash-command time; modal disables the checkbox. If someone somehow forces it (YOLO config edit), the SDK will reject the request and we log + fall back to local pipeline. Fall-back behavior should be identical to "cloud failed" path - no user data loss.
- **pause_turn loop exceeded.** After `max_continuations=3` hits, we stop continuing and ask Sonnet to finalize with what it has. If it can't, we fall back to local pipeline with a warning. This is the "Sonnet went off on a tangent searching 20 things" guardrail.
- **Rate limit on server tools.** Anthropic's per-org web search RPM is separate from synth RPM. Surface a clear error ("deep mode rate-limited, falling back to local for this query") and let the local pipeline complete the request instead of leaving the user empty-handed.
- **Cost blowup on ambiguous questions.** Sonnet in deep mode might search 5-10 things on a vague query. Cap via the `task_budget` param if we want a hard cost ceiling (Opus 4.7 beta - skip for now), or via iteration cap alone.
- **Cached deep result gets served to /refine.** /refine reuses cached sources - but deep mode may not have full source excerpts cached. If the most recent research was deep mode and sources are summaries only, /refine sends summary-only context to cloud and quality drops. Either (a) block /refine after a deep-mode result with a clear message, or (b) let it run - still better than hallucination. Probably (a) for clarity.

## Done criteria
- `/cloud deep on` in a session with Sonnet selected routes `/research` through the deep path. `/cloud deep off` restores default.
- Bare `/cloud` modal shows a fourth checkbox "Use deep web search (Sonnet+ only)", disabled when Haiku is selected.
- `--validate` output shows `Cloud LLM on (claude-sonnet-4-6, deep mode), key sk-ant-...` when both flags are on.
- `/research` log lines clearly distinguish paths: `> research (deep): <q>` vs `> research: <q>`.
- `docs/research-architecture.md` has a "Deep mode" subsection.
- CLAUDE.md updated with one-line note.
- Full suite green. Token cost of the deep path logged per-run so usage can be reviewed after a week of dogfooding.

## Parking lot
- **`task_budget` for hard cost caps.** Opus 4.7 beta param that lets us set a token ceiling the model sees and self-moderates around. Nice-to-have for deep mode power users who want a firm per-run cost cap. Land after the beta stabilizes.
- **Hybrid mode.** "Use local sources when they work, deep mode only for queries the local path bails on." Requires a failure-signal contract between the two paths and adds complexity. Wait and see if users want it.
- **Deep-mode /refine.** Currently /refine reuses local-sourced excerpts. A deep-mode version would re-search Anthropic-side with the follow-up. Costs another full deep run. Useful if deep mode becomes a common path; unnecessary if it stays a 10% case.
- **Citation repair in deep mode.** Our local pipeline repairs bad `[N]` citations by matching pick names to other sources. Deep mode could do the same if we have model-reported source excerpts. Worth doing if deep mode turns out to get citations wrong often in practice.
- **Token budget estimation.** Display per-run cost in the research log for the first N runs so the user builds intuition for when deep mode is worth it.
