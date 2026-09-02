# Agent thinking: model reasoning for the /agent loop, off by default

## Phase map
**Phase p1 — backend surfaces reasoning text and a thinking-effort dial**
- Enters when: start here
- Done signal: `tests/test_server/test_llm_backend.py` proves `reasoning_content` lands on `LLMResponse.reasoning` and the effort field is written per engine; see shard
- If it fails: no gate — fix-forward
- Shard: `plans/agent-thinking-p1.md`

**Phase p2 — agent loop opts in, shows thoughts, guards truncation**
- Enters when: p1 committed
- Done signal: three `/agent` goals measured thinking off vs on against the live MTPLX server, thoughts visible in the Qt agent log; see shard
- If it fails: if thinking on loses on all three goals, keep the code (default off) and record the numbers in p2's Decisions & findings; the feature stays opt-in
- Shard: `plans/agent-thinking-p2.md`

## Status & cold-start
**Approval: APPROVED 2026-09-02**
**Authored at: 6e76b9b**
Verification pass 2026-09-02 — grounding 61/64 claims resolve (3 line drifts fixed: `to_assistant_message` cite `agent.py:156`→`:159`, table row `agents-and-tools.md:416`→`:415`, `insertHtml`→`QTextBrowser.append`; 5 live-server claims re-stated with the probe that backs them) · executability 24/31 → 8 fixed (`tests/_helpers.py` edit stated outright and listed; runner defaults now mirror `AgentConfig` so tests (1)-(2) hold; deepseek-r1 line kept and table note fixed, no worker forks; `try` widened over both `_step` calls; `startswith("… ")` assertion; `screencapture` and the driver recipe named; `_force_synthesis` reworded to "unchanged") · coherence 8 found → 7 fixed (Ollama rationale, Files touched, comparison-table location and columns, retry gated on thinking in the scope contract, effort value set, Ollama exclusion wording) · 1 promoted (reasoning-line persistence, below) · 0 refuted · 0 uncheckable.
NEXT: p1. Read `plans/agent-thinking-p1.md` FIRST. Binding decisions for p1:
- `thinking_effort` is a new keyword-only parameter on both `generate` and `generate_with_tools`, threaded to `_apply_thinking_controls`; the ABC's default `generate_with_tools` forwards to `generate` by name, so both must accept it.
- `LLMResponse.reasoning: str | None = None` is filled from `message["reasoning_content"]` in both HTTP paths; `text` stays content-only.

Operator sign-off 2026-09-02: reasoning lines persist to the memory.db chat log like every other agent log line ("persist is fine for now"). The Qt `<br>` transform applies to every agent log line, not only reasoning ("as drafted is fine").

## Goal
Let `/agent` run with model reasoning enabled when `[agent] thinking = true` (default false), show the model's thoughts untruncated in the agent log, raise the per-step timeout default to 60 s, give the agent step its own max_tokens (2048), and retry a step once with thinking off when max_tokens cut it off before any tool call.

## Scope contract
- **Requested outcome:** `/agent` runs can use model reasoning when a new `[agent] thinking` flag is true (default false); the reasoning text is shown, untruncated, in the agent log for each step; `[agent] per_step_timeout_s` defaults to 60; the agent step gets a configurable max_tokens starting at 2048; a step run with thinking on that is cut off by max_tokens before any tool call is retried once with thinking off instead of ending the run with an empty answer.
- **Named semantic boundary:** the per-step LLM call in `AgentRunner.run` (`tokenpal/brain/agent.py`) and the thinking controls in `HttpBackend._apply_thinking_controls` (`tokenpal/llm/http_backend.py`).
- **Explicit inclusions:** `[agent] thinking`; a thinking-effort dial (`reasoning_effort`; MTPLX honors auto/low/medium/high/xhigh, llama-server accepts and ignores the field); `[agent] max_tokens`; length-truncation retry; reasoning text surfaced to the agent log; timeout default 60; correcting the stale Qwen3 tool-call note in `docs/agents-and-tools.md`.
- **Explicit exclusions:** no thinking on observation, idle tools, conversation, wedges, session/EOD summaries, research planner, or `AgentRunner._force_synthesis`; no change to the global `[llm] disable_reasoning` default; no Ollama-path change beyond `_apply_thinking_controls` sending the given effort string in place of its hardcoded `"high"` when thinking is on.
- **Intent class:** bounded outcome.

## Non-goals
- No thinking for any caller other than the agent step. Verified: the only callers passing `enable_thinking` today are `tokenpal/brain/research.py:751,759` (synth, stays as is) and `tokenpal/brain/session_summarizer.py:167` (False, stays); `grep -rn "enable_thinking=" tokenpal/` at 6e76b9b.
- No new overlay log affordance (dim style, collapsible block). Reasoning is logged through the existing `log_callback` as a plain line.
- No `persist=False` path for log lines. Reasoning lines persist to the chat log like tool results do (open question in Status & cold-start).
- No change to `CloudBackend`: it is not an `AbstractLLMBackend` subclass and has no `generate`/`generate_with_tools` (`tokenpal/llm/cloud_backend.py:284,312`).
- No throughput-estimator change: `_record_sample` (`tokenpal/llm/http_backend.py:248-291`) keys on `completion_tokens` over elapsed time; reasoning tokens decode at the same rate as content tokens, so the EWMA is not biased. Probed this session: MTPLX `usage.completion_tokens` was 587 for a 24-token answer with 2046 chars of reasoning, so it counts reasoning tokens.

