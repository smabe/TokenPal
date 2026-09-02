# agent-thinking-p2 — agent loop opts in, shows thoughts, guards truncation

You are phase `p2` of the `agent-thinking` plan. This phase wires the `[agent]` config into `AgentRunner`, logs the model's reasoning before each tool call, retries a truncated step once, and updates the docs, as one commit. p1 has landed: `LLMResponse.reasoning` exists and `generate_with_tools` accepts `thinking_effort`.

## Locked decisions
See the master `plans/agent-thinking.md`. The decisions binding this phase, all signed off by the operator this session (2026-09-02):
- `[agent] thinking` defaults to `false`. Reason: thinking on the Ollama path is unmeasured for the agent loop; the one data point is gemma4 burning ~900 tokens per call at the old `high` mapping (`docs/claude/llm.md:4`), and the only measured win is Qwen3.8 on MTPLX.
- Reasoning is shown in the agent log untruncated. No cap, no `_truncate`.
- `[agent] per_step_timeout_s` default becomes `60.0`.
- `[agent] max_tokens` defaults to `2048` and is passed explicitly on the step call whether or not thinking is on. Explicit `max_tokens` wins in `_resolve_max_tokens` (`tokenpal/llm/http_backend.py:209-210`), bypassing the 150 static default and the 1024 hard cap.
- `[agent] thinking_effort` defaults to `"low"` and is only sent when thinking is on (p1 contract).
- `_force_synthesis` (`tokenpal/brain/agent.py:243-254`) is unchanged: it passes no `enable_thinking` and no `max_tokens`, so it keeps following `[llm] disable_reasoning` and the resolver default.

## Work
- Scope trace: DIRECT — the requested outcome is the agent loop using thinking under a flag, showing thoughts, with the timeout and max_tokens the operator chose.
- `tokenpal/config/schema.py` — `AgentConfig` (`:309-317`) gains `thinking: bool = False`, `thinking_effort: str = "low"`, `max_tokens: int = 2048`; `per_step_timeout_s` becomes `60.0`. One comment line: effort values are engine-specific and MTPLX 400s on anything outside auto/low/medium/high/xhigh.
- `config.default.toml` — `[agent]` block (`:164-170`): `per_step_timeout_s = 60.0`, add `thinking = false`, `thinking_effort = "low"`, `max_tokens = 2048`, each with a one-line comment. Keep the `DO NOT use deepseek-r1` line; R1 was not measured and its prohibition stands.
- `tokenpal/brain/agent.py` — `AgentRunner.__init__` (`:72-112`) gains `thinking: bool = False`, `thinking_effort: str = "low"`, `max_tokens: int = 2048` (proposed names), and `per_step_timeout_s` default moves to `60.0`. Runner defaults mirror `AgentConfig` the way `max_steps`, `token_budget`, and `per_step_timeout_s` already do (`agent.py:81-112` vs `schema.py:309-317`). In `run` (`:141-156`) the step call becomes:
  ```python
  response = await self._step(messages, thinking=self._thinking)
  if (
      self._thinking
      and response.finish_reason == "length"
      and not response.tool_calls
  ):
      log.warning("Agent step %d truncated at max_tokens while thinking; retrying without", step)
      self._log("(step truncated while thinking; retrying without thinking)")
      response = await self._step(messages, thinking=False)
  ```
  where `_step` (proposed) is `await asyncio.wait_for(self._llm.generate_with_tools(messages=messages, tools=self._tool_specs, max_tokens=self._max_tokens, enable_thinking=thinking, thinking_effort=self._thinking_effort if thinking else None), timeout=self._per_step_timeout_s)` and does not catch. The existing `try`/`except TimeoutError` in `run` (`agent.py:140-149`) widens to wrap both `_step` calls, so a timeout on the retry ends the run as `TIMEOUT` the same way. After a successful step, if `response.reasoning` is a non-empty string, `self._log(f"… {response.reasoning}")` before the tool calls are logged (the existing `→` / `←` prefixes are at `:217,227`; `…` is the new prefix for thoughts). Tests assert `startswith("… ")`, because `_truncate` (`agent.py:273`) already ends truncated `←` lines with the same character. Retry tokens count toward `session.tokens_used` like any step.
