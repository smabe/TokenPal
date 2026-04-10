# Future Features Plan [ALL SHIPPED]

## Context

TokenPal has a solid foundation: console overlay, 4 senses, personality engine with voice training, session memory, and a setup script. These 4 features are the next wave — making the buddy interactive, capable, easier to set up, and properly documented.

---

## Feature 1: Text Input (Large)

Let users type messages to the buddy in the console. He responds conversationally.

### Key changes

**`tokenpal/ui/input_buffer.py`** (new) — Character-by-character input state: buffer string, cursor, render method. Isolated for testability.

**`tokenpal/ui/console_overlay.py`** — Major changes:
- Switch terminal to cbreak mode (`tty.setcbreak`) on startup, restore on teardown
- Non-blocking stdin reads via `select.select([sys.stdin], [], [], 0)` each loop iteration — keeps typing animation and callbacks responsive
- Render input buffer at bottom of screen: `> your text here_`
- On Enter → call `_on_user_message` callback, clear buffer
- On Escape → cancel input mode
- Show "listening" buddy frame when input active
- Windows: use `msvcrt.kbhit()` / `msvcrt.getwch()`

**`tokenpal/ui/base.py`** — Add `set_message_callback()` to AbstractOverlay (default no-op)

**`tokenpal/brain/orchestrator.py`** — Add `async handle_user_message(text)`:
- Bypasses `_should_comment()` entirely (no cooldown, no interestingness gate)
- Uses `asyncio.Queue` for thread-safe message passing from UI → brain
- Calls `build_conversational_prompt()` instead of `build_prompt()`
- Uses higher `max_tokens` (512) for longer responses
- Thread boundary: `asyncio.get_event_loop().call_soon_threadsafe()` to enqueue

**`tokenpal/brain/personality.py`** — Add:
- `build_conversational_prompt(user_message, context_snapshot, memory_lines)` — includes user message as a turn, allows multi-sentence responses, no [SILENT] instruction
- `filter_conversational_response(text)` — relaxed filter: up to 200 chars, multiple sentences, still strips artifacts

**`tokenpal/app.py`** — Wire message callback from overlay to brain

### Threading model
```
Main Thread (UI)          Brain Thread (asyncio)
  └─ stdin char read        └─ asyncio.Queue.get()
  └─ InputBuffer.append()   └─ handle_user_message()
  └─ on Enter:              └─ build_conversational_prompt()
     call_soon_threadsafe()  └─ llm.generate(max_tokens=512)
     → queue.put(text)       └─ ui_callback(response)
```

---

## Feature 2: Tool Use (Large)

Let the buddy perform actions — open apps, set timers, get system info. Works autonomously or via text input.

### Key changes

**`tokenpal/tools/actions/`** (new package) — Built-in tools:
- `timer.py` — `set_timer(seconds, label)`: asyncio sleep then UI callback
- `open_app.py` — `open_app(name)`: allowlisted app launcher (`open -a` on macOS)
- `system_info.py` — `system_info()`: return CPU/RAM from hardware sense
- `web_search.py` — `web_search(query)`: open browser with query

**`tokenpal/tools/actions/registry.py`** (new) — `@register_tool` decorator, `discover_tools()`, `get_tool_definitions()` (OpenAI format)

**`tokenpal/tools/actions/safety.py`** (new) — Allowlists and `validate_tool_call(name, args) -> bool`

**`tokenpal/llm/base.py`** — Extend:
- Add `ToolCall` dataclass (id, name, arguments)
- Add `tool_calls: list[ToolCall]` to `LLMResponse`
- Add `generate_with_tools(messages, tools, max_tokens)` to AbstractLLMBackend (default fallback to `generate()`)

**`tokenpal/llm/http_backend.py`** — Implement `generate_with_tools()`:
- Pass `tools` in request JSON
- Parse `message.tool_calls` from response
- Graceful fallback if model returns malformed tool calls

**`tokenpal/brain/orchestrator.py`** — Add tool execution loop:
- `_generate_with_tools(messages)` — call LLM, execute tool calls, feed results back, max 3 rounds
- In `handle_user_message()`: include tool definitions
- In `_generate_comment()`: optionally include safe tools for autonomous use

**Note**: gemma3:4b may struggle with tool calling. Implementation must be defensive — try/except on parsing, fall back to plain text. Consider a text-based fallback where tool definitions are in the prompt and we parse `ACTION: tool_name(args)` from the response.

### Depends on
- Soft dependency on Feature 1 (text input). Works autonomously without it.

---

## Feature 3: Voice Management via Text Input (Medium)

Voice management lives inside the running buddy — slash commands for reliability, natural language as fallback via LLM. Depends on Feature 1 (text input).

### Slash commands

