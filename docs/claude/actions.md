# Actions / Tools Registry

- `@register_action` is the tool-registry decorator. Each `AbstractAction` subclass declares `action_name`, `description`, `parameters` (JSON Schema), `safe: bool`, `requires_confirm: bool`
- Flags `safe` and `requires_confirm` gate future autonomous LLM tool-calling (safe actions with requires_confirm=False can eventually fire without user prompting)
- Built-ins: `timer`, `system_info`, `open_app`, `do_math`. `do_math` proves the registry end-to-end via the `/math` slash command -- uses an ast walker restricted to `BinOp`/`UnaryOp`/numeric `Constant`, never `eval()`
- `ActionResult.display_url`: when set, the orchestrator surfaces the URL as a clickable link in the chat log via `@click` action (Textual handles the click, opens in browser via `webbrowser.open`). `/ask` sets this to `source_url`
- Tool-use debug logging: `--verbose` shows tool round number, action name, arguments (`fmt_args`), and truncated results. Guarded by `isEnabledFor(DEBUG)` to avoid `json.dumps` overhead in production

## Desktop content tools

Tools that read text out of *another* desktop app (selection, documents, OCR — epic #59, issues #52-#55). Their output is prompt-only: it reaches the LLM and nothing else. The contract below is enforced by the runner and pinned by `tests/test_desktop/test_privacy_contract.py`, which enumerates the registry, so a new tool is measured the moment it registers.

### The marker

```python
reads_desktop_content: ClassVar[bool] = True
cacheable: ClassVar[bool] = False
```

`reads_desktop_content` is the only switch; everything below keys on it. `cacheable = False` is belt-and-braces — `AgentRunner._execute_one` already excludes a marked action from the in-run cache — and the contract test requires it so the intent survives a refactor of the runner.

### Call order inside `execute()`

1. `require_consent()` — **first, before any argument validation**, so a missing grant refuses identically however the model called the tool. Returns `None` when the grant exists, otherwise `ActionResult(success=False, output="Tool requires 'desktop content' consent. Open /consent to grant it.")`. Assign it, test for `None`, and return it unchanged when it is not — never `return require_consent()`, which returns `None` on a consented machine.
2. Read from the OS.
3. `refuse_if_sensitive(source_app)` — return its result when not `None`.
4. `DesktopContent(text, source_app, kind).to_prompt_block()` — put **only** that string into `ActionResult.output`.

Steps 1, 3 and 4 live in `tokenpal/desktop/content.py`; step 2 is yours. `Category.DESKTOP_CONTENT` is `"desktop_content"`; `/consent` lists and revokes it like any other category.

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

Three the defaults already satisfy, so the test only guards against a regression: `safe`/`requires_confirm`, absence from `M3_CATALOG` and every `M1_RULES` `tool_name`/`extra_tool_names` (both ambient paths build from hand-written allowlists that consult no marker, and both feed the persisted observation path), and absence from `DEFAULT_TOOLS` (a marked tool is opt-in via `[tools] enabled_tools`, never on by default). The test also refuses a marked tool that reaches any network client, or that hand-builds the envelope string.

Each tool must write for itself, because a generic test cannot supply valid arguments or fake an OS read:

- a sensitive-source refusal test that stubs the OS read to report a sensitive app and asserts the refusal;
- an `assert_no_leak(fixture, lines=..., caplog_text=..., memory=...)` sweep (`tests/_helpers.py`) over its own path, proving a fixture string reaches no persisted trace line, no log output, and neither `chat_log` nor `conversation_summaries`.