- `tokenpal/brain/orchestrator.py` — the `AgentRunner(...)` construction (`:1841-1852`) passes `thinking=self._agent.config.thinking`, `thinking_effort=self._agent.config.thinking_effort`, `max_tokens=self._agent.config.max_tokens`.
- `tokenpal/ui/qt/chat_window.py` — in the log-append path (`:208-230`) replace `\n` with `<br>` after `html.escape` so multi-paragraph reasoning keeps its breaks. Applies to every log line; tool results with newlines gain the same fix.
- `tests/_helpers.py` — `ScriptedLLM.generate_with_tools` records its keyword arguments alongside `(messages, tools)`.
- `tests/test_agent.py` — `_HangLLM.generate_with_tools` (`:398-400`) gains `**kwargs` so the new keywords do not raise. New tests using `ScriptedLLM` from `tests/_helpers.py`: (1) default runner passes `enable_thinking=False` and `max_tokens=2048`; (2) `AgentRunner(thinking=True)` with default effort passes `enable_thinking=True` and `thinking_effort="low"` and logs a `…` line carrying the full `reasoning` string; (3) a scripted first response with `finish_reason="length"`, no tool calls, and `thinking=True` is followed by a second call with `enable_thinking=False`, and the run completes with the second response's text; (4) `thinking=False` with `finish_reason="length"` does not retry. `ScriptedLLM.generate_with_tools` (`tests/_helpers.py:57-63`) discards kwargs via `**_` and records only `(messages, tools)`; change it to record the kwargs the way `generate` already does (`call_kwargs`), so tests (1)-(4) can assert them.
- `docs/agents-and-tools.md` — `[agent]` config block (`:231-236`) shows the new keys and timeout 60; replace the `deepseek-r1` / "Same failure as Qwen3" note (`:243`) with the measured result: Qwen3.8 on MTPLX returns a well-formed `tool_calls` array with thinking on (2026-09-02, 6.3 s / 118 tokens for a two-tool request); retire the "no `<think>`-tag problem" aside at `:279`; in the agent-loop row of the recommendation table (`:415`) append to the notes cell: "`[agent] thinking = true` measured on Qwen3.8/MTPLX; off by default". No new column.

## Decisions & findings
### Decision: retry with thinking off, not with a larger cap  *(status: active)*
- **Rationale:** a second thinking attempt at the same or larger cap costs another 15-30 s and may truncate again; thinking off at the same cap is the fastest path to a usable tool call, and the log line tells the user what happened.
- **Alternatives considered:** doubling `max_tokens` on retry (rejected: unbounded latency against the 60 s timeout); treating truncation as `TIMEOUT` (rejected: hides a recoverable step).
- **Evidence:** measured thinking cost table in the master's Background findings.

### Decision: reasoning lines go through the ordinary log path  *(status: active)*
- **Rationale:** the log path already reaches both overlays, the INFO file log, and the persisted chat log; a parallel channel for one line type is the abstraction the repo rules say to wait for.
- **Alternatives considered:** a `persist=False` flag on `log_buddy_message` (no such path exists at `tokenpal/ui/base.py:68-71`; adding it touches every overlay). Promoted to the operator at approval as the persistence question.
- **Evidence:** `tokenpal/app.py:232-242`, `tokenpal/ui/qt/overlay.py:1201`.

### Decision: truncation fallback is sticky and only fires on an empty answer  *(status: active, supersedes the "retry once" wording in Work)*
- **Rationale:** review found two gaps in the drafted branch. A step whose *answer* was cut at the cap has non-empty content and must be kept, not regenerated cold; the retry now also requires `not response.text`. And re-reading `self._thinking` every step meant a model that overruns 2048 while thinking would pay the wasted call plus a second timeout window on every later step; the run-local `thinking` flag flips off after the first truncation.
- **Also from review:** `_step(session, messages, *, thinking)` owns token accounting and the `…` log line, so the truncated step's reasoning is shown too and nothing is accumulated twice; `thinking_effort` is passed unconditionally because the backend already gates it on thinking; runner defaults derive from one `AgentConfig()` instance (`_DEFAULTS`) instead of six mirrored literals; the redundant `log.warning` beside the user-visible `_log` line is gone.
- **Evidence:** `tests/test_agent.py::test_thinking_truncation_falls_back_to_no_thinking_for_the_run` and `::test_truncated_answer_is_kept_not_retried`.

## Failure modes to anticipate
- The Qt `<br>` change lands in a widget path that `WA_TranslucentBackground` has bitten before (CLAUDE.md, UI conventions). It is a string transform before `QTextBrowser.append` (`chat_window.py:229`), not a paint change, but the screenshot criterion exists to prove it renders. `<br>` inside one `<div>` is expected to stay one block for the `LOG_MAX_LINES` trim (`_log_window.py:42,167-174`); if the log starts trimming early after the change, that expectation was wrong and the trim needs to count divs.
- A 2000-character reasoning line in the Textual log is markup-escaped (`textual_overlay.py:1301-1322`) and the 500-line cap counts it as one line; fine. In Qt the block cap (`_log_window.py:167-174`) counts it as one block; fine.
- `tokens_used` now includes reasoning tokens, so the 12000 soft `token_budget` trips sooner with thinking on: at ~600 tokens per step that is 20 steps, above the 8-step cap. Not a change for this phase; note it in the docs block.
- The dogfood comparison must run with no other MTPLX client active (project memory `project_mtplx_backend.md`: the serial scheduler queues calls and the estimator books queueing as slow decode).

