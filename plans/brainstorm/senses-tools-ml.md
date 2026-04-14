# Brainstorm: New Senses, Slash Commands, and Agent Tools — ML Lens

## Framing

TokenPal's quip budget is ~500ms and ~4k context on gemma4. The core ML question is *where does LLM reasoning add value vs. where is deterministic code + context injection strictly better?* The current Ollama + gemma4 tool-call bug (2026) pushes us hard toward bucket 1 and 2, and motivates a **two-model architecture**: gemma4 as the quip-writer, functiongemma-270M as a tool dispatcher. Memory cost is ~180 MB (Q4) for the 270M — trivial compared to the 2.5 GB chat model — and it sidesteps the streaming/tool-parser bug by running tool classification as a separate non-streaming call.

---

## Bucket 1 — Passive Senses (context injection, no LLM decisions)

These are cheap, deterministic, and add 1-3 lines to the prompt. The LLM only riffs.

1. **calendar** — read next event from local Calendar.app / ICS. Emits "3pm standup in 20min". No ML. ~25 tokens. Sensitive-app-style redaction for event titles matching banking/health regex. **Pure code wins.**

2. **network_state** — ssid + vpn + latency-to-1.1.1.1. Transitions only ("switched to Starbucks wifi"). ~15 tokens. **Pure code.**

3. **battery_thermal** — psutil battery pct, discharge rate, thermal throttle flags. Already partially in hardware; split out for transition events ("on battery, 18% left, you're doomed"). ~20 tokens. **Pure code.**

4. **focus_mode** — macOS DND / Focus status via `defaults read`, Windows Focus Assist. Gates commentary pacing (silence during Do-Not-Disturb). ~10 tokens. **Pure code, high value as a gate.**

5. **world_awareness (HN/GitHub trending)** — already planned. Embed title+1-line summary. ~40 tokens per reading. TTL 30min. **Pure code; LLM just riffs.**

6. **screen_ambient (Moondream2 cached)** — VLM produces a *coarse* scene caption every 60-120s when app changes. Caption ("dark IDE with many red squiggles") feeds chat prompt as text. **This needs a specialist model** (Moondream2 @ 1.2 GB) but the chat LLM never sees pixels. Latency: offloaded to a 60s poll, not in the quip path. **Small specialist wins decisively over 4B VLM.**

7. **ocr_badge (Florence-2 on NPU)** — narrow OCR: read unread-count badges + notification text from a small screen region. ~100ms on Intel NPU, ~15 tokens injected ("47 unread in Slack"). Strictly deterministic — regex the OCR output. **Specialist model wins.**

8. **ambient_audio_class (YAMNet/PANNs-tiny)** — classify mic audio into ~500 labels (typing, music, speech, silence) **without transcribing**. 5MB model, <50ms, zero privacy leakage (labels only, no text). Emits "speech detected" → buddy shuts up. **High leverage, low risk; specialist model essential.**

9. **typing_cadence** — pynput keystroke velocity + backspace ratio. Derived signal: "fast flow" vs "frustrated" (backspace spike). ~10 tokens. **Pure code, high signal.**

10. **commit_velocity (derived from git sense)** — rolling commits/hour, test-pass-rate if we scrape CI webhook. **Pure code on top of existing git sense.**

---

## Bucket 2 — User-Triggered Slash Commands (deterministic path, LLM does final quip)

The LLM touches these **only at the end** — pure code does the work, buddy reacts in character. Safe on today's Ollama.

11. **/ask `<query>`** — planned. DuckDuckGo/Wikipedia, truncate to 500 chars, feed to `build_search_response_prompt()`. **Value-add-by-LLM: high** (voice-character riff on factual text) but tool choice is deterministic. No tool calling required.

12. **/define, /wiki, /stock, /crypto** — cheap keyless APIs, fixed endpoint per command. LLM just quips. ~200 tokens of result, truncated. **Pure code dispatch.**

13. **/summarize-clipboard** — *rejected permanently* (no clipboard monitoring, per memory). Do not build.

14. **/explain `<file:line>`** — read local code, feed N lines to LLM, quip in character. 4B handles short snippets (~50 lines) reliably; longer code causes hallucination. Cap at 1k tokens input.

15. **/diary** — dump today's MemoryStore aggregate (top apps, duration, switches) to LLM for a one-paragraph EOD summary. Pure retrieval + one LLM call. **LLM value is high** — natural-language synthesis of structured data is what 4B models are good at.

