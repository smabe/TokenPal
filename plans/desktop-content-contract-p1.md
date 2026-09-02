# desktop-content-contract-p1 — value type, consent category, sensitive-app refusal

You are phase `p1` of the `desktop-content-contract` plan. This phase delivers, as one commit, the consent category, the `DesktopContent` value type with its prompt wrapper, the two gate helpers every later content tool calls first, and the shared `scrub_body` seam they reuse.

## Locked decisions
See the master `plans/desktop-content-contract.md`. The decisions binding this phase:
- `scrub_body` moves to `tokenpal/util/untrusted_text.py`. `tokenpal/actions/network/_http.py` imports it from there and keeps the name so the seven network tools importing `scrub_body` from `_http` (`grep -rn "import .*scrub_body" tokenpal/actions/network/` at 1de1d43: `book_suggestion`, `random_fact`, `trivia_question`, `word_of_the_day`, `on_this_day`, `joke_of_the_day`, `random_recipe`) are untouched.
- `consent_error` is generalized in `tokenpal/actions/base.py` as `consent_error(category_label: str) -> ActionResult`. The network `_base.consent_error()` delegates with `"web fetches"`; its message stays byte-identical (`"Tool requires 'web fetches' consent. Open /consent to grant it."`, `tokenpal/actions/network/_base.py:12-14`).
- The sensitive-app filter for source apps is `contains_sensitive_term` (`tokenpal/brain/personality.py:285-290`). Actions already import it from there (`tokenpal/actions/grep_codebase.py:12`, `memory_query.py:13`, `git_log.py:11`), so `tokenpal/desktop/` importing from `tokenpal.brain.personality` follows precedent.
- No new config keys, no new dialog (master Non-goals).

## Work
- Scope trace: DIRECT — the consent category, the value type, and the refusal helper are named in the requested outcome. The `scrub_body` move is PREREQUISITE: `to_prompt_block` needs it and `tokenpal/desktop/` must not import from `tokenpal/actions/network/_http.py` (an HTTP helper module owning aiohttp sessions, `_http.py:1-60`). The `consent_error` generalization is PREREQUISITE for the same reason (second consumer; CLAUDE.md Discipline "Grep before adding a helper").
- `tokenpal/config/consent.py` — add `DESKTOP_CONTENT: Final = "desktop_content"` to `Category` (`consent.py:26-32`), append it to `ALL_CATEGORIES` (`consent.py:35-42`), add one docstring line under "Known categories" (`consent.py:6-12`): `desktop_content — text read from other apps (selection, documents, OCR); prompt-only`.
- `tokenpal/actions/base.py` — add, below `ActionResult` (`base.py:20-36`):
  ```python
  def consent_error(category_label: str) -> ActionResult:  # proposed
      return ActionResult(
          output=f"Tool requires '{category_label}' consent. Open /consent to grant it.",
          success=False,
      )
  ```
- `tokenpal/actions/network/_base.py` — `consent_error()` body becomes `return base_consent_error("web fetches")` (import under a distinct name to avoid shadowing); keep the function so the 13 network tools and `tests/test_actions/test_network/conftest.py:29-46` are untouched.
- `tokenpal/util/untrusted_text.py` — new. Move `_SENSITIVE_PLACEHOLDER` (`tokenpal/actions/network/_http.py:25`), `_scrub_line` (`:117-118`), and `scrub_body` (`:121-127`) verbatim (keep the `scrub_body` docstring, drop the sentence about `wrap_result` living beside it). Imports `contains_sensitive_term` from `tokenpal.brain.personality`.
- `tokenpal/actions/network/_http.py` — delete the three moved definitions; add `from tokenpal.util.untrusted_text import scrub_body` at the top so `wrap_result` (`_http.py:130-132`) and the re-export keep working. Remove the now-unused `_SENSITIVE_PLACEHOLDER` import/definition if ruff flags it.
- `tokenpal/util/text_guards.py` — `neutralize_envelope_tags` gains a `tag: str = "transcript"` parameter and its pattern is built per tag, tolerating ignorable characters interleaved in the name and attributed/self-closing forms. Added during p1 review: `to_prompt_block` must neutralize a forged `</desktop_content>`, and CLAUDE.md Discipline ("Grep before adding a helper") points at this existing guard rather than a second one. `<transcript>` behavior for both existing callers (`personality.py:1397`, `session_summarizer.py:77`) is unchanged, verified by differential run against the old pattern.
- `tokenpal/desktop/__init__.py` — new. Module docstring: OS-integration helpers for tools that read content from other desktop apps; nothing here is a sense or an action.
- `tokenpal/desktop/content.py` — new. Shape (proposed names, exact shape):
  ```python
  ContentKind = Literal["selection", "document", "ocr"]

  @dataclass(frozen=True, repr=False)
  class DesktopContent:
      text: str
      source_app: str
      kind: ContentKind

      def __repr__(self) -> str:
          return f"DesktopContent(kind={self.kind}, app={self.source_app!r}, chars={len(self.text)})"

      __str__ = __repr__

      def to_prompt_block(self) -> str:
          app = self.source_app.replace('"', "")
          return (
              f'<desktop_content kind="{self.kind}" app="{app}">\n'
              f"{scrub_body(self.text)}\n</desktop_content>"
          )

  def refuse_if_sensitive(source_app: str) -> ActionResult | None:
      """Error result when *source_app* matches SENSITIVE_APPS, else None."""

  def require_consent(path: Path | None = None) -> ActionResult | None:
      """consent_error('desktop content') unless Category.DESKTOP_CONTENT is granted.
      *path* mirrors has_consent's test hook."""
  ```
  The refusal message names the app: `f"Won't read from {source_app}: it's on the sensitive-app list."`. The consent label is `"desktop content"`. Import `Category` and `has_consent` at module level (`from tokenpal.config.consent import Category, has_consent`) so p3's test can monkeypatch `tokenpal.desktop.content.has_consent`; do not import inside the function. The module docstring states the contract every caller must follow, in this order: `require_consent()` first, before any argument validation, then read from the OS, then `refuse_if_sensitive(source_app)`, then wrap with `to_prompt_block()`; never log `.text`, never assign it to `ActionResult.display_text`.
