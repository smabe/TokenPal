# selected-text-primitive-p2 — `read_selection` agent tool, the contract test goes live, raw tool-call args stop reaching the log

You are phase `p2` of the `selected-text-primitive` plan. This phase registers the first `reads_desktop_content` action on top of p1's primitive, makes the #51 contract test prove it is measuring something, and closes the one log line below the agent layer that could echo desktop content. One commit.

## Locked decisions
See the master `plans/selected-text-primitive.md`. The decisions binding this phase:
- **The tool caps its own text so the envelope fits `_MESSAGE_RESULT_CAP`.** `_truncate` (`tokenpal/brain/agent.py:405-406`) cuts a tool result at 2,047 chars + "…" before it enters `messages` (`:244`); a longer envelope loses `</desktop_content>`. `read_selection` passes `max_chars=1_000` to `capture_selection`. Measured 2026-09-03 with the real `to_prompt_block()` on a body of repeated `venmo\n` lines (the shortest `SENSITIVE_CONTENT_TERMS` entry, each line scrubbed to `[filtered]`): 1,800 chars → 3,368-char envelope; 1,200 → 2,268; 1,000 → 1,899. So 1,000 is the number, and the test below pins it.
- **No `consent_category` in the catalog entry** (master Non-goals; `_needs_consent` at `agent.py:383-387` would drop the tool after its own first read). The blurb states the consent requirement instead.
- **`platforms = ("darwin",)`, `safe = True`, `requires_confirm = False`, `cacheable = False`, `reads_desktop_content = True`, no parameters.** Opt-in through `[tools] enabled_tools` like every non-default tool (`tokenpal/actions/registry.py:56-63`); never in `DEFAULT_TOOLS`, `M3_CATALOG`, or `M1_RULES`.
- **Consent first, before anything else, exactly as `docs/claude/actions.md` § "Call order" states**: `refusal = require_consent()`; `if refusal is not None: return refusal`.
- **The contract test names the tool.** Registry enumeration fails open when an import fails (`registry.py:33-34`), so a non-parametrized test asserts `"read_selection"` is among `_MARKED` on every host. p1's function-scope imports are what make that hold on Linux/Windows CI.
- **`http_backend.py`'s malformed-tool-call warning logs the function name and the argument length only.** Carried-in item 3 from #51: the line sits below the agent layer, so `session.desktop_content` cannot redact it, and malformed JSON correlates with long unescaped strings — exactly a copied selection.

## Work
- Scope trace: DIRECT — "`read_selection` is exposed to `/agent` as the first `reads_desktop_content` action" (scope contract); the contract-test assertion and the log line are SAFETY — initial state: a marked tool registered → action: CI on Linux fails the pyobjc-free import, or a local model malforms a tool call that carries copied selection text → failing outcome: the contract test reports a green "skipped" run, or the selection lands in `~/.tokenpal/logs` at WARNING.
- `tokenpal/actions/read_selection.py` — new. Proposed:

  ```python
  @register_action
  class ReadSelectionAction(AbstractAction):
      action_name = "read_selection"
      description = (
          "Read the text the user selected in the app they were using before "
          "TokenPal (macOS). Falls back to the focused field's whole text. "
          "Needs 'desktop content' consent."
      )
      parameters = {"type": "object", "properties": {}}
      platforms = ("darwin",)
      safe = True
      requires_confirm = False
      cacheable: ClassVar[bool] = False
      reads_desktop_content: ClassVar[bool] = True

      async def execute(self, **kwargs: Any) -> ActionResult:
          refusal = require_consent()
          if refusal is not None:
              return refusal
          captured = capture_selection(max_chars=_MAX_CHARS)
          if isinstance(captured, ActionResult):
              return captured
          return ActionResult(output=captured.content.to_prompt_block())
  ```
  `_MAX_CHARS = 1_000` with a one-line comment naming `_MESSAGE_RESULT_CAP` and the scrub growth as the reason (a hidden constraint, allowed by the comment rule). The action description and catalog blurb copy are proposed and listed at approval. The module imports `tokenpal.desktop.content` and `tokenpal.desktop.selected_text` only, plus `tokenpal.actions.base`/`registry` — nothing under `tokenpal.brain`, so the contract's unbounded network walk (`tests/test_desktop/test_privacy_contract.py:106-125`) stays clean (verified this session: the walk from `tokenpal.desktop.content` reaches no network module). Never assign `display_text`; never log the text.
