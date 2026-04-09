# TokenPal Next Feature Batch — Master Plan

## Context
TokenPal has a working prototype: 3 senses, console overlay, gemma3:4b via Ollama. The buddy makes quips but they're repetitive, structurally monotonous, and the model sometimes just states facts. Five persona agents analyzed the next steps. This plan consolidates their recommendations.

---

## Priority 1: Personality Overhaul (Biggest Impact, Lowest Effort)

All five personas agree: the buddy's personality is the #1 thing to fix before adding features.

### 1a. Rotating few-shot examples (~30 min)
- Pool of 20+ examples across different structures (questions, fragments, dramatic, callbacks, fake concern)
- `PersonalityEngine` randomly samples 5-7 per `build_prompt()` call
- **Source:** Personality Writer section 6, with 20 examples in section 12

### 1b. Comment history deque (~20 min)
- `_recent_comments: deque(maxlen=5)` in PersonalityEngine
- Include in prompt: "Your recent comments (DON'T repeat these or use the same structure): ..."
- **Source:** Personality Writer section 8, Engineer section 6

### 1c. Structure hints (~15 min)
- Rotate a style directive each call: "Respond as a question" / "dramatic narration" / "short (3-5 words)"
- Breaks the "App at Time. Snark." pattern without relying on the model
- **Source:** Personality Writer section 1

### 1d. Rewrite persona prompt (~15 min)
- Character: "TokenPal, a tired, sarcastic ASCII gremlin who lives in a terminal. Night-shift security guard energy."
- 3 rules max (gemma3:4b ignores later ones)
- Reduce max_tokens from 60 to 40
- Add negative examples: "DON'T say: 'Ghostty is open.' (boring)"
- **Source:** Personality Writer sections 7, 11

### 1e. Easter eggs (~45 min)
- Hardcoded special cases that bypass LLM: 3:33 AM, Friday 5 PM, Zoom/Teams, Calculator, milestones
- Guaranteed quality for special moments
- **Source:** Personality Writer section 9, End User

---

## Priority 2: New Senses — Idle + Clipboard (Highest Value, Low Effort)

Every persona ranked these as the top two new senses.

### 2a. Idle detection (~2 hours)
- Track last input via `pynput` listeners (mouse + keyboard)
- Only emit on transitions: active→idle, idle→active
- Return-from-idle is the comedy gold moment: "Oh, you're back."
- Reference what user was doing before leaving: "You walked away mid-compile. Brave."
- Idle states: short (2-5 min, no comment), medium (5-15 min, one dry ack), long (30+ min, dramatic)
- **Source:** End User section on idle, Engineer implementation sketch

