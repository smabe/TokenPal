# Remote Pipeline — Native Windows Support

## Goal
Add native Windows training to the remote fine-tuning pipeline so the RTX 4070 host no longer needs WSL. Uses bf16 LoRA with gradient checkpointing + eager attention (VRAM-verified: 7.43 GB peak on 8.59 GB card). One-time PowerShell setup script replaces `install.sh` on Windows hosts. Eliminates the filesystem doubling tax, the DrvFS SSL flakes, and the WSL install complexity in one move.

**Also implicitly fixes a latent bug** discovered during the research pass: the current code on Windows+CUDA would trigger the QLoRA path, which requires `bitsandbytes` (Linux-only). Windows+CUDA training was never actually functional, it just wasn't attempted. This plan makes it functional by routing Windows+CUDA through the existing bf16 gate alongside ROCm.

## Non-goals
- **No changes to the Linux path.** Pure Linux hosts (including WSL-via-direct-SSH on port 2222, which is already working) continue to use `install.sh` + bf16 or QLoRA as they do today. This plan is purely additive.
- **No SSH-survivable training on Windows in the MVP.** Windows doesn't have tmux, and replacing tmux cleanly is a big side quest. MVP requires the user to keep their SSH session open during the 5-10 minute training run. SSH-survivable training becomes a follow-up plan once the native path is proven.
- **No QLoRA on Windows.** `bitsandbytes-windows` is community-maintained, frequently broken, and not worth the ~4 GB VRAM savings for Gemma-2 2B. bf16 + gradient checkpointing is the committed path.
- **No multi-model support as part of this plan.** Target: `google/gemma-2-2b-it` specifically. Other models may or may not fit in 8 GB bf16; out of scope to verify.
- **No GUI setup wizard.** The PowerShell script is user-invoked manually, one time, per new Windows remote. Documented in `docs/remote-training-guide.md`.
- **No flock equivalent on Windows.** Concurrent-training detection via flock is Linux-only. On Windows, we skip the lock check with a brief log warning. Worst case: two concurrent trainings would stomp each other, but that requires deliberate user action.
- **No rsync on Windows.** Pull uses SCP (same as the existing WSL fallback path). No progress bar, no resume on network drop — documented tradeoff.
- **No removal of the existing WSL path.** Old use_wsl=true config keeps working for backward compat, even though the recommendation becomes "don't use WSL on Windows, use native."
- **No support for Git Bash, MSYS2, or Cygwin** as intermediate runtimes. If it's not pure PowerShell + Python, it's out of scope.
- **No change to the /voice finetune CLI surface.** Windows vs Linux routing is internal to `remote_train.py`.
- **Not going to try to CI-test the PowerShell script.** Manual test on geefourteen is the acceptance gate.
- **No changes to `use_wsl` semantics.** The flag remains ambiguous (it could mean either "WSL on a Windows SSH host" or "direct SSH to WSL on port 2222") but we don't disambiguate or deprecate it. Adding `platform: str = "auto"` gives the new explicit signal; old users configured with `use_wsl=true` keep working.
- **No model-name branching in `setup_model()`.** Eager attention is applied unconditionally on the bf16 LoRA path because Gemma-2 is the committed target. If a future non-Gemma model becomes a target, the branching is a 5-line change then.
- **No changes to `auto_tune()`.** On Windows, training code bypasses `auto_tune()` entirely for `batch_size` / `gradient_accumulation_steps` (those stay locked at 1 / 4 on Windows regardless of dataset size). `lora_rank` and `epochs` continue to be auto-tuned normally.

