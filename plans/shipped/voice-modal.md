# Voice modal

## Goal
Build a `VoiceModal` that surfaces every `/voice` subcommand as a discoverable UI (list active + saved voices, switch with one click, kick off train/finetune/regenerate flows), then add a "Voice..." launcher button to `OptionsModal` and extend its `navigate_to` literal with `"voice"`. Make the slash command and modal share one dispatch helper so behavior never forks.

## Non-goals
- Replacing `/voice` slash dispatch. The modal calls into the same `_handle_voice_command` (or a refactored core) — no parallel command tree.
- Inline progress UI for long-running jobs (train ~minutes, finetune ~7-15min). Modal kicks them off, dismisses, and the existing chat-log/status-bar feedback paths report progress. A spinner/progress modal is parking-lot.
- Voice editing (mood tweaks, persona prompt edits, anchor lines). View-only for now; "Regenerate" is the only mutation path beyond train/finetune.
- Voice deletion. Parking-lot — needs confirmation flow + on-disk cleanup story.
- Console + tkinter overlay parity. The modal is Textual-only, mirroring `CloudModal` / `SelectionModal`. Slash command keeps working everywhere.
- Refactoring `_start_voice_training` / `_start_voice_finetune` / `_start_voice_regenerate` thread-launch helpers. We call them as-is from the modal result handler.
- Cross-machine voice sync or HF Hub publishing.

## Files to touch
- `tokenpal/tools/voice_profile.py` — **prerequisite, do this first**. Add `list_profile_summaries(voices_dir) -> list[ProfileSummary]` returning `(slug, character, line_count, source, finetuned_model)` in one disk pass. The existing `list_profiles` only returns `(slug, character, line_count)` (voice_profile.py:107) — without the new helper the modal Status block has to re-load every JSON to show source wiki + finetune model id.
- `tokenpal/ui/voice_modal.py` (new) — `VoiceModal(ModalScreen[VoiceModalResult | None])`. Frozen `VoiceModalState` in (active voice name + summary, list of `ProfileSummary` from the new helper), frozen `VoiceModalResult` out. Inline `DEFAULT_CSS` class var (matches OptionsModal/CloudModal/SelectionModal/ConfirmModal convention — no separate .tcss file). Layout as a `VerticalScroll` of section `Container`s mirroring `OptionsModal` shape:
  1. **Status** (top, always visible) — read-only block with the active voice's character, source wiki, line count, and finetune model id if any. Mirrors `/voice info` output. When no custom voice is active, shows "Default TokenPal voice." Includes a "Use default voice" `Button` (maps to `/voice off`) inline when a custom voice is active.
  2. **Saved voices** — `OptionList` (or vertical button stack) of `(character, lines, [FT])` rows. Selecting a row + pressing "Switch" maps to `/voice switch <name>`. Double-click also switches. Disabled when list is empty.
  3. **Train new** — `Input` for wiki URL, `Input` for character name, `Button` "Train" (maps to `/voice train <wiki> "<character>"`). Both inputs required; button disabled until both are non-empty.
  4. **Finetune active voice** — `Button` "Run fine-tune" (maps to `/voice finetune`) and `Button` "Setup remote host..." (maps to `/voice finetune-setup`). Button row is disabled with help text when no custom voice is active.
  5. **Regenerate** — `Button` "Regenerate all assets" (maps to `/voice regenerate`) and `Button` "Regenerate ASCII art only" (maps to `/voice ascii`). Both disabled when no custom voice is active. "Regenerate all" wired through a `ConfirmModal` because it's a ~60s LLM job.
  6. **Import** — `Input` for path + `Button` "Import" (maps to `/voice import <path>`).
  Result encodes the chosen action: `VoiceModalResult(action: Literal["switch","off","train","finetune","finetune_setup","regenerate","ascii","import"], payload: dict[str, str])`. `Cancel` returns `None`. State factory `VoiceModalState.from_disk(voices_dir, personality)` lives next to the dataclass so the app layer doesn't reach into voice internals.
