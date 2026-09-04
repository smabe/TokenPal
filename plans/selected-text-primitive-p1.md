# selected-text-primitive-p1 — the macOS selected-text primitive with a fakeable accessibility layer

You are phase `p1` of the `selected-text-primitive` plan. This phase delivers `tokenpal/desktop/selected_text.py` — resolve the app the user came from, read its focused element's selection (or whole value) through the Accessibility API behind a small bridge that tests can fake — plus the host-name helper the permission-missing message needs, as one commit. Nothing registers as an action yet and no slash command exists yet.

## Locked decisions
See the master `plans/selected-text-primitive.md`. The decisions binding this phase:
- **Source app = the second distinct layer-0 owner in the front-to-back Quartz window list.** The first owner is whatever hosts TokenPal at the moment the user typed the command (TokenPal's own process under Qt, the terminal app under Textual); skip that pid and `os.getpid()`; the next window with a different owner pid is the source. Evidence: order documented front-to-back (master, Background findings "Window order"); probe 2026-09-03 showed the host's pid is not always an ancestor of this process, so pid-ancestry cannot identify it.
- **Every pyobjc import is inside a function** (`Quartz`, `HIServices`), following `tokenpal/desktop/permissions.py:26-54`. Module import must succeed on every host so the registry and the contract test's AST walker both see the p2 action.
- **Refuse `AXSecureTextField` before reading a value.** Read `kAXSubroleAttribute` on the focused element first; if it equals `"AXSecureTextField"`, return the `secure_field` failure without touching `kAXSelectedText`/`kAXValue`. Evidence: master, "Attributes and roles".
- **Messaging timeout of 2.0 s, set on the application element before the focused-element read and again on the focused element before its attributes are read.** The setting is per element (`AXUIElement.h:386-402`), so setting it once on the application element would leave the focused element at the default. Evidence: master, "Messaging timeout".
- **Sensitive-app gate runs on the window owner name before any accessibility call.** `capture_selection` resolves the source app, calls `refuse_if_sensitive(name)`, and only then reads. Stricter than the order in `docs/claude/actions.md` (read, then refuse), which was written for readers that learn the app name from the read itself; the docs paragraph is updated in p2 to say "as early as the app name is known".
- **Whole-field fallback truncates in-process** to `max_chars` and sets `truncated=True`; no range read (master Non-goals).
- **Failure messages never contain content or a sensitive app name.** A non-sensitive app name and a character count are the only variable parts.
- **The message copy in this shard is proposed, not signed off.** Every user-facing string below is listed at approval; the worker uses them verbatim unless the approval record changes one.

## Work
- Scope trace: DIRECT — the primitive is the first inclusion in the scope contract; the host-name helper is PREREQUISITE for the permission-missing message (it must name the process that holds the grant, `tokenpal/cli.py:249-256` precedent) and its extraction from `cli.py` is required by CLAUDE.md's grep-before-adding rule (second consumer of the same three lines).
- `tokenpal/desktop/selected_text.py` — new. Proposed shape (names, defaults and optionality proposed; arities and error paths are contract):

  ```python
  DEFAULT_MAX_CHARS = 8_000
  _MESSAGING_TIMEOUT_S = 2.0
  _SECURE_SUBROLE = "AXSecureTextField"

  FailureReason = Literal[
      "permission", "no_response", "nothing_focused", "secure_field", "empty",
  ]   # only what read_selected_text itself produces; platform, missing pyobjc and
      # "no other window" are ActionResults built in capture_selection

  @dataclass(frozen=True, repr=False)
  class SelectedText:
      text: str
      source_app: str
      whole_field: bool      # True when nothing was selected and kAXValue was read
      truncated: bool
      # __repr__/__str__ omit text, same reason as DesktopContent (content.py:58-70)

  @dataclass(frozen=True)
  class ReadFailure:
      reason: FailureReason
      message: str           # user-facing; may name a non-sensitive app, never content

  class AXBridge(Protocol):
      def windows(self) -> list[tuple[int, str]]: ...            # layer-0 (pid, owner) front-to-back
      def application(self, pid: int) -> Any: ...
      def set_timeout(self, element: Any, seconds: float) -> None: ...
      def attribute(self, element: Any, name: str) -> tuple[int, Any]: ...  # (AXError, value)

  class _MacAXBridge:            # imports Quartz/HIServices inside each method
      def attribute(self, element: Any, name: str) -> tuple[int, Any]:
          import HIServices  # noqa: PLC0415
          err, value = HIServices.AXUIElementCopyAttributeValue(element, name, None)
          return int(err), value   # unpack; returning the call directly fails mypy no-any-return

  def source_app(bridge: AXBridge) -> tuple[int, str] | None
  def read_selected_text(pid: int, app: str, *, max_chars: int, bridge: AXBridge) -> SelectedText | ReadFailure
  def capture_selection(*, max_chars: int = DEFAULT_MAX_CHARS, bridge: AXBridge | None = None) -> SelectedText | ActionResult
  ```
  `read_selected_text` order: `application(pid)` → `set_timeout(app_el, 2.0)` → `attribute(app_el, "AXFocusedUIElement")`; map `-25211` → `permission` (message: "TokenPal can't read other apps: grant Accessibility to {responsible_host('Darwin')} in System Settings > Privacy & Security > Accessibility.", pane path per `tokenpal/cli.py:268`), `-25204` → `no_response` ("{app} didn't answer the accessibility request"), `-25212`/`-25205`/`None` value → `nothing_focused` ("Nothing is focused in {app}, or it doesn't expose its text while in the background. Try /proofread <text> instead."). Then `set_timeout(el, 2.0)` on the focused element, then `attribute(el, "AXSubrole")` → `secure_field` if it equals `_SECURE_SUBROLE` ("Won't read a password field."). Then `attribute(el, "AXSelectedText")`: a non-empty string is the selection (`whole_field=False`); otherwise `attribute(el, "AXValue")`: a non-empty string is the fallback (`whole_field=True`); both empty or unsupported → `empty` ("Nothing selected and the focused field in {app} is empty."). Truncate to `max_chars` with `truncated=True` when cut. Any other error code → `nothing_focused`. Non-string values (a number, a CFRange) are treated as absent.
  `capture_selection`: `current_platform() != "darwin"` → `ActionResult(success=False, output="Selected-text reading is only available on macOS. Try /proofread <text> instead.")`; `bridge = bridge or _MacAXBridge()`; the two bridge-using calls below sit in one `try`/`except ImportError` → `ActionResult(success=False, output="pyobjc is not installed — run: pip install -e '.[macos]'")`; `source_app(bridge)` `None` → `ActionResult(success=False, output="Couldn't tell which app you came from — no other window is on screen.")`; `refuse_if_sensitive(name)` result returned unchanged when not `None`; `read_selected_text(...)`; a `ReadFailure` becomes `ActionResult(success=False, output=failure.message)`; a `SelectedText` is returned as-is. Callers (p2 action, p3 brain handler) build `DesktopContent(sel.text, sel.source_app, "selection")` themselves, because p3's status line needs `whole_field`/`truncated` and the p2 action module must contain the `to_prompt_block()` call the contract test looks for.
  `source_app`: iterate `bridge.windows()`; `host = first pid`; return the first `(pid, name)` with `pid not in (host, os.getpid())` and a non-empty name; `None` when none. `_MacAXBridge.windows()` mirrors `tokenpal/senses/app_awareness/macos_apps.py:54-68` (layer 0, skip empty owners, `Window Server`, `Dock`), returning `(kCGWindowOwnerPID, kCGWindowOwnerName)` pairs in list order. `_MacAXBridge.attribute` calls `HIServices.AXUIElementCopyAttributeValue(el, name, None)` and returns the `(err, value)` pair; `application` → `AXUIElementCreateApplication(pid)`; `set_timeout` → `AXUIElementSetMessagingTimeout(el, seconds)`. An `ImportError` from any bridge method propagates; `capture_selection` is the one place that catches it.
  Logging: at most one `log.debug` per read with `reason`, app name (only when not sensitive — the refusal path logs nothing) and `len(text)`; never the text.
- `tokenpal/desktop/permissions.py` — add `responsible_host(plat: str) -> str`: `os.environ.get("TERM_PROGRAM") or sys.executable` when `plat == "Darwin"`, `sys.executable` otherwise. Transcribed from `tokenpal/cli.py:249-255` (813f8c7); it takes the `platform.system()` string as a parameter because `tests/test_desktop/test_validate.py:81-93` calls `_check_desktop_permissions("Darwin")` with `TERM_PROGRAM` set on every host and asserts the header names the terminal. Move the three-line "responsible parent process" comment from `cli.py:250-252` into the helper's docstring, and correct the module docstring (`permissions.py:8-11`), which currently says the grant attaches to the interpreter and tells callers to name `sys.executable` — the opposite of what `cli.py` does and of what the API specialist confirmed (master, "Trust").
- `tokenpal/cli.py` — `_check_desktop_permissions` uses `permissions.responsible_host(plat)` for `host`; the printed rows are unchanged.
- `tests/test_desktop/test_selected_text.py` — new. A `FakeBridge(windows=[...], attrs={element: {name: (err, value)}})` implementing `AXBridge`. Cases: (1) selection present → `SelectedText(text, "TextEdit", whole_field=False, truncated=False)`; (2) empty selection, value present → `whole_field=True`; (3) value longer than `max_chars` → truncated flag and length; (4) subrole `AXSecureTextField` → `secure_field`, and the fake records that `AXSelectedText`/`AXValue` were never requested; (5) `-25211` → `permission`, message contains `responsible_host("Darwin")`; (6) `-25204` → `no_response`; (7) `-25212` → `nothing_focused`; (8) both attributes empty → `empty`; (9) `source_app`: `[(1, "Python"), (1, "Python"), (7, "TextEdit"), (9, "Safari")]` with `os.getpid()` patched to 1 → `(7, "TextEdit")`; single-owner list → `None`; own pid listed second is skipped; (10) `capture_selection` with a bridge whose second owner is `Messages` → the `refuse_if_sensitive` result and the fake records zero attribute reads; (11) `capture_selection` off-Darwin (patch `tokenpal.desktop.selected_text.current_platform` — the function is `lru_cache`d and imported by name, so patching `tokenpal.util.platform` would not reach it) → the unsupported result without constructing a bridge; (11b) a bridge whose `windows()` raises `ImportError` → the pyobjc-missing result; (11c) the timeout: the fake records `set_timeout` calls and the test asserts one on the application element and one on the focused element, both before any attribute read on that element; (12) `repr(SelectedText)` omits the fixture and shows the char count; (13) `assert_no_leak(FIXTURE, lines=[], caplog_text=caplog.text)` around a successful read at DEBUG. Consent is not this module's concern (callers call `require_consent()` per the checklist); the tests do not patch consent.
- `tests/test_desktop/test_validate.py` — only if an assertion there pins the inline host computation; expected untouched.
- `tokenpal/util/macos_windows.py` — added at simplify (planning miss): `on_screen_windows()` / `layer0_windows()`, the Quartz window-list filter the bridge and the app-awareness sense both consume.
- `tokenpal/senses/app_awareness/macos_apps.py` — added at simplify (planning miss): `poll` uses `layer0_windows()` instead of its inline loop.
- `tokenpal/desktop/content.py` — added at review (planning miss): `refuse_if_sensitive(source_app, window_title="")` checks browser titles with the content-term list; module docstring states the as-early-as-known order.
- `tests/test_desktop/test_content.py` — added at review: the window-title refusal case.

## Decisions & findings
### Decision: a bridge protocol instead of monkeypatching pyobjc  *(status: active)*
- **Rationale:** the four calls the reader makes (`window list`, `create application`, `set timeout`, `copy attribute`) are the whole OS surface; a protocol with a fake lets tests assert *order* (secure-field check before value read; zero reads on a sensitive app), which patching module attributes cannot express cleanly. The Windows implementation, when written on the Windows box, is a second bridge behind the same `read_selected_text` only if its attribute model fits; otherwise it is a sibling function — decide there, not here.
- **Alternatives considered:** `monkeypatch.setattr("HIServices.AXUIElementCopyAttributeValue", ...)` — needs pyobjc importable in CI and cannot run on Linux; a `<platform>_impl.py` split like the senses — premature with one platform (CLAUDE.md "Don't abstract on the first use"), so the bridge is a Protocol and one class in one module.
- **Evidence:** `tokenpal/desktop/permissions.py:26-54` for the deferred-import shape; `tests/test_desktop/test_privacy_contract.py:64-79` for why module import must succeed everywhere.

### Decision: `capture_selection` returns `SelectedText | ActionResult`  *(status: active)*
- **Rationale:** every failure — unsupported platform, no source app, sensitive app, read failure — is already an `ActionResult(success=False)` for the p2 action, and p3 shows `.output` in a bubble. One failure type, one success type; callers build `DesktopContent` themselves so the p2 action module contains the `to_prompt_block()` call the contract test looks for (`test_privacy_contract.py:336-358`).
- **Alternatives considered:** returning `DesktopContent` directly — loses `whole_field`/`truncated`, which p3's status line needs; a three-way union — more branches at both call sites.
- **Evidence:** `tokenpal/desktop/content.py:98-124` (`refuse_if_sensitive`, `require_consent` already return `ActionResult | None`).

### Finding: what the probes established (this Mac, 2026-09-03)
- Front-to-back window order with this session hosted by Orca while the operator worked in Edge: `Microsoft Edge`, `Orca`, `Finder`, `Messages`, `Sublime Text`, …; the executability auditor's later run saw `Microsoft Edge`, `Signal`, `Orca`, … and `source_app()` would have returned Signal. Both runs had another app frontmost, which is the probe's condition, not the command's: a slash command is typed into the host, so the host holds keyboard focus and is the first owner. A command fired while a different app is frontmost (e.g. a future global hotkey) would read that app's neighbour — not a supported entry point today; the live check below is what proves the rule under the real condition.
- Chromium/Electron apps return `-25212` for the focused element while inactive; Cocoa apps answer. Recorded in the master; the `nothing_focused` message wording covers it.

### Finding: live probe on this Mac, 2026-09-03 (p1 implementation)
- **Selection read — CONFIRMED.** Terminal host (Orca) frontmost, TextEdit next. Front-to-back layer-0 owners: `Orca(6160)`, `TextEdit(36941)`, `TextEdit(36941)`, `Finder`, `Ghostty`, `Signal`, `Microsoft Edge`, `MTPLX`, `Messages`, `BetterDisplay`. `source_app()` -> `(36941, 'TextEdit')`; `capture_selection()` -> `SelectedText(app='TextEdit', chars=581, whole_field=False, truncated=False)`. Reproduced twice, identical. The host was the first layer-0 owner as the rule assumes, and TextEdit's two windows share one pid so the duplicate entry is skipped by the same-pid loop.
- **Sensitive source app — covered by the fake.** Not exercised live: bringing Messages forward would have disturbed the operator's windows. `tests/test_desktop/test_selected_text.py::test_sensitive_source_app_is_refused_without_touching_the_bridge` asserts the refusal and zero bridge reads.
- **Nothing selected — PENDING OPERATOR.** Could not create a scratch TextEdit document: `osascript -e 'tell application "TextEdit" to make new document ...'` returned `AppleEvent timed out (-1712)` after ~120 s while the accessibility read against the same process answered instantly, so TextEdit's AppleEvent handler is stalled while its AX server is healthy. No window was created and the follow-up `close document 1 saving no` was killed before it could act on the operator's document (verified: both TextEdit windows and the 581-char selection intact afterwards). Probe script: `/private/tmp/claude-501/-Users-smabe-projects-windoze/b235ba69-8871-4966-ab0d-a667a8b96aac/scratchpad/probe_selection.py`. The `whole_field=True` path is covered by the fake-bridge test; p3's live check closes the live case.
- **Qt-overlay host not covered.** The probe ran under the terminal host, which is the Textual/console condition. The failure mode "the host window is not layer 0" under the Qt overlay stays open for p3's live check.
- **No hung-app observation**, so `_MESSAGING_TIMEOUT_S` stays at 2.0.

## Failure modes to anticipate
- **The host window is not layer 0.** If TokenPal's Qt chat window were a floating panel, the first layer-0 owner would already be the source app and the heuristic would skip it. Check in the p1 live probe: with the Qt overlay running and TextEdit behind it, `source_app()` must return TextEdit. If it returns the app *behind* TextEdit, the host detection needs `os.getpid()` only (drop the first-owner rule) — record which it was.
- **pyobjc lazy-bind race.** `_keyboard_bus.py:24-39` warms `AXIsProcessTrusted` because pyobjc's lazy constant binding is not thread-safe. The bridge uses string attribute names, not `kAX…` constants, so no constant is resolved on the brain thread; keep it that way.
- **A target app that hangs** holds the caller for up to the timeout per element read (2 s each, at most three reads after the focused element: subrole, selected text, value); p3 runs the read on the brain loop, which already tolerates a 2 s poll. If the live probe shows a hung app stalls longer than ~4 s, lower `_MESSAGING_TIMEOUT_S` to 1.0 and record it.

## Done criteria
- `tests/test_desktop/test_selected_text.py` passes with every case above; the secure-field and sensitive-app cases assert zero value reads.
- Live probe on this Mac (script in the scratchpad, not committed). This needs the window order arranged by a person or by the `computer-use` skill: the worker writes the script and may use `osascript -e 'tell application "TextEdit" to activate'` followed by activating the terminal to set the order, but selecting text in TextEdit is an operator action. Three observations to record in this shard's Decisions & findings: with a paragraph selected in TextEdit and the terminal frontmost, `capture_selection()` returns a `SelectedText` whose `source_app == "TextEdit"` and whose length matches the selection; with Messages as the previous app it returns the sensitive refusal; with nothing selected it returns `whole_field=True`. If the worker cannot arrange the order, it records the criterion as PENDING OPERATOR with the script path, and the phase still commits on its tests; the master's live criteria then close it in p3's live check.
- `tokenpal --validate` on this Mac still prints the `desktop tools (permissions granted to Orca)` header (or the current `TERM_PROGRAM`) — the extraction changed nothing visible.
- `ruff check tokenpal/` and `mypy tokenpal/ --ignore-missing-imports` report nothing new.

### Finding: simplify pass 2026-09-03 (four angles; applied before review)
- **`ReadFailure`/`FailureReason` dropped.** No production reader of `.reason`; `read_selected_text` now returns `SelectedText | ActionResult` and `_failed(reason, message, app)` logs the reason and builds the `ActionResult`. p2/p3 consume `capture_selection` only, whose shape is unchanged.
- **`SelectedText` composes `DesktopContent`** (`content`, `whole_field`, `truncated`) instead of mirroring its `text`/`source_app`/repr-omits-text invariant. One privacy invariant, one test; p2 calls `captured.content.to_prompt_block()`, p3 uses `captured.content`.
- **`layer0_windows()` extracted to `tokenpal/util/macos_windows.py`.** The bridge's window loop was the second consumer of the app-awareness sense's filter (same options, same `Window Server`/`Dock` skips); both now call the helper, so the definition of "a foreground app window" cannot drift between them.
- **`responsible_host()` takes no platform argument.** Decides on `platform.system()` like `accessibility_granted`; the two `--validate` header tests patch `tokenpal.desktop.permissions.platform.system` so they hold off-Mac. Skipped: the microphone row at `cli.py:228-232` still inlines the same lookup with a different fallback ("your terminal app") — outside the phase, parked in the master.
- Test file: one autouse fixture for the Darwin/pid patches, `_bridge(focus=...)` replaces four repeated `FakeBridge` literals, the module's error constants are imported rather than redefined.
- Efficiency angle: clean (per-call pyobjc imports are `sys.modules` hits after the first; `cli.py`'s earlier `permissions` import is stdlib-only and reached only from `--validate`).

### Finding: review round 2026-09-04 (host-native fallback; Codex at its usage limit until 2026-09-06)
- **The "first layer-0 owner is the host" rule was wrong under Qt** — the failure mode this shard named. The chat window uses `buddy_overlay_flags(focusable=True)` (`tokenpal/ui/qt/_log_window.py:58`): frameless + `WindowStaysOnTopHint`, i.e. `NSFloatingWindowLevel` (`tokenpal/ui/qt/platform.py:135`), so TokenPal's process never appears at layer 0 and the rule skipped the source app itself (two angles, one live probe of the flags: own windows at `kCGWindowLayer` 8). Repair: `AXBridge.windows()` returns every on-screen window `(pid, owner, title, layer)` via `tokenpal/util/macos_windows.py:on_screen_windows()`; `source_app` skips the first normal window only when this process owns no on-screen window (terminal host) and skips only `os.getpid()` when it does (Qt host). Tests cover both hosts, a foreign floating window in front, and the locked-screen list.
- **Browser titles now reach the sensitive gate.** `refuse_if_sensitive(source_app, window_title="")` also refuses when `contains_sensitive_content_term(title)` — "Safari" never matches the app list, a banking page in it must; the narrow content-term list because titles are prose. `capture_selection` passes the title the window list already carried. Live: `kCGWindowName` came back empty while the screen was locked, so the title gate degrades to the app-name gate whenever titles are unavailable.
- **Refuted:** `cli.py`'s `plat` vs `responsible_host()`'s `platform.system()` — one runtime caller passes `platform.system()`; only tests can diverge, and they patch it. **Parked:** the microphone row's inline host lookup (master Parking lot).
- **Live state:** the Mac's screen locked during the review (`loginwindow` at layers 2001-2004 lead the list); `source_app()` still resolved `(36941, "TextEdit", "")` under the Orca host, but `AXSelectedText`/`AXValue` both read empty behind the lock screen. The selection read (581 chars) was observed before the lock; the Qt-host and nothing-selected observations are PENDING OPERATOR, closed by p3's live check.
