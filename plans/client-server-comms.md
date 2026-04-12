# Client-Server Architecture

## Goal
Replace the current SSH-based remote training orchestration with an HTTP(S) server that runs on GPU machines (geefourteen, future boxes). TokenPal clients talk to the server for both inference (proxied Ollama) and training (fine-tune orchestration). Eliminates SCP/rsync file transfers, SSH session fragility, and per-machine Ollama model registration. Server targets all three platforms: Windows (geefourteen, CUDA), Linux, macOS.

## Non-goals
- **No rewriting the LLM backend abstraction.** `HttpBackend` already talks to any OpenAI-compat endpoint — we're changing what URL it points at, not how it generates.
- **No auth in v1.** V1 trusts the LAN. Auth module is pluggable so v2 can add shared-secret or token auth after a security review. Ship `NoAuth` only.
- **No HTTPS in v1.** LAN-only traffic. Document the risk and the path to TLS.
- **No GUI installer.** Server setup is a CLI script.
- **No ROCm on bare Windows.** Still blocked on ROCm 7.3+ for gfx1201. Server works with CUDA on Windows, ROCm on Linux. (But see TheRock in parking lot.)
- **No streaming inference proxy.** V1 proxies Ollama requests as full request/response. SSE streaming is a follow-up.
- **No mobile clients.** Desktop Python clients only.
- **No removal of the SSH training path.** Keep it working as a fallback. Deprecate over time.
- **No new `LLMConfig` fields.** `api_url` already handles pointing at a remote server.
- **No voice profile upload.** Server always regenerates from wiki. Simpler pipeline.

## Architecture

```
┌─────────────┐         HTTP          ┌──────────────────────────┐
│ TokenPal    │ ───────────────────── │ TokenPal Server          │
│ Client      │  /v1/* (byte proxy)   │  (FastAPI + uvicorn)     │
│ (any machine)│  /api/v1/train       │                          │
│             │  /api/v1/models       │  ┌────────┐  ┌─────────┐ │
│             │  /api/v1/server       │  │ Ollama  │  │ Training│ │
│             │                       │  │ (local) │  │ Worker  │ │
└─────────────┘                       │  └────────┘  └─────────┘ │
                                      └──────────────────────────┘
Port 8585. Ollama stays on localhost:11434 (not exposed to network).
```

**Two logical endpoints, one HTTP server:**
1. **Inference proxy** — byte-forwards `/v1/*` to local Ollama. No JSON deserialization (streaming-ready from day 1). Clients set `[llm] api_url = "http://geefourteen:8585/v1"`.
2. **Training API** — `POST /api/v1/train {"wiki": "adventure-time", "character": "BMO"}`. Server handles the full pipeline: wiki fetch → voice profile generation → dataset prep → train → merge → register with local Ollama. Client just polls status.

## Design decisions (resolved via brainstorm)

