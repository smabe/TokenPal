# Add text input and slash commands to TokenPal [SHIPPED]

## Context
TokenPal can observe and comment but can't be talked to. The buddy is one-directional — users have no way to interact. Adding text input lets users type messages to get conversational responses and issue slash commands (`/model`, `/help`, etc.) to control the buddy at runtime.

## Architecture Overview

```
Main thread (run_loop):
  poll stdin (cbreak mode) → input buffer
    ├─ /command → CommandDispatcher → immediate UI response
    └─ free text → asyncio.Queue → Brain thread
                                      └─ build_conversation_prompt()
                                      └─ LLM generate (max_tokens=100)
                                      └─ filter_conversation_response()
                                      └─ schedule_callback → UI
```

## Plan

### 1. Terminal cbreak mode + keystroke capture
**File: `tokenpal/ui/console_overlay.py`**
- `setup()`: save terminal state via `termios.tcgetattr()`, enter cbreak via `tty.setcbreak()` (NOT full raw — keeps Ctrl+C as SIGINT)
- `teardown()`: restore original termios. Also register `atexit` handler as crash safety net
- Add `_poll_input()` to `run_loop()`: use `select.select([sys.stdin], [], [], 0)` for non-blocking read, `sys.stdin.read(1)` per char
- Handle: printable chars → append buffer, Enter → submit, Backspace (0x7f/0x08) → delete, Escape sequences (arrows) → ignore
- New state: `self._input_buffer: str`, `self._input_callback: Callable[[str], None] | None`
- Platform: macOS/Linux only (termios). Add Windows TODO.

### 2. Render input line in overlay
**File: `tokenpal/ui/console_overlay.py`**
- In `_render()`, insert input prompt between bottom border and status bar:
  ```
  ──────────────────
  > typed text here_
    snarky | 3 senses | ...
  ```
- Truncate display if buffer exceeds terminal width
- Cursor shown as `_` at end of input

### 3. Slash command dispatcher
**New file: `tokenpal/commands.py`**
- `CommandResult` dataclass: `message: str`
- `CommandDispatcher`: dict registry, `dispatch(raw_input) → CommandResult | None`
- Built-in commands:
  - `/help` — list commands
  - `/clear` — hide speech bubble
  - `/mood` — show current mood
  - `/status` — show model, senses, actions
  - `/model [name]` — show or swap model

### 4. User message routing (main thread → brain)
**File: `tokenpal/brain/orchestrator.py`**
- Add `self._user_input_queue: asyncio.Queue[str]` to Brain
- Store `self._loop = asyncio.get_running_loop()` in `start()`
- In `_run_loop()`, check queue each iteration with `get_nowait()`
- Add `_handle_user_input(user_message)` — builds conversation prompt, calls LLM, routes response to UI

**File: `tokenpal/app.py`**
- Wire input callback: main thread puts text onto brain's queue via `loop.call_soon_threadsafe(queue.put_nowait, text)`
- Register slash commands with closures over `personality`, `llm`, `senses`, `actions`
- On submit: try dispatcher first, if not a command → route to brain

### 5. Conversational prompt path
**File: `tokenpal/brain/personality.py`**
- `build_conversation_prompt(user_message, context_snapshot)` — different template:
  - Includes user message directly: `User says: "{text}"`
  - Still includes mood + screen context for situational awareness
  - No [SILENT] option — always respond to user
  - Rules: stay in character, 1-2 sentences, under 30 words, respond to what they said
- `filter_conversation_response(text)` — relaxed filter:
  - Allow up to 2 sentences, 150 chars (vs 1 sentence, 70 chars)
  - Min length 5 chars (vs 15) — "No." is a valid reply
  - No [SILENT] check
  - Same cleanup: strip quotes, markdown, asterisks

### 6. Runtime model swap
**File: `tokenpal/llm/http_backend.py`**
- Add `set_model(model_name: str)` — just updates `self._model_name`
- Thread-safe: Python string assignment is atomic under GIL, in-flight request keeps old model, next request uses new one

### 7. Abstract overlay interface
**File: `tokenpal/ui/base.py`**
- Add `set_input_callback(callback)` with default no-op so TkOverlay doesn't break

## Files to modify
- `tokenpal/ui/console_overlay.py` — cbreak mode, input polling, input rendering, submit routing
- `tokenpal/ui/base.py` — optional input callback on interface
- `tokenpal/commands.py` — new, slash command dispatcher + built-in commands
- `tokenpal/brain/orchestrator.py` — asyncio.Queue, _handle_user_input, loop exposure
- `tokenpal/brain/personality.py` — conversation prompt + relaxed filter
- `tokenpal/llm/http_backend.py` — set_model()
- `tokenpal/app.py` — wire everything: input callback, command dispatcher, queue bridge

## Verification
1. Type text while buddy is running → input line renders at bottom, characters appear as typed
2. Backspace deletes, Enter submits, buffer clears after submit
3. Type a free-text message → buddy responds conversationally (not observation-style)
4. `/help` → lists commands in speech bubble
5. `/model` → shows current model
6. `/model gemma3:1b` → swaps model, next response uses new model
7. `/mood` → shows current mood
8. `/clear` → hides speech bubble
9. Unknown `/foo` → "Unknown command" message
10. Typing during typing animation works (input and animation are independent)
11. Ctrl+C still exits cleanly, terminal restored to normal
12. Existing observation commentary continues working alongside input