## Done criteria
- The four new `tests/test_agent.py` cases pass; `pytest` green; ruff and mypy clean.
- Comparison table recorded in this shard under Decisions & findings: three goals run through a scratchpad driver that builds `AgentRunner` against the live `HttpBackend` on `http://localhost:8000/v1` with the registered actions and an auto-approving confirm callback, once with `thinking=False` and once with `thinking=True, thinking_effort="low"`. Goals: (a) a single-tool lookup (`system_info` or `weather_forecast_week`), (b) the two-tool "set an 8-minute timer and check Boston weather" goal, (c) a goal that needs a judgment between two plausible tools. Columns: steps, wall time, tokens, correct answer yes/no. The driver script is written this phase into the scratchpad, not the repo. Recipe: `discover_actions()` + `resolve_actions(...)` with the arguments `tokenpal/app.py:127-160` passes; `HttpBackend({...})` from this Mac's `config.toml` `[llm]` values then `await setup()`; `ToolInvoker()` with no callbacks; `log_callback=print`, `confirm_callback` returning True, `is_sensitive` returning False. Goal (b) resolves to the `timer` and `weather_forecast_week` actions (those are the registered names, not `set_timer`).
- Qt screenshot: with `thinking = true` in this Mac's `config.toml`, run `/agent` with goal (b) in the Qt overlay and capture the agent log showing a `…` reasoning line with visible paragraph breaks before the first `→` tool line. Capture with macOS `screencapture -x <path>` (present at `/usr/sbin/screencapture`) after the run finishes. The capture post-dates the last edit to `chat_window.py`.
- Default config on a fresh `AgentConfig()` yields `thinking is False`, `per_step_timeout_s == 60.0`, `max_tokens == 2048`, asserted in a test.

### Findings from execution
- **MTPLX emits no reasoning on tool-call turns.** A raw request with `enable_thinking=true`, `reasoning_effort="low"`, and the 17 registered tool specs returned `finish_reason="tool_calls"` with message keys `content`, `role`, `tool_calls` only (148 completion tokens). The server (`--reasoning auto --preserve-thinking auto --tool-prompt-mode hybrid`) produces thinking only on the final text turn. The `…` line therefore appears after the last `←` and before `=`, never before the first `→`. The screenshot criterion was met in that adapted form: `scratchpad/agent-log.png` shows the `…` line on the final step and a `<br>` paragraph break in the final answer.
- **Comparison table** (model `mtplx-qwen38-27b-optimized-quality`, 17 tools ≈ 2.2k prompt tokens per call; tokens = prompt+completion across all calls; steps = tool executions; nothing else on MTPLX during the runs):

| goal | thinking | steps | wall | tokens | correct |
|---|---|---|---|---|---|
| a: system_info, OS + RAM | off | 1 | 11.7 s | 4636 | partial: 48 GB RAM right, OS reported unavailable (true, `system_info` has no OS field) |
| a | on/low | 2 | 18.9 s | 7369 | no: added `list_processes` and inferred "Windows" from Edge processes on a Mac |
| b: 8-min timer + Boston weather | off | 2 | 8.1 s | 4675 | timer yes; weather blocked by consent, reported honestly |
| b | on/low | 2 | 11.8 s | 4851 | same |
| c: bike ride, bring a jacket? | off | 1 | 7.4 s | 4614 | picked `weather_forecast_week`; consent-blocked; sensible fallback |
| c | on/low | 1 | 14.4 s | 4818 | same |

  In-app Qt run of (b) with thinking on: 2 steps, 16.9 s, 4869 tokens. Thinking on cost +3.7 to +7 s per run and, on (a), induced an extra tool call and a wrong inference. No run truncated, so the retry path is exercised only by tests. Verdict per the phase map's "if it fails" branch: code stays, default off, feature is opt-in.
- `weather_forecast_week` needs the `web_fetches` consent, which is ungranted on this Mac (no `~/.tokenpal/.consent.json`); the worker did not grant it. Goals (b) and (c) are judged on tool selection and honest reporting.
- Qt trim expectation held: appending an entry containing `\n\n` leaves `blockCount()` at one block per entry, so `<br>` does not eat into `LOG_MAX_LINES`.
- Driving the Qt overlay from a script: Orca's accessibility listing does not see the app (accessory activation policy). What worked: `CGWindowListCopyWindowInfo` by pid to locate the dock (360x58, layer 8) and history window (520x380), `NSRunningApplication.activateWithOptions_`, Quartz `CGEvent` click and type, `screencapture -x -l <windowNumber>`. The buddy wanders, so locate the dock immediately before clicking. `NSWorkspace.frontmostApplication()` is the truthful frontmost check for accessory apps.
- The truncated call's `tokens_used` is added to `session.tokens_used` before the retry, so the soft budget counts both attempts; test (3) asserts `2048 + 30`.
- This Mac's gitignored `config.toml` now carries `[agent] thinking = true` (left on for dogfooding).
