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

# Sentence terminators: western + Devanagari danda.
_TERMINATORS = ".!?…।"
# May follow a terminator and still belong to the sentence: closing quotes and
# brackets, and the markdown markers a model wraps text in ("**Yes.** Then").
_CLOSERS = "\"')\\]”’*_`"
_OPENERS = "([{\"'“‘*_`"
# Punctuation that is not speech on its own (a streamed lone "!", a bare bullet).
_UNSPOKEN = _TERMINATORS + _CLOSERS + _OPENERS + " \t\r-–—•|#>~"
# Common abbreviations that end with '.' but do not end a sentence.
_ABBREVIATIONS = frozenset(
    "dr mr mrs ms prof st vs etc e.g i.e eg ie no fig approx a.m p.m rs".split()
)
# Only the full stop is ambiguous: it also sits inside "12.5", "v6.0.2" and
# "example.com", so it ends a sentence only when whitespace follows (after any
# closer). '!', '?', '…' and the danda end one wherever they stand. A full stop
# at the very end of the buffer is therefore NOT a boundary yet -- the next
# character decides -- which is why the bridge flushes a buffer that has gone
# quiet (ends_sentence below).
#
# The pattern finds boundaries; it does not match whole sentences. A lazy ".*?"
# in front of it retried every failed lookahead from every offset -- cubic on a
# run of dots -- and this runs on the asyncio loop once per token.
_BOUNDARY_RE = re.compile(
    rf"(?:[!?…।]|\.(?=[{_CLOSERS}]*\s))[{_TERMINATORS}]*[{_CLOSERS}]*\s*"
)
_DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")


def _is_abbreviation(sentence: str) -> bool:
    """True when the chunk ends on an abbreviation dot, not a real stop."""
    stripped = sentence.rstrip()
    if not stripped.endswith("."):
        return False
    words = stripped[:-1].split()
    if not words:
        return False
    # "10." with nothing before it is a list marker: it belongs with its item.
    if len(words) == 1 and words[0].isdigit():
        return True
    last_word = words[-1].lower().lstrip(_OPENERS)  # "(e.g." is still "e.g."
    bare = last_word.replace(".", "")
    # Single letters cover initials and the halves of "e.g."/"i.e.".
    return len(bare) == 1 or last_word in _ABBREVIATIONS or bare in _ABBREVIATIONS


def _speakable(text: str) -> bool:
    """False for a chunk that is punctuation only: Kokoro should never be
    handed a bare "!" or a list bullet. A symbol ("%", "@") is speech."""
    return bool(text.strip(_UNSPOKEN))


def _split_line(line: str, final: bool) -> tuple[list[str], str]:
    sentences: list[str] = []
    pos = 0
    pending = ""  # accumulates chunks glued across abbreviation dots
    for match in _BOUNDARY_RE.finditer(line):
        chunk = pending + line[pos : match.end()]
        pos = match.end()
        if _is_abbreviation(chunk):
            pending = chunk
            continue
        text = chunk.strip()
        if _speakable(text):
            sentences.append(text)
        pending = ""
    remainder = pending + line[pos:]
    if final:
        tail = remainder.strip()
        if _speakable(tail):
            sentences.append(tail)
        remainder = ""
    return sentences, remainder


def split_sentences(buf: str, *, final: bool = False) -> tuple[list[str], str]:
    """Split a streaming text buffer into complete sentences + remainder.

    Called repeatedly as tokens arrive; returns sentences ready for TTS and
    the unfinished tail to carry into the next call. final=True flushes the
    tail as a last sentence (end of turn).

    A newline is a boundary: every completed line is flushed whole, so a list
    is spoken item by item instead of waiting for a full stop that never comes.
    """
    *lines, tail = buf.split("\n")
    sentences: list[str] = []
    for line in lines:
        sentences += _split_line(line, True)[0]
    more, remainder = _split_line(tail, final)
    return sentences + more, remainder