- `tokenpal/actions/catalog.py` — append to `LOCAL_SECTION.entries`: `CatalogEntry("read_selection", "Read the selected text of the app you came from (macOS). Needs desktop_content consent.", kind="local")`. `test_every_registered_action_has_a_catalog_entry` (`test_privacy_contract.py:304-310`) fails without it.
- `tokenpal/llm/http_backend.py` — at the `json.JSONDecodeError` branch inside `HttpBackend.generate_with_tools` (813f8c7 `:441-449`, after the `await self._client.post(...)`): `log.warning("Bad tool call arguments for %s (%d chars)", fn.get("name", ""), len(raw_args) if isinstance(raw_args, str) else 0)`; `args = {}` unchanged.
- `tests/test_desktop/test_privacy_contract.py` — add `test_the_first_marked_tool_is_registered_on_every_host()` asserting `"read_selection" in {cls.action_name for cls in _MARKED}` with a docstring stating why enumeration alone fails open. Update the module docstring's "With no marked tool shipped…" sentence (`:5-8`) to say the parametrized cases now run for `read_selection`.
- `tests/test_desktop/test_read_selection.py` — new. (1) Sensitive source: patch `tokenpal.desktop.selected_text.capture_selection`'s bridge — simpler: monkeypatch `tokenpal.actions.read_selection.capture_selection` to return `refuse_if_sensitive("Messages")` and assert the result is that refusal verbatim (message does not contain "Messages"); (2) success: monkeypatch it to return `SelectedText(DesktopContent(FIXTURE, "TextEdit", "selection"), whole_field=False, truncated=False)` (p1 shipped `SelectedText` composing a `DesktopContent`; see p1 Decisions & findings) and assert `output` starts with `<desktop_content kind="selection" app="TextEdit">` and contains the fixture, `display_text is None`; (3) envelope budget: `SelectedText(DesktopContent(("venmo\n" * 300)[:_MAX_CHARS], "TextEdit", "selection"), whole_field=False, truncated=True)` — every line scrubs to `[filtered]`, the worst growth — assert `len(output) <= _MESSAGE_RESULT_CAP` (import the constant from `tokenpal.brain.agent` and `_MAX_CHARS` from the action — tests may import what the action must not) and that `output` ends with `</desktop_content>`; (4) agent-run sweep: reuse the `_agent_brain` shape from `tests/test_brain/test_tool_loop.py:412-431` with the real `ReadSelectionAction` (capture monkeypatched to the fixture, consent patched to `True` via `tokenpal.desktop.content.has_consent`), run `brain._handle_agent_goal(...)`, and `assert_no_leak(FIXTURE, lines=buf, caplog_text=caplog.text, memory=memory)`; also assert the trace line `← [desktop content: N chars, not shown] [unpersisted]` appears. Consent-missing behaviour is already covered by the parametrized contract case; do not duplicate it.
- `tests/test_llm/test_tool_calling.py` — one test feeding a tool call whose `arguments` is the string `'{"text": "SECRET-FIXTURE-7731'` (unterminated) through the backend's parse path with `caplog` at WARNING; assert `SECRET-FIXTURE-7731 not in caplog.text` and the warning names the function. The parse is inline in `HttpBackend.generate_with_tools` after the POST, so reach it offline the way `tests/test_max_tokens_auto_probe.py:113-131` does: construct the backend, set `backend._client = AsyncMock()` with `post = AsyncMock(return_value=<fake response whose .json() carries the malformed tool call>)`, and call `generate_with_tools`.
- `tests/test_actions/test_catalog.py` — added by the worker (planning miss): `LOCAL_SECTION` membership is pinned as an exact set.
- `tests/_helpers.py` — added at simplify/review (planning miss): `agent_brain`, `tool_call`, `tool_call_response`, `allow_confirm`, `JsonResponse`.
- `tests/test_brain/test_tool_loop.py` — added at simplify: `_agent_brain`/`_reads_call` delegate to the shared helpers.
- `tests/test_agent.py` — added at review: uses `allow_confirm`/`tool_call_response` instead of private copies.
- `tests/test_max_tokens_auto_probe.py` — added at review: three inline response fakes → `JsonResponse`.
- `tests/test_server/test_llm_backend.py` — added at review: `_FakeResponse` → `JsonResponse`.
- `tokenpal/desktop/content.py` — added at simplify: the chase/fidelity tradeoff moved into `refuse_if_sensitive`'s docstring (docstring only).
- `plans/RESUME-selected-text-primitive.md` — added at review: call-order line corrected.
- `docs/claude/actions.md` — in "Desktop content tools": (a) name `tokenpal/actions/read_selection.py` as the reference implementation under "The marker"; (b) add to "What a new tool inherits vs. must write": "cap the text so the whole envelope is under `_MESSAGE_RESULT_CAP` (2,048 chars, `tokenpal/brain/agent.py`) — the runner truncates a longer result and the closing tag is lost, and `scrub_content_body` can nearly double a body of short sensitive lines; `read_selection` uses 1,000"; (c) in "Call order", step 3 becomes "`refuse_if_sensitive(source_app, window_title)` — as soon as the app name is known, which may be before the OS read (`capture_selection` refuses on the window owner name and the window title before touching the Accessibility API); the title check uses the narrow content-term list, so a bank whose name is an ordinary word (chase, fidelity) in a browser title passes — an accepted tradeoff, the app-name list would false-positive on ordinary page titles"; the `content.py` module docstring already states the new order (p1 review); (d) note that `_MARKED` is now asserted non-empty by name, so the next marked tool should be added to that assertion's set.

