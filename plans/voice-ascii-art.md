# Voice-Specific ASCII Art

## Goal
Generate unique Rich-markup buddy art per trained voice using the LLM during `/voice train`. Each voice gets 3 custom frames (idle, idle_alt, talking) with per-character colors stored in the voice profile. The buddy displays voice-specific art with idle blink animation and mood-based offset effects.

## Non-goals
- Image-to-ASCII conversion pipeline — no new dependencies, LLM generates the art
- More than 3 frames per voice — no walking, waving, etc.
- Per-voice color themes for the whole UI — only the buddy art gets custom colors
- Changing the generic fallback buddy art — it stays for default/no-voice mode

## Files to touch
- `tokenpal/tools/train_voice.py` — add `_generate_ascii_art()` LLM call with Rich markup prompt, wire into `_generate_voice_assets()`
- `tokenpal/tools/voice_profile.py` — add `ascii_idle`, `ascii_idle_alt`, `ascii_talking` fields (list[str] of Rich markup lines) to VoiceProfile
- `tokenpal/ui/ascii_renderer.py` — add `BuddyFrame.from_voice()` that returns Rich-markup frames, update `BuddyFrame` to carry markup flag
- `tokenpal/ui/textual_overlay.py` — BuddyWidget renders Rich markup when available, idle blink animation (set_interval 3-5s alternating idle/idle_alt), mood offset animations (hyper bounces, sleepy sags)
- `tokenpal/brain/personality.py` — expose voice art frames via personality engine so UI can access them on voice change
- `tokenpal/app.py` — wire voice art into overlay on startup and voice switch
- `tests/test_tools/test_voice_ascii.py` — **new file**, test art generation, frame loading, markup validation

## Failure modes to anticipate
- LLM generates art with wrong dimensions (not 8 lines, inconsistent widths) — need validation/padding
- LLM includes markdown fences or preamble around the art — need stripping
- Rich markup tags broken or unbalanced — need validation, fall back to plain text
- Art contains characters that break terminal alignment (wide Unicode, tabs) — need sanitization
- Existing voice profiles missing the new fields — backward compat, fall back to generic frames
- `/voice regenerate` needs to also regenerate art — wire into the regenerate flow
- Mood offset animation conflicting with speech bubble show/hide transitions
- idle_alt too similar or too different from idle — prompt engineering to get a subtle variant (blink/shift)

## Done criteria
- `/voice train` generates 3 Rich-markup frames (idle, idle_alt, talking) stored in voice profile
- Buddy displays voice-specific colored art when a voice is active
- Idle blink animation cycles idle↔idle_alt every 3-5s
- Mood offset: hyper bounces (small y offset oscillation), sleepy sags (slight downward offset)
- Switching voices (`/voice switch`) swaps the art and resets animation
- `/voice off` reverts to generic buddy with no animation
- `/voice regenerate` regenerates art
- Existing profiles without art fields gracefully fall back to generic buddy
- Tests pass, lint clean

## Parking lot

