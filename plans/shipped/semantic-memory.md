# Persistent semantic memory via local vector DB

## Context

The buddy forgets every session. MemoryStore (SQLite) persists observations and session facts, but there is no semantic retrieval layer, so yesterday's "I'm wiring up the auth service" is gone when today's conversation starts. The mem0 / Letta pattern (local vector DB + tiered memory + retrieval into the prompt) solves this without phoning home, and a sub-400MB embedder (Nomic-Embed-Text-v2) runs on CPU fast enough to stay out of the brain loop's critical path.

Goal: when the user says "what did we talk about last week" or when a keyword in the current conversation matches a past observation, the buddy surfaces it naturally in character. No raw content ever written to the DB unless it passes `contains_sensitive_term`.

## Approach

Add a semantic layer on top of the existing MemoryStore:

1. `tokenpal/memory/embedder.py` wraps Nomic-Embed-Text-v2 (via sentence-transformers, CPU-only)
2. `tokenpal/memory/vector_store.py` wraps Chroma persistent client at `~/.tokenpal/semantic.chroma/` with 0o600 dir perms
3. Writes gated by `contains_sensitive_term` - drop the whole entry if any term matches, never partial redaction
4. Retrieval called from `brain/context.py` alongside existing context builder: top-3 semantic matches injected as `memory` field in observation prompts
5. Conversation prompt path also consults the store: on new user input, embed query, retrieve past-session snippets, inject as "you mentioned last week..." context

Write triggers: at end of each conversation turn (on session close or every N turns), batch-embed the user + buddy lines. Observation comments embed once the buddy emits. Never embed raw sense summaries that contain redacted-looking placeholders.

## Files

New:
- `tokenpal/memory/__init__.py`
- `tokenpal/memory/embedder.py` - lazy-init Nomic wrapper, encode(texts) -> np.ndarray
- `tokenpal/memory/vector_store.py` - add, query, forget_session, compact
- `tokenpal/memory/semantic.py` - SemanticMemory facade, MemoryStore integration
- `tests/test_memory/test_semantic.py` - sensitive-term gate, round-trip, retrieval ordering, purge

Modify:
- `tokenpal/config/schema.py` - add `MemoryConfig.semantic: bool = False`, `embedding_model`, `top_k`, `db_path`
- `tokenpal/brain/context.py` - new hook `collect_memory_context(query_text)` returns injected block
- `tokenpal/brain/orchestrator.py` - on conversation turn close, push transcript to SemanticMemory.record; on new observation prompt build, query semantic store
- `tokenpal/brain/personality.py` - prompt templates gain optional `{memory_context}` slot (empty string when feature off)
- `config.default.toml` - `[memory] semantic = false` with notes
- `CLAUDE.md` - Brain section update
- `scripts/install-semantic-memory.sh` and `.ps1` - download Nomic-Embed weights + install chromadb + sentence-transformers

Reuse:
- `tokenpal.util.text_guards.contains_sensitive_term` for the write gate
- `tokenpal.brain.memory.MemoryStore` session-id + timestamp scheme (foreign key into semantic store)
- Existing chmod 0o600 pattern from consent + memory.db

## Phases

1. Embedder wrapper + smoke test against fixed strings
2. Vector store CRUD + session-scoped purge
3. Sensitive-term write gate + tests (never writes "therapy", "bank", etc.)
4. SemanticMemory facade glued onto MemoryStore
5. Context builder integration: observation prompts get memory_context block
6. Conversation integration: user input triggers retrieval, injected as system note
7. `/memory forget` slash command (session, day, all)

## Verification

- `tokenpal --validate` checks for model weights and chromadb install
- Unit: write-gate drops any entry containing a sensitive term; test covers 5+ terms
- Unit: retrieval ordering - closer vectors rank first
- Integration: after a fake "yesterday" session recorded, same-topic query pulls the relevant line into the prompt block
- Manual smoke: have a 5-minute conversation about topic X on day 1, restart buddy, ask about topic X on day 2, observe callback in reply
- Performance: embedding a 100-word turn completes in under 300ms on CPU

## Done criteria

semantic = true persists across restarts, retrieved snippets actually land in prompts (verify via `/verbose` prompt dump), sensitive-term gate has unit coverage, `/memory forget all` zeros the store and reissuing the same query returns empty. ChromaDB files at 0o600 perms. No network calls during any memory operation.
