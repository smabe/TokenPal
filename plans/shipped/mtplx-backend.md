# mtplx-backend — run the brain against MTPLX on this Mac

## Status
**Approval: APPROVED 2026-09-01**
**Shipped: 2026-09-01** (b1b8d0b; follow-ups filed as #44)
**Authored at: f11978e**
**Spec check** — 2/2 Work items evidenced (`docs/claude/llm.md`,
`config.default.toml`) · none unclaimed · operational steps applied to the
repo-root `config.toml` and verified via the loader harness.
Done criteria 2026-09-01: `--check` reachable line ✓ (one pre-existing
`voice` sense warning unrelated to `[llm]`); live auto-adopt + observation
bubbles + typed reply + no 400 ✓ across runs 1-3 (findings 12-14); estimator
line ✓ (value 13 t/s, criterion re-worded, see finding 14); budgets loaded ✓;
docs bullets ✓; 26 backend tests green.
Approval notes: open question 1 → MTPLX is this Mac's default via config edit;
open question 2 → both code items stay parked; scope extended by operator with
raised latency budgets (tools 12 s, conversation 20 s, research 40 s) on this
Mac's config only.
Verification pass 2026-09-01 — 45 claims checked, 38 resolved, 4 wrong, 3
unchecked · 4/4 done criteria covered after fixes · 1 contradiction fixed.
Fixed: Done criterion 1 asserted `--check` prints the advertised model id
(`cli.py` echoes `config.llm.model_name` on `llamacpp`) → criterion now checks
the reachable line and moves the model-id proof to the auto-adopt log line;
`response_format json_schema` called advisory → grammar-enforced
(`mtplx/constrained.py:4,160-171`), probe schema lacked
`additionalProperties: false`; operational step deferred to a nonexistent
"Open question" section → settled on the repo-root `config.toml` edit with the
default-vs-per-session choice promoted to approval; `config.default.toml`
comment cited at lines 29-31 → line 54; Evidence launch line put
`--tool-prompt-mode hybrid` on the launcher pid → on the child server pid;
four line hints refreshed. Unchecked by the auditor, measured by this session:
finding 9 (real prompt size) and finding 2's reasoning-token count (now given
as a range). Parking-lot session-header note left as observed.

## Goal
Point TokenPal's brain at the MTPLX OpenAI-compatible server already running on
this Mac, verify every request path the brain uses works against it, and record
the constraints so the next machine or session does not re-derive them.

## Scope contract
- **Requested outcome:** TokenPal's brain (observation loop, conversation, tool
  calls, idle tool rolls, local `/research` synth) generates through the MTPLX
  server on this Mac instead of the remote llama-server on apollyon.
- **Named semantic boundary:** the `[llm]` config surface and the request body
  `HttpBackend` sends per `inference_engine` (`tokenpal/llm/http_backend.py`).
- **Explicit inclusions:** MTPLX serving Qwen3.8-27B on `127.0.0.1:8000`; the
  `llamacpp` engine path; documentation of the verified constraints.
- **Explicit exclusions:** the cloud Anthropic path (`cloud_backend.py`), the
  server-side training worker (`tokenpal/server/`), MTPLX's own launch flags,
  and any new `InferenceEngine` value or backend class.
- **Intent class:** bounded outcome.

## Non-goals
- No third `InferenceEngine` value. `InferenceEngine = Literal["ollama", "llamacpp"]`
  at `tokenpal/config/schema.py:8`; the `llamacpp` request shape is exactly what
  MTPLX accepts (see Decisions). A value that exists only to change two log
  labels is a first-use abstraction (CLAUDE.md "Don't abstract on the first use").
- No change to `HttpBackend` request construction. Every body the `llamacpp`
  branch emits was accepted by MTPLX in this session (Decisions, probes 1-6).
- No per-server `inference_engine`. The engine is global
  (`tokenpal/config/schema.py:114`) and both servers this Mac talks to (apollyon
  llama-server, local MTPLX) want the same `llamacpp` shape. Parked.
- No context-length probe for MTPLX and no `--validate` label change. Parked
  with evidence below.
- No MTPLX server reconfiguration (port, `--reasoning`, `--tool-prompt-mode`,
  session cache). TokenPal adapts to the server as the app launches it.