def ends_sentence(buf: str) -> bool:
    """True when the buffer stops on a terminator that is not an abbreviation.

    The bridge uses this to flush a buffer the model has stopped adding to:
    "Let me check." followed by a tool call has no next character to prove the
    full stop was a sentence end.
    """
    stripped = buf.rstrip().rstrip(_CLOSERS)
    if stripped[-1:] not in tuple(_TERMINATORS):
        return False
    # "The answer is 5." ends on a single character, which _is_abbreviation
    # reads as an initial. A digit before the stop of a quiet buffer is an answer.
    return stripped[-2:-1].isdigit() or not _is_abbreviation(stripped)


# Markdown → speakable text: Kokoro/espeak read "**" aloud ("asterisk
# asterisk" — owner report). Applied inside synth(), the single funnel for
# replies, announcements, and the briefing. Order matters: paired constructs
# first, then a sweep for unpaired leftovers.
_MD_RULES: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"</?think>"), " "),  # leaked reasoning tags are never speakable
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
    # Not markdown, same funnel: a full stop inside a token. Kokoro's phonemizer
    # cuts at every '.', so "12.5" came out as "twelve" -- pause -- "five" and
    # "example.com" as two words, however the sentence had been split. Lowercase
    # only after "dot": a domain or a file extension, never "check.It".
    (re.compile(r"(?<=\d)\.(?=\d)"), " point "),
    (re.compile(r"(?<=[A-Za-z0-9])\.(?=[a-z]{2,}\b)"), " dot "),
    # espeak reads an emoji's name: "Done 👍" became "done thumbs up".
    (re.compile(r"[\U0001f300-\U0001faff\u2600-\u27bf\ufe0f]"), " "),
)


# espeak's language-switch marks in phonemizer output: "(en)", "(hi)", "(en-us)".
_SWITCH_FLAG_RE = re.compile(r"\([a-z]{2,3}(?:-[a-z]{2,3})?\)")


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
        cpu_threads: int = 4,
    ) -> None:
        self.model_path = Path(model_path)
        self.voices_path = Path(voices_path)
        self.voice_en = voice_en
        self.voice_hi = voice_hi
        self.speed = speed
        self.cpu_threads = cpu_threads
        self._kokoro = None

    def load(self) -> None:
        import onnxruntime as ort  # heavy; lazy
        from kokoro_onnx import Kokoro
        from kokoro_onnx.config import KoKoroConfig

        # Kokoro() checked the files before building a session; building the
        # session ourselves would raise onnxruntime's NoSuchFile instead of the
        # FileNotFoundError health and setup report on a missing download.
        KoKoroConfig(str(self.model_path), str(self.voices_path)).validate()

        # Kokoro's own constructor takes onnxruntime's defaults: one worker per
        # physical core, spin-waiting between ops. That held all 8 cores of the
        # 9700X (7.1 cores measured) for every sentence Baby spoke. Four threads
        # without spinning synthesise the same sentence in the same time (0.62 s
        # against 0.58 s for 4 s of speech) on 2.8 cores -- DECISIONS #167.
        options = ort.SessionOptions()
        options.intra_op_num_threads = self.cpu_threads
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        session = ort.InferenceSession(
            str(self.model_path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self._kokoro = Kokoro.from_session(session, str(self.voices_path))

    def synth(self, sentence: str):
        """One sentence → (int16 numpy samples, sample_rate)."""
        import numpy as np

        if self._kokoro is None:
            self.load()
        sentence = strip_markdown(sentence)
        if not sentence:  # pure-markdown chunk (e.g. a lone "**")
            return np.zeros(0, dtype=np.int16), SAMPLE_RATE
        voice, lang = pick_voice(sentence, self.voice_en, self.voice_hi)
        # espeak marks a switch of language inside a sentence -- the English words
        # in a Hindi one -- as "(en)...(hi)", and kokoro-onnx hands those letters to
        # Kokoro, which says them. Phonemize here so the marks can go first.
        phonemes = _SWITCH_FLAG_RE.sub("", self._kokoro.tokenizer.phonemize(sentence, lang))
        samples, sample_rate = self._kokoro.create(
            phonemes, voice=voice, speed=self.speed, is_phonemes=True
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
