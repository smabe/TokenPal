# Server

- `tokenpal/server/` package: FastAPI inference proxy + training orchestration
- Byte-forwarding `/v1/*` proxy to local inference engine (Ollama or llama-server, both bind 11434). Streaming-ready.
- `create_app()` accepts `inference_url` + `inference_engine` params. `ollama_url` kept as deprecated alias for one release.
- Auto-fallback: server unreachable -> tries `localhost:11434/v1` (does not auto-adopt models from fallback)
- Launch scripts: `start-server.bat` (Ollama path) or `start-llamaserver.bat` (llamacpp path, includes `-ngl 99 -c 8192 -np 1 --no-mmap --jinja --reasoning off`, auto-kills llama-server on exit)
- `scripts/download-model.ps1` -- interactive GGUF picker for the llamacpp path. Downloads from HF, updates config.toml + start-llamaserver.bat.
- See `docs/server-setup.md` for Ollama setup, `docs/amd-dgpu-setup.md` for llamacpp setup