1. **Framework**: FastAPI — async-native, Pydantic validation, auto `/docs`, earns its weight. Don't use DI beyond auth; keep worker/state as plain classes in `app.state` via lifespan context manager.
2. **Inference proxy**: Raw byte-forwarding via shared `httpx.AsyncClient` (created in lifespan, `read=120.0` timeout). Never deserialize Ollama responses. SSE-streaming-ready from day 1.
3. **Training worker**: `asyncio.to_thread()` — GIL released during CUDA compute, event loop stays responsive. `asyncio.Lock()` enforces one-job-at-a-time. If training OOMs and crashes the server, the process exits; systemd/NSSM restart policy brings it back. Server must log clearly on crash so the cause is obvious.
4. **Job state**: JSON files on disk at `~/.tokenpal-server/jobs/<job-id>.json`. Survives restart, human-debuggable. On server start, scan for `status: "running"` with dead PID → mark failed.
5. **Auth**: Pluggable `AbstractAuth` with `authenticate(request) -> bool`. V1 ships `NoAuth` only. `SharedSecretAuth` (X-API-Key header) ready for v2. Simple if/else in config loading, no plugin system.
6. **Default bind**: `127.0.0.1` (localhost only). LAN access is opt-in via `host = "0.0.0.0"`. Log warning when non-localhost + no auth.
7. **Auto-fallback**: When configured server is unreachable, automatically try `localhost:11434`. Status bar shows which backend is active. Retry remote every 60s. Config: `mode = "auto" | "remote" | "local"` (default "auto").
8. **Config**: Server reads same `config.toml` format with `[server]` section. On server machine, `[server]` matters. On client, `[llm]` matters. One file format, potentially different files on different machines. `tokenpal-server --config PATH` overrides.
9. **Server venv**: Separate at `~/.tokenpal/server-venv/` (clean separation from training venv at `~/tokenpal-training`).
10. **Entry point**: `tokenpal-server` (separate executable, matches `tokenpal-finetune` precedent).
11. **API versioning**: `/v1/*` for inference (Ollama's contract, never modify). `/api/v1/*` for own endpoints. `GET /api/v1/server/info` returns `server_version`, `api_version`, `capabilities`.
12. **Input validation**: Wiki `^[a-zA-Z0-9-]+$`, character `^[a-zA-Z0-9 _.'-]+$`, model `^[a-zA-Z0-9_.-]+(:[a-zA-Z0-9_.-]+)?$`. Prevents SSRF via wiki param and command injection via model names.
13. **Installer scripts**: Standalone in `scripts/` (not embedded in Python like `_INSTALL_SH`). Editable on target, testable in isolation, proper syntax highlighting.

## Files to touch

### New package: `tokenpal/server/`
- `__init__.py`
- `app.py` — FastAPI app, lifespan (shared httpx client, job store, Ollama health check), `create_app()` factory, uvicorn runner, error handlers
- `auth.py` — `AbstractAuth`, `NoAuth`, `SharedSecretAuth` stub, `require_auth` FastAPI Depends()
- `models.py` — Pydantic request/response models: `TrainRequest`, `TrainingJob`, `TrainingStatus` enum (QUEUED/FETCHING/PREPARING/TRAINING/MERGING/REGISTERING/COMPLETE/FAILED), `ServerInfo`, `ModelInfo`, `PullRequest`
- `routes_inference.py` — `/v1/{path:path}` byte-forwarding proxy to local Ollama. 502 on connect error, 504 on timeout.
- `routes_training.py` — `POST /api/v1/train` (submit job, 202 response), `GET /api/v1/train/{job_id}` (poll status), 409 on concurrent
- `routes_models.py` — `GET /api/v1/models/list` (via Ollama `/api/tags`), `POST /api/v1/models/pull` (via Ollama `/api/pull`)
- `routes_server.py` — `GET /api/v1/server/info` (version, Ollama health, GPU info, active job, `hf_token_set` bool — never actual secrets)
- `worker.py` — `asyncio.to_thread()` wrapper calling `train_from_wiki()` → `prepare_dataset()` → `setup_model()` → `train()` → `merge_adapter()` → `register_ollama()`. Error classification: OOM → GPU hint, HF 401 → token hint, Ollama down → check hint.
- `job_store.py` — `AbstractJobStore` interface (`put`, `get`, `get_active`, `list_recent`), `JsonFileJobStore` impl writing to `~/.tokenpal-server/jobs/`

### Existing files
- `tokenpal/config/schema.py` — add `ServerConfig` dataclass (`host: str = "127.0.0.1"`, `port: int = 8585`, `mode: str = "auto"`, `auth_backend: str = "none"`, `api_key: str = ""`, `ollama_url: str = "http://localhost:11434"`), add `server` field to `TokenPalConfig`
- `tokenpal/config/loader.py` — add `ServerConfig` to `_SECTION_MAP`
- `config.default.toml` — add `[server]` section
- `tokenpal/llm/http_backend.py` — add `set_api_url(url: str)` method (~4 lines: set `_api_url`, reset `_reachable`/`_model_available`, log). Add abstract method to `base.py`.
- `tokenpal/tools/remote_train.py` — thin HTTP client path: when `[server] host` is configured and mode != "local", `remote_finetune()` POSTs wiki+character to `/api/v1/train` and polls `/api/v1/train/{job_id}` instead of SSH pipeline. ~50 lines.
- `tokenpal/commands.py` — `/server` slash command: `status`, `switch local`, `switch remote`, `switch <host>`
- `pyproject.toml` — add `tokenpal-server` entry point, add `server` extras group (`fastapi>=0.115`, `uvicorn[standard]>=0.32`)

### Scripts + docs
- `scripts/install-server.sh` — Linux/macOS: install Ollama, Python deps, firewall rule (ufw/firewalld best-effort), systemd user unit (`loginctl enable-linger`), HF token prompt → `~/.tokenpal/server.env`
- `scripts/install-server.ps1` — Windows: same but PowerShell, Windows Firewall (`-Profile Private`), startup shortcut or NSSM for v1.1, HF token via `setx`
- `docs/server-setup.md` — user-facing guide, verification curl command
- `CLAUDE.md` — server architecture section

### Tests
- `tests/test_server/` — route tests (FastAPI TestClient + `httpx.MockTransport`), worker tests (mock heavy training imports), auth middleware tests, job store tests, input validation tests

## Failure modes to anticipate
- **Firewall blocks 8585**: Installer adds rule (best-effort) or warns loudly. Windows: `-Profile Private` only.
- **Training crashes server (OOM/SIGKILL)**: `asyncio.to_thread` means the whole process dies. Mitigation: systemd `Restart=on-failure` / NSSM restart policy. Server logs crash cause clearly. On restart, scan jobs/ for running-with-dead-PID → mark failed with "Server crashed during training" error.
- **Concurrent training**: 409 Conflict with active job info.
- **Server unreachable**: Client auto-falls back to local Ollama (mode=auto). Status bar shows `geefourteen (unreachable)`. Retry every 60s.
- **Model not on server**: Clear error with available models list and `/model pull` hint.
- **Wiki fetch fails**: Fandom down, bad wiki name, character not found. Structured error in job status.
- **Version drift**: `/api/v1/server/info` returns version. Client warns on mismatch, doesn't block. Handles 404 from unknown endpoints gracefully.
- **HF token**: Server-side only via env var. Never in API responses. `hf_token_set: bool` in server info.
- **Windows service management**: V1 = "run in terminal." V1.1 = startup shortcut/NSSM.
- **Two config files confusion**: Minimize server config to `[server]` section. Everything else has sensible defaults or comes from client request.
- **`_cmd_all` takes argparse.Namespace**: Worker calls underlying functions directly, not CLI wrappers.

## Implementation phases

### Phase 1: Server skeleton + inference proxy
- `ServerConfig` in schema.py + loader.py + config.default.toml
- `tokenpal/server/` package: app.py, auth.py, models.py, job_store.py, routes_inference.py, routes_server.py
- `set_api_url()` on HttpBackend
- `/server status` slash command
- `tokenpal-server` entry point in pyproject.toml
- Tests with httpx.MockTransport

### Phase 2: Model management + training
- routes_models.py (list/pull via Ollama HTTP API)
- worker.py (training pipeline wrapper)
- routes_training.py (submit/poll)
- `/server switch` slash command
- Client-side HTTP training path in remote_train.py

### Phase 3: Installer + polish
- install-server.sh (Linux/macOS) + install-server.ps1 (Windows)
- Auto-fallback to local Ollama (mode=auto)
- Status bar server indicator
- docs/server-setup.md + CLAUDE.md update

### Phase 4 (v1.1)
- Windows auto-start (startup shortcut or NSSM)
- systemd `Restart=on-failure` confirmation on Linux

## Done criteria
- `tokenpal-server` starts FastAPI server, binds to configurable host:port (default 127.0.0.1:8585)
- `/v1/chat/completions` proxies to local Ollama — client with `api_url = "http://server:8585/v1"` generates commentary without local Ollama
- `/api/v1/train` accepts `{"wiki": "...", "character": "..."}`, runs full pipeline server-side
- `/api/v1/train/{job_id}` returns job state with progress messages and actionable error hints
- `/api/v1/models/list` returns server models, `/api/v1/models/pull` triggers server-side download
- `/api/v1/server/info` returns version, Ollama health, GPU info, active job
- Job state persisted as JSON files (survives restart)
- Server installer scripts work on Windows (geefourteen) and Linux
- Client auto-falls back to local Ollama when server unreachable (mode=auto)
- Status bar shows which server is active
- `/server status` and `/server switch` slash commands work
- Input validation on all API parameters (wiki, character, model names)
- Existing SSH path still works when server not configured (backward compat)
- Training crash is survivable: server restarts, stale job marked failed, cause logged
- End-to-end: Mac client → `/voice train adventuretime bmo` → server trains → inference through server → BMO voice works

## Parking lot
- **TheRock (ROCm on bare Windows)** — AMD's new lightweight ROCm build system ([github.com/ROCm/TheRock](https://github.com/ROCm/TheRock)) ships nightly Windows PyTorch wheels with ROCm support. Currently "early preview." Could unblock the RX 9070 XT (gfx1201) on bare Windows without waiting for official ROCm 7.3. Worth testing: install their nightly PyTorch wheel on the AMD desktop, check if `torch.cuda.is_available()` (HIP) returns True for gfx1201. If it works, add TheRock's wheel index as an option in install-server.ps1 alongside the CUDA path.
- ROCm on bare Windows via official channel — blocked on ROCm 7.3+ for gfx1201. TheRock nightlies may leapfrog this.
- HTTPS/TLS — reverse proxy (nginx/caddy) or built-in cert generation. Required before exposing beyond trusted LAN, and before SharedSecretAuth (API key in cleartext headers).
- SSE streaming for inference proxy — swap to `httpx.stream()` + `StreamingResponse`. Day-1 architecture supports it.
- mDNS server discovery — auto-discover TokenPal servers on LAN for `/server list`.
- NSSM / Windows Service wrapper for server process management (v1.1 target).
- SharedSecretAuth implementation — post security review. Requires HTTPS first.
- Training worker subprocess extraction — if OOM crashes become a problem in practice, extract training from `asyncio.to_thread()` to a monitored child process for crash isolation.
- `/api/server/logs` endpoint — debug training from client without SSH.
- Multi-server named configs — `[server.geefourteen]`, `[server.linux-box]` for multiple GPU boxes.
- `tokenpal-server --upgrade` one-command server update.
- Local-only mode — `mode = "local"` in config to never try remote. For users who don't want a server.
