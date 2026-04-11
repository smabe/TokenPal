# Pipeline Hardening

> **Confirmed**: pipeline = remote fine-tuning (`remote_train.py` + embedded `_INSTALL_SH` + `finetune_voice.py`).
>
> **Graphify-validated scope** (2026-04-10): repo-wide graph analysis confirms the modularity concern is localized to `remote_train.py` — specifically its test cluster (56 nodes, cohesion 0.06, 5x bigger than any other test cluster and tied for lowest cohesion with the kitchen-sink docs community). Rest of codebase is cleanly decomposed. Attach-mode support for already-running training is **deferred to a separate `remote-train-phase-extraction.md` plan** because it requires restructuring `remote_finetune()` into phase functions (180-line re-indent). This plan stays bolt-on hardening only.

## Goal
Take the remote fine-tuning pipeline from "works on the happy path" to "survives the ugly paths" — flakes, partial state, concurrent runs, weird hosts — without adding new features.

## Non-goals
- No new backends (MLX, Intel NPU inference, remote serving — those live in separate plans)
- No new model support beyond what's already configured
- No UI changes to `/voice finetune` command surface
- No multi-remote support (Phase 5, deferred)
- No GGUF export rework
- No test infra overhaul — add targeted tests only where hardening lands
- No ROCm 7.3 work (blocked upstream)
- **No attach-to-running-training mode** — if preflight detects a live training session for the same slug, raise `RemoteTrainError` with a `tmux attach` hint. Actual attach-and-stream is deferred to `remote-train-phase-extraction.md` because it requires decomposing `remote_finetune()`.
- **No phase extraction of `remote_finetune()`** — that's the follow-up plan, not this one. This plan bolts onto the existing linear structure.

## Files to touch
- `tokenpal/tools/remote_train.py` — new `RemoteState` dataclass + `_preflight_remote_state()` helper, stale-flock auto-remove, venv integrity check, dead-tmux cleanup, sentinel validation replacement, base model integrity, Ollama register recovery. `_INSTALL_SH` is inlined at lines 36–190 (no separate file to touch).
- `tokenpal/tools/finetune_voice.py` — HF_TOKEN error surfacing
- `tests/test_tools/test_remote_train.py` — new tests covering each preflight state branch, extends existing `_MockSSH` routing

## Failure modes to anticipate
- SSH drops mid-training but tmux session survives — detect and surface with actionable hint (`ssh host 'tmux attach -t tokenpal-<slug>'`). True resume-and-stream is out of scope here.
- `flock` lock file left behind after SIGKILL — stale lock blocks next run forever. Auto-remove with WARN log (approved policy — user does not want manual SSH friction).
- Partial wheel install when `pip` bombs halfway (e.g., WSL SSL flake) — sentinel file lies
- Base model download interrupted → `config.json` exists but weights are truncated → `_ensure_base_model` passes but training OOMs on load
- rsync partial pull leaves `.safetensors.partial` in output dir, sha256 mismatch on retry
- Concurrent `finetune` invocations on the same remote racing the same bundle path
- HF_TOKEN expired or revoked — current error message is opaque
- Disk fills up mid-training (checkpoint + logs + base model) — no preflight beyond the 25GB warning
- tmux session name collision if previous run didn't clean up
- Checkpoint resume picks up a corrupted checkpoint from a crashed run
- Training log tee'd to `train.log` grows unbounded on long runs
- Ollama register step fails after successful merge → user has safetensors but no model, no clear recovery path
- Network drop during `_run_rsync` — `--partial` helps but caller doesn't distinguish "resume" from "restart"
- Wheel bundle hash collision across Python versions on the remote

## Done criteria
- Every failure mode above has either: (a) a code path that handles it, or (b) an explicit decision to ignore with a comment saying why
- Stale `flock` auto-removed (with WARN log) before next training run — no manual SSH intervention required
- Dead tmux session (exists but no live process) detected and cleaned up, not silently swallowed
- Venv integrity check replaces `.install-ok` grep — runs `python -c "import torch"` on remote
- `_INSTALL_SH` clears `.install-ok` on any failure path before exit (defense in depth for the above)
- Partial file cleanup (`*.partial`, truncated safetensors) on pull failure
- Base model integrity check goes beyond `config.json` grep — verify at least one weight shard exists and has nonzero size
- Ollama register failure → clear recovery hint pointing at local safetensors path
- HF_TOKEN expired → actionable error, not opaque HTTP 401
- `train.log` rotation/truncation on long runs (decision: probably just cap the tail we read, not actually rotate)
- Targeted tests added for the new recovery paths (not aiming for coverage — aiming for the specific failure modes)
- Manual test on geefourteen: kill a training run mid-flight via `tmux kill-session`, re-run, confirm clean preflight recovery
- Manual test: corrupt `.venv/.install-ok` while `.venv` is empty, re-run, confirm sentinel check forces reinstall
- All existing 135 tests still pass
- No new lint/mypy errors
- CLAUDE.md updated if any new gotchas land that future-Claude needs to know

## Commit sequencing
1. **Commit 1 — preflight cluster**: `RemoteState` + `_preflight_remote_state()` + stale-flock auto-remove + venv integrity check + dead-tmux cleanup + live-training error with hint. **Tested on geefourteen before commit 2.**
2. **Commit 2 — install.sh sentinel discipline**: clear `.install-ok` on any install failure path.
3. **Commit 3 — base model + pull integrity**: partial file cleanup, weight shard existence check.
4. **Commit 4 — error surfacing**: HF_TOKEN, Ollama register recovery, wheel bundle hash collision detection.
5. **Commit 5 — log hygiene**: `train.log` tail cap.

Each commit passes lint/mypy/tests on its own. No stacking.

## Parking lot
- **(skill-meta, not pipeline-meta)** `/plan` skill should run research agents + a brainstorm pass *after* approval but *before* coding starts. Today it drafts from conversation context only, so recommendations like "start with the stale-state cluster" are based on a quick `wc -l` + one grep instead of an actual read of the 1184-line file. Better home: GitHub issue against the plan skill, or a `plan-skill-v2` plan file — flagging here so it's not lost.
