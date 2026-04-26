# Audio I/O

Local-first voice path: Kokoro TTS for output, openWakeWord + Silero VAD + faster-whisper for input. All opt-in via `[audio]` config; the modularity test pins "ambient-only never opens a mic".

## Three independent toggles
- `voice_conversation_enabled` — wake-word + voice replies. Needs `AUDIO_INPUT` + `AUDIO_OUTPUT` consent.
- `speak_ambient_enabled` — narrate spontaneous bubbles (idle, freeform, observations, EOD). Output-only, no mic.
- `speak_typed_replies_enabled` — narrate replies even when the user typed the question. Output-only. Default off — breaks the "type-in/type-out, voice-in/voice-out" symmetry deliberately.

All four states valid (none / ambient / voice / both). UI parity in Qt `OptionsDialog` and Textual `OptionsModal`.

## Routing (`tokenpal/audio/tts.py:speak`)
- `typed` source → speak only when `speak_typed_replies_enabled`. Reply path fires fire-and-forget (no FSM transition needed).
- `voice` source → speak only when `voice_conversation_enabled`. Reply path *awaits* speak() so `notify_tts_done` lands AFTER playback drains; otherwise the trailing-window mic re-opens on top of in-flight Kokoro audio.
- `ambient` source → speak only when `speak_ambient_enabled`. Fire-and-forget from `_emit_comment`.
- Module-level `asyncio.Lock` (`_playback_lock`) serializes the OutputStream lifecycle — concurrent typed + voice on the same device would otherwise race two PortAudio streams unpredictably.

## Pipeline lifecycle (`tokenpal/audio/pipeline.py`, `input.py`)
- `boot(config, data_dir)` → `AudioPipeline` (output-side discovery; if voice mode on, marker import of `openwakeword` to trip the modularity anti-test).
- `start_input(loop, on_voice_text)` → constructs `InputPipeline`, warms wake + VAD in parallel via `asyncio.gather`, opens `sounddevice.RawInputStream`, spawns daemon thread.
- atexit cleanup: cancel → join (250ms) → close stream. Order is load-bearing — closing before the thread observes cancel leaves PortAudio dead and the macOS orange dot stuck until reboot.

## FSM (`tokenpal/audio/session.py`)
States: `IDLE → LISTENING → SPEAKING → TRAILING → IDLE`. Pure state machine; `on_*` methods return `Decision(action, text)`. Two meaningful actions: `SUBMIT` (caller hands transcript to brain), `CLOSE_SESSION` (caller drains queues + closes mic). Hard-close on trailing-window deadline regardless of VAD — prevents TV/music in the room from holding the window open. `<2-char` transcript closes silently (wake fired on noise).

## Brain integration (`tokenpal/brain/orchestrator.py`)
- `_emit_comment` fires `_speak_async(source="ambient")` — every unsolicited bubble path (idle tool, freeform, observation, EOD, drift, easter egg) routes through here.
- `_handle_user_input(message, source)` carries the source through `submit_user_input`. Reply branch:
  - `source == "voice"` → `await self._speak_voice_reply(text)` then `notify_tts_done()`.
  - `source == "typed"` and `speak_typed_replies_enabled` → `_speak_async(source="typed")` fire-and-forget.
  - `source == "typed"` mid-voice-session → `audio_pipeline.input.notify_typed_input()` drops the voice path before generating reply.
- Sensitive-app gating: `_sync_audio_sensitive_state(snapshot)` runs every poll tick, edge-detects transitions, bridges to `notify_sensitive_app` / `notify_sensitive_app_cleared`. Wake listener pauses while sensitive app foregrounded.

## Backends + registry (`tokenpal/audio/registry.py`)
Generic `_BackendRegistry[B]` (PEP-695 type params) underpins three triples: TTS / wakeword / ASR. `discover_backends(include_input=False)` walks `tokenpal/audio/backends/` and skips `asr_*` / `wake_*` filenames so ambient-only boots don't pull faster-whisper / openwakeword.

- TTS: `KokoroBackend` (only one currently; `tts_backend = "kokoro"`).
- Wakeword: `OpenWakeWordBackend` — kwarg-drift fallback (`wakeword_models=` → `wakeword_model_paths=`), volume gate at -40dBFS, explicit `melspec_model_path` + `embedding_model_path` so onnxruntime loads from `<data_dir>/audio/wakeword/` instead of the empty package resources dir.
- ASR: `LocalWhisperBackend` (faster-whisper, `download_root=<data_dir>/audio/whisper/`), `RemoteWhisperBackend` (POST `/v1/audio/transcriptions`, 2s connect timeout, raises `ASRUnreachableError` on failure), `ASRWithFallback` (server mode wraps both).

