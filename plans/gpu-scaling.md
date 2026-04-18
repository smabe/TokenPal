# GPU-throughput-aware max_tokens scaling

**Status:** proposed — approval pending
**Issue:** [#35](https://github.com/smabe/TokenPal/issues/35)
**Supersedes:** `plans/gpu-scaling-stub.md` (delete on land)

## Problem

Today's `max_tokens` is a static config value (default 150 after
69eb085). Ollama path auto-derives from `/api/show`'s `context_length`
— but that's a context-safety cap, not a throughput cap. llamacpp
path doesn't probe at all. A 200 t/s setup and a 20 t/s setup both run
with the same static value; users hand-tune via
`[llm.per_server_max_tokens]`.

Result: either long output on fast hardware stalls observations (bad
voice), or fast hardware under-uses its budget on `/ask` (shallow
answers). The fix is to declare **target latency** per call path and
let the backend compute tokens from measured throughput.

## Goals

- **G1**  Callers declare intent (`target_latency_s=2.0`) instead of
  mechanical caps (`max_tokens=150`).
- **G2**  First observation after restart still works — no hard
  dependency on a warm-up probe succeeding.
- **G3**  Measurement is passive — piggyback on real generate calls,
  no synthetic warm-up burning tokens at startup.
- **G4**  Per-path budgets tunable in config, not hardcoded in call
  sites.
