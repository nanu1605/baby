"""Voice pipeline package (Phase 3): wake word, VAD, STT, TTS, barge-in.

Everything heavy (onnxruntime, ctranslate2, torch, sounddevice) is imported
lazily inside load() methods so importing this package — and running the
test suite — never touches audio hardware or model weights.
"""
