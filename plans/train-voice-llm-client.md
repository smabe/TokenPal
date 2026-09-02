# Voice trainer uses HttpBackend as its LLM client

## Status
**Approval: APPROVED 2026-09-02**
**Authored at: b47da23**
Approved 2026-09-02; the operator will run it in a separate session with `/plan train-voice-llm-client`. Nothing started.
Re-planned 2026-09-02 after the operator rejected the first draft's mechanism ("no reason it can't be async"): the trainer adopts `HttpBackend` instead of sharing a pure body-shape function with its own urllib client. Research re-run on the new shape.
Verification pass 2026-09-02 (re-plan) — grounding 37/41 resolve → 4 line drifts fixed (`_classify_via_cloud` 478→473, urllib timeout 126→125, `temperature` read 35-76→38, ScriptedLLM no-ops 48-56→45-46) and the httpx citation restated with the audit's own probe result · executability 5/7 → 2 fixed (`ScriptedLLM.generate` now records `max_tokens` so test (1) is assertable; `TYPE_CHECKING` imports, the `_classify_via_cloud` import, and the `json`/`urllib` question decided in Work) · coherence 5 → 5 fixed (boundary names the two supporting seams; exclusion reworded to "no behavior change"; `_classify_via_cloud` import pinned; unreachable-server cost 60→120 s; stray `Config` name) · 0 promoted · 0 refuted · 0 uncheckable.

## Goal
The voice trainer's LLM call goes through `HttpBackend`, so thinking controls, response parsing, and engine quirks have one definition, and the trainer stops reloading config three times per prompt.

## Scope contract
- **Requested outcome:** `tokenpal/tools/train_voice.py`'s LLM call goes through `HttpBackend` instead of hand-built urllib requests, so thinking controls, response parsing, and engine quirks have one definition; `_ollama_generate(prompt, max_tokens, temperature) -> str | None` keeps its name and signature for the six callers and seven test patches, returns `None` on any failure including a malformed or empty response, and reads config once per call.
- **Named semantic boundary:** `_ollama_generate` and its config helpers (`_get_model`, `_get_ollama_url`, `_thinking_controls`) in `tokenpal/tools/train_voice.py`; `HttpBackend.setup`/`generate` as the client, plus the two seams that adoption needs: the backend config-dict builder the app already has (`tokenpal/app.py:156-162`, moving to `tokenpal/llm/registry.py`) and a request-timeout key on `HttpBackend`.
- **Explicit inclusions:** the async-to-sync bridge from the trainer's worker threads; per-call `temperature`; the empty-`choices` and `content: null` cases; removal of the duplicated thinking-controls function.
- **Explicit exclusions:** no thinking-on for training; no change to prompts, voice-card generation, the ASCII classifier, `_classify_via_cloud` (its local `load_config` import at `:484` stays as is), or `_get_voices_dir`; no behavior change to the brain loop's backend (the app only swaps in the shared dict builder, same keys, same values).
- **Intent class:** bounded consolidation.

## Non-goals
- `_ollama_generate`'s signature does not change. `tests/test_tools/test_ascii_skeletons.py:192` installs a positional fake `(prompt, max_tokens, temperature)`; seven test sites patch the name (`tests/test_voice_ascii_classifier.py:112,123,133`, `tests/test_tools/test_ascii_skeletons.py:197,204`, `tests/test_tools/test_voice_hardening.py:335,349`).
- No `temperature` keyword on `generate`. The backend is built per call, so temperature is a config-dict key (`HttpBackend.__init__` reads `temperature`, `tokenpal/llm/http_backend.py:38`; `generate_with_tools` writes `self._temperature`, `:412`). A kwarg would touch the ABC (`tokenpal/llm/base.py:113-123`) and the two keyword-enumerating fakes for nothing.
- No shared backend across threads. Verified by probe this session (httpx 0.28.1, httpcore 1.0.9): one `httpx.AsyncClient` reused under `asyncio.run()` from a second thread raises `RuntimeError: Event loop is closed`, because pooled keep-alive connections hold anyio streams bound to the creating loop (`httpcore/_async/connection_pool.py`, `httpcore/_backends/anyio.py`). The audit reproduced it against a keep-alive HTTP/1.1 server and saw it pass only against an HTTP/1.0 server that closes per request; Ollama, llama-server, and MTPLX keep connections alive. Each call owns its loop, client, and backend.
- No thinking-controls function is lifted out of `HttpBackend`; the trainer no longer builds a request body at all. `grep -rn '\["reasoning_effort"\]\|"chat_template_kwargs":\|"reasoning_effort":' tokenpal/ tools/ scripts/ --include='*.py'` at b47da23 hits `http_backend.py:189,191` and `train_voice.py:82,85`; after this plan only the former remain.
- `_classify_via_cloud` (`train_voice.py:473-502`) and `_get_voices_dir` (`:88-96`) keep their own `load_config()`; neither is on the per-prompt path.
- `tokenpal/server/worker.py:219` runs the trainer via `asyncio.to_thread`, so it is a plain executor thread with no running loop; nothing there changes.