- `tests/test_consent.py` — assert `Category.DESKTOP_CONTENT in ALL_CATEGORIES` and that `save_consent`/`load_consent` round-trip it (pattern: existing tests in that file, fixture `consent_path` at `tests/test_consent.py:21-23`).
- `tests/test_actions/test_network/test_http.py` — retarget the `contains_sensitive_term` monkeypatch from `tokenpal.actions.network._http` to `tokenpal.util.untrusted_text`, assertions unchanged; and add `test_consent_error_message_unchanged` pinning the byte-identical `'web fetches'` refusal string, so it runs with the network suite rather than from `tests/test_desktop/`.
- `tests/test_actions/test_network/test_tools.py` — same monkeypatch retarget. (Both added during p1: the patch target moves with the seam, so the `scrub_body` move cannot land without them. Planning miss — the shard tracked the seven tools importing `scrub_body` but not the two tests patching the function it calls.)
- `tests/test_desktop/__init__.py` — new, empty (tests packages carry `__init__.py`, e.g. `tests/test_actions/__init__.py`).
- `tests/test_desktop/test_content.py` — new. Fixture text `FIXTURE = "SECRET-FIXTURE-7731"`. Tests: `repr`, `str`, `f"{c}"`, `"%s" % c`, and `logging` via `caplog` at DEBUG of `log.debug("%s", c)` contain no `FIXTURE`; `to_prompt_block` opens with `<desktop_content kind="selection" app="TextEdit">`, contains `FIXTURE`, and replaces a line containing a sensitive app name (pick one from `SENSITIVE_APPS`, `tokenpal/brain/personality.py`) with the placeholder while keeping the other lines; `refuse_if_sensitive("1Password")` returns an unsuccessful `ActionResult` naming the app and `refuse_if_sensitive("TextEdit")` returns `None`; `require_consent(path=tmp_path/"c.json")` returns the consent error with no file and `None` after `save_consent({Category.DESKTOP_CONTENT: True}, path)`.

## Decisions & findings
### Decision: exclude the path, not flag the turn  *(status: active)*
- **Rationale:** history dicts go to the LLM server verbatim; adding a key would either ship to the server or need stripping in every backend. Keeping desktop content and its replies off `ConversationSession.history` removes the summarizer sink by construction. p2 enforces it in the orchestrator; p1 states it in the module docstring so #52 reads it.
- **Alternatives considered:** a `ConversationSession.ephemeral_turns: set[int]` parallel index (rejected: two structures to keep aligned through `pop()` at `orchestrator.py:2580` and `_clear_conversation`); a flag on the message dict (rejected above).
- **Evidence:** `tokenpal/brain/orchestrator.py:110-151` (history shape), `:947-971` (summarizer input), `:1692-1697` (tool results never enter history).

### Decision: `repr=False` dataclass with a manual `__repr__`  *(status: active)*
- **Rationale:** the leak the contract guards against is an accidental `log.debug("%s", content)`; `dataclass(repr=False)` plus `__str__ = __repr__` closes both `%s` and `%r`. The text stays a normal attribute because the tool must still read it.
- **Alternatives considered:** a `__slots__` class with a private attribute and accessor (more ceremony, same guarantee).
- **Evidence:** the `caplog` test in Work.

