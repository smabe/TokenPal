# Actions / Tools Registry

- `@register_action` is the tool-registry decorator. Each `AbstractAction` subclass declares `action_name`, `description`, `parameters` (JSON Schema), `safe: bool`, `requires_confirm: bool`
- Flags `safe` and `requires_confirm` gate future autonomous LLM tool-calling (safe actions with requires_confirm=False can eventually fire without user prompting)
- Built-ins: `timer`, `system_info`, `open_app`, `do_math`. `do_math` proves the registry end-to-end via the `/math` slash command -- uses an ast walker restricted to `BinOp`/`UnaryOp`/numeric `Constant`, never `eval()`
- File tools (`find_files`, `open_path`, opt-in via `[tools] enabled_tools`) are confined to `[paths] allowed_dirs` plus the cwd git root; an explicitly empty list disables both (`tokenpal/util/paths.py`). `open_path` is a **denylist**: anything opens with the OS default handler unless its suffix is in `_DENIED`, the executable bit is set, or an ancestor is an app bundle -- so a text file lands in an editor and an `.html` in a browser, but nothing runs. Every one of those checks reads the *resolved* target, never the argument, so a `notes.txt` symlink pointing at a shell script is refused as the script it is. Refusals never echo the path (the filename itself can be the sensitive part). Neither tool carries `reads_desktop_content`, so arguments and returned paths persist like any unmarked tool's
- The conversation path confirms too: `Brain._execute_tool_call` prompts through `AgentBridge.confirm_callback` for any action with `requires_confirm`, serialized behind `Brain._confirm_lock` so two calls in one `gather` round cannot stack modals. Denial returns `"User denied <name>."`; an unwired callback refuses rather than running. The headless deny is `app.py:_agent_confirm` itself: on the console and tkinter overlays `open_confirm_modal` returns False, so a chat request to open something answers `"User denied <name>."` without ever showing a prompt. The ambient observation path never offers a `requires_confirm` action at all (`_build_ambient_specs`) — `_run_loop` awaits it inline and the confirm future has no timeout, so a modal on an unattended tick would stall the brain loop. That gate covers `open_app`, which used to launch from chat with no prompt. **`requires_confirm` is not the only ambient exclusion:** suitability is declared per tool by `allow_unprompted: ClassVar[bool]`, which **defaults False**, so a new tool is ambient-ineligible until you opt it in — the confirm flag will not decide that for you, and `reminder` stays out because an unprompted tick must not arm or cancel a standing commitment the user will be held to later. The eligible set is pinned in both directions by `test_the_ambient_eligible_set_is_exactly_the_signed_off_list` (`tests/test_brain/test_tool_loop.py`), so adding a tool forces a deliberate decision, and a sibling test pins `M1_RULES` and `M3_CATALOG` inside it — those idle rollers pick by hardcoded name and read no flag. **The ambient gate is advertise-only:** `_execute_tool_call` resolves any name the model emits against the full enabled set, so `allow_unprompted` narrows what the model is shown, not what it can run. `_REMINDER_TOOL` still exists in `orchestrator.py` for its other rule — armed rows only hydrate while the tool that can cancel them is enabled. A second flag, `writes_durable_sink: ClassVar[bool]` (`reminder`, `habit_streak`, `mood_check`), gates the durable-write tools once desktop content is in an agent run's context — **on both sides**, dropped from the advertised specs *and* refused at execution, because the model can call a sink in the same batch as the read or re-emit a name it saw earlier. The consent gate beside it only covers tools that reach the *network*, and these three write `reminders`, `habit_log` and `mood_log`, which `_prune` and `/clear` both leave alone — so that residue would be permanent. A new tool that writes model-authored text to `memory.db` must declare `writes_durable_sink = True`; nothing derives it for you
- `ActionResult.display_url`: when set, the orchestrator surfaces the URL as a clickable link in the chat log via `@click` action (Textual handles the click, opens in browser via `webbrowser.open`). `/ask` sets this to `source_url`
- Tool-use debug logging: `--verbose` shows tool round number, action name, arguments (`fmt_args`), and truncated results. Guarded by `isEnabledFor(DEBUG)` to avoid `json.dumps` overhead in production

## Desktop content tools