## Decisions & findings
### Decision: 1,000-char cap inside the action, not in `capture_selection`'s default  *(status: active)*
- **Rationale:** the slash path (p3) is not subject to `_MESSAGE_RESULT_CAP` and uses the 8,000-char default; only the agent path has the 2,048 ceiling. The cap belongs where the constraint lives.
- **Alternatives considered:** raising `_MESSAGE_RESULT_CAP` for marked tools — changes every agent tool's budget for one consumer; making `_truncate` envelope-aware — a second envelope mechanism.
- **Evidence:** `tokenpal/brain/agent.py:37`, `:244`, `:405-406`.

### Decision: the contract test names `read_selection`  *(status: active)*
- **Rationale:** a registry-enumerating test cannot distinguish "no marked tool exists" from "the marked tool failed to import"; naming the tool is the only assertion that fails on the second.
- **Alternatives considered:** having `discover_actions` record import failures and asserting none — broader change to the registry (#60 territory), and a legitimately platform-gated module would still need an allowlist.
- **Evidence:** `tokenpal/actions/registry.py:33-34`; issue #52 comment "Carried in from #51", item 1.

## Failure modes to anticipate
- **`_instantiate` skip.** If `ReadSelectionAction.__init__` ever touches the OS, every parametrized case skips off-Mac and the named test is the only survivor. Keep `__init__` to `super().__init__(config)`.
- **The catalog `Kind` Literal.** `"local"` exists (`catalog.py:19`); do not add a "desktop" kind for one entry.
- **Scrub lengthening.** `scrub_content_body` replaces a whole line with `[filtered]` (10 chars), so a body of `venmo\n` lines (6 chars each) grows by ~1.7×; `neutralize_envelope_tags` can also grow the body. That is why the cap is 1,000, not the 1,800 first drafted; the test pins it.

## Done criteria
- `pytest tests/test_desktop/test_privacy_contract.py -v` on this Mac shows the parametrized cases for `read_selection` as PASSED (not skipped), and `test_the_first_marked_tool_is_registered_on_every_host` passes.
- `pytest tests/test_desktop/test_read_selection.py` passes, including the agent-run `assert_no_leak` sweep with a real `MemoryStore`.
- `tests/test_llm/test_tool_calling.py`: the malformed-arguments warning carries no argument text.
- Live: `tokenpal --check` lists `read_selection` when `[tools] enabled_tools = ["read_selection", "agent_mode"]` is set in `~/.tokenpal/config.toml`, and `/tools describe read_selection` prints `platforms: darwin`, `safe: True`, `cacheable: false`. Leave the config in place for p3's live check.
- Live (operator-arranged, same terms as p1's probe): with `desktop_content` consent granted and a paragraph selected in TextEdit, `/agent tell me what I selected` runs `read_selection`; the trace shows `← [desktop content: N chars, not shown]`, the answer arrives in the chat pane, `chat_log` holds the trace lines and the persona bubble but not the answer. If the order cannot be arranged in the session, record PENDING OPERATOR and let p3's live check close it.
- `ruff check tokenpal/` and `mypy tokenpal/ --ignore-missing-imports` report nothing new.

### Finding: what p2 shipped (worker report 2026-09-04)
- **Envelope measured: 1,000-char `venmo\n` body → 1,899-char envelope**, 149 under `_MESSAGE_RESULT_CAP`; the trailing partial line (`venm`) escapes the scrub, which is why growth is ~1.9× rather than 2×. `neutralize_envelope_tags` is length-preserving, so this is the true worst case.
- **The runner's trace line counts envelope chars, not selection chars.** `AgentRunner` takes `len(step_record.result)` and the result is already the `<desktop_content>` block, so a 581-char TextEdit selection reports ~640+. p3's live criterion and any assertion on `← [desktop content: N chars, not shown]` must expect envelope length.
- **`tests/test_actions/test_catalog.py` pins `LOCAL_SECTION` membership as an exact set** — planning miss; every future catalog entry (p3 has none, #53-#58 do) must update it.
- **The catalog blurb split across two adjacent literals** to stay under ruff's 100 columns; copy byte-identical to the shard.
- **The contract's envelope-literal scan never sees the catalog blurb**: `_module_closure(cls, "tokenpal.actions.")` yields `read_selection`, `base`, `registry`, none of which import `catalog`.
- **No cross-test-module imports in this repo except `tests._helpers`**, so the agent-run harness was first built locally, then (simplify pass) promoted: `tests/_helpers.py` gained `agent_brain(llm, actions, memory)`, `tool_call_response(name)`, `allow_confirm`; `tests/test_brain/test_tool_loop.py`'s `_agent_brain`/`_reads_call` delegate to them. The `HttpBackend` fake-client copies (`test_max_tokens_auto_probe.py`, `test_server/test_llm_backend.py`, the new `test_tool_calling.py` case) differ in shape (different endpoints, one records the body) and were left as they are.
- **Docs call order renumbered** to match `content.py`: consent → refuse (as early as the app name is known) → read → envelope. The chase/fidelity title tradeoff lives in `refuse_if_sensitive`'s docstring, not the docs step list.
- **Live checks PENDING OPERATOR** (screen locked; config untouched): `[tools] enabled_tools = ["read_selection", "agent_mode"]`, then `tokenpal --check` lists `read_selection`; `/tools describe read_selection` → `platforms: darwin`, `safe: True`, `cacheable: false`; with consent and a TextEdit selection, `/agent tell me what I selected` → trace `← [desktop content: N chars, not shown]`, answer in the pane, `chat_log` without it. Offline substitute passed: `resolve_actions(..., optin_allowlist={"read_selection","agent_mode"}, default_tools=set())` → `['read_selection']`; `find_entry` returns the LOCAL entry with an empty `consent_category`.

### Finding: review round 2026-09-04 (host-native fallback; Codex still at its limit)
- **Fixed:** `http_backend.py` — the `else 0` arm was dead (the except only fires for a `str`) and a JSON `null`/`[]` argument string produced `ToolCall(arguments=None|list)`, which the executors turned into a raised `TypeError` instead of `{}`; now `if not isinstance(args, dict): args = {}` after the parse, with a test. **Fixed:** the worst-case envelope test derives its line from `min(SENSITIVE_CONTENT_TERMS, key=len)` so a shorter term added later fails the test, not the runner. **Fixed:** the action description discloses the 1,000-char cap and the whole-field fallback, since `whole_field`/`truncated` cannot ride inside the envelope (the contract puts only the envelope in `output`). **Fixed:** the stale resume prompt's call order; the docs' "four-line" count.
- **Reuse (CLAUDE.md "two concrete consumers → refactor in that pass"):** `tests/_helpers.py` now owns `tool_call`, `tool_call_response(*calls)`, `allow_confirm`, `agent_brain`, and `JsonResponse(payload)`; `tests/test_agent.py` dropped its private copies of the first three, and the six inline httpx-response fakes in `test_max_tokens_auto_probe.py`, `test_server/test_llm_backend.py` and `test_tool_calling.py` became `JsonResponse(...)`. Planning miss: those four test files were not in Work; the review pulled them in.
- **Refuted / no change:** `test_a_sensitive_source_app_refusal_is_returned_unchanged` asserts pass-through, not the refusal itself — the refusal is `test_selected_text.py`'s job and the docs sentence "stubs the OS read to report a sensitive app" is satisfied there. `read_selection` has no `consent:` line in `/tools describe` because its `consent_category` is empty by design; the blurb carries the hint.
