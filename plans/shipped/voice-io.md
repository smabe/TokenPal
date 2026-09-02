# Optional voice I/O (Kokoro TTS + whisper.cpp + Silero VAD)

## Context

The buddy is text only. A local-first voice loop ("hey buddy, what's up?" + spoken reply) is one of the highest-leverage UX changes surfaced by the agent research pass, and the stack is mature enough to ship without cloud dependencies: Kokoro-TTS-82M runs in realtime on CPU at under 300MB, whisper.cpp handles ASR with minimal latency, Silero VAD + openWakeWord keep the mic fully gated until the user says a wake phrase.

All components run local, opt-in, default off. Zero egress.

## Approach

New `/voice-io` slash command and `[audio]` config section. When enabled:

1. Kokoro wraps the brain's text output (observation + conversation + freeform) through a local TTS pipeline
2. openWakeWord listens for a user-configured phrase ("hey buddy" default) via a lightweight pynput-equivalent mic stream
3. On wake: Silero VAD gates audio, whisper.cpp transcribes, result is fed through `brain.submit_user_input()` (same path as typed input)
4. Active-call detection (meeting sense, once shipped) suppresses wake listening to avoid double-duty

Audio pipeline runs in its own daemon thread; output speaking is queued so the buddy finishes before listening resumes.

## Files

New:
- `tokenpal/audio/__init__.py` - module marker
- `tokenpal/audio/tts.py` - Kokoro wrapper, simple speak(text) -> coroutine
- `tokenpal/audio/asr.py` - whisper.cpp via whisper-cpp-python binding
- `tokenpal/audio/vad.py` - Silero VAD gate
- `tokenpal/audio/wakeword.py` - openWakeWord listener with cancellable stream
- `tokenpal/audio/pipeline.py` - orchestrates wake -> VAD -> ASR -> submit_user_input
- `scripts/install-audio-models.sh` and `.ps1` - download Kokoro + whisper-small + openWakeWord bundle
- `tests/test_audio/` - pipeline tests with mocked audio backends

Modify:
- `tokenpal/config/schema.py` - add `AudioConfig` (tts_voice, wakeword_phrase, enabled)
- `tokenpal/brain/orchestrator.py` - speak-out hook after observation emission and conversation reply
- `config.default.toml` - `[audio] enabled = false` + notes
- `CLAUDE.md` - new section "Voice I/O" describing behavior + gates
- New slash command `/voice-io [on|off|test]` in `tokenpal/app.py`

Reuse:
- Daemon thread pattern from `tokenpal/senses/_keyboard_bus.py`
- submit_user_input flow (already thread-safe via asyncio.Queue)
- sensitive-app filter before speaking output
- Consent category: add AUDIO_IO to tokenpal/config/consent.py

## Phases

1. Model downloader script + hash verification in `~/.tokenpal/audio/`
2. TTS only: /voice-io say "hello" returns spoken audio via Kokoro
3. Wire speak-out into brain loop after filter_response (suppress on sensitive-app-active)
4. Wakeword listener standalone: prints "wake" on hotword
5. VAD + ASR pipeline: /voice-io test records 5s and transcribes
6. Full loop: wake -> listen -> transcribe -> submit_user_input
7. Meeting-aware gating (depends on meeting sense)

## Verification

- `tokenpal --validate` detects missing audio models and tells user to run installer
- Consent category `audio_io` required before `/voice-io on` activates any mic code
- Unit test: when sensitive-app is active, speak() is a no-op, mic stream is closed
- Manual smoke: wake phrase triggers within 500ms, transcription completes within 2s for 10s speech, buddy speaks reply within 1s of receiving the text
- Mic listener exits cleanly on /voice-io off (no leaked pyaudio stream)

## Done criteria

User can install models, toggle /voice-io on, say "hey buddy what time is it", hear the buddy answer, and toggle /voice-io off without leaking a mic stream. Fully local; no network calls made during the voice path; sensitive-app gating verified.
