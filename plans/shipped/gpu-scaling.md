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

## Prior art

No published system does exactly this (client-side, piggyback-measured,
per-caller latency budget), but the serving-side literature settles the
vocabulary and the structural pitfalls. Terms borrowed: **TTFT** (time
to first token, prefill-dominated), **TPOT / ITL** (time per output
token / inter-token latency, decode-dominated), **latency SLO / deadline**
(the caller-declared target). Non-borrowed: **goodput** (SLO-attaining
throughput), which is a server fleet metric that doesn't apply to a
single-user desktop app.

Two lessons shaped this design:

- **Prefill and decode are separate-variance regimes.** KV-cache hits
  collapse prefill; decode is steadier but thermally sensitive. Papers
  (DistServe, SCORPIO) all separate them. Our first-pass plan used a
  single rolling-median tps; rewriting to `(target - ttft) * decode_tps`.
- **Token elasticity** (arxiv 2412.18547): a tight `max_tokens` doesn't
  make the model compress its answer, it truncates mid-sentence. Hence
  the `min_tokens_per_path` floor.

llama.cpp and Ollama both have per-request **reasoning** budget
discussions open (llama.cpp #21445, Ollama #10925) but no per-request
**latency** target. This feature puts TokenPal ahead of both upstream
projects in that dimension.

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

On `HttpBackend`. Prior-art note: production serving stacks (vLLM,
SGLang) treat prefill and decode as separate-variance regimes — prefill
absorbs KV-cache-hit bimodality, decode is steadier but thermally
sensitive. Collapsing them into one rolling tps figure is what blows
up the estimate on short-budget paths like observations.

```python
@dataclass
class ThroughputSample:
    prompt_tokens: int
    completion_tokens: int
    total_elapsed_s: float  # wall-clock around the HTTP call
    timestamp: float

class HttpBackend:
    _samples: deque[ThroughputSample]                 # maxlen=20
    _decode_tps_ewma: float | None                    # α=0.2, None < 3 samples
    _ttft_ewma_s: float | None                        # α=0.2, None < 3 samples
    _estimator_key: tuple[str, str] | None            # (server_url, model)
```

Measurements, per successful call with `completion_tokens > 0`:

- Record one `ThroughputSample`.
- Derive per-call `decode_tps = completion_tokens / max(total_elapsed_s - ttft_estimate, epsilon)`;
  feed into EWMA. For the very first sample (no TTFT estimate yet)
  approximate `decode_tps ≈ completion_tokens / total_elapsed_s` and
  carry a zero TTFT — subsequent samples correct it.
- Derive per-call `ttft_estimate_s = total_elapsed_s - completion_tokens / decode_tps_ewma`;
  feed into TTFT EWMA. Floor at 0.
- EWMA α = 0.2 — responds in ~5 samples, matches vLLM internals.
  Open to dogfood tuning.
- Estimators cleared when `_estimator_key` changes (model swap /
  server switch); seeded from persisted state otherwise (see below).
- Below 3 samples both estimators are `None`; resolution falls back
  to the current static cap.

### Cold-start seeding

A machine that's been run before shouldn't relearn its own GPU every
restart. Persist `(server_url, model) → (decode_tps_ewma, ttft_ewma_s,
sample_count)` in `memory.db` (new table `llm_throughput_estimators`,
same 0o600 file). On connect, if the key matches, seed the in-memory
EWMAs and skip the 3-sample bootstrap. If the key doesn't match (first
time on this model, or hardware change), fall back to static default
until 3 real samples accumulate.

Write-back: throttled to once per minute during steady state, always
on graceful shutdown. Missing/corrupt row → ignore, fall back to
bootstrap path.

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