Tools that read text out of *another* desktop app (selection, documents, OCR — epic #59, issues #52-#55). Their output is prompt-only: it reaches the LLM and nothing else. The contract below is enforced by the runner and pinned by `tests/test_desktop/test_privacy_contract.py`, which enumerates the registry, so a new tool is measured the moment it registers.

### The marker

```python
reads_desktop_content: ClassVar[bool] = True
cacheable: ClassVar[bool] = False
```

`tokenpal/actions/read_selection.py` is the reference implementation — the whole tool is the `execute()` this contract prescribes, nothing more.

`reads_desktop_content` is the only switch; everything below keys on it. `cacheable = False` is belt-and-braces — `AgentRunner._execute_one` already excludes a marked action from the in-run cache — and the contract test requires it so the intent survives a refactor of the runner.

### Call order inside `execute()`

1. `require_consent()` — **first, before any argument validation**, so a missing grant refuses identically however the model called the tool. Returns `None` when the grant exists, otherwise `ActionResult(success=False, output="Tool requires 'desktop content' consent. Open /consent to grant it.")`. Assign it, test for `None`, and return it unchanged when it is not — never `return require_consent()`, which returns `None` on a consented machine.
2. `refuse_if_sensitive(source_app, window_title)` — as soon as the app name is known: before the OS read when the window list gives it (`capture_selection` refuses on the window owner name and the window title before touching the Accessibility API), after the read otherwise. Return its result when not `None`.
3. Read from the OS.
4. `DesktopContent(text, source_app, kind).to_prompt_block()` — put **only** that string into `ActionResult.output`.

Steps 1, 2 and 4 live in `tokenpal/desktop/content.py`; step 3 is yours. `Category.DESKTOP_CONTENT` is `"desktop_content"`; `/consent` lists and revokes it like any other category.

Never hand-build the `<desktop_content>` envelope. `to_prompt_block` scrubs the body with `scrub_content_body` (the narrower `contains_sensitive_content_term` list — the full app-name list in `scrub_body` matches ordinary prose like "signal" and "health" and stays with the network tools and `display_text`), runs `neutralize_envelope_tags(..., "desktop_content")` so a forged closing tag in the read text cannot break out, and strips `"`, `<`, `>` and line separators from both interpolated attributes.

`refuse_if_sensitive` deliberately does **not** name the app: its result is returned to the model as the tool result, and the repo's precedent for a sensitive app name reaching any sink is to substitute a generic label (`list_processes.py`, `senses/process_heat/sense.py` both emit `"something"`). The trace line is unpersisted for a marked tool, so this is defence in depth rather than the last line — keep it anyway if a marked tool is ever reachable from a persisted path.

### What the runner does for a marked tool

Redaction is a property of the **session**, not of the tool: once desktop content is in `messages`, the model can copy it into any later call's arguments. `AgentSession.desktop_content` is set in `_execute_one` *before* the confirm gate — a denied confirm still flags the session — and from then on, for the rest of the run:

- every trace line goes out `persist=False` (`AgentRunner._trace`);
- the marked tool's own result is never cached (`cache_eligible` excludes it). Note this is per-tool, not run-wide: a later non-marked cacheable tool in the same run is still cached, so do not rely on the cache being off after a read;
- every tool whose catalog entry names a `consent_category` is dropped from the tool list (`_tools_for`). A gated tool requested in the **same** LLM response is not invoked either, provided it comes after the content read in the call list — it returns the fixed `"skipped: desktop content is in context"`. One positioned *before* the marked tool runs normally, which is safe because no content is in `messages` yet;
- reasoning is replaced with `… (reasoning hidden: desktop content in context)`;
- the final answer is delivered through `log_buddy_message(..., persist=False)` and the persisted bubble is a persona line generated from a content-free prompt (`build_desktop_done_prompt`), falling back to `"Done. The answer is in the chat log and was not saved."`. A run that produces no answer shows `"Nothing came back that time — and nothing was saved."` instead.

That drop keys on the catalog, so **every action must have a catalog entry** — `find_entry` returning `None` reads as "ungated" and would leave a network tool available after a content read. The contract test asserts both registry/catalog parity and that every action whose own code imports a network client declares a `consent_category`.

Marked tools assume an overlay with a chat log. `AbstractOverlay.log_buddy_message` is a no-op and only Qt and Textual override it, so under `--overlay console` or the tkinter fallback the unpersisted final answer goes nowhere while the bubble still points at a chat log that overlay does not have.

### The conversation path never sees them

`_build_conversation_specs` filters marked actions out of the conversation tool specs, and `_execute_tool_call` refuses one by name anyway with `"Tool '<name>' is only available in /agent."`. That is deliberate: `ConversationSession.history` feeds the summarizer, the speech bubble and the INFO reply log, three sinks with no per-turn flag. Marked tools are reachable from `/agent` and from slash commands that call the LLM directly — such a command must log its reply with `log_buddy_message(text, persist=False)`.

### Two things never to do

- Never `log` (or `%s`-interpolate) `DesktopContent.text`. `repr`/`str` omit it on purpose, so log the object, never the field.
- Never assign the text, or anything derived from it, to `ActionResult.display_text`. The conversation path passes that field straight to the persisting log callback.

Desktop content **is** sent to the configured inference server, which may be a remote `[llm] api_url` on the LAN. That is not gated separately; the contract is about local persistence and logging.

### What a new tool inherits vs. must write

`tests/test_desktop/test_privacy_contract.py` **checks** these the moment your tool registers — it does not supply them. Two you must write yourself:

- `cacheable: ClassVar[bool] = False` — `AbstractAction.cacheable` defaults to **True**.
- the `require_consent()` call — nothing calls it on your behalf.
- a `max_chars` cap so the scrubbed envelope stays under `_MESSAGE_RESULT_CAP` (`tokenpal/brain/agent.py`) — the runner truncates a longer result and the closing tag is lost. `read_selection` passes 1,000; its test pins the worst case.

Three the defaults already satisfy, so the test only guards against a regression: `safe`/`requires_confirm`, absence from `M3_CATALOG` and every `M1_RULES` `tool_name`/`extra_tool_names` (both ambient paths build from hand-written allowlists that consult no marker, and both feed the persisted observation path), and absence from `DEFAULT_TOOLS` (a marked tool is opt-in via `[tools] enabled_tools`, never on by default). The test also refuses a marked tool that reaches any network client, or that hand-builds the envelope string.

Each tool must write for itself, because a generic test cannot supply valid arguments or fake an OS read:

- a sensitive-source refusal test that stubs the OS read to report a sensitive app and asserts the refusal;
- an `assert_no_leak(fixture, lines=..., caplog_text=..., memory=...)` sweep (`tests/_helpers.py`) over its own path, proving a fixture string reaches no persisted trace line, no log output, and neither `chat_log` nor `conversation_summaries`.

Add your tool's name to `test_the_first_marked_tool_is_registered_on_every_host` in that file; the parametrized cases skip silently when a marked module fails to import.
