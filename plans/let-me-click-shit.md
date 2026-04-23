# let-me-click-shit

## Goal
Make the Qt Options dialog and its sub-dialogs (Cloud, Voice, Senses, Tools) non-modal windows. Users can interact with the rest of the app while Options is open, and launching a sub-dialog opens it on top of Options without closing Options.

## Non-goals
- Textual overlay modal behavior (untouched — Qt only)
- ConfirmDialog behavior (yes/no prompts stay modal; short-lived, decision-required)
- Reworking `OptionsModalResult` schema or the save/cancel contract
- Visual restyling of the windows (pure behavior change)
- Multi-instance sub-dialogs (still one Cloud/Voice/Senses/Tools at a time, focus existing if already open)

## Files to touch
- `tokenpal/ui/qt/options_dialog.py` — drop `setModal(True)`; add `Qt.WindowType.WindowStaysOnTopHint` to match buddy z-order; rework `_on_launch` to invoke new `on_open_subdialog(target: NavigateTo)` callback instead of `accept()` + `navigate_to`; replace "this modal closes first" help text with "Open another settings window alongside this one"; **pending server/model picks stay pending** across sub-dialog opens (don't auto-save, don't clear)
- `tokenpal/ui/qt/cloud_dialog.py` — drop `setModal(True)`; add `WindowStaysOnTopHint`
- `tokenpal/ui/qt/voice_dialog.py` — same treatment
- `tokenpal/ui/qt/modals.py` — drop `setModal(True)` on `SelectionDialog` only; add `WindowStaysOnTopHint` to `SelectionDialog`; **ConfirmDialog stays modal** (yes/no prompts need to block)
- `tokenpal/ui/qt/overlay.py` — add `_options_dialog`/`_cloud_dialog`/`_voice_dialog`/`_selection_dialog` instance attrs; on each `_do_open_*` check if the existing instance is alive and visible, focus it instead of spawning a new one; clear the attr on dialog destroyed; pass `on_open_subdialog` callback into `OptionsDialog` that re-enters `open_cloud_modal` / `open_voice_modal` / `open_selection_modal` without closing options
- `tokenpal/app.py` — remove the `navigate_to` branch in the Options `on_save` handler (lines 674-689). Leave the field intact in `OptionsModalResult` for backwards compat (Textual already writes None, Qt stops writing it)

## Failure modes to anticipate
- **Always-on-top buddy obscuring non-modal windows** — `BuddyWindow` sets `Qt.WindowType.WindowStaysOnTopHint` permanently (`buddy_window.py:64`). `_focus_dialog` raises once but the WM re-surfaces the buddy on next focus event. **Mitigation**: give the non-modal dialogs `WindowStaysOnTopHint` too so they sit at the same z-level as the buddy.
- **Sub-dialog parented to Options gets destroyed** if the user closes Options while Cloud is still open. Parent sub-dialogs to `self._history` (same as today), not to Options, so they outlive it.
- **Duplicate windows** if `/options` fires while Options is already open. Need singleton guard in overlay that focuses the existing instance.
- **One-shot callback semantics** — `_OneShotCallback._deliver` fires `on_result` exactly once. Launcher buttons used to fire it with `navigate_to`; now they shouldn't fire `on_result` at all. `on_result` must still fire exactly once on save or cancel.
- **macOS Qt non-modal QDialog focus quirks** — Qt on macOS sometimes treats a QDialog with no parent modality oddly (ghost title bar, no taskbar entry). Setting `Qt.WindowType.Window` on construction should normalize this.
- **Save/Cancel interplay** — user opens Options, opens Cloud, hits Save in Cloud, then hits Cancel in Options. Cloud's callback already fired (correct). Options' callback fires with no-op result (correct). Verify no state bleed between the two `_pending_*` sets.
- **Opacity live-preview** still works while Options is non-modal (the preview callback doesn't depend on modality), but now the user can drag other windows around during preview. Non-issue, just noting.
- **`on_opacity_preview` reset on Cancel** — if the user cancels Options, opacity should revert to initial. Already handled, but verify the revert still fires when Options is closed via the X button (non-modal windows are closed via `closeEvent`, not just Cancel button).
- **Always-on-top chat window covering sub-dialog** — chat history window has raise/always-on-top treatment; verify sub-dialogs stack above it after `_focus_dialog`.
- **Windows focus behavior** — can't test directly (Mac primary), but non-modal on Windows generally works. Flag as "test on AMD desktop next session" if user cares.

## Done criteria
- With Options open, the user can type in chat input and see the buddy respond (no input block).
- With Options open, clicking Cloud/Voice/Senses/Tools opens that window **on top of Options**, Options stays visible and editable.
- Closing the sub-window (save or cancel or X) leaves Options still open.
- Closing Options while a sub-window is open does not crash and does not force-close the sub.
- Re-running `/options` while Options is already open focuses the existing window, does not spawn a second one. Same for `/cloud`, `/voice`, `/senses`, `/tools`.
- `ruff check tokenpal/ui/qt/` and `mypy tokenpal/ui/qt/ --ignore-missing-imports` pass.
- Manual run of `./run.sh` on Mac: Options no longer has the "this modal closes first" help text.

## Parking lot
(empty at start)
