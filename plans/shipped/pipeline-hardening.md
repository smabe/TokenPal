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
- `CLAUDE.md` + `docs/remote-training-guide.md` — docs updates when recovery mechanism changes (commit 2 retired the sentinel file)

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
- ✅ Stale `flock` auto-removed (with WARN log) before next training run. **Live-verified on geefourteen** (commit 1).
- ✅ Dead/orphan tmux session detected and cleaned up with explicit kill-session, not silently swallowed. **Live-verified** (commit 1).
- ✅ Venv integrity check replaces `.install-ok` grep — runs `python -c "import torch"` on remote (commit 1).
- ✅ Sentinel file retired entirely from `_INSTALL_SH`. Docs updated to `rm -rf ~/tokenpal-training/.venv` instead (commit 2).
- ✅ Base model integrity check requires nonzero weight shards (`find -name '*.safetensors' -size +0c`), not just `config.json` grep. **Live-verified on geefourteen**: full state → `BASE_MODEL_OK`, weights hidden → check correctly fails (commit 3).
- ✅ Pull failure raises `RemoteTrainError` with `rm -rf LOCAL_DIR` recovery hint (commit 3).
- ✅ sha256 mismatch after pull escalated from silent warning → hard error with recovery hint. Corrupted models no longer reach Ollama registration (commit 3).
- ✅ HF_TOKEN expired/missing → `RemoteTrainError("auth")` with specific fix instructions (where to set the token, where to accept the license) on both remote and local download paths (commit 4).
- ✅ Ollama register failure → hint includes local safetensors path + manual `ollama create` command so user knows training isn't lost (commit 4).
- ✅ Targeted tests added: 13 new tests covering preflight branches, base model integrity, pull failure/mismatch, and auth surfacing. Total suite 135 → 148. All green.
- ✅ All existing tests still pass. Lint/mypy clean on modified code.
- ✅ CLAUDE.md updated where recovery mechanism changed.

### Failure modes investigated but NOT fixed (with reason)
- ❌ **`train.log` unbounded growth**: investigated and found not-actually-unbounded in practice. `tee` at remote_train.py:990 uses default behavior (truncate on open), so each run starts with a fresh log. A single Gemma-2 2B run produces ~250 lines (~50KB) — bounded. The plan's "unbounded on long runs" was a theoretical concern that doesn't apply to realistic workloads. **No code action taken.**
- ❌ **Wheel bundle hash collision across Python versions**: investigated and found not-a-failure-mode for this codebase. `tokenpal` builds a pure-Python `py3-none-any` wheel — the wheel content is byte-identical across Python 3.x versions given the same source. The existing `_hash_training_sources()` already covers what matters (the `.py` files). **No code action taken.**
- ❌ **Partial file cleanup (`.partial`)**: investigated; with default `rsync --partial` (no `--partial-dir`), rsync writes to the final filename and the partial content IS the final file on disk. On retry, rsync's checksum logic handles resume correctly. The real failure mode — corrupted final file — is now caught by the sha256 mismatch hard-error in commit 3. **No separate code action needed.**
- ❌ **Concurrent finetune invocations racing the bundle path**: current `flock` check at remote_train.py:890 + commit 1's preflight detection cover this. No additional action needed.
- ❌ **Checkpoint corruption from crashed runs**: deferred. Would require HF Trainer-level introspection; risk is low (trainer validates checkpoints on load and fails cleanly).
- ❌ **Disk-fill mid-training**: the existing 25GB preflight warning is the entire mitigation. Mid-run disk-fill would cause a training crash with a clear OOM/ENOSPC error in `train.log`, which the existing error path at remote_train.py:~995 already surfaces.

## Commit sequencing (as shipped)
1. ✅ **Commit 1 — `42c8345`** Preflight cluster: `RemoteState` + `_preflight_remote_state()` + stale-flock auto-remove + venv integrity check + dead-tmux cleanup + live-training error with `tmux attach` hint. Live-verified on geefourteen (4 branches: orphan tmux, stale lock, live training detection, clean state).
2. ✅ **Commit 2 — `7391202`** Retire `.install-ok` sentinel: mid-work scope shrink — the sentinel became dead code after commit 1. Removed from `_INSTALL_SH`, test replaced with re-introduction guard, docs updated to point at `rm -rf ~/tokenpal-training/.venv` as the new force-reinstall workaround.
3. ✅ **Commit 3 — `6b4f13b`** Base model + pull integrity: `_ensure_base_model` check extended to require nonzero weight shards. Pull failure and sha256 mismatch both raise `RemoteTrainError` with `rm -rf LOCAL_DIR` recovery hints. Mismatch escalated from warning to hard error. Live-verified on geefourteen.
4. ✅ **Commit 4 — `29ab106`** Error surfacing: HF auth detection heuristic (`_looks_like_hf_auth_error`) + `RemoteTrainError("auth")` on both remote and local download paths with specific HF_TOKEN / license-acceptance fix instructions. Ollama register failure includes safetensors path + manual `ollama create` command.
5. 🚫 **Commit 5 — cancelled.** Both items in the original commit 5 scope (`train.log` tail cap, wheel hash collision) turned out to not be real failure modes. See "Failure modes investigated but NOT fixed" above. This plan closeout commit documents the findings instead.

### Supporting commits
- `04304b7` Ignore `graphify-out/` build artifacts (hygiene)
- This commit — plan closeout with final status

## Parking lot
(empty — the one skill-meta note that surfaced here was migrated to `plans/plan-skill-v2.md` on 2026-04-11 before shipping)