- `tokenpal/ui/options_modal.py` — add a "Voice..." `Button` in the Settings shortcuts section. Extend the `NavigateTo` literal at options_modal.py:36 from `Literal["cloud", "senses", "tools"]` to `Literal["cloud", "senses", "tools", "voice"]`. Mechanical change.
- `tokenpal/ui/base.py` — add `open_voice_modal(state, on_result) -> bool` to `AbstractOverlay` (returns `False` by default — console + tkinter inherit the noop). Textual overlay overrides to post `OpenVoiceModal` and return `True`. This is the canonical capability-detection pattern (base.py:83-99): app-layer callers check the bool return and fall back to the usage string. NO `hasattr` checks.
- `tokenpal/ui/textual_overlay.py` — three changes:
  1. New `OpenVoiceModal(Message)` class mirroring `OpenOptionsModal` (textual_overlay.py:144-148).
  2. `on_open_voice_modal` handler. **Must call `_modal_already_active()` and early-return if true** (textual_overlay.py:851 — added in commit de8fb32, every other modal handler does this). Without the guard, F4-then-/voice or two rapid keybinding presses will stack VoiceModal on top of itself.
  3. Add F4 binding (F1/F2/F3 taken; F4 free per textual_overlay.py:453-459). Subject to keybinding approval — drop if it collides with macOS Mission Control.
- `tokenpal/app.py` — three changes:
  1. Refactor `_handle_voice_command` (96 lines, app.py:1946-2042) so each branch becomes a small helper (`_voice_switch`, `_voice_off`, `_voice_train`, etc) that takes the same args. The slash dispatcher and the modal result handler both call these helpers — no string round-trip through the slash parser. The function is a shallow dispatcher; risk is low but smoke tests come first.
  2. New `_open_voice_modal()` helper builds `VoiceModalState.from_disk(...)` and calls `overlay.open_voice_modal(state, callback)`. Returns `True` if the overlay accepted, `False` otherwise. Bare `/voice` (no args) calls this; if it returns `False`, fall back to the usage string. Mirror the `/cloud` pattern at app.py:376-378 exactly. `/voice <subcommand>` keeps working unchanged for scripted/power-user paths.
  3. `OptionsModalResult` handler: add `elif navigate_to == "voice": _open_voice_modal()`. No `call_after_refresh` needed — the existing dismiss + result-callback pattern (options_modal.py:202-207, app.py:426-442) already sequences the next modal cleanly. The modal-stacking guard in `on_open_voice_modal` is the safety net.
  4. Modal result handler: dispatch `result.action` → matching helper, passing `result.payload` fields. `train`/`finetune`/`regenerate`/`finetune-setup` re-use the existing thread-launch helpers untouched (all are background-threaded today, including `finetune-setup` per app.py:2338-2377).
- `tests/test_voice_modal.py` (new, sibling to `tests/test_options_modal.py`) — **dataclass-level tests only**, no Textual Pilot harness (matches the convention established in test_options_modal.py:1-79). Tests: `VoiceModalState` and `VoiceModalResult` are frozen; `VoiceModalResult(action="switch", payload={"name": ...})` round-trips; `VoiceModalState.from_disk` builds correctly from a fixture voices dir; gating logic — finetune/regenerate fields are flagged disabled when `state.active_voice is None`; train requires both inputs (validator on `_collect`).
- `tests/test_app_voice.py` (new) — **smoke test for every /voice subcommand BEFORE the dispatcher refactor lands** (mandatory, not optional — there are zero tests for `_handle_voice_command` today). One test per subcommand: list, switch, off, info, train (mock thread launcher), finetune (mock), finetune-setup (mock), regenerate (mock), ascii (mock), import. After the refactor, re-run to confirm parity. Also add: `OptionsModalResult(navigate_to="voice")` triggers `_open_voice_modal`.
- `tests/test_voice_profile.py` (extend or new) — round-trip test for `list_profile_summaries`: build a fixture voices dir with two profiles (one with finetuned_model set), assert the helper returns both with correct source + finetune fields in one disk pass.
- `CLAUDE.md` — update the `/voice` slash-command line to mention that bare `/voice` now opens `VoiceModal` (Textual only; falls back to usage on console/tkinter), and add the F4 binding.

