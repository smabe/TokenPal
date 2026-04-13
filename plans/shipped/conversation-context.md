# Multi-turn conversation context

## Goal
Give TokenPal memory within a conversation session so it can follow up, reference what it just said, and have real back-and-forth exchanges instead of treating every user message as a cold start.

## Non-goals
- Persistent cross-session conversation memory (that's a separate feature on top of MemoryStore)
- Changing the observation or freeform prompt paths — only the conversation path gets multi-turn
- Adding a new LLM backend method — `generate_with_tools()` already accepts a messages array, reuse it
- Streaming responses
- Changing the overlay/UI layer

## Files to touch
- `tokenpal/brain/orchestrator.py` — add conversation session state (history buffer, timeout tracking), modify `_handle_user_input()` to accumulate turns and pass messages array
- `tokenpal/brain/personality.py` — add `build_conversation_messages()` that returns a `list[dict]` (system + history + user) instead of a single string; keep `build_conversation_prompt()` as fallback for non-conversation-mode callers
- `tokenpal/llm/http_backend.py` — possibly add a `generate_chat()` convenience method, OR just reuse `generate_with_tools(messages, tools=[])` from orchestrator (no tools during conversation)
- `tests/` — unit tests for conversation session lifecycle and message building

## Failure modes to anticipate
- Token budget blowout: long conversations fill the context window. Need a hard cap on history length (turns, not tokens — simpler and good enough for local LLMs with 4-8k context)
- Conversation timeout race: user types, then observation fires during the "still in conversation" window → weird interleaving. Need to suppress observations during active conversation
- Stale screen context: if conversation lasts 5 min, the screen context from turn 1 is irrelevant. Should refresh context each turn, not freeze it
- Freeform collision: freeform thought triggers while user is mid-conversation → confusing. Suppress freeform during active session
- Thread safety: `submit_user_input()` is called from main thread, conversation state lives in async brain thread. Current Queue pattern is fine, but session state must only be touched from the async side
- Empty/filtered responses breaking history: if `filter_conversation_response()` returns None, we shouldn't append a phantom assistant turn
- Fine-tuned model path: fine-tuned models use a different template — need to handle the messages path for both
- Tool-calling during conversation: current `_generate_with_tools()` already builds a messages array internally — need to merge conversation history into that flow cleanly

## Done criteria
- User can have a 3+ turn conversation where TokenPal references prior exchanges
- Conversation session auto-expires after ~2 min of silence, returning to observation mode
- Observation and freeform comments are suppressed during an active conversation
- Screen context refreshes each turn (not frozen at conversation start)
- History is capped at a configurable max (default 10 turns = 20 messages)
- Fine-tuned and base model paths both work
- Tool-calling still works during conversation
- Tests pass for: session start/timeout, message building, history cap, observation suppression

## Parking lot
- Future: when bigger models land (5090, Claude API), the history cap should scale with available context. Design the cap as a config value so it's trivial to bump later.