16. **/dictate (whisper-base push-to-talk)** — STT into the input field. Whisper is the specialist; chat LLM receives text. 80 MB model, 500ms on 5s clip. **Specialist model wins; chat LLM blind to audio.**

---

## Bucket 3 — Agent Tools the LLM Calls Itself (BLOCKED on Ollama today)

Tool calling requires the LLM to *decide* when to invoke. Blocked until we move to vLLM or llama.cpp, or adopt the two-model dispatcher.

17. **search(query)** — same backend as /ask, but LLM invokes it mid-conversation. Risk: 4B gemma4 over-calls tools ("let me search that!" for trivial questions). Needs strict system-prompt gating or a dispatcher model.

18. **get_calendar(range)**, **get_weather(loc)**, **read_file(path)** — standard companion tools. 4B can handle these with a tight schema. Hallucination risk: inventing file paths. Mitigation: tool returns error for bad paths, LLM learns in one turn.

19. **set_reminder(when, text)** — writes to a local store. LLM picks time from natural language ("remind me in 20min"). 4B is *borderline* on time parsing — better to do rule-based date parsing in code, have LLM just fill slots.

20. **memory_recall(topic)** — RAG over MemoryStore. **This is where embeddings earn their keep** (see below).

### The Two-Model Dispatcher Architecture

**Proposal:** run functiongemma-270M (or a custom 270M fine-tune) as a dispatcher. On each user turn:

1. Dispatcher classifies → `{tool: "search" | "none", args: {...}}` in ~50ms.
2. If tool, execute deterministically, inject result into gemma4's context.
3. gemma4 streams quip normally — never sees the `tools` param, so no parser bug.

**Cost:** ~180 MB extra RAM, ~50ms added latency on turns that trigger tools. **Benefit:** unblocks bucket 3 *today* on Ollama, and gives us a reliable dispatcher that a 4B generalist can't match for structured extraction. Dispatcher can be upgraded to a LoRA fine-tune on our actual tool schema — training pipeline already exists.

---

## RAG / Embeddings Angle

A small embedding model (e.g. `all-MiniLM-L6-v2`, 22MB, 384-dim) enables:

- **Semantic memory_recall** — "didn't we talk about this last Tuesday?" Query embedding against a SQLite-vec index of past conversation turns + observations.
- **Cross-session callbacks with meaning** — current callback system is rule-based (day-of-week, first-app). Embeddings add "you always open this app when you open that one" fuzzy patterns.
- **Duplicate-quip detection** — embed last N quips, cosine-reject new ones too similar. Kills the "already said that" problem more robustly than string matching.

Cost: 22 MB model, <20ms per embed, SQLite-vec for storage. **This is the highest-leverage specialist model after vision** — runs cheap, persistent, improves three existing systems.

---

## Ranked Top 5 — "Value Added by LLM vs Pure Code"

Ranked by how much the LLM's generative reasoning genuinely beats a coded alternative:

1. **/diary EOD synthesis** — structured-data → natural-language paragraph is pure LLM win. No code equivalent gives warmth/voice.
2. **/ask web search riff** — factual text → in-character quip. Deterministic code can't do voice.
3. **screen_ambient (Moondream2 caption + gemma4 riff)** — specialist VLM produces text, chat LLM contextualizes it to user's activity. Neither model alone is enough.
4. **memory_recall via embeddings + LLM** — embeddings retrieve, LLM weaves callback into conversation naturally. Rule-based callbacks already exist but feel robotic.
5. **Two-model tool dispatcher (functiongemma-270M + gemma4)** — unlocks bucket 3, architecturally the biggest unblock. Dispatcher is near-deterministic; gemma4 stays at what it's good at (voice).

**Everything else is better as pure code + passive injection.** Calendar, network, battery, focus, typing cadence, HN headlines — code wins, LLM just flavors the output. That's the correct division of labor for a 4B local model: it's a voice, not a brain.

## Parking Lot
- Could a fine-tuned 270M dispatcher be trained from gemma4's *own* synthesized tool-use traces? Self-distillation pipeline.
- Ambient audio class + typing cadence → composite "deep focus" mood that forces silence. Needs composite detector in ContextWindowBuilder.
- sqlite-vec vs faiss for embedding store — sqlite-vec is simpler, already using SQLite.