## Tunables (`[audio]` config)
- `wakeword_threshold = 0.7` — drop to 0.5 on a custom-trained model with high false-fire rate.
- `vad_threshold = 0.5` — drop to 0.3 for low-amplitude mics (network KVM, USB conferencing). Listening-timeout log line includes the max VAD prob seen during the window so you can pick a value empirically.
- `trailing_window_s = 8.0` — hard close at this regardless of VAD state.
- `kokoro_quantization = "fp16"` — int8 (~80MB) auto-recommended on ≤8GB RAM by the installer.
- `asr_backend = "local" | "server"`; `asr_server_url`; `asr_model_size = "small.en"`.

## Server-side ASR (`tokenpal/server/routes_audio.py`)
Mounted at `/v1/audio/transcriptions` (NOT `/api/v1`) so RemoteWhisperBackend and any OpenAI-compatible whisper client drop in. faster-whisper loads lazily behind `asyncio.Lock` with a fast-path for cached hits. 503 + install hint when `[audio]` extras aren't present. Compute via env: `TOKENPAL_ASR_DEVICE=cuda`, `TOKENPAL_ASR_COMPUTE_TYPE=float16`.

Route order in `create_app`: audio_router BEFORE inference_router because inference's `/v1/{path:path}` catch-all proxies to Ollama and would otherwise hijack `/v1/audio/transcriptions`.

## Install + validate
- `/voice-io install` → `install_all(data_dir, quantization)`: pip wheels (kokoro-onnx, sounddevice, openwakeword, faster-whisper) + atomic-rename downloads. Models from openwakeword v0.5.1 release tag (silero_vad.onnx, melspectrogram.onnx, embedding_model.onnx, hey_jarvis_v0.1.onnx) fetched in parallel via ThreadPoolExecutor. Kokoro models fetched separately from kokoro-onnx releases.
- `tokenpal --validate` → `_check_audio` surfaces missing wheels, missing kokoro models, missing input models, and on macOS prints the parent terminal binary the user must grant Microphone permission to (read from `TERM_PROGRAM` — naming "tokenpal" sends them hunting in the wrong place).

## Slash command (`/voice-io`)
- bare → state line: `voice {on|off}, ambient {on|off}, typed-speak {on|off}` plus deps warning if anything's missing.
- `on` / `off` flips voice conversation.
- `ambient on` / `ambient off` flips ambient.
- `typed-speak on` / `typed-speak off` flips typed replies.
- `install` runs `install_all`.
All writes go through `tokenpal/config/audio_writer.py:set_audio_field` and update `config.audio` in place — live, no restart.

## Modularity contract (`tests/test_audio/test_modularity.py`)
Ambient-only boot (`voice_conversation_enabled=False, speak_ambient_enabled=True`) must NOT import `pyaudio`, `openwakeword`, `faster_whisper`. Anti-test: voice-on under the same blocker MUST fail. `sounddevice` is intentionally NOT in the blocker — its `OutputStream` is the ambient sink. The blocker plus `missing_deps(include_input=False)` split keeps spec-finder probes from tripping the contract on innocent presence-checks.

## Custom activation — status: `hey_jarvis` placeholder, runtime ready
Currently defaults to the stock `hey_jarvis_v0.1.onnx` from openWakeWord's release. `[audio] wakeword_model_name` is config-driven — the runtime is ready for any custom model. To train `hey_tokenpal`: see `tools/wakeword-training/README.md` (Colab notebook, ~1h on free T4). After training, drop the .onnx into `~/.tokenpal/audio/wakeword/` and set `wakeword_model_name = "hey_tokenpal"` in `~/.tokenpal/config.toml` under `[audio]`.

## Known limitations
- Live-toggle of voice mode requires restart — `start_input` / `stop_input` exist but the orchestrator only calls `start_input` once at brain bootup.
- ASR fallback has no cooldown — if the remote is consistently dead, every voice utterance pays the 2s connect timeout before falling back. Add a circuit breaker if it bites in practice.
- The three trailing-window scenarios from `plans/say-what.md` done-criteria (5s speech + silence, 5s speech + pink noise, 5s speech + 4s gap + 5s speech) are integration tests against real audio. Manual smoke only; nothing automated.
- `--verbose` doesn't yet show per-frame VAD probability; the listening-timeout log surfaces max-prob since reset. For finer-grained tuning, instrument `SileroVAD.process` directly.