- **G5**  Honor user pins. A pinned `max_tokens` always wins; log
  (don't warn spam) when the measured suggestion differs.

## Non-goals

- **NG1**  Per-prompt-length adjustment. Cold/warm cache variance
  ignored for v1.
- **NG2**  Streaming latency targets (first-token-to-user). We only
  target completion latency.
- **NG3**  Cross-backend aggregation. Measurements are per-backend
  (per api_url + model pair).
- **NG4**  Calling-path inference. The caller passes explicit intent;
  we don't guess from the stack.

## Current state (audit)

Call sites of `generate` / `generate_with_tools` in prod:

| Site | Today's cap | target_latency_s |
|---|---|---|
| `orchestrator._generate_comment` (observation) | default | 5.0 |
| `orchestrator._generate_freeform_comment` | default | 6.0 |
| `orchestrator._generate_tool_riff` (idle-tool) | default | 6.0 |
| `orchestrator._handle_user_input` (conversation) | derived-or-300 | 12.0 |
| `orchestrator._handle_user_input` continuations | carry | (derived) |
| `orchestrator._generate_with_tools` (multi-round) | per-call | 8.0 |
| `research._research_synth` | 400 | 20.0 |
| `research._sub_query` | explicit | 8.0 |
| `agent._llm.generate_with_tools` (agent loop) | default | 8.0 |

Sizing rationale: at 57 t/s (Qwen3-14B-Q4 on 9070 XT), 5s observation
= ~285 tokens — comfortable headroom for 2-sentence quips plus a
tool-calling riff. On a 15 t/s laptop iGPU, same budget = ~75 tokens
— still clears a normal observation. Non-observation paths doubled
for consistent scaling.

Only two sites pass an explicit `max_tokens` today (`research.py:271`
with 400, and the conversation continuation path). Everywhere else
inherits the backend default. That's the lever: drop explicit caps in
those two call sites in favor of `target_latency_s` and route the rest
through the new default-derivation.

llama-server capability probe:

- `GET /props` returns `default_generation_settings.n_ctx`,
  `default_generation_settings.n_predict`, and model name. Parity with
  Ollama's `/api/show` for the context-length number.
- Per-response `timings` object breaks out
  `prompt_per_second` / `predicted_per_second`. That's the cleaner
  throughput source than our stopwatch-around-`generate`.
- OpenAI-compat `/v1/chat/completions` responses DO NOT include
  `timings` by default — we'd need to either probe the native
  `/completion` endpoint at setup, or switch to using `usage.completion_tokens`
  plus our existing `latency_ms` for tps estimation.

Recommendation: use `usage.completion_tokens / latency_s` as the
primary throughput signal. Works across Ollama + llamacpp OpenAI-compat
paths, no new endpoint.

## Design

### Data

On `HttpBackend`:

```python
@dataclass
class ThroughputMeasurement:
    completion_tokens: int
    elapsed_s: float
    timestamp: float

class HttpBackend:
    _throughput_samples: deque[ThroughputMeasurement]  # maxlen=10
    _measured_tps: float | None  # median of samples, None until 3+ samples
```

Measurements:

- Every `generate` / `generate_with_tools` call records a sample when
  it completes successfully with `completion_tokens > 0`.
- Median of the last 10 is the steady-state tps estimate.
- Samples cleared on model swap / backend reconnect (same places we
  already clear `_derived_max_tokens`).
- Below 3 samples we fall back to the current static cap.

Bootstrap: the first ~3 real generate calls use the existing static
default. By the 4th call we have a measurement. No dedicated warm-up
round.

### Per-path budgets

New config table:

```toml
[llm.target_latency_s]
observation  = 5.0
freeform     = 6.0
idle_tool    = 6.0
tools        = 8.0        # /agent one-round, tool-calling observation round
conversation = 12.0
research     = 20.0
```

`target_latency_s` stays in a compact, named-slot config rather than
propagating through every call site as a float. Orchestrator code
imports these constants (or reads them off the backend) and passes
a `path: Literal[...]` string to `generate`.

### API shape

Extend `AbstractLLMBackend.generate` with an optional `target_latency_s`:

```python
async def generate(
    self,
    prompt: str,
    max_tokens: int | None = None,
    *,
    target_latency_s: float | None = None,
    ...
) -> LLMResponse: ...
```

Resolution order inside `HttpBackend.generate`:

1. If `max_tokens` passed explicitly → use as-is (honors user code
   that knows what it wants).
2. Else if user-pinned `max_tokens` for this server → use pin.
3. Else if `target_latency_s` passed AND we have `_measured_tps` →
   `int(target_latency_s * _measured_tps)`, clamped to `ctx_length // 4`
   and to `MAX_TOKENS_HARD_CAP`.
4. Else → fall back to current `_max_tokens` default.

Same rule applied in `generate_with_tools`. No existing caller breaks:
passing nothing still works, passing `max_tokens=N` still works.

### Call-site migration

`orchestrator._generate_comment`: currently `self._llm.generate(prompt)`.
New: `self._llm.generate(prompt, target_latency_s=self._budgets.observation)`.
`self._budgets` is a cached `TargetLatencyBudgets` dataclass read from
the config on construct.

Six sites migrate. Four of them (observation, freeform, idle-tool,
conversation) we just add the kwarg. The two explicit-max_tokens sites
(`research.py:271`, conversation continuation) stay as-is — user code
that knew what it wanted still takes precedence per rule 1.

### Log discipline

- INFO once when we cross the 3-sample threshold ("measured ≈ 57 t/s").
- INFO when we swap models and discard samples.
- DEBUG per-call only with `--verbose` — the sample record itself is
  too chatty for INFO.
- If user-pinned `max_tokens < measured_suggestion`, INFO once per
  session ("user-pinned cap leaves 40 tokens on the table per
  observation"). Don't warn-spam per call.

### llama-server `/props` probe

Add a companion to `_probe_context_length`:

```python
async def _probe_llamacpp_props(self) -> int | None:
    """llama-server's equivalent of /api/show for n_ctx."""
```

Called in `_apply_auto_max_tokens` when `inference_engine == "llamacpp"`
and Ollama's probe returns None. Closes the llamacpp context-length
gap independently of the throughput work — useful even if the
throughput measurement isn't available yet.

## File-level changes

### NEW
- `tests/test_llm/test_throughput_scaling.py` — mocks `HttpBackend._client`
  to drive sample accumulation, asserts resolution order 1-4.

### EDIT
- `tokenpal/llm/http_backend.py` — add `_throughput_samples`,
  `_measured_tps`, the `/props` probe, and the resolution logic in
  `generate` + `generate_with_tools`.
- `tokenpal/llm/base.py` — new optional `target_latency_s` kwarg on the
  abstract method signature.
- `tokenpal/config/schema.py` — `LLMConfig.target_latency_s` nested
  dataclass with the six slots, defaults as above.
- `config.default.toml` — annotated `[llm.target_latency_s]` stubs.
- `tokenpal/brain/orchestrator.py` — route the six observation/freeform/
  idle-tool/conversation/tools/research-synth call sites through the
  new kwarg.
- `tokenpal/brain/research.py` — synth call site gets
  `target_latency_s=10.0`; drop the explicit `max_tokens=400`.
- `tokenpal/brain/agent.py` — agent-loop call site gets
  `target_latency_s=4.0`.
- `CLAUDE.md` — one line under LLM Notes pointing to the new scaler.

## Tests

- `test_throughput_scaling.py`
  - `test_first_calls_use_static_default_before_three_samples`
  - `test_three_samples_triggers_measured_tps_switch`
  - `test_explicit_max_tokens_overrides_measurement`
  - `test_user_pin_beats_measurement`
  - `test_measurement_clamped_to_context_quarter`
  - `test_measurement_clamped_to_hard_cap`
  - `test_model_swap_clears_samples`
  - `test_low_sample_count_after_clear_falls_back_to_static`
  - `test_median_resistant_to_one_slow_call`
- Extend `test_http_backend.py` with a `/props` probe test for
  llamacpp.

## Sequencing

- **Phase 1** — ship the `/props` probe for llamacpp. Standalone;
  closes a legit gap without depending on the rest. ~0.5 day.
- **Phase 2** — throughput accumulator + resolution order, behind a
  feature flag `[llm] target_latency_scaling = false` default OFF.
  Wire the six orchestrator sites. ~2 days.
- **Phase 3** — flip the flag on via dogfood config. Watch real
  generation times — if observations consistently hit the 5s cap we
  over-shot and should trim; if they finish fast we're not burning
  anything, just sitting at headroom.
- **Phase 4** — default ON in `config.default.toml`, flag deletable.
  Update CLAUDE.md + close #35.

## Open decisions (for approval)

1. **Rolling-median window size.** 10 samples. Could be shorter (5) for
   faster adaptation at some noise cost. Verdict: 10 unless dogfood
   shows lag.
2. **Per-path target_latency_s in config vs hardcoded.** Config adds
   surface area. Hardcoded (per-path constants in `orchestrator.py`)
   is simpler. Leaning config — lets the user tune per rig without a
   rebuild.
3. **What to do if `completion_tokens` is missing from the response**
   (some llama-server builds omit it under tool-calling). Fall back
   to stopwatch-only estimation or skip the sample entirely? Leaning
   skip — keeps the median honest.
4. **Should the conversation continuation path re-measure mid-run?**
   Today it pins max_tokens across continuations. With target_latency,
   each continuation could re-derive — but that risks drift across a
   single reply. Leaning pin-on-first-continuation and carry.
5. **Ollama `/api/show` already gives us context_length; do we still
   need `/props` on the llamacpp path if we're measuring tps directly?**
   Yes — `ctx_length // 4` is the upper clamp (rule 3). Without it the
   llamacpp path would clamp to `MAX_TOKENS_HARD_CAP` (1024) only,
   potentially above the model's safe ceiling.

## Known risks

- **Measurement noise from cache hits.** llama-server's host-memory
  cache makes the second call with an overlapping prefix very fast.
  That inflates the tps estimate, which expands max_tokens, which may
  then stall on a genuine cold-prompt call. Mitigation: samples are a
  rolling median, so one fast hit can't dominate. If dogfood shows
  clear bimodality, drop to the 20th-percentile sample instead of median.
- **Tool-calling round-trips.** `_generate_with_tools` issues up to 3
  LLM calls per observation. Target latency is per-path not per-round,
  so a 4s tools budget divided across 3 rounds is ~1.3s each. Either
  divide in the backend or let callers pass a per-round budget.
  Leaning pass `target_latency_s / max_rounds` from the multi-round
  call site.
- **Config footgun.** User sets `observation = 0.5` and all quips
  truncate mid-sentence. Mitigation: floor each slot at 1.0s; log the
  clamp once on startup.
- **User-pinned `max_tokens` interaction with research's explicit
  400.** Today the user pin overrides the call-site default. Under
  the new rules, an explicit `max_tokens=400` (rule 1) beats both the
  user pin and the measurement. That's a behavior change — users who
  pinned low expecting it to cap everything will see research ignore
  the pin. Document clearly; consider adding a `force_pin = true`
  escape hatch if dogfood complains.