## Files to touch
- `tokenpal/tools/remote_train.py` — new `_INSTALL_PS1` constant (~150 lines, mirroring `_INSTALL_SH` phases), Windows branches in `_build_bundle` (include .ps1 in bundle), a `_ensure_base_model_windows` helper (the existing `_ensure_base_model` uses `test -f`, `grep -q`, `find -size`, `source ~/.bashrc` — *all four* are POSIX-only, so Windows needs a parallel PowerShell check rather than a one-liner tweak), Windows training command launch via `Start-Process` in place of `tmux new-session`, skip of `flock` concurrent-training check on Windows (5 call sites), skip of `tmux` session management on Windows (9 call sites). Probably +250 lines (revised up from +200 after research found flock/tmux surface is larger than expected).
- `tokenpal/tools/finetune_voice.py` — **small diff, not large.** Extend the existing `_is_rocm()` gate at lines 93-146 to also trigger on Windows+CUDA (reuse the working bf16 LoRA path). Inside that path, call `model.gradient_checkpointing_enable()` and pass `attn_implementation="eager"` on model load. In the SFTConfig construction (lines 211-225), on the Windows path, force `per_device_train_batch_size=1` and `gradient_accumulation_steps=4` regardless of what `auto_tune()` returned. ~30 lines total, not 50.
- `tokenpal/config/schema.py` — add `platform: str = "auto"` field to `RemoteTrainConfig` (values: `auto`, `linux`, `windows`). One line addition. Runtime detection lives in `remote_train.py` when `platform == "auto"` (SSH probe via `uname -s 2>/dev/null || ver`).
- `tests/test_tools/test_remote_train.py` — new tests (~6-8): Windows platform detection, Windows install command generation, Windows training command generation, flock-skip on Windows, Windows base model path handling, regression test that existing `use_wsl=true` path is unchanged.
- `tests/test_tools/test_finetune_voice.py` — new SFTConfig regression tests (3-4): `gradient_checkpointing=True` when on bf16 path, `per_device_train_batch_size=1` when `platform="windows"`, QLoRA branch NOT triggered when `platform="windows"`, `attn_implementation="eager"` is set on model load in the bf16 path.
- `docs/remote-training-guide.md` — new section "Windows Native Setup (recommended for Windows hosts)" with the one-time PowerShell script invocation + HF_TOKEN setup + migration note for users currently on `use_wsl=true`
- `docs/dev-setup-windows-amd.md` — native path becomes recommended; WSL section marked legacy
- `CLAUDE.md` — Windows native note in Fine-Tuning section; update "SSH/SCP/rsync" bullet to mention local `scp.exe` prerequisite
- `plans/remote-pipeline-windows.md` — this file (ships to `plans/shipped/` when done)