## Work
- Scope trace: DIRECT — the outcome is reproducible only if the constraints
  found this session are written where the LLM sub-doc convention puts them
  (`CLAUDE.md` sub-documentation table routes `HttpBackend` edits to
  `docs/claude/llm.md`).
- `docs/claude/llm.md` — add an "MTPLX (macOS, MLX)" bullet stating: use
  `inference_engine = "llamacpp"` with `api_url = "http://localhost:8000/v1"`;
  why the Ollama shape fails (`reasoning_effort: "none"` is rejected with
  `must be one of: auto, low, medium, high, xhigh`); `/props` and `/api/show`
  both 404 so `max_tokens` stays at the config default until the throughput
  estimator has three samples; MTPLX ignores the `model` field (one daemon, one
  model) so auto-adopt of the advertised id is cosmetic; `response_format`
  `json_object` and `json_schema` are grammar-enforced, including TokenPal's
  wrapper-less `{"type": "json_schema", "schema": ...}` shape; measured decode 16-22 t/s and
  cold prefill ~360-425 t/s on Qwen3.8-27B; MTPLX prefix cache needs a 512-token
  shared prefix in 256-token blocks and skips requests with `max_tokens <= 48`
  whose system prompt differs from the last foreground one.
- `config.default.toml` — extend the `inference_engine` comment (single line
  at `config.default.toml:54` at f11978e) so `"llamacpp"` is documented as the
  value for any llama-server-shaped OpenAI server, naming MTPLX on macOS
  alongside the AMD dGPU case.

Operational steps that are part of this phase but touch no repo file (the
per-machine `config.toml` is gitignored):
- On this Mac the live config is the repo-root `config.toml`
  (`~/.tokenpal/config.toml` does not exist; do not create one, it would shadow
  the repo-root file per the loader search order in `CLAUDE.md`). Set
  `[llm] api_url = "http://localhost:8000/v1"` there and keep
  `inference_engine = "llamacpp"`. `/server switch http://localhost:8000/v1`
  (`tokenpal/app.py:527-544`) is equivalent: it calls `set_api_url` and then
  persists `llm.api_url` to `config.toml` through `update_config`
  (`tokenpal/app.py:588-598`, reply text "Switching to ... (persisted)"), so
  there is no session-only switch; restore the old URL afterwards if the change
  was meant as a test. Operator decision 2026-09-01: MTPLX becomes this Mac's
  default.
- In the same `config.toml`, add `[llm.target_latency_s]` with
  `tools = 12.0`, `conversation = 20.0`, `research = 40.0` (section and keys per
  `config.default.toml:84-90`, mapped by `tokenpal/config/loader.py:90`). Leave
  observation, freeform, and idle_tool at defaults. Operator decision
  2026-09-01: longer replies at longer waits on this Mac only; at ~17 t/s this
  yields roughly 190 / 320 / 660 tokens for tools / conversation / research
  (research capped by `_MAX_TOKENS_HARD_CAP` 1024 and the 40 s budget).
- Leave `[llm.per_server_models]` alone. `_try_connect` adopts the advertised id
  (`tokenpal/llm/http_backend.py:104-117`) and `_model_available` has no reader
  outside the backend (`grep -rn model_available tokenpal/` returns only
  `http_backend.py` writes), so a stale pin cannot block generation.

## Decisions & findings
### Decision: treat MTPLX as the `llamacpp` engine  *(status: active)*
- **Rationale:** `_apply_thinking_controls` (`tokenpal/llm/http_backend.py:166-186`)
  sends `chat_template_kwargs.enable_thinking` + `reasoning_format: "deepseek"`
  on `llamacpp` and `reasoning_effort: "none" | "high"` otherwise. MTPLX rejects
  `"none"` (probe 1) and honors `enable_thinking: false` (probe 2, zero
  reasoning tokens, content clean). `_apply_cache_hints` adds `cache_prompt`
  (`:188-192`), which MTPLX ignores harmlessly (probe 3). `warmup()` is a no-op
  on `llamacpp` (`:654-676`), correct for a one-model daemon. `/model
  list|pull|browse` is disabled on `llamacpp` (`tokenpal/app.py:2595-2598`),
  correct because MTPLX has no registry.
