# Say What — local-first voice I/O for the buddy

## Goal
Add an opt-in **voice conversation mode** modeled on ChatGPT/Claude voice: user says "hey tokenpal", the buddy answers in voice, the mic stays hot through a short trailing window so the user can keep talking without re-waking. Typed conversations stay text-only. Random ambient observations stay text-only by default; speaking them is a separate sub-opt-in that does not require the mic.

The new client-server split is honored: mic + wake + VAD + TTS run on the client (where the mic physically is); ASR is pluggable so a beefier whisper can optionally run on the inference server.

## Non-goals
- No cloud STT/TTS providers. Local-first, zero egress on the audio path.
- No always-on transcription. Mic stays gated until wake word fires (or a session is open).
- No voice cloning / custom-voice training in this plan (Kokoro stock voices only — custom voices live in the existing voice-training pipeline).
- No barge-in / interrupt-while-speaking. Spacebar interrupts (mirrors the be-more-agent pattern) are out of scope for v1.
- No multi-language. English only for v1.
- No speaker diarization. One user, one mic.
- No push-to-talk hotkey in v1.
- **Typed input NEVER produces voice output, even when voice mode is on.** Type-in / type-out and voice-in / voice-out are kept symmetric.
- **Ambient observation bubbles do NOT speak by default.** Separate sub-toggle, off by default.
- ASR-on-server is a separate optional endpoint, not entangled with `/v1/*` proxy.
- F5-TTS is NOT a candidate for the future trained-voice path (CC-BY-NC weights). XTTS-v2 / Piper are the realistic targets.

## User-facing behavior

**Two fully independent toggles in the options modal (Qt + Textual):**