### 2b. Clipboard sensing (~3 hours, opt-in)
- Poll via `pyperclip`, hash-compare for changes
- Classify shape only: URL, code block, error message, short snippet — NEVER leak content
- Privacy: explicit opt-in, never echo clipboard text
- "User copied an error message (342 chars)" → LLM quips about it
- **Source:** End User (#1 entertainment sense), Engineer implementation sketch

---

## Priority 3: Brain Improvements

### 3a. Interestingness scoring overhaul
- Current: line-diff ratio (broken by time sense constantly changing)
- Fix: add `interest_weight` to SenseReading, time-decay old readings, boredom bonus after long silence
- **Source:** Engineer section 4

### 3b. Silence as default
- Target ratio: 60% silence, 30% reactive, 10% unsolicited
- Max 4-5 comments per 5-minute window
- Mandatory cool-off after 3 consecutive snarky comments
- Quiet mode during idle (no comments into the void)
- **Source:** End User frequency table, Personality Writer section 4

### 3c. Per-sense polling intervals
- Currently all senses poll at brain interval (2s). Should be per-sense:
  - Clipboard: 1s
  - App awareness: 2s
  - Hardware: 10s
  - Time: 30s
- **Source:** Engineer section 3 (Debt #1)

---

## Priority 4: UI Polish

### 4a. Bottom-anchor layout
- Buddy sits at fixed offset from terminal bottom, not floating at top
- Fill unused space above with empty lines
- Prevents jump when bubble appears/disappears
- **Source:** UX Designer section 1

### 4b. Typing effect for speech bubble
- Characters appear one-by-one (or word-by-word) using `schedule_callback` with staggered delays
- Makes comments feel delivered, not slapped on screen
- **Source:** UX Designer section 2

### 4c. Status bar
- Replace "Ctrl+C to quit" with: `[mood] | senses: 3 active | last quip: 12s ago | Ctrl+C`
- **Source:** UX Designer section 5

### 4d. Fix ASCII art symmetry
- Current: `│ o o│` has missing trailing space
- Fix to 7-wide head, consistent spacing
- **Source:** UX Designer section 7

---

## Priority 5: Mood System (~2 hours)

### Moods (all personas agreed on this)
| Mood | Trigger | Tone |
|---|---|---|
| Snarky (default) | Normal activity | Classic TokenPal |
| Bored | Same app 30+ min | Shorter, more deadpan |
| Impressed (rare) | 2+ hours productive, no distractions | Grudging respect |
| Concerned | 2 AM+, long sessions | Fake parental worry |
| Hyper | Rapid switching, lots of clipboard | Caffeinated |
| Sleepy | Early morning, low activity | Mumbling |

- Track as state in PersonalityEngine
- Include in prompt: "Your current mood: BORED"
- Gradual transitions, not random flips
- Visible in ASCII art (different expressions)
- **Source:** Personality Writer section 2, End User section on moods

---

## Priority 6: Session Memory (Killer Feature, Medium Effort)

End User called this THE killer feature. Engineer designed the architecture.

- SQLite-based: `observations` + `daily_summaries` tables
- Track: app usage patterns, recurring behaviors, tallies
- Feed into prompt as "Session notes": "Chrome opened 4 times, no commits yet"
- Running gags: "Chrome visit #5 today. I'm keeping score."
- Cross-session callbacks: "You started every Monday this month on Twitter."
- 500-token budget within context window
- Never store clipboard content or URLs
- **Source:** End User (killer feature), Engineer section 6, Personality Writer section 3

---

## Priority 7: ML Backend Improvements

### 7a. Graceful degradation + canned quips
- Fallback tiers: Full → Chat-only → Degraded model → Canned quips → Silent
- `data/canned_quips.json` with ~100 quips keyed by signals
- **Source:** ML Expert section 11

### 7b. MLX backend (macOS)
- ~100ms model load vs 13s Ollama cold start
- Memory-mapped weights, multi-model coexistence
- Default on macOS once implemented
- **Source:** ML Expert section 6

### 7c. Music detection (~6 hours)
- macOS: `osascript` for Music.app/Spotify
- Windows: WinRT SMTC
- Three platform impls needed — most cross-platform work of any sense
- **Source:** Engineer priority matrix, End User ranked #4

---

## What to Skip

All personas agreed:
- **Voice/STT**: Too complex, too creepy, marginal comedy payoff
- **Full OCR**: Overkill for quip generation
- **Web search**: Defer to batch 3+
- **Vision**: Defer until MLX/ONNX backends exist

---

## Open Questions

1. **Clipboard opt-in UX**: Config flag? First-run prompt? (End User says explicit opt-in, Engineer says config flag)
2. **Session memory privacy**: What's safe to persist? (End User says no URLs/paths, Engineer says SQLite with easy delete)
3. **Model alternatives**: Stay with gemma3:4b or try llama3.2:3b? (ML Expert says keep gemma3, add llama3.2 as fallback)
4. **Mood visibility**: Should mood show in the status bar or just in the ASCII art? (UX says both)

---

## Implementation Order

| Phase | What | Effort | Impact |
|---|---|---|---|
| **Now** | Personality overhaul (1a-1e) | ~2 hours | Fixes the core experience |
| **Next** | Idle + Clipboard senses (2a-2b) | ~5 hours | Best new content sources |
| **Then** | Brain improvements (3a-3c) | ~3 hours | Better timing and silence |
| **Polish** | UI fixes (4a-4d) | ~3 hours | Feels professional |
| **Character** | Mood system (5) | ~2 hours | Makes buddy feel alive |
| **Memory** | Session memory (6) | ~4 hours | The killer feature |
| **ML** | Graceful degradation + MLX (7a-7b) | ~6 hours | Reliability + performance |