- **Alternatives considered:** a third engine value. Rejected: it would change
  only `_check_inference`'s label/hint (`tokenpal/cli.py:92-94`) and the
  `/model` gate message; every request-shaping branch would duplicate the
  `llamacpp` one.
- **Evidence:** probes below, all run 2026-09-01 against the MTPLX app's
  server: launcher `mtplx serve --port 8000 --model
  .../Qwen3.8-27B-MTPLX-Optimized-Quality --reasoning auto --reasoning-effort
  medium` (pid 37317) and its child `mtplx.server.openai ... --tool-prompt-mode
  hybrid --reasoning-mode auto` (pid 37319, the process bound to 8000).

### Findings (live probes, 2026-09-01)
1. `POST /v1/chat/completions` with `reasoning_effort: "none"` → HTTP 400
   `reasoning_effort must be one of: auto, low, medium, high, xhigh`. The
   Ollama-shaped body cannot be used.
2. With `chat_template_kwargs: {enable_thinking: false}` → content only, no
   `reasoning_content`, `completion_tokens` 4-5. Without any control → thinking
   runs (32-146 reasoning tokens across probes, prompt-dependent, ~2-5 s) in a
   separate `reasoning_content` field; content stays clean either way.
3. `cache_prompt: true` and `reasoning_format: "deepseek"` accepted without
   error.
4. `tools` → `finish_reason: "tool_calls"` with well-formed `tool_calls[]`, in
   the server's default hybrid mode with no extra header. `tool_choice: "auto"`
   accepted.
5. `response_format` `json_object` and `json_schema` are grammar-enforced via
   llguidance token bitmasks (`mtplx/constrained.py:4`), and the parser accepts
   the wrapper-less `{"type": "json_schema", "schema": {...}}` shape TokenPal
   sends from `tokenpal/brain/research.py:752,760` (`constrained.py:160-171`,
   "Lenient shape some clients send"). A schema without
   `additionalProperties: false` still admits extra keys; that is the schema,
   not the server. Stronger than Ollama's advisory `response_format`.
6. `GET /v1/models` → one entry, `id: mtplx-qwen38-27b-optimized-quality`,
   `context_length: 65536`. `GET /props` → 404. `POST /api/show` → 404. So
   `_apply_auto_max_tokens` (`tokenpal/llm/http_backend.py:624-646`) returns
   without setting `_context_length`; `_derive_from_latency` (`:232-247`) still
   sizes caps from measured TPS once three samples exist.
7. Throughput: decode 16-22 t/s; cold prefill 360-425 t/s (1,226-1,468 token
   prompts, 2.9-4.1 s). Latency budgets in `TargetLatencyConfig`
   (`tokenpal/config/schema.py:79-89`) were sized for 57 t/s; the estimator
   scales caps down (observation budget 5 s minus ~1 s TTFT at ~17 t/s ≈ 65
   tokens, above the 40-token floor at `:98`).
8. Prefix cache: identical prompt → `cached_tokens` 1467/1468, prefill 0.12 s.
   Shared 1,024-token prefix with a different user turn → `cached_tokens`
   1024/1226, prefill 0.6 s (`block_prefix_boundary_clone`). Block size 256,
   minimum match 512 (`mtplx/session_bank.py:149-150` in the MTPLX runtime
   venv). Requests with `max_tokens <= 48` and a system prompt differing from
   the last foreground one are classed background and bypass the bank
   (`mtplx/engine_session.py:476-500`).