```
/voice list                           — show saved voices
/voice switch                         — numbered picker (inline)
/voice switch mordecai                — switch directly by slug
/voice train regularshow "Mordecai"   — train from wiki
/voice delete mordecai                — delete a profile
/voice info                           — show current voice details
/voice off                            — reset to default TokenPal
```

### Natural language (LLM-parsed fallback)

When user types something like "switch to Finn" or "train a voice from Adventure Time", the LLM detects intent and maps to the equivalent slash command. This is a tool use action (ties into Feature 2) — the buddy calls a `voice_manage` tool with the parsed intent.

### Key changes

**`tokenpal/brain/commands.py`** (new) — Slash command router:
- `parse_command(text) -> tuple[str, list[str]] | None` — returns (command, args) if text starts with `/`, else None
- `execute_voice_command(args, voices_dir, config_path) -> str` — handles voice subcommands, returns response text for the buddy to speak
- Runs on the brain thread, results sent to UI via callback

**`tokenpal/brain/orchestrator.py`** — In `handle_user_message()`:
- First check for slash commands via `parse_command(text)`
- If matched: execute directly, return result as speech (no LLM call needed)
- If not matched: send to LLM as conversational prompt (existing flow)
- For natural language voice commands: include `voice_manage` in tool definitions so the LLM can invoke it

**`tokenpal/tools/voice_profile.py`** — Add:
- `delete_profile(slug, voices_dir) -> bool`
- `get_profile_details(slug, voices_dir) -> dict`

**`tokenpal/tools/wiki_fetch.py`** — Add `fetch_sample_transcripts(wiki, sample_size=10) -> str`

**`tokenpal/tools/transcript_parser.py`** — Add `discover_characters(text, min_lines=5) -> list[tuple[str, int]]`

### UX flow example
```
> /voice list
  Saved voices:
  • mordecai (710 lines)
  • finn (423 lines)
  Current: mordecai

> /voice switch finn
  Switched to Finn. Restarting personality...

> hey, can you sound like Bender from Futurama?
  Hold on, training a voice from futurama.fandom.com...
  Found 340 lines for Bender. Generating persona... done!
  I am Bender. Please insert girder.
```

### Standalone CLI preserved
`python -m tokenpal.tools.train_voice` with all existing flags continues to work for scripting / headless use. The slash commands call the same underlying functions.

---

## Feature 4: README Rewrite (Small)

The current README is outdated — references phi3:mini, tkinter overlay, unimplemented backends (MLX, llama.cpp, ONNX), and missing all new features.

### Sections to rewrite

**`README.md`** — Full rewrite:

1. **Header** — "Sarcastic ASCII terminal buddy" not "transparent overlay"
2. **Quick Start** — Lead with `python3 setup_tokenpal.py`, 3 commands total
3. **What It Does** — Console buddy with speech bubbles, typing animation, mood system, activity-aware commentary, session memory, voice training
4. **Voice Training** — New section: `--wiki` example, `--activate`, how profiles work
5. **Senses** — Update table: mark which are implemented vs planned. Remove AI-required column (none of those work yet)
6. **LLM Backends** — Just HTTP (Ollama/LM Studio). Remove MLX/llama.cpp/ONNX rows (not implemented). Add note about gemma3:4b as default model
7. **Architecture diagram** — Keep, but update labels (console overlay not tkinter)
8. **Config example** — Match actual `config.default.toml` (add `active_voice`, correct cooldown values, etc.)
9. **Project structure** — Update to include `tools/` directory
10. **Terminal screenshot** — Code block showing the rendered buddy with speech bubble

### Key fixes
- `phi3:mini` → `gemma3:4b` everywhere
- Remove "transparent overlay" language (it's a console app)
- Remove unimplemented backend/sense references or mark as "planned"
- Add voice training CLI docs
- Point install to `setup_tokenpal.py`

---

## Implementation Order

Feature 4 (README) can be done now — it documents what exists.
Feature 1 (Text input) is the foundation for everything interactive.
Feature 3 (Voice commands) and Feature 2 (Tool use) both depend on text input.
Feature 3 is simpler and ties into Feature 2 (voice_manage as a tool).

Suggested: **4 → 1 → 3 → 2**

## Verification

- **Feature 1**: Type a message in the console, buddy responds conversationally. Typing animation and status bar keep working during input. Test on macOS terminal and Ghostty.
- **Feature 2**: Ask buddy to "open Safari" or "set a 5 minute timer". Verify tool executes and buddy confirms.
- **Feature 3**: Run `python -m tokenpal.tools.train_voice` with no args, walk through menu, train a voice from wiki, switch voices, delete a voice.
- **Feature 4**: Read the README. Clone fresh, follow instructions, verify they work end-to-end.