## Files touched
- `tokenpal/llm/base.py` — p1 — `LLMResponse.reasoning`; `thinking_effort` kwarg on `generate` and `generate_with_tools` ABC and default forwarder
- `tokenpal/llm/http_backend.py` — p1 — `_apply_thinking_controls` writes effort; both request paths read `reasoning_content`
- `tests/test_server/test_llm_backend.py` — p1 — effort field per engine; reasoning parsed
- `tests/test_brain/test_eod_summary.py` — p1 — fake `generate` gains the new kwarg
- `tests/test_brain/test_session_summarizer.py` — p1 — fake `generate` gains the new kwarg
- `docs/claude/llm.md` — p1 — thinking bullet reflects the effort dial and `reasoning` field
- `tokenpal/config/schema.py` — p2 — `AgentConfig.thinking`, `thinking_effort`, `max_tokens`; `per_step_timeout_s = 60.0`
- `config.default.toml` — p2 — `[agent]` block gains the three keys, timeout 60
- `tokenpal/brain/agent.py` — p2 — step call passes thinking, effort, max_tokens; logs reasoning; one retry on length truncation
- `tokenpal/brain/orchestrator.py` — p2 — `AgentRunner(...)` construction passes the new config fields
- `tokenpal/ui/qt/chat_window.py` — p2 — log lines keep paragraph breaks (`\n` to `<br>` after escaping)
- `tests/_helpers.py` — p2 — `ScriptedLLM.generate_with_tools` records call kwargs
- `tests/test_agent.py` — p2 — `_HangLLM` accepts kwargs; new tests for pass-through, reasoning log, truncation retry
- `docs/agents-and-tools.md` — p2 — `[agent]` config block, model-choice notes, recommendation table row

## Background findings
- Measured 2026-09-02 on this Mac (MTPLX, Qwen3.8-27B, 20-word quip prompt): thinking off 2.3 s / 24 completion tokens; on with `reasoning_effort` low 15.7 s / 331; medium (server default) 26.2 s / 587; high 30.7 s / 741. A two-tool request with thinking on returned a well-formed `tool_calls` array in 6.3 s / 118 tokens. Recorded in project memory `project_mtplx_backend.md`.
- The agent step resolves max_tokens to the static default because it passes neither `max_tokens` nor `target_latency_s` (`tokenpal/brain/agent.py:142-145`; resolver order `tokenpal/llm/http_backend.py:194-230`). `config.default.toml:61` sets that default to 150. An explicit `max_tokens` argument bypasses the 1024 `_MAX_TOKENS_HARD_CAP` (`http_backend.py:26,209-210`).
- `HttpBackend` reads only `choice["message"]["content"]` (`http_backend.py:399,457`) and drops `reasoning_content`, which MTPLX's `--reasoning-parser qwen3` populates (probed this session: `reasoning_content` of 2046 chars with `reasoning_format=deepseek` in the body) and llama-server's `--reasoning-format deepseek` documents (`docs/amd-dgpu-setup.md:108`).
- Log path: `AgentRunner._log` → `AgentBridge.log_callback` → `app.py:232-242 _agent_log` (INFO file log + `overlay.log_buddy_message`) → Qt `chat_window.py:208-230` (html-escaped `<div>` per call, newlines collapse) or Textual `textual_overlay.py:1301-1322` (newlines preserved). Every buddy log line also persists to memory.db via `overlay.py:1201` / `app.py:1761-1771` when `[chat_log] persist` is true.
- Config loader: `_SECTION_MAP` maps `agent` to `AgentConfig` (`tokenpal/config/loader.py:66`) and `_dict_to_dataclass` filters on declared fields (`loader.py:99-114`), so new fields load once declared. Issue #16 is closed.
- Existing `finish_reason == "length"` handling: conversation auto-continue loop `orchestrator.py:1712-1741`; research warn-only `research.py:767-775`. The agent retry is one shot with no concatenation, so it is a new three-line branch, not a reuse of the continuation loop.

## Done criteria
- `pytest` green, `ruff check tokenpal/` and `mypy tokenpal/ --ignore-missing-imports` clean.
- Default config (`[agent] thinking = false`) sends `enable_thinking: false` on the agent step, proven by a runner test.
- With `thinking = true` in this Mac's `config.toml`, a `/agent` run in the Qt overlay shows a reasoning line before each tool call in the agent log, screenshot captured after the last edit to `chat_window.py`.
- Comparison table in p2's Decisions & findings: three goals, thinking off vs on, columns steps / wall time / tokens / correct answer, measured against the live MTPLX server.

## Parking lot
- `tokenpal/tools/train_voice.py:68-85 _thinking_controls` re-derives the per-engine thinking body shape with thinking hardcoded off (a second copy of `HttpBackend._apply_thinking_controls`). Surfaced by the p1 simplify pass. Useful to unify; not required because the trainer builds raw request bodies and never needs effort or reasoning.
- `tests/test_brain/test_eod_summary.py:15-40 FakeLLM` and `tests/test_brain/test_session_summarizer.py:25-60 FakeLLM` enumerate every `generate` keyword and had to be touched for the new one; `tests/_helpers.py ScriptedLLM` absorbs kwargs. Their `raise_next` and prompt-capture semantics differ, so replacing them is a small test refactor, not a rename. Surfaced by the p1 simplify pass.
- `AgentBridge.log_callback` (`orchestrator.py:221`) and `LogFn` (`agent.py:41`) are typed `Callable[[str], None]` while the real callback accepts `markup=` and `url=` (`app.py:232`). Harmless today; tighten when a caller needs markup from the runner.