## Failure modes to anticipate
- **Modal-stacking guard is mandatory**: commit de8fb32 added `_modal_already_active()` (textual_overlay.py:851) and EVERY `on_open_*_modal` handler early-returns if a ModalScreen is already on the stack. `on_open_voice_modal` MUST do the same. Without it, hitting F4 twice or F4-then-/voice will stack VoiceModal on itself and re-introduce the bug that commit fixed.
- **Bare-`/voice` on non-Textual overlays**: console + tkinter overlays can't push a modal. Use the canonical capability pattern (base.py:83-99): `overlay.open_voice_modal(...)` returns `bool`. False → fall back to usage string. Mirror `/cloud` at app.py:376-378.
- **Long-running job + dismissed modal**: `/voice train` and `/voice regenerate` take 10s-60s+. The current flow logs progress to the chat log and returns immediately — modal dispatch must do the same (`schedule_callback` already used by the existing helpers). Don't block modal dismissal on the worker thread; users will think the app froze.
- **Active-voice gating logic**: Finetune/Regenerate/ASCII rows must disable when `personality.voice_name is None`. If we forget the gate, the helpers will throw on `_personality_active_voice()` lookups. Tests must cover both states.
- **State staleness mid-modal**: if the user trains a new voice while the modal is open (e.g. via slash command in another session) the saved-voices list is stale. Acceptable for v1 — modal is single-shot; reopen to refresh. Note in help text.
- **Voice-list disk scan cost**: `list_profiles` opens every JSON in `voices/`. With ~5 voices it's fine; with 50+ it'd lag the modal-open path. Add a quick benchmark in `test_voice_modal.py` (or a TODO) and consider caching at the `voice_profile` layer if it bites.
- **Sensitive paths in import field**: the import path `Input` is unrestricted free-text. If the user types a sensitive path it could get echoed in error toasts. Truncate any path in error messages to basename only, mirror `_log_user`'s 30-char truncation rule for any logged copy.
- **Subcommand drift**: `/voice` subcommands evolve; the modal will silently lag. Pin the helper-refactor in `_handle_voice_command` so adding a new branch forces an explicit decision about whether the modal exposes it. Add a CLAUDE.md note.
- **Result-handler ordering**: pressing a launcher in `OptionsModal` must dismiss it BEFORE pushing `VoiceModal` (same `call_after_refresh` pattern as `dont-forget.md`'s navigate-to). Skipping the deferral risks focus stuck on the buddy `Input` or two modals briefly stacked.
- **Refactor regression risk in `_handle_voice_command`**: it's a ~100-line function with side effects (`overlay.schedule_callback`, `personality.set_voice`, `activate_voice`). Splitting into helpers without touching test coverage will break in subtle ways. Mitigate: add a smoke test for each subcommand BEFORE the split, then verify the split keeps green.
- **`OptionList` keyboard nav vs scroll**: Textual's `OptionList` traps arrow keys. If the saved-voices list is taller than the modal, ensure the parent `VerticalScroll` still works for outer sections. Visually verify on a small terminal.
- **Finetune-setup is one-shot, not interactive**: `/voice finetune-setup` runs a background-threaded job with progress logged via callback (app.py:2338-2377). Modal "Setup remote host..." button kicks it off, dismisses, and progress flows to the chat log — same shape as `train` and `finetune`.
- **OptionList double-click path is visual-only**: `OptionList.OptionSelected` fires on Enter AND double-click. Tests cover the explicit "Switch" button via dataclass round-trip; double-click is visually-verified-only (no Pilot harness in this codebase).
- **F4 keybinding choice**: F1/F2/F3 are taken (textual_overlay.py:453-459). F4 is open today but conflicts with macOS Mission Control on some setups. Defer the final binding to user approval — drop if it collides.

## Done criteria
- `OptionsModal` shows a "Voice..." button. Pressing it dismisses the options modal and opens `VoiceModal` at the same state bare `/voice` would (parity test).
- Bare `/voice` opens `VoiceModal` on Textual overlays; falls back to the usage string on console/tkinter.
- `/voice <subcommand>` continues to work for every existing subcommand (no regression).
- F4 keybinding opens `VoiceModal` (subject to keybinding approval — drop if it collides).
- The Status block at the top of the modal shows active voice details (character, source wiki, line count, finetune model id) or "Default TokenPal voice" when none is active.
- All ten `/voice` subcommands are reachable from the modal: list (visible), switch, off, info (status block), train, finetune, finetune-setup, regenerate, ascii, import.
- Switching to a saved voice via the modal produces the exact same end state as `/voice switch <name>` (verified by snapshot of `personality.voice_name`, on-disk active-voice marker, and reloaded buddy art).
- Train/finetune/regenerate kicked off from the modal log progress to the chat log via the existing helpers, not blocking modal dismissal.
- Modal's Finetune/Regenerate/ASCII buttons are disabled (with explanatory help text) when no custom voice is active.
- "Regenerate all" prompts via `ConfirmModal` before the ~60s job.
- `_handle_voice_command` is refactored into per-subcommand helpers that BOTH the slash dispatcher and the modal call. No subcommand has two implementations.
- `pytest` passes including new modal tests + refactored slash-dispatch tests.
- `ruff check tokenpal/` + `mypy tokenpal/ --ignore-missing-imports` clean.
- CLAUDE.md updated to mention `VoiceModal`, the new entry points, and the "modal lags subcommand drift" maintenance note.

## Parking lot
(empty at start — append "ooh shiny" thoughts that surface mid-work for later)
