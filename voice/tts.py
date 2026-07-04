"""Kokoro TTS via kokoro-onnx, plus the pure text helpers it depends on.

split_sentences/pick_voice are pure functions kept import-light — they carry
most of the unit tests. Per spec Section 13, TTS routes per SENTENCE by
script: any Devanagari → the Hindi voice; otherwise the English voice
(a Roman-script Hinglish reply uses the English voice — that's how people
read it aloud).
"""

from __future__ import annotations

import re
import wave
from pathlib import Path

SAMPLE_RATE = 24000  # Kokoro output rate

# Sentence terminators: western + Devanagari danda. Ellipsis handled by the
# abbreviation guard below (a '…' or '...' ends a sentence only at a break).
_TERMINATORS = ".!?…।"
# Common abbreviations that end with '.' but do not end a sentence.
_ABBREVIATIONS = frozenset("dr mr mrs ms prof st vs etc e.g i.e eg ie no fig approx".split())
_SENTENCE_RE = re.compile(rf"[^{_TERMINATORS}]*[{_TERMINATORS}]+[\"')\]]*\s*", re.DOTALL)
_DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")


def _is_abbreviation(sentence: str) -> bool:
    """True when the chunk ends on an abbreviation dot, not a real stop."""
    stripped = sentence.rstrip()
    if not stripped.endswith("."):
        return False
    last_word = stripped[:-1].split()[-1].lower() if stripped[:-1].split() else ""
    bare = last_word.replace(".", "")
    # Single letters cover initials and the halves of "e.g."/"i.e." the
    # sentence regex cuts at their first dot.
    return len(bare) == 1 or last_word in _ABBREVIATIONS or bare in _ABBREVIATIONS


def split_sentences(buf: str, *, final: bool = False) -> tuple[list[str], str]:
    """Split a streaming text buffer into complete sentences + remainder.

    Called repeatedly as tokens arrive; returns sentences ready for TTS and
    the unfinished tail to carry into the next call. final=True flushes the
    tail as a last sentence (end of turn).
    """
    sentences: list[str] = []
    pos = 0
    pending = ""  # accumulates chunks glued across abbreviation dots
    for match in _SENTENCE_RE.finditer(buf):
        chunk = pending + match.group(0)
        if _is_abbreviation(chunk):
            pending = chunk
            pos = match.end()
            continue
        text = chunk.strip()
        if text:
            sentences.append(text)
        pending = ""
        pos = match.end()
    remainder = pending + buf[pos:]
    if final:
        tail = remainder.strip()
        if tail:
            sentences.append(tail)
        remainder = ""
    return sentences, remainder


# Markdown → speakable text: Kokoro/espeak read "**" aloud ("asterisk
# asterisk" — owner report). Applied inside synth(), the single funnel for
# replies, announcements, and the briefing. Order matters: paired constructs
# first, then a sweep for unpaired leftovers.
_MD_RULES: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"```[^\n]*"), " "),  # code-fence lines
    (re.compile(r"`([^`\n]*)`"), r"\1"),  # inline code
    (re.compile(r"\[([^\]]+)\]\([^)\s]*\)"), r"\1"),  # [text](url) → text
    (re.compile(r"\*\*([^*]+)\*\*"), r"\1"),  # bold
    (re.compile(r"__([^_]+)__"), r"\1"),
    (re.compile(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])"), r"\1"),  # italic
    (re.compile(r"(?<!\w)_([^_\n]+)_(?!\w)"), r"\1"),
    (re.compile(r"^#{1,6}\s+", re.MULTILINE), ""),  # headings
    (re.compile(r"^\s*[-*•]\s+", re.MULTILINE), ""),  # bullet markers
    (re.compile(r"[*_#`]{2,}"), " "),  # unpaired leftovers
)


def strip_markdown(text: str) -> str:
    """Reduce markdown to plain speakable text; collapses whitespace."""
    for pattern, repl in _MD_RULES:
        text = pattern.sub(repl, text)
    return " ".join(text.split())


def pick_voice(sentence: str, voice_en: str, voice_hi: str) -> tuple[str, str]:
    """(voice, espeak lang code) for one sentence — any Devanagari → Hindi."""
    if _DEVANAGARI_RE.search(sentence):
        return voice_hi, "hi"
    return voice_en, "en-us"


class TextToSpeech:
    """Kokoro-82M v1.0 over onnxruntime, CPU."""

    def __init__(
        self,
        model_path: str | Path = "models/kokoro-v1.0.onnx",
        voices_path: str | Path = "models/voices-v1.0.bin",
        voice_en: str = "af_heart",
        voice_hi: str = "hf_beta",
        speed: float = 1.05,
    ) -> None:
        self.model_path = Path(model_path)
        self.voices_path = Path(voices_path)
        self.voice_en = voice_en
        self.voice_hi = voice_hi
        self.speed = speed
        self._kokoro = None

    def load(self) -> None:
        from kokoro_onnx import Kokoro  # heavy; lazy

        self._kokoro = Kokoro(str(self.model_path), str(self.voices_path))

    def synth(self, sentence: str):
        """One sentence → (int16 numpy samples, sample_rate)."""
        import numpy as np

        if self._kokoro is None:
            self.load()
        sentence = strip_markdown(sentence)
        if not sentence:  # pure-markdown chunk (e.g. a lone "**")
            return np.zeros(0, dtype=np.int16), SAMPLE_RATE
        voice, lang = pick_voice(sentence, self.voice_en, self.voice_hi)
        samples, sample_rate = self._kokoro.create(
            sentence, voice=voice, speed=self.speed, lang=lang
        )
        pcm16 = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
        return pcm16, sample_rate

    def prerender(self, text: str, out_path: str | Path) -> None:
        """Render text to a WAV file (used by setup.ps1 for the ready cue)."""
        pcm16, sample_rate = self.synth(text)
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(pcm16.tobytes())


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Baby TTS utility")
    parser.add_argument("--prerender", nargs=2, metavar=("TEXT", "OUT_WAV"))
    parser.add_argument("--model", default="models/kokoro-v1.0.onnx")
    parser.add_argument("--voices", default="models/voices-v1.0.bin")
    args = parser.parse_args()
    if args.prerender:
        text, out = args.prerender
        tts = TextToSpeech(args.model, args.voices)
        tts.prerender(text, out)
        print(f"rendered {out!r}")


if __name__ == "__main__":
    _main()