## Failure modes to anticipate
- **SSH session drops during training**: no tmux, process dies. Need to detect + warn user before training starts that they should keep the session open. Mitigation: explicit progress message "Training will run for ~10 min. Keep this session open." Hint on failure: "SSH dropped mid-training. Native Windows doesn't survive disconnects — run in a terminal you don't close, or switch to tmux-capable WSL path."
- **PowerShell execution policy blocks the script**: `Set-ExecutionPolicy` default is `Restricted` on Windows. Setup script must either set `-Scope Process Bypass` at the top or be invoked with `powershell.exe -ExecutionPolicy Bypass -File install.ps1`.
- **Multiple Python installs** (python.org, Microsoft Store, Anaconda, Windows Store): script needs to explicitly call a known-good Python or detect 3.12+. Probably: require user to install python.org Python 3.12 manually, then script uses `py -3.12`.
- **CUDA toolkit version mismatch**: PyTorch CUDA wheels expect a compatible runtime. Detection: check `nvidia-smi` output for driver version, map to a CUDA runtime requirement. Installation of the CUDA toolkit itself is out of scope — user needs to have it installed (RTX 4070 box already does).
- **Path separators**: `\\` vs `/` — Python tolerates both but PowerShell command strings need to pick one. Use forward slashes where possible (PowerShell tolerates them for most paths).
- **`huggingface_hub` cache location on Windows**: defaults to `%USERPROFILE%\.cache\huggingface`, not `~/.cache`. Base model download path needs Windows-style expansion.
- **HF_TOKEN environment variable setup on Windows**: no `~/.bashrc`. User sets it via `setx HF_TOKEN "hf_..."` (persistent) or `$env:HF_TOKEN = "hf_..."` (session). Setup script documents both.
- **Start-Process process detachment**: `Start-Process -NoNewWindow -RedirectStandardOutput train.log -RedirectStandardError train.err` may or may not survive SSH disconnect depending on Windows SSH server config. Needs testing.
- **Windows Defender or antivirus** quarantining downloaded PyTorch DLLs. Common pain point. Setup script can pause and let user add an exclusion if needed.
- **Venv activation**: `source .venv/bin/activate` is bash; Windows is `.venv\Scripts\Activate.ps1`. Training command construction must branch on platform.
- **The install_ps1 install script is not testable in CI** (macOS test env can't run PowerShell meaningfully). Tests for Windows code paths rely on SSH mock responses; the actual .ps1 content is validated by a content-check test (grep for specific commands).
- **Regression risk on the existing WSL path**: platform detection code must not accidentally route WSL hosts to the Windows branch. `use_wsl=true` explicitly means "bash-in-WSL", even on Windows.
- **The bf16 + grad_checkpoint + eager config is fragile**: one wrong knob (e.g. forgetting eager attention) and VRAM jumps to 9.48 GB → OOM. Training code needs a clear comment at the load site: "if you change any of these three, re-measure VRAM."
- **Gradient accumulation (steps=4) changes the effective batch**: training quality with bs=1/accum=4 may differ from bs=2 on WSL with QLoRA. Need to verify the trained voice output is still coherent on one dogfood run.
- **SCP on native Windows**: OpenSSH for Windows ships with `scp.exe`, but path handling is slightly different from Linux `scp`. The existing `_run_scp` helper may need Windows-specific path handling.
- **Base model path divergence across hosts**: different Windows users have different `%USERPROFILE%` paths. `_ensure_base_model` needs to resolve the path at runtime via SSH, not assume `~/tokenpal-training`.
- **tokenpal wheel install on Windows** — pure Python wheel should install fine, but `pip install --force-reinstall` on Windows might hit file-lock issues if any process is using the installed files. Mitigation: install into a fresh venv created at the start of each install.
- **Local SCP prerequisite**: `_run_scp` silently fails if `scp.exe` is not in the local controller's PATH. Affects the machine running `remote_finetune()` (not the remote). Need a clearer error than "subprocess returned 127".
- **`use_wsl=true` is dual-meaning**: `remote_train.py:632` treats `use_wsl=true` identically whether the remote is "WSL via Windows SSH on port 22" or "direct SSH to WSL on port 2222". Adding `platform="windows"` coexists without touching this, but users flipping from `use_wsl=true` to `platform="windows"` must also explicitly set `use_wsl=false`. Document the migration step.
- **`auto_tune()` blind spot**: `finetune_voice.py:54-76` tunes `lora_rank` and `epochs` by dataset size but never touches `batch_size` or `gradient_accumulation_steps`. On Windows we must clamp `batch_size=1` AFTER auto_tune returns, or auto_tune's output will silently allow the batch=2 path that OOMs (VRAM measurement showed 9.64 GB at bs=2 — 1 GB over the card).
- **QLoRA latent bug on Windows+CUDA**: the current `_is_rocm()` gate at `finetune_voice.py:93-146` routes Windows+CUDA to QLoRA, which needs bitsandbytes. bitsandbytes-windows is unreliable. This plan implicitly fixes the bug by extending the gate. A regression test that asserts "QLoRA path NOT taken when platform=windows" is **non-optional**.
- **Default `base_model` in `schema.py:100` is still `TinyLlama`** — any test exercising `FinetuneConfig()` defaults will hit TinyLlama, not Gemma-2. Tests for the "Windows bf16 + eager" path need to explicitly override `base_model="google/gemma-2-2b-it"`.
- **Zero SFTConfig regression tests today**: `test_finetune_voice.py` checks `LoRAConfig` defaults + `auto_tune()` but not the actual `SFTConfig` values used by the trainer. Adding grad_checkpoint / eager / bs=1 has no existing safety net, which is why we're adding 3-4 new SFTConfig assertions in this plan.
- **`_ensure_base_model` is not a one-liner fix**: the current `check_cmd` uses `test -f`, `grep -q`, AND `find -size +0c` — *all three* are POSIX-only. Plus `source ~/.bashrc` for HF_TOKEN. The Windows path needs a parallel PowerShell helper function, not a small tweak.
- **flock is used 5 times, tmux is used 9 times** in `remote_train.py`: the branching surface for the two primitives alone is ~14 touch points. Favors a helper-extraction approach (e.g. `_start_training_session(remote, platform, ...)`) over inline `if platform == "windows"` checks at every site.

## Done criteria
- `_INSTALL_PS1` constant added to `remote_train.py` with phases mirroring `_INSTALL_SH`: Python check, venv setup, CUDA PyTorch install, tokenpal wheel install, torch+CUDA verification
- `platform: str = "auto"` field added to `RemoteTrainConfig` in `schema.py`; `remote_train.py` probes the remote via `uname -s 2>/dev/null || ver` when `platform == "auto"` and caches the result for the run
- `_ensure_base_model_windows` helper (or equivalent) added that handles the Windows-specific check via PowerShell `Test-Path` + `Select-String` for config.json + `Get-ChildItem` for nonzero weight shards + `$env:HF_TOKEN` instead of `~/.bashrc`
- `_is_rocm()` gate in `finetune_voice.py:93-146` extended to also fire on Windows+CUDA — **Windows routes through the existing bf16 LoRA path, not a new branch**
- Gemma-2 loaded with `attn_implementation="eager"` + `gradient_checkpointing_enable()` on the bf16 path (applies unconditionally since Gemma-2 is the committed target)
- `SFTConfig` on the Windows path uses `per_device_train_batch_size=1` and `gradient_accumulation_steps=4` — `auto_tune()`'s output for those two fields is overridden on Windows (`lora_rank` + `epochs` still come from auto_tune)
- **Regression test: QLoRA path NOT triggered when `platform="windows"`** — asserts no `BitsAndBytesConfig` is constructed on the Windows branch. Non-optional per failure modes list.
- New tests in `test_remote_train.py` (~6-8): platform detection, Windows install command generation, Windows training command generation, flock-skip, Windows base model path. Existing `use_wsl=true` tests still pass untouched.
- New tests in `test_finetune_voice.py` (3-4): `gradient_checkpointing=True` on bf16 path, `batch_size=1` on Windows path, eager attention active on bf16 path, QLoRA NOT triggered on Windows.
- Existing 148 tests still pass. No new lint/mypy errors.
- **Dogfood test on geefourteen**: switch config.toml to `platform="windows"` + `use_wsl=false`, run the PowerShell setup script once manually, run `/voice finetune BMO` end-to-end (bundle push → install → model verify/download → train → merge → pull → Ollama register), verify trained voice is functionally equivalent to the previous WSL-trained voice, verify VRAM stays under 8 GB during training
- Training time within 2x of the previous WSL+QLoRA time (estimate: ~10 min vs ~7 min current)
- `docs/remote-training-guide.md` has a complete "Windows Native Setup" section with the PowerShell one-liner + HF_TOKEN setup + migration note for users currently on `use_wsl=true`
- `docs/dev-setup-windows-amd.md` recommends native path for future setup; WSL section marked legacy
- `CLAUDE.md` updated with native Windows option + local `scp.exe` prerequisite
- Plan shipped to `plans/shipped/remote-pipeline-windows.md`

## Parking lot
- **(scope creep, defer)** Add a Phase 0 to `_INSTALL_PS1` that auto-installs Python 3.12 via `winget install --id Python.Python.3.12 --silent --accept-source-agreements --accept-package-agreements` if the `py` launcher isn't present. Verified 2026-04-11 on geefourteen: winget works non-interactively over SSH, no admin required, no reboot, ~60 seconds. Would reduce the user's manual prereq list from "{SSH, auth, CUDA driver, Python 3.12}" to "{SSH, auth, CUDA driver}". Not doing it now because commit 3 already shipped install.ps1 and re-opening it is scope creep; add in a follow-up plan if we decide the UX improvement is worth it.