9. Real observation prompt built by `PersonalityEngine.build_prompt` with the
   default persona (measured this session with a scratch script that loaded the
   repo config, built three prompts with different context snapshots, and read
   MTPLX's `request_session_prefix_diagnostic`): 304-321 tokens, stable prefix
   118 tokens, prefill 0.9-1.2 s.
   Caching cannot apply (below 512) and does not need to; ~1 s TTFT plus a
   ~15-token reply lands well inside the 5 s observation budget. Conversation
   history is a growing prefix and will hit once it passes 512 tokens.
10. The server is on port **8000**, not 8080. Nothing listens on 8080 on this
    Mac (`lsof -iTCP:8080 -sTCP:LISTEN` empty).
11. `api_url` on localhost never triggers the Ollama fallback
    (`tokenpal/llm/http_backend.py:145-147`), so an MTPLX outage surfaces as
    "Could not reach LLM API" rather than a silent switch to Ollama.
12. Live run 1 (2026-09-01 22:41-22:44, `tokenpal --overlay console --verbose`
    under `script`): auto-adopt line present, `/props` 404 logged at DEBUG,
    typed reply produced (223 tokens in 10.3 s ≈ 21 t/s), no HTTP 400. But the
    estimator's first three samples were 35 tokens / 16.0 s, 28 / 2.2 s, 50 /
    15.6 s, giving `throughput measured: ≈4 t/s decode, 0.81s TTFT`, and that
    value was persisted to `~/.tokenpal/memory.db` table
    `llm_throughput_estimators`. Two causes, both real: (a) the operator was
    running opencode against the same MTPLX daemon at the time, and MTPLX runs
    `--scheduler-mode serial --batching-preset solo` (pid 37319 args), so
    TokenPal's requests queued behind opencode's; (b) `_record_sample`
    (`tokenpal/llm/http_backend.py:248-289`) splits wall-clock into TTFT and
    decode with one EWMA per server, so a tool-bearing call whose prompt is
    several thousand tokens (17 tools enabled in `config.toml`) books its
    prefill as slow decode. On apollyon prefill is fast enough to hide (b).
    Consequence: with decode estimated at 4 t/s the derived conversation cap
    is `(20 - 0.81) * 4 ≈ 77`, clamped to the 80-token floor, and the 223-token
    reply reached the user only through `_MAX_CONTINUATIONS=2` auto-continue.
    The raised budgets do not take effect until the EWMA recovers (α = 0.2).
    Observation bubbles are logged as `TokenPal says: ...`
    (`tokenpal/brain/orchestrator.py`), which the first grep missed.
13. Live run 2 (22:46-22:50) overlapped run 1's process, which never exited
    because `/quit` is not a console-overlay command (Ctrl+C is, and the
    `SIGINT` path at `tokenpal/ui/console_overlay.py:317` exits cleanly). The
    server had also been switched to `mtplx-qwen38-27b-optimized-speed` by then;
    auto-adopt followed it. One observation bubble (after a `memory_query` tool
    round) and one typed reply logged; estimator 13 t/s with the sibling
    instance competing. The contaminated 4 t/s row was deleted from
    `llm_throughput_estimators` before run 2.
14. Live run 3 (22:52-22:55, no other MTPLX client, clean Ctrl+C exit):
    `resuming throughput estimator: 13 t/s decode, 0.15s TTFT` on startup, two
    observation bubbles (1.6 s and 2.3 s wall-clock including a tool round
    each), samples 11.5-14.5 t/s, no 400. The typed line sent at 110 s produced
    no reply before the 160 s Ctrl+C; the reply path is proven by runs 1 and 2.
    The estimator's 13 t/s is systematically below the server-reported
    `predicted_per_second` (16-22 t/s in the probes) because it assigns part of
    each call's prefill to decode; the 15-25 t/s range in the original Done
    criterion was taken from server timings and was the wrong yardstick for the
    estimator line.

## Failure modes to anticipate
- **Port drift.** The MTPLX app chooses `--port 8000` at launch; if a later app
  version or a manual `mtplx serve` picks another port, TokenPal logs
  unreachable and the buddy goes quiet. `tokenpal --check` names the URL.
- **Thermal.** The brain loop keeps a 27B model busy every few seconds on a
  laptop; MTPLX runs `--fan-mode smart` with thermal polling. If the fans annoy,
  raise `[brain]` pacing rather than touching the server.
- **Estimator carry-over.** EWMAs are persisted per `(api_url, model)`
  (`docs/claude/llm.md` throughput bullet), so apollyon's 57 t/s numbers do not
  leak into the MTPLX key. First three calls use the static 150-token cap
  (~7-9 s at 17 t/s) before caps shrink to budget.
- **Thinking accidentally on.** Only `enable_thinking=None` with
  `disable_reasoning=false` in config would enable thinking; no call site passes
  `enable_thinking=True` (`grep -rn "enable_thinking=True" tokenpal/` empty).
  Thinking on costs ~5 s of reasoning tokens per call at this decode rate.

## Done criteria
- `tokenpal --check` with `api_url = "http://localhost:8000/v1"` prints
  `llama-server reachable at http://localhost:8000/v1` and zero problems
  (harness: `tokenpal/cli.py:_check_inference`, exists at f11978e). On the
  `llamacpp` path the model line echoes `config.llm.model_name` unconditionally
  (`tokenpal/cli.py:101-102`), so it proves nothing about MTPLX; the advertised
  id is proven by the next criterion.
- A live buddy session (`./run.sh --verbose`) against MTPLX logs
  `Server advertises 'mtplx-qwen38-27b-optimized-quality' ... auto-adopted`
  (`tokenpal/llm/http_backend.py:110-113`), produces at least one observation
  bubble and one typed-chat reply, and the log contains no `400` from
  `/chat/completions` and no `reasoning_effort` error.
- After three or more brain calls, the verbose log shows either the cold-start
  `throughput measured: ≈N t/s decode` line or the warm-start `resuming
  throughput estimator` line (`tokenpal/llm/http_backend.py:_record_sample` and
  `_seed_estimator_from_store`) for the MTPLX key, with the decode value
  recorded in Findings (measured 13 t/s in runs 2-3; see finding 14 for why it
  sits below the server's own 16-22 t/s).
- The raised budgets load from the repo-root `config.toml` (harness:
  `.venv/bin/python -c "from tokenpal.config.loader import load_config;
  t=load_config().llm.target_latency_s; assert (t.tools, t.conversation,
  t.research) == (12.0, 20.0, 40.0), t"` exits 0). `tokenpal --check` does not
  print budgets; the loader command is the canonical check.
- `docs/claude/llm.md` carries the MTPLX bullet; `config.default.toml`'s
  `inference_engine` comment names MTPLX. `pytest tests/test_server/test_llm_backend.py
  tests/test_max_tokens_auto_probe.py` stays green (no source change expected).

## Parking lot
- **Context length from `/v1/models`.** MTPLX (and vLLM, LM Studio) advertise
  `context_length` / `max_model_len` on the models entry `_try_connect` already
  fetches (`tokenpal/llm/http_backend.py:88-94`). A third fallback in
  `_apply_auto_max_tokens` would set `_context_length` without a native probe.
  Not required: with it absent the cap is `min(raw, 1024)` instead of
  `min(raw, 16384, 1024)`, identical for a 64k context. Useful for smaller-context
  servers only.
- **`--validate` label and hint.** `tokenpal/cli.py:93-94` prints "llama-server"
  and "start-llamaserver.bat" for every `llamacpp` engine; on this Mac the hint
  is wrong. Cosmetic; fix when a second non-llama-server target appears.
- **Per-server `inference_engine`.** Switching between an Ollama server and a
  `llamacpp`-shaped one with `/server switch` keeps the global engine and sends
  the wrong body shape. Pre-existing; both servers this Mac uses are
  `llamacpp`-shaped.
- **Estimator from server timings.** Both llama-server and MTPLX return a
  `timings` object on every completion (`prompt_ms`, `predicted_per_second`,
  `predicted_n`; observed in every MTPLX probe this session). Feeding those into
  `_record_sample` instead of inferring TTFT/decode from wall-clock would make
  the caps immune to prompt-size variance between paths and to queueing behind
  another client (finding 12). Ollama returns `prompt_eval_duration` /
  `eval_duration` on its native API only, so the OpenAI-compat path would keep
  the wall-clock fallback. Not in this plan: `HttpBackend` response parsing is
  outside the approved Work; surfaced to the operator as a follow-up.
- **MTPLX session header.** `x-mtplx-session-id` made the first shared-prefix
  call hit fully (1228/1229 cached) instead of at the 1024 block boundary.
  Worth ~0.5 s per conversation turn; not worth engine-specific headers today.

**Ship disposition 2026-09-01:** all five parking-lot items (the four above plus the estimator-from-server-timings note) filed together as GitHub issue #44. Nothing dropped.