[llm.min_tokens_per_path]
observation  = 40         # token-elasticity floor: below this, quips truncate
freeform     = 40
idle_tool    = 40
tools        = 60
conversation = 80
research     = 120
```

`target_latency_s` stays in a compact, named-slot config rather than
propagating through every call site as a float. Orchestrator code
imports these constants (or reads them off the backend) and passes
a `path: Literal[...]` string to `generate`.

**Token-elasticity floor.** Per arxiv 2412.18547, models don't compress
output when given a tight cap — they get truncated mid-sentence. The
`min_tokens_per_path` table sets a lower bound for the derived cap so
an unlucky decode_tps estimate can't produce a 15-token observation
that cuts off at "I see you're in Chro—". Ceiling is still
`ctx_length // 4` and `MAX_TOKENS_HARD_CAP`.

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
3. Else if `target_latency_s` passed AND both `_decode_tps_ewma` and
   `_ttft_ewma_s` are populated →
   `int((target_latency_s - _ttft_ewma_s) * _decode_tps_ewma)`,
   clamped to `[min_tokens_for_path, ctx_length // 4, MAX_TOKENS_HARD_CAP]`.
   If `target_latency_s ≤ _ttft_ewma_s` (user set a budget smaller
   than typical prefill), clamp to `min_tokens_for_path` and INFO-log
   once per session.
4. Else → fall back to current `_max_tokens` default.

Why `(target - ttft) * decode_tps` not `target * total_tps`: TTFT can
be 0.5–2s on a cold prompt; decode on Qwen3-14B-Q4 runs 50+ t/s. A 1s
TTFT misestimate wipes out a 50-token error in the cap. Keeping the
two estimators separate is what makes the 5s observation budget
correct on both warm and cold calls.

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

### Tool-calling budget propagation

A 5s observation that calls `search_web` is really N sequential
generations separated by tool I/O. Dividing `target_latency_s / N`
statically wastes budget if an early round finishes fast. The
orchestrator-layer approach: stash a `deadline_monotonic_s = now + target_latency_s`
at the top of the multi-round call and pass `remaining = deadline - now`
as `target_latency_s` into each inner `generate`. Last round gets
whatever runway is left. Floor per-round at `min_tokens_for_path` so
a burned-down budget still yields a coherent final turn.

Implementation: pass `target_latency_s` through the existing
`_generate_with_tools` loop in `orchestrator.py`, recomputing before
each round. No new abstraction.

### Log discipline