## Work
- Scope trace: DIRECT — routing the call through `HttpBackend` with per-call temperature and a `None` on failure is the requested outcome. The shared config-dict builder is PREREQUISITE — the trainer is the second consumer of the three-line dict the app builds, and the repo rule shares at the second consumer. The request-timeout key is SAFETY — `setup()` hardcodes a 60 s client timeout (`http_backend.py:135`) while the trainer's urllib call allowed 120 s (`train_voice.py:125`); a 600-token persona generation on a slow rig that succeeded before would time out and return `None`.
- `tokenpal/llm/registry.py` — add next to `resolve_backend` (`:37-54`) a builder (proposed):
  ```python
  def backend_config(
      config: TokenPalConfig, *, memory_store: MemoryStore | None = None, **overrides: Any,
  ) -> dict[str, Any]:
      llm_config = dataclasses.asdict(config.llm)
      llm_config["server_mode"] = config.server.mode
      if memory_store is not None:
          llm_config["memory_store"] = memory_store
      llm_config.update(overrides)
      return llm_config
  ```
  Transcribed from `tokenpal/app.py:156-162`. `TokenPalConfig` is the top-level dataclass (`tokenpal/config/schema.py:535`), what `load_config` returns (`tokenpal/config/loader.py:141-144`). Import both `TokenPalConfig` and `MemoryStore` under `TYPE_CHECKING` only: `MemoryStore` lives in `tokenpal/brain/memory.py:187` and a runtime import from `tokenpal/llm/registry.py` into `tokenpal/brain/` would invert the dependency direction (`registry.py` imports only `logging`, `typing`, `tokenpal.llm.base`, `tokenpal.util.platform` today, `:1-11`). `import dataclasses` is a new runtime import.
