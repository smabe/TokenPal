"""Audio I/O subsystem.

Output (TTS) and input (mic + wake + ASR + VAD) sides are kept structurally
independent so that ambient narration alone never opens a microphone stream.
The contract is enforced by ``tests/test_audio/test_modularity.py``.
"""
