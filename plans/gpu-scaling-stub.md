# GPU-throughput-aware max_tokens scaling — STUB

**Status:** parked — expand into a real plan before implementation
**Issue:** [#35](https://github.com/smabe/TokenPal/issues/35)

## One-line summary

Replace the static/context-only max_tokens derivation with a dynamic
budget sized by measured GPU throughput × target latency per path.

## Today (what exists)

- **Ollama**: probe `/api/show` → `context_length` → `min(ctx//4, 1024)`.
- **llamacpp**: no probe at all. Uses config value.
- **No throughput measurement anywhere.**
- Users hand-tune per server via `[llm.per_server_max_tokens]`.

## Why punt it for now

Not blocking on enrichment or observation quality. A one-line default
bump (60→150) closes 80% of the gap for this user's setup without
new infrastructure. Dynamic scaling is worth doing properly, not
rushed as a companion to another plan.

## When to revisit

After the app-enricher lands and bakes. When picked back up:

1. Read issue #35 carefully — the shape is sketched there.
2. Audit every call site of `_llm.generate` / `generate_with_tools` —
   categorize each by target latency (observation/freeform/ask/research/
   conversation).
3. Decide: single dynamic cap per backend, or per-path? (Per-path is
   correct but needs per-call overrides threaded through.)
4. Design the warm-up probe — which model, which prompt length, how
   many runs to average, cache across reconnects?
5. Figure out llamacpp `/props` parity — does it exist, does it give us
   `n_ctx`, does it give us anything about the GPU?

## Related

- M3 idle-tool-roll plan (`plans/idle-tool-rolls-m3.md`) also needs
  generation budgets for the LLM-initiated tool-use path; may want to
  land M3's conservative pinned cap first and fold both into the
  dynamic scaler later.
- `CLAUDE.md` — update the `[llm]` section once the scaler ships.
