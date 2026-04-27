# Icant-read — log spam tighten

## Goal
Make `tokenpal --verbose` readable live by taming the highest-rate log emitters, starting with the 2s context dump. Preserve enough signal in the file log for postmortem debugging.

## Answer to user's question
The buddy does NOT need the context log every 2s to function. `orchestrator.py:643` only logs `snapshot` for human inspection — the snapshot itself is built and consumed by the LLM regardless. Suppression / rate-limit of the log line is purely observational and safe.

## Non-goals
- No new logging framework (structlog, loguru). stdlib `logging` stays.
- No per-module verbosity UX (`--verbose-brain` etc.) yet — revisit only if phase 1-2 leave terminal still unreadable.
- No change to user-facing chat output, comment log, or memory.db logging.
- No reduction of WARNING/ERROR-level lines.
- No tightening of the rotating file log's verbosity — file is for postmortem, terminal is what hurts.
- No new abstraction (`LogRateLimiter` class, `log_changes_only` helper) unless a second consumer materializes inside this plan.

## Files to touch
- `tokenpal/brain/orchestrator.py` — gate the every-2s context dump behind change-detection + ≥30s heartbeat (line 643).
- `tokenpal/brain/orchestrator.py` (other DEBUGs) — audit the 85 logger calls in this file for any other per-tick emitters; demote or drop.
- TODO investigate after phase 1: top runners-up — `tokenpal/brain/research.py` (37 calls), `tokenpal/brain/idle_tools*.py` (24), `tokenpal/audio/vad.py` (10). Only touch if they fire ≥once/tick during normal idle.

## Failure modes to anticipate
- Change-detection key too coarse → context line never emits when meaningful sub-fields change (e.g., app stays the same but git status moves). Snapshot string equality should be safe but verify.
- Change-detection key too fine → snapshot includes a timestamp or floating sense (e.g., `idle for Xs`) that ticks every snapshot, defeating dedupe. Need to inspect what `ContextWindowBuilder.snapshot()` actually returns.
- Heartbeat clock drift if brain loop stalls — must wall-clock the heartbeat, not count ticks.
- Premature abstraction: writing a `LogRateLimiter` for the single context-log caller. Inline `_last_emitted` state on the orchestrator instance; promote only if a second site needs it inside this plan.
- File log silently loses the per-tick context history — acceptable per non-goal, but call out in commit message so future-me knows where it went.
- `--verbose` users who *want* the full context dump for a deep-debug session lose it. Mitigation: keep an env-var escape hatch (`TOKENPAL_LOG_CONTEXT_FULL=1`) that restores per-tick emit. Cheap, one branch.

## Done criteria
- `tokenpal --verbose` running idle for 2 minutes produces terminal output a human can read in real time (no per-tick context dump dominating the screen).
- Context log still emits when the snapshot string materially changes, and at minimum every 30s as a heartbeat.
- `TOKENPAL_LOG_CONTEXT_FULL=1 tokenpal --verbose` restores the per-tick dump for deep debugging.
- No new top-level helper / class introduced (state lives on the orchestrator).
- Phase 2 audit: surface counts of any other periodic emitters identified, with one-liner per emitter (kept / demoted / dropped). If audit finds none worth touching, plan ships at end of phase 1.

## Parking lot
(empty)