### Finding: the shard's `scrub_body` inventory missed two monkeypatch sites  *(status: resolved)*
- Both `tests/test_actions/test_network/test_http.py` and `test_tools.py` patch `contains_sensitive_term` **on `_http`**. After the move that attribute is gone, so `monkeypatch.setattr` raises at patch time. Retargeted to `tokenpal.util.untrusted_text`; `_scrub_line` reads the module global at call time, so the patch still reaches the seven tools' `display_text` path. Assertions unchanged.

### Finding: mypy strict rejects a plain re-export  *(status: resolved)*
- `from tokenpal.util.untrusted_text import scrub_body` in `_http.py` produced seven `attr-defined` errors, one per importing tool, because `pyproject.toml` sets `strict = true` which implies `no_implicit_reexport`. The redundant `import scrub_body as scrub_body` is the explicit-re-export form and is load-bearing; a comment on the line says so. Confirmed by shadow-running mypy with the alias collapsed: exactly seven errors. Any later phase relocating a symbol behind a re-export hits this.

### Finding: no import cycle, and the fallback was not needed  *(status: resolved)*
- `tokenpal/util/__init__.py` and `tokenpal/brain/__init__.py` are both empty and `personality.py` imports only `tokenpal.util.text_guards` / `timefmt`, so `tokenpal.util.untrusted_text` importing `tokenpal.brain.personality` is safe today. The direction is nonetheless inverted (a leaf utility importing brain), and the natural second consumers of `scrub_body` — `eod_summary.py:142`, `session_summarizer.py:178`, `intent.py:79` — all live in `brain` and would close the loop. Parked in the master.

### Finding: two prompt-injection holes in `to_prompt_block`, found at review  *(status: resolved)*
- A body containing a literal `</desktop_content>` closed the envelope; `source_app` was sanitized for `"` only, so `<`, `>` and line separators forged the opening tag. Both are fixed and tested, and both tests were confirmed to fail against the pre-fix code. The fix reuses `neutralize_envelope_tags` rather than adding a second guard, which required giving it a `tag` parameter.
- Hardening the neutralizer for attributed tags initially broke `<transcript>` handling: `[^<>]*` after the name matched the trailing `s` in `<transcripts>`, which the old pattern left alone. The name must now be followed by a separator, `/`, or the bracket. Verified identical to the old pattern over ~200k differential inputs.
- `kind` is interpolated into the same header and its `Literal` is unenforced at runtime, so it goes through the same sanitizer. Unreachable today (only tests construct `DesktopContent`), live the moment a tool forwards a model-supplied value.

### Finding: `load_consent` raised on a non-object consent file  *(status: resolved)*
- `raw.get(...)` raised `AttributeError` when `.consent.json` held valid JSON that was not an object. `require_consent()` is documented as the gate that runs *first*, so it would throw rather than refuse. Guarded with `isinstance(raw, dict)`, returning the same all-`False` shape as the missing-file and decode-error branches.

## Failure modes to anticipate
- Moving `scrub_body` changes the import graph: `tokenpal/util/` importing `tokenpal.brain.personality` could create a cycle if `personality` (or anything it imports) imports from `tokenpal.util`. Check with `python -c "import tokenpal.util.untrusted_text"` and `grep -n "^from tokenpal.util\|^import tokenpal.util" tokenpal/brain/personality.py` before moving; if a cycle appears, import inside the function as `_keyboard_bus.py:33` does for pyobjc.
- `ALL_CATEGORIES` is the schema of `.consent.json`: adding a key is backward compatible because `load_consent` fills missing keys with `False` (`consent.py:59`). No migration.
- Thirteen network tools import `consent_error` from `_base` by name; keep it a real module-level function there (delegating), not a bare re-export, so those imports and any future monkeypatch of `_base.consent_error` keep working. (`tests/test_actions/test_network/conftest.py:33-36` patches only `web_fetches_granted`, not `consent_error`.)

## Done criteria
- `tests/test_desktop/test_content.py` and `tests/test_consent.py` pass; the `caplog` test proves the observable: a DEBUG log line formatted from a `DesktopContent` shows `chars=19`, not the fixture text.
- `pytest tests/test_actions/test_network` passes unchanged (proves the `scrub_body` move and the `consent_error` delegation are behavior-preserving).
- `python -c "from tokenpal.actions.network._http import scrub_body, wrap_result"` succeeds.
- `ruff check tokenpal/` and `mypy tokenpal/ --ignore-missing-imports` clean.
- Diff stays under roughly 150 lines of source (tests excluded).
