# agent-thinking-p1 — backend surfaces reasoning text and a thinking-effort dial

You are phase `p1` of the `agent-thinking` plan. This phase makes the HTTP backend return the model's reasoning text and accept a per-call thinking-effort string, as one commit. No caller behavior changes yet; every existing call passes `thinking_effort=None` by default.

## Locked decisions
See the master `plans/agent-thinking.md`. The decisions binding this phase:
- `thinking_effort: str | None = None` is a keyword-only parameter on both `generate` and `generate_with_tools`. The ABC default `generate_with_tools` forwards to `generate` by name (`tokenpal/llm/base.py:153-160`), so both signatures gain it and the forwarder passes it through.
- On the llamacpp body shape, `reasoning_effort` is written only when thinking is effectively on and an effort string was given. On the Ollama shape the existing mapping stays (`"none"` when off) and the given effort replaces the hardcoded `"high"` when on (`tokenpal/llm/http_backend.py:186`).
- `LLMResponse.reasoning: str | None = None` is appended after `finish_reason` (`tokenpal/llm/base.py:22-30`). It is `None` when the server sent no thinking text (key absent or empty), and the string otherwise. Review finding during p1: Ollama's OpenAI layer uses the key `reasoning` (`openai/openai.go`, `Reasoning string \`json:"reasoning,omitempty"\``), so both keys are read. `text` remains content-only.
- Operator sign-off (this session, 2026-09-02): thoughts are shown untruncated, so the backend returns the full string with no cap.

## Work
- Scope trace: PREREQUISITE — p2 cannot log reasoning or dial effort until the backend returns one and accepts the other.
- `tokenpal/llm/base.py` — add `reasoning: str | None = None` to `LLMResponse`; add `thinking_effort: str | None = None` keyword-only to the `generate` and `generate_with_tools` abstract signatures and to the default forwarder; document in the `generate` docstring that the value is engine-specific (MTPLX honors low/medium/high, llama-server accepts and ignores it, Ollama maps it onto `reasoning_effort`).
- `tokenpal/llm/http_backend.py` — `_apply_thinking_controls(self, body, enable_thinking, thinking_effort)` (existing symbol at `:166-186`, signature extended):
  ```python
  if self._inference_engine == "llamacpp":
      body["chat_template_kwargs"] = {"enable_thinking": effective}
      body["reasoning_format"] = "deepseek"
      if effective and thinking_effort:
          body["reasoning_effort"] = thinking_effort
  else:
      body["reasoning_effort"] = (thinking_effort or "high") if effective else "none"
  ```
  Both `generate` (`:363-412`) and `generate_with_tools` (`:415-484`) accept the kwarg, pass it through, and set `reasoning=choice["message"].get("reasoning_content")` on the returned `LLMResponse`.
- `tests/test_server/test_llm_backend.py` — extend the existing `_apply_thinking_controls` tests (`:47-66`): llamacpp shape with `thinking_effort="low"` and thinking on writes `reasoning_effort: "low"`; thinking off writes no `reasoning_effort` key; Ollama shape with effort on writes `"low"`, off writes `"none"`. Add a `generate_with_tools` test with a mocked response carrying `reasoning_content` that asserts `response.reasoning` equals it and `response.text` is the content only; and one without the key asserting `reasoning is None`.
- `tests/test_brain/test_eod_summary.py` — the fake `generate` (`:25-33`) spells out the keyword list with no `**kwargs`; add `thinking_effort=None`.
- `tests/test_brain/test_session_summarizer.py` — same for the fake at `:37-45`.
- `docs/claude/llm.md` — rewrite the thinking bullet (line 4): `reasoning_format=deepseek` in the request body routes thinking to `reasoning_content`, which the backend now surfaces as `LLMResponse.reasoning`; per-call `thinking_effort` maps to `reasoning_effort` (MTPLX honors low/medium/high, llama-server ignores, Ollama uses it in place of `high`).

## Decisions & findings
### Decision: kwarg on the call, not a backend-level setting  *(status: active)*
- **Rationale:** the agent wants low effort while research synth (`tokenpal/brain/research.py:751,759`) should keep the server default. A backend-wide setting would force one value on both.
- **Alternatives considered:** `[llm] thinking_effort` read by `HttpBackend.__init__`. Rejected for the reason above.
- **Evidence:** `_apply_thinking_controls` is the single place both request paths write thinking fields (`tokenpal/llm/http_backend.py:385,440`).

### Decision: `reasoning` is a separate field, not prepended to `text`  *(status: active)*
- **Rationale:** `text` feeds `to_assistant_message` (`tokenpal/llm/base.py:32-50`), which is round-tripped to the model on the next step. The history we send today carries content and tool calls only; keeping reasoning out of `text` keeps it that way and avoids re-prefilling a scratchpad on every step.
- **Evidence:** `tokenpal/brain/agent.py:159` appends `response.to_assistant_message()` to the message list.

## Failure modes to anticipate
- A test fake that enumerates keywords without `**kwargs` breaks on the new parameter. Known: `tests/test_brain/test_eod_summary.py:25-33`, `tests/test_brain/test_session_summarizer.py:37-45`. `tests/_helpers.py:48-70 ScriptedLLM` absorbs via `**kwargs`. If `pytest` finds another, it belongs to this phase.
- MTPLX rejects unknown `reasoning_effort` values with HTTP 400 (`docs/claude/llm.md:11` lists the accepted set). The value is a free string from config in p2; p1 only threads it. Do not validate here.

## Done criteria
- `tests/test_server/test_llm_backend.py` passes with the new cases; `pytest` green; ruff and mypy clean.
- A live probe against `http://localhost:8000/v1` (MTPLX, running on this Mac; check with `pgrep -fl mtplx`) through `HttpBackend.generate_with_tools(..., enable_thinking=True, thinking_effort="low", max_tokens=2048)` returns a non-empty `reasoning` and a content-only `text`. Run it as a scratchpad script and paste the token count and latency into this shard's findings.
