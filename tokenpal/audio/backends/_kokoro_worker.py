"""Kokoro TTS subprocess worker.

Spawned by ``KokoroBackend`` so ONNX inference + Kokoro's numpy/python
post-processing run in a different OS process from the parent's Qt main
thread. The two no longer share a GIL, and a 200ms synth burst can't
starve the buddy's 60Hz tick.

Protocol (binary on stdout, JSON on stdin, text on stderr):

stdin (parent → worker)
    Newline-delimited JSON commands.
        {"op": "synth", "text": "...", "voice": "af_bella", "speed": 1.0}
        {"op": "exit"}

stdout (worker → parent)
    On startup, after Kokoro has finished loading, the worker emits a
    4-byte big-endian zero as a "ready" handshake. The parent's warmup()
    blocks on that read so callers can rely on warmup() meaning "model
    loaded, next synth is fast".

    For each ``synth`` command, the worker emits:
        <4-byte big-endian length N><N bytes float32 mono PCM @ 24kHz>
    Length 0 means "no audio (see stderr for the error)".

stderr (worker → parent)
    Free-form log lines. The parent forwards them via ``log.warning``.
"""

from __future__ import annotations

import asyncio
import json
import struct
import sys


async def _run(model_path: str, voices_path: str) -> None:
    from kokoro_onnx import Kokoro

    kokoro = Kokoro(model_path, voices_path)
    out = sys.stdout.buffer
    out.write(struct.pack(">I", 0))
    out.flush()

    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            return
        try:
            cmd = json.loads(line)
        except json.JSONDecodeError as e:
            print(f"bad json: {e}", file=sys.stderr, flush=True)
            continue
        op = cmd.get("op")
        if op == "exit":
            return
        if op != "synth":
            print(f"unknown op: {op}", file=sys.stderr, flush=True)
            continue
        text = cmd.get("text", "")
        voice = cmd.get("voice", "af_bella")
        speed = float(cmd.get("speed", 1.0))
        chunks: list[bytes] = []
        try:
            async for samples, _sr in kokoro.create_stream(
                text, voice=voice, speed=speed,
            ):
                chunks.append(samples.tobytes())
        except Exception as e:  # noqa: BLE001 — bad input must not crash worker
            print(f"synth error: {e}", file=sys.stderr, flush=True)
        pcm = b"".join(chunks)
        out.write(struct.pack(">I", len(pcm)))
        if pcm:
            out.write(pcm)
        out.flush()


def main() -> None:
    if len(sys.argv) < 3:
        print("usage: _kokoro_worker <model> <voices>", file=sys.stderr)
        sys.exit(2)
    asyncio.run(_run(sys.argv[1], sys.argv[2]))


if __name__ == "__main__":
    main()