- `tokenpal/app.py` — replace the three lines at `:156-162` with `backend_config(config, memory_store=memory)`; the `resolve_backend(llm_config)` call at `:163` stays.
- `tokenpal/llm/http_backend.py` — `__init__` reads `request_timeout_s` from the dict (default `60.0`, matching today's literal) and `setup()` passes it to `httpx.AsyncClient(timeout=...)` at `:135`. One-line comment on the key: the voice trainer raises it because persona generations run to 600 tokens.
- `tokenpal/tools/train_voice.py` — delete `_get_model` (`:59-66`), `_thinking_controls` (`:69-85`), `_get_ollama_url` (`:98-106`). Move `from tokenpal.config.loader import load_config` to module top; `_classify_via_cloud`'s local import at `:484` is untouched (excluded). Add module-top imports `import asyncio`, `from tokenpal.config.schema import TokenPalConfig`, `from tokenpal.llm.http_backend import HttpBackend`, `from tokenpal.llm.registry import backend_config`. Rewrite `_ollama_generate` (`:108-131`), signature unchanged:
  ```python
  def _ollama_generate(prompt: str, max_tokens: int = 60, temperature: float = 0.7) -> str | None:
      """One prompt through the configured backend; None on any failure."""
      try:
          config = load_config()
      except Exception:
          log.warning("Voice-training LLM call: config unreadable, using defaults")
          config = TokenPalConfig()
      llm_config = backend_config(config, temperature=temperature, request_timeout_s=120.0)

      async def _run() -> str:
          backend = HttpBackend(llm_config)
          await backend.setup()
          try:
              response = await backend.generate(
                  prompt, max_tokens=max_tokens, enable_thinking=False,
              )
          finally:
              await backend.teardown()
          return response.text

      try:
          text = asyncio.run(_run()).strip()
      except Exception as e:
          log.warning("Voice-training LLM call failed: %s", e)
          return None
      if not text:
          log.warning("Voice-training LLM call returned no text")
          return None
      return text.strip("\"'").strip()
  ```
  `TokenPalConfig()` is the top-level dataclass with all defaults (`tokenpal/config/schema.py:535`), which is what the three deleted fallbacks hardcoded piecewise. `HttpBackend.generate` returns `text=""` for `content: null` (`http_backend.py:431`, `or ""`) and raises `IndexError` on `{"choices": []}` (`:429`); both land in the two `None` paths above. Broad `except Exception` is this file's convention (`:65,78,94,104,486,490,499`). Drop the `urllib` imports (only `_ollama_generate` used them); `json` stays (used at `:646-647`).
- `tests/_helpers.py` — `ScriptedLLM.generate` (`:48-56`) records `{"max_tokens": max_tokens, **kwargs}` in `call_kwargs`, the way `generate_with_tools` already does since 1150dd9, so test (1) below can assert `max_tokens`. `grep -n "call_kwargs" tests/` first: `tests/test_research.py:753,803` use `.get(...)` and are unaffected; adjust any equality assertion on a `generate` entry.
- `tests/test_tools/test_voice_hardening.py` — three tests that patch `tokenpal.tools.train_voice.HttpBackend` with a factory capturing the config dict and returning `tests/_helpers.py:ScriptedLLM` (no-op `setup`/`teardown` at `:45-46`; `generate` at `:48-56` records its `**kwargs` in `call_kwargs`), and patch `tokenpal.tools.train_voice.load_config` with a counting stub returning a `TokenPalConfig()` whose `llm.inference_engine` is `"llamacpp"`: (1) the captured dict has `temperature == 0.3` (the value passed), `request_timeout_s == 120.0`, `inference_engine == "llamacpp"`, `generate` was called once with `max_tokens=500, enable_thinking=False`, and `load_config` was called exactly once; (2) a scripted `LLMResponse(text="")` yields `None`; (3) a factory whose `generate` raises `IndexError` yields `None`. `asyncio.run` inside the wrapper means these are plain sync tests.
- `tests/test_server/test_llm_backend.py` — one test that `HttpBackend({"api_url": ..., "request_timeout_s": 120.0})` builds its client with a 120 s timeout after `setup()` (patch `httpx.AsyncClient` in the module and assert the `timeout=` it received; `setup()` also calls `_try_connect`, so give the fake client a `get` that raises `httpx.ConnectError`), and the default stays `60.0`.
- `tests/test_llm/test_registry.py` — new file; no test targets `resolve_backend` today (`grep -rln resolve_backend tests/` hits only `tests/test_research*.py`, which test research's own `_resolve_backend`). One test for `backend_config`: `server_mode` comes from `config.server.mode`, `memory_store` is present only when passed, overrides win.
- `docs/claude/llm.md` — the thinking bullet (line 4) says the voice trainer is a second `HttpBackend` consumer (per-call backend, `request_timeout_s` 120) and no longer builds request fields itself.

## Decisions & findings
### Decision: one backend per call inside `asyncio.run()`  *(status: active)*
- **Rationale:** operator's direction is async; the httpx client is loop-bound (see Non-goals), and every caller is a plain thread with no running loop: app daemon threads (`tokenpal/app.py:2897,2965,3033`), the trainer's `ThreadPoolExecutor(max_workers=6)` (`train_voice.py:768,1028`), the server worker's `asyncio.to_thread` (`server/worker.py:219`), and the CLI `main()` (`:1423`). `asyncio.run` from a plain thread is the repo's canonical bridge (`app.py:1787`, `cli.py:84,344`, `audio/backends/_kokoro_worker.py:83`).
- **Cost accepted:** per call, `setup()` does one `GET {api_url}/models` (`http_backend.py:89`) and one 5 s-capped max_tokens probe (`/props` or `/api/show`, `:601-606`) that is wasted because the trainer passes `max_tokens` explicitly (`:214`). Locally that is milliseconds per prompt against multi-second generations.
- **Alternatives considered:** a dedicated loop thread holding one backend with `run_coroutine_threadsafe` (the brain-loop pattern, `app.py:585`); rejected as machinery for a training tool that makes tens of calls per run. Constructing the backend without `setup()` to skip the probes; rejected because it means touching `_client` privately.

### Decision: model auto-adopt now applies to training  *(status: active)*
- **Rationale:** `setup()` adopts the server's first advertised model when the configured name is not listed and no per-server pin exists (`http_backend.py:103-115`; disabled on the local-Ollama fallback path, `:151`). Previously the trainer sent the configured name and got a server error. This matches what the brain loop already does on the same config, so the trainer follows it. Recorded because it is a behavior change a user could notice on a multi-model Ollama box.

### Decision: `TokenPalConfig()` is the fallback when config is unreadable  *(status: active)*
- **Rationale:** the three deleted helpers each hardcoded one field of `LLMConfig`'s defaults; the dataclass carries all of them, plus `server.mode`, in one instance.
- **Evidence:** `tokenpal/config/schema.py:114-117`, research finding on the fallback literals.

## Failure modes to anticipate
- An unreachable non-local `api_url` now costs up to 120 s (the `request_timeout_s` the trainer passes) plus one local-Ollama fallback attempt per call when `server_mode == "auto"` (`http_backend.py:146-151`), where urllib failed on its own 120 s timeout. Same wall time; the difference is the fallback could silently succeed against a local Ollama the user did not intend to train against. Same rule the brain loop lives under.
- `asyncio.run` inside a `ThreadPoolExecutor` worker that is itself inside `asyncio.to_thread` (server worker) is fine: the worker thread has no loop. It would break only if a caller ever ran `_ollama_generate` from inside the brain loop's thread; `grep -n "_ollama_generate\|train_voice" tokenpal/brain/` is empty at b47da23.
- Import cycle: `.venv/bin/python -c "import tokenpal.llm.http_backend, tokenpal.tools.train_voice, tokenpal.app"` succeeds at b47da23 (`http_backend.py:12-14` imports only `config.schema`, `llm.base`, `llm.registry`). Re-run after adding the imports.
- `json` and `urllib` in `train_voice.py`: grep before removing the imports.

## Done criteria
- `pytest` green; ruff and mypy clean on `tokenpal/tools/train_voice.py`, `tokenpal/llm/http_backend.py`, `tokenpal/llm/registry.py`, `tokenpal/app.py`, and the touched tests; tree-wide ruff/mypy counts unchanged from HEAD (10 N802 in `tokenpal/ui/quick/`, 38 mypy in unrelated files).
- The five new tests pass.
- Observable: `grep -rn '\["reasoning_effort"\]\|"chat_template_kwargs":\|"reasoning_effort":' tokenpal/ tools/ scripts/ --include='*.py'` hits only `tokenpal/llm/http_backend.py`.
- Observable: a live `/voice regenerate <name>` on this Mac against MTPLX (`http://localhost:8000/v1`, engine `llamacpp`; `tokenpal/app.py:2806`; the CLI `main()` at `train_voice.py:1423` has no regenerate path) produces non-empty persona text, and `~/.tokenpal/logs/` shows no `Voice-training LLM call` warnings. Drive the Qt overlay the way the agent-thinking p2 worker did (Quartz events to the dock window, `screencapture -l`; its "Driving the Qt overlay" notes are in `plans/shipped/agent-thinking-p2.md` under Findings from execution), or use the Textual overlay under `script -q` and type the command. The worker names the voice used and pastes the persona line.

## Parking lot
(empty)