1. **Voice conversation** (default off). Wake-word + voice replies *to voice input only*. Requires `AUDIO_INPUT` consent (mic) AND `AUDIO_OUTPUT` consent (speakers).
2. **Speak ambient observations** (default off, **independent** of #1). Random observation bubbles get spoken at the same pacing as text bubbles. Requires `AUDIO_OUTPUT` consent only — no mic, no wake word, no portaudio input stream.

The four states are all valid:
- Neither on → today's behavior, zero audio code touched at runtime.
- Ambient only → Kokoro narrates bubbles; mic untouched, no permission prompt for input.
- Voice only → wake word + voice replies; ambient bubbles stay text-only.
- Both → full conversational + narrated.

**Session state machine** (when voice conversation is on):

- **Idle** — wake-word listener active, all other audio silent. Ambient bubbles render as text (only spoken if #2 is on).
- **Wake fires** ("hey tokenpal") — small earcon, switch to listening.
- **Listening** — Silero VAD watches end-of-utterance (0.7s sustained sub-threshold silence). ASR transcribes the clipped utterance. Transcript flows through `submit_user_input(text, source="voice")`. Empty / <2-token results close the session silently (treated as false fire).
- **Speaking** — Kokoro speaks the brain's reply, sentence-streamed (split on `.!?\n`, queued per sentence so first sentence plays while remainder generates). Mic closed.
- **Trailing window** (`trailing_window_s`, default 8s) — mic reopens *without* re-wake. **Hard close at expiry regardless of VAD state** — do not extend on hot VAD (TV / music in the room would otherwise hold it open forever). Speech detected during the window → loop back to listening.
- **Interrupted by sensitive-app or typed input** — session closes immediately, queue drains, mic releases.

**Routing rules:**
- Typed → text-only reply, always.
- Voice (wake-word or trailing-window) → voice reply, always (text bubble in parallel).
- Ambient → text bubble always; spoken only if ambient toggle on.
- Slash commands typed → text only.

## Architecture for pluggability

The audio module is split into **abstractions + registry-pluggable backends**, mirroring the existing `@register_sense` / `@register_action` / `@register_backend` patterns proven at `tokenpal/senses/registry.py:18`, `tokenpal/actions/registry.py:18`, `tokenpal/llm/registry.py:18`. A future custom-trained voice (XTTS-v2, Piper, or whatever the voice-training pipeline outputs) plugs in as a new `@register_tts_backend` without touching the session state machine, routing, or UI toggles.

Concrete ABC shape (informed by Pipecat / RealtimeTTS prior art):

```python
class TTSBackend(ABC):
    sample_rate: ClassVar[int]               # 24000 for Kokoro, 22050 for Piper, etc.
    channels: ClassVar[int] = 1
    sample_format: ClassVar[str] = "int16"   # backends declare their output format

    @abstractmethod
    def list_voices(self) -> list[VoiceInfo]: ...

    @abstractmethod
    async def synthesize(
        self, text: str, voice_id: str, *, speed: float = 1.0
    ) -> AsyncIterator[bytes]: ...           # streaming-first; buffer-first backends yield one chunk

    async def warmup(self) -> None: ...      # lazy-load on first use, not at import
    async def aclose(self) -> None: ...      # release model RAM on toggle-off
```

`ASRBackend` mirrors the shape (declares expected input sample rate; takes audio bytes + sample rate, returns text). `WakeWordBackend` exposes `stream() -> AsyncIterator[WakeEvent]`.

`AudioSink` / `AudioSource` wrap **sounddevice** (not pyaudio — pyaudio is dormant; sounddevice ships PortAudio inside the wheel on macOS/Windows/Linux x86_64, returns numpy directly which Kokoro emits natively). Backends never touch portaudio directly.

`tokenpal/audio/voices.py` aggregates `list_voices()` across all registered TTS backends. The options modal reads from this; voice IDs are namespaced `<backend>:<voice>` (e.g. `kokoro:af_bella`, future `trained:abe-buddy`).

**Custom-voice plug-in path:** drop a new file in `tokenpal/audio/backends/`, declare `@register_tts_backend("trained")`, done. No edits to session/pipeline/orchestrator/modals.

## Files to touch

New (client-side audio):
- `tokenpal/audio/__init__.py` — module marker
- `tokenpal/audio/base.py` — `TTSBackend`, `ASRBackend`, `WakeWordBackend` ABCs + `AudioSink` / `AudioSource` wrappers (sounddevice) + `VoiceInfo` dataclass
- `tokenpal/audio/registry.py` — `@register_tts_backend`, `@register_asr_backend`, `@register_wakeword_backend` + walk-packages discovery
- `tokenpal/audio/voices.py` — voice catalog aggregator
- `tokenpal/audio/backends/__init__.py` — discovery target
- `tokenpal/audio/backends/kokoro.py` — `KokoroBackend` via `kokoro-onnx`, default. Quantization configurable (int8 / fp16 / fp32).
- `tokenpal/audio/backends/whisper_local.py` — `LocalWhisperBackend` (faster-whisper or whisper.cpp via `pywhispercpp` — pin in research before implementing)
- `tokenpal/audio/backends/whisper_remote.py` — `RemoteWhisperBackend` HTTP client to `/v1/audio/transcriptions`
- `tokenpal/audio/backends/openwakeword.py` — `OpenWakeWordBackend` using the shipped `hey_tokenpal.onnx`. Handles `wakeword_model_paths=` vs `wakeword_models=` kwarg drift via TypeError fallback.
- `tokenpal/audio/tts.py` — speak queue, sentence-streaming split (`.!?\n`), drain-on-cancel, source-aware gating
- `tokenpal/audio/asr.py` — backend-by-config facade
- `tokenpal/audio/vad.py` — Silero VAD wrapper (chunk=512 @16kHz, threshold=0.5, min_silence=0.7s, min_speech=0.05s)
- `tokenpal/audio/wakeword.py` — backend-by-config facade. Volume-gates frames (skip prediction when `max < 200`) for CPU savings.
- `tokenpal/audio/session.py` — state machine (idle / listening / speaking / trailing). Hard-closes trailing window at expiry.
- `tokenpal/audio/pipeline.py` — wires wake → VAD → ASR → submit_user_input(source="voice"). Owns daemon thread + cancel event + atexit handler that joins thread with 250ms timeout before closing streams (prevents macOS orange-dot-stuck bug). Uses `loop.call_soon_threadsafe(queue.put_nowait, ...)` on the hot path; reserves `asyncio.run_coroutine_threadsafe` for `--validate` synchronous probes only.
- `tokenpal/data/wakeword/hey_tokenpal.onnx` — trained model committed to the repo (~200KB) plus shared `melspectrogram.onnx` and `embedding_model.onnx` (~2MB total)
- `scripts/install-audio-models.sh` / `.ps1` — fetch Kokoro voices + whisper-small.en into `~/.tokenpal/audio/`. Auto-detect RAM and set Kokoro quantization default (int8 for ≤8GB, fp16 elsewhere). Wakeword model is in-repo, no download.
- `tools/wakeword-training/README.md` — dev-only doc on retraining `hey_tokenpal.onnx` via openWakeWord's Colab notebook (~1 hour on free T4). Not user-facing.
- `tests/test_audio/` — pipeline + session-state-machine + source-routing + backend-registry + modularity-import-blocker tests

New (optional server-side ASR):
- `tokenpal/server/audio.py` — FastAPI route `POST /v1/audio/transcriptions` (OpenAI-compatible, crib from `speaches`). Accepts `file`, `model`, `language`, `prompt`, `response_format`, `temperature`, `timestamp_granularities[]`. Wraps faster-whisper on the GPU box.
- `start-asr.bat` / `start-asr.sh` — launch script for the ASR endpoint (separate process from llama-server so VRAM contention is the user's choice)

Modify:
- `tokenpal/config/schema.py` — `AudioConfig` (voice_conversation_enabled, speak_ambient_enabled, tts_backend, tts_voice, kokoro_quantization, wakeword_backend, wakeword_threshold (default 0.7), trailing_window_s (default 8), asr_backend, asr_server_url, asr_model_size)
- `tokenpal/config/consent.py` — add `AUDIO_INPUT` (mic) and `AUDIO_OUTPUT` (speakers) to `ALL_CATEGORIES` (line 31). Voice conversation requires both; ambient narration requires only OUTPUT.
- `tokenpal/brain/orchestrator.py` — `submit_user_input` (line 1834) gains `source: Literal["typed","voice","ambient"]` (default "typed"); reply path routes to TTS only when `source == "voice"` OR (ambient + speak_ambient enabled). Sensitive-app filter (`filter_response` at `personality.py:1067` plus `is_clean_english` at `text_guards.py:26`) checked right before each `sounddevice.write`, not just at queue time.
- `ConversationSession` (orchestrator.py:90) — add source tracking to `add_user_turn` so wake-initiated multi-turn conversations are coherent across the 120s window.
- `tokenpal/ui/options_modal.py` (Textual) — Voice conversation + Speak ambient toggles, persisted live to `config.toml`, take effect without restart.
- `tokenpal/ui/qt/options_dialog.py` (Qt) — same two toggles, parity with Textual.
- `tokenpal/app.py` — `/voice-io [on|off|test|say <text>]` slash command (mirrors modal toggles for headless / muscle-memory use)
- `tokenpal/server/app.py` (`create_app` at line 73) — when ASR endpoint lands, mount via `app.include_router(audio_router, prefix="/api/v1")` behind a config flag. (`tokenpal/server/__init__.py` only holds `__version__`; the FastAPI factory lives in `app.py`.)
- `tokenpal/cli.py` — extend `_validate()` (line 275) with `_check_audio()`: sounddevice install, models present, on macOS read `TERM_PROGRAM` and tell user to grant mic to the parent terminal binary (not "tokenpal"), AVCaptureDevice `authorizationStatusForMediaType_(AVMediaTypeAudio)` for mic-permission detection.
- `config.default.toml` — `[audio]` block, all defaults off, with comments explaining the two toggles
- `CLAUDE.md` — Voice I/O section under Architecture
- `docs/claude/server.md` — note the optional ASR endpoint and that it's a separate process
- `docs/claude/ui.md` — note the new options-modal toggles
- `docs/claude/brain.md` — note the `source` tag on user input and the routing rules

Reuse:
- Daemon-thread pattern from `tokenpal/senses/_keyboard_bus.py`
- `submit_user_input` queue (asyncio.Queue, thread-safe via `call_soon_threadsafe`) — voice ASR result feeds this same path with `source="voice"`, no new code path through the brain
- `ConversationSession` (orchestrator.py:90) — voice session reuses it so wake-initiated multi-turn is coherent
- `filter_response` + `is_clean_english` for sensitive-app + drift gating
- `--validate` framework (cli.py with `_CHECK`/`_WARN`/`_FAIL` markers)
- be-more-agent patterns: sample-rate negotiator (walk `[device_default, 48000, 44100, 32000, 16000]` until `sd.check_input_settings` passes), sentence-boundary TTS streaming, volume-gated wake prediction, two-tier ALSA `blocksize=0 → 1024` retry, `sd.stop(); time.sleep(0.2)` before opening recording stream

## Failure modes to anticipate
- **Mis-routed reply** (typed turn accidentally speaks). Source tag must propagate end-to-end; unit-test all four routing rules.
- **Sample-rate mismatch → demonic voice** (be-more-agent README documents this exact failure). Backend declares `sample_rate` ClassVar; sink resamples or matches.
- **Trailing-window false trigger** — cough / bark / TV opens fake follow-up. VAD threshold + 250–300ms min-speech-duration before committing to ASR; empty / <2-token transcript closes the session silently.
- **Trailing-window held open by ambient noise** — hard close at `trailing_window_s` regardless of VAD state. Don't extend on hot VAD.
- **Sensitive app mid-utterance** — speech queue drains, mic closes, session ends. Live-check filter right before each `sounddevice.write`.
- **User types during voice session** — typed wins, session closes, speech queue dropped, reply is text-only.
- **macOS orange-dot stuck after Ctrl+C** — atexit / SIGTERM handler must cancel daemon thread and join with 250ms timeout *before* closing streams. Without this, the OS host API doesn't unwind and the dot persists until reboot.
- **`run_coroutine_threadsafe(...).result()` deadlocks** if called from the loop thread itself. Hot path uses `call_soon_threadsafe(queue.put_nowait, ...)` instead.
- **SystemExit raised inside a `run_coroutine_threadsafe`'d coroutine hangs** the calling thread's `.result()` (known bug). Catch SystemExit explicitly in any cross-thread coroutine; never let it propagate.
- **openWakeWord kwarg drift** — `wakeword_model_paths=` vs `wakeword_models=` between versions. Pin a version + handle both via TypeError fallback (be-more-agent `agent.py:249-256`).
- **ALSA mmap overflow on Linux** — needs two-tier `blocksize=0` → `blocksize=1024 + nearest-neighbor resample` retry chain (be-more-agent pattern).
- **Quiet-frame openWakeWord calls waste CPU** — gate predictions on `frame.max() > 200`.
- **sounddevice install pain on Linux ARM / source builds** — needs `portaudio19-dev`. macOS / Windows / x86_64 Linux ship PortAudio inside the wheel (no system dep). Installer detects and explains.
- **Mic-stream leak on toggle-off** — sounddevice `stream.stop(); stream.close()` (no separate terminate needed; `Pa_Terminate` is registered as atexit hook). Daemon thread observes cancel event before closing.
- **Wake-word quality regression after first ship** — synthetic-only training expects 5–15% FRR / 1–3 false-fires/hour at default threshold. If dogfood is bad, scale training (`n_samples=30k`, `steps=50k`) or add a custom-verifier model trained on the user's voice in the first-run wizard.
- **macOS mic permission lives on the parent terminal binary**, not `python3`. `--validate` reads `TERM_PROGRAM` and tells the user "grant mic to Terminal.app / iTerm2 / Cursor / etc." — naming the actual app, not "tokenpal".
- **VRAM contention on the server** — running whisper-large alongside a 14B LLM on the same GPU OOMs. Default `asr_model_size = "small.en"` even on the server path; let the user opt up.
- **Kokoro 82M memory footprint on low-RAM Macs** — installer auto-detects RAM and sets `kokoro_quantization = "int8"` (~80MB on disk / ~200MB resident) on machines with ≤8GB total RAM, `fp16` elsewhere.
- **ASR server unreachable** — `RemoteWhisperBackend` 2s timeout, fall back to local with a one-line log; never block wake pipeline on a dead server.
- **Conversation latency feels broken** — local Qwen 14B + whisper-small + Kokoro could hit several seconds round-trip. Sentence-streaming TTS hides some of this. Measure on each platform; surface as known limitation if total >4s.
- **Modal toggle vs config drift** — flipping must persist to `config.toml` AND take effect live without restart, both Qt and Textual.
- **Toggling ambient on without mic permission** — must work cleanly. Output-only path must not import / instantiate any input-side backend. Verified by import-blocker test (see done criteria).
- **Backend registry not discovered** — voice catalog returns empty if walk-packages fails. `--validate` reports each registered backend and the models each can find.
- **Voice ID collision across backends** — IDs namespaced `<backend>:<voice>` everywhere user-facing AND in config.

## Done criteria

**Phase 1 falsifiable test (lands first commit, gates further work):**
- `test_ambient_only_does_not_open_input` — set voice OFF + ambient ON, install a `sys.meta_path` finder that raises on any import of `pyaudio`, `sounddevice`, `openwakeword`, `tokenpal.audio.backends.openwakeword`, `tokenpal.audio.backends.whisper_*`, `tokenpal.audio.wakeword`, `tokenpal.audio.asr`, `tokenpal.audio.vad`. Boot the audio subsystem, run `await speak("hello", source="ambient")`, assert no input-side modules imported (post-hoc `sys.modules` scan as belt-and-braces).
- **Anti-test**: same scenario but with voice ON must FAIL (input modules now imported). Proves the blocker isn't trivially passing.

**Toggles + consent:**
- Two **fully independent** toggles in BOTH the Qt options dialog and the Textual options modal: "Voice conversation" and "Speak ambient observations". All four combinations (neither / ambient-only / voice-only / both) are valid and exercised by tests.
- Voice conversation toggle on first activation prompts for both `AUDIO_INPUT` + `AUDIO_OUTPUT` consent. Ambient toggle on first activation prompts for `AUDIO_OUTPUT` only.
- Toggle changes persist to `config.toml` and take effect live without restart.
- Default state is voice OFF, ambient OFF — fresh-machine install matches existing behavior exactly until the user opts in.

**Voice loop:**
- "Hey tokenpal" wake fires within 500ms; whisper transcribes within 2s for ≤10s of speech; buddy speaks reply within 1s of receiving brain text. Sentence-streaming TTS makes first audio land within 300ms of brain output.
- Wake-word default threshold 0.7, configurable via `wakeword_threshold` in `[audio]`.
- Trailing window: follow-up within 8s without re-wake; **hard close at 8s regardless of VAD state**; subsequent speech requires re-wake.
- Empty / <2-token ASR result during trailing window closes session silently.
- Three-test trailing-window suite passes:
  1. 5s speech + 12s silence → session closes within 9s, exactly one transcription.
  2. 5s speech + 12s pink noise at -30dBFS → session closes within 9s.
  3. 5s speech + 4s silence + 5s speech → two transcriptions in one session.

**Routing rules verified by unit test:**
- typed input + voice on + ambient on → text-only reply
- voice input → voice reply (and text bubble in parallel)
- ambient observation + ambient toggle off → text bubble only
- ambient observation + ambient toggle on → text bubble + spoken
- typed input during voice session → session closes immediately, queue dropped, text-only reply

**Sensitive-app gating:**
- Sensitive-app active → speak() is no-op AND wake listener pauses; verified by unit test and by manual Slack-DM smoke.

**Server ASR:**
- `[audio] asr_backend = "server"` works against the new endpoint on apollyon.
- Stopping the server mid-session → 2s timeout → falls back to local whisper-small.en with one-line log; pipeline unblocked.

**Lifecycle:**
- Toggling voice off (modal or `/voice-io off`) releases mic stream — verified by macOS orange dot disappearing, Windows mic icon clearing, no leaked PipeWire client.
- atexit handler joins daemon thread within 250ms before closing streams.

**Backend registry + plugin path:**
- `KokoroBackend`, `LocalWhisperBackend`, `RemoteWhisperBackend`, `OpenWakeWordBackend` all register via decorator and are discovered by walk-packages.
- Adding a new TTS backend file to `tokenpal/audio/backends/` makes it appear in the voice dropdown without edits to session / pipeline / orchestrator / modals.
- Voice IDs namespaced `<backend>:<voice>`.

**Validate + docs:**
- `tokenpal --validate` reports each audio dependency (sounddevice, models, mic permission) with PASS/FAIL. On macOS, names the parent terminal binary correctly (read from `TERM_PROGRAM`).
- `pytest`, `ruff`, `mypy --strict` all green on new code.
- `CLAUDE.md`, `docs/claude/{ui,brain,server}.md` updated.
- `tools/wakeword-training/README.md` documents the Colab T4 retraining path for future tuning.

## Parking lot
(empty at start — append "ooh shiny" thoughts that surface mid-work for later)