- INFO once when we cross the 3-sample threshold ("measured ≈ 57 t/s
  decode, 0.8s TTFT").
- INFO when we swap models/servers and discard samples. INFO when we
  seed from persisted state ("resuming estimator: 57 t/s, 0.8s TTFT,
  142 prior samples").
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
- `tokenpal/llm/http_backend.py` — add `_samples`, `_decode_tps_ewma`,
  `_ttft_ewma_s`, `_estimator_key`, the `/props` probe, seeding from
  persisted estimator row, throttled write-back, and the resolution
  logic in `generate` + `generate_with_tools`.
- `tokenpal/llm/base.py` — new optional `target_latency_s` kwarg on the
  abstract method signature.
- `tokenpal/config/schema.py` — `LLMConfig.target_latency_s` +
  `LLMConfig.min_tokens_per_path` nested dataclasses with the six
  slots each, defaults as above.
- `config.default.toml` — annotated `[llm.target_latency_s]` and
  `[llm.min_tokens_per_path]` stubs.
- `tokenpal/memory/store.py` (or wherever `memory.db` DDL lives) —
  new `llm_throughput_estimators` table keyed `(server_url, model)`.
- `tokenpal/brain/orchestrator.py` — route the six observation/freeform/
  idle-tool/conversation/tools/research-synth call sites through the
  new kwarg; add the deadline-propagation loop in `_generate_with_tools`
  so each round gets remaining-wall-clock.
- `tokenpal/brain/research.py` — synth call site gets
  `target_latency_s=20.0`; drop the explicit `max_tokens=400`.
- `tokenpal/brain/agent.py` — agent-loop call site gets
  `target_latency_s=8.0`.
- `CLAUDE.md` — one line under LLM Notes pointing to the new scaler.

## Tests

- `test_throughput_scaling.py`
  - `test_first_calls_use_static_default_before_three_samples`
  - `test_three_samples_populate_decode_and_ttft_ewmas`
  - `test_resolution_uses_target_minus_ttft_times_decode_tps`
  - `test_explicit_max_tokens_overrides_measurement`
  - `test_user_pin_beats_measurement`
  - `test_measurement_clamped_to_context_quarter`
  - `test_measurement_clamped_to_hard_cap`
  - `test_measurement_floored_to_min_tokens_per_path`
  - `test_target_below_ttft_clamps_to_min_and_logs_once`
  - `test_model_swap_clears_estimators_and_reseeds_from_db`
  - `test_first_time_model_seen_falls_back_to_bootstrap`
  - `test_ewma_tracks_thermal_drift_over_rolling_samples`
  - `test_ewma_not_whipsawed_by_one_fast_cache_hit`
  - `test_persisted_estimator_roundtrip_across_restart`
- `test_tool_deadline_propagation.py`
  - `test_multi_round_tool_loop_divides_remaining_wallclock`
  - `test_last_round_floors_at_min_tokens_when_budget_exhausted`
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

1. **EWMA α.** 0.2 (responds in ~5 samples, matches vLLM internal
   estimators). Rolling median was the original pick; switched after
   prior-art review — median's strength is outlier rejection, and the
   TTFT/decode split already isolates the cache-hit bimodality that
   motivated it. Lower α = steadier, higher α = faster to thermal
   drift. Verdict: 0.2 unless dogfood shows lag.
2. **Per-path target_latency_s in config vs hardcoded.** Config adds
   surface area. Hardcoded (per-path constants in `orchestrator.py`)
   is simpler. Leaning config — lets the user tune per rig without a
   rebuild.
3. **What to do if `completion_tokens` is missing from the response**
   (some llama-server builds omit it under tool-calling). Fall back
   to stopwatch-only estimation or skip the sample entirely? Leaning
   skip — keeps the EWMA honest.
4. **Should the conversation continuation path re-measure mid-run?**
   Today it pins max_tokens across continuations. With target_latency,
   each continuation could re-derive — but that risks drift across a
   single reply. Leaning pin-on-first-continuation and carry.
5. **Ollama `/api/show` already gives us context_length; do we still
   need `/props` on the llamacpp path if we're measuring tps directly?**
   Yes — `ctx_length // 4` is the upper clamp (rule 3). Without it the
   llamacpp path would clamp to `MAX_TOKENS_HARD_CAP` (1024) only,
   potentially above the model's safe ceiling.
6. **Persisted estimator invalidation.** Besides model/server change,
   when else should we discard the row? Proposal: version the schema
   with a `schema_version` column and drop rows on upgrade. GPU driver
   changes aren't detectable — the EWMA will just re-converge in ~5
   calls, which is fine.

## Known risks

- **Measurement noise from cache hits** (largely absorbed by TTFT/decode
  split). Cache hits collapse prefill, not decode, so `_ttft_ewma_s`
  carries the bimodality and `_decode_tps_ewma` stays steady. Residual
  risk: llama-server can serve part of a long completion from cache on
  continuation — if dogfood shows `decode_tps` spiking on those calls,
  gate samples where `prompt_tokens` overlaps the prior prompt.
- **Persisted estimator goes stale.** A user swaps their GPU but keeps
  the same model+server URL — the DB row mis-seeds decode_tps by 3×.
  EWMA re-converges in ~5 calls, which is survivable, but the first
  few observations could overrun or underuse. Accepted; mitigation via
  `schema_version` bump when we know we've invalidated the priors.
- **Tool-calling round-trips** — addressed above under "Tool-calling
  budget propagation." No longer a static divide; deadline is tracked
  at the orchestrator layer and each round gets remaining wall-clock.
- **Config footgun.** User sets `observation = 0.5` and all quips
  truncate mid-sentence. Mitigation: floor each slot at 1.0s; log the
  clamp once on startup. Separately, `min_tokens_per_path` provides a
  second safety net — derived cap never dips below a complete-one-liner
  budget.
- **User-pinned `max_tokens` interaction with research's explicit
  400.** Today the user pin overrides the call-site default. Under
  the new rules, an explicit `max_tokens=400` (rule 1) beats both the
  user pin and the measurement. That's a behavior change — users who
  pinned low expecting it to cap everything will see research ignore
  the pin. Document clearly; consider adding a `force_pin = true`
  escape hatch if dogfood complains.
- **EWMA cold-start bias on first sample.** We approximate
  `decode_tps ≈ completion_tokens / total_elapsed_s` for sample 1 (no
  TTFT yet). That's biased low — actual decode is faster than the
  prompt-included estimate. Second and third samples correct it once
  the TTFT EWMA has data. Quantified risk: the 4th-call derived cap is
  ~15-20% tight; observation still completes well, just with a bit
  more headroom than optimal. Accepted.
