"""Kokoro TTS via kokoro-onnx, plus the pure text helpers it depends on.

split_sentences/pick_voice are pure functions kept import-light — they carry
most of the unit tests. Per spec Section 13, TTS routes per SENTENCE by
script: any Devanagari → the Hindi voice; otherwise the English voice
(a Roman-script Hinglish reply uses the English voice — that's how people
read it aloud).

Hindi sentences go to Sarvam AI's Bulbul v3 instead, when the user has saved a
SARVAM_API_KEY and the voice bridge cleared the sentence to leave the PC
(CloudOk). Any failure falls back to Kokoro's Hindi voice -- DECISIONS #168.
"""

from __future__ import annotations

import logging
import os
import queue
import re
import threading
import time
import wave
from pathlib import Path

SAMPLE_RATE = 24000  # Kokoro output rate

# Longer chunks go straight to Kokoro. The API takes 2500 characters, but a reply
# flushed without full stops can be one huge chunk that cannot be voiced and
# downloaded inside the 4 s wait -- and the timeout would then switch Sarvam off.
# ponytail: guessed, not measured -- set it from a real key's latency.
_SARVAM_MAX_CHARS = 400
# Wall clock, not per-socket-operation: nothing polls barge-in or the kill switch
# while synth blocks, and httpx's timeouts leave DNS uncapped. 4 s is about one
# long spoken sentence -- the most Baby should ever go deaf for.
_SARVAM_WAIT_S = 4.0
# After any failure, stay on Kokoro this long, so an offline PC pays one stall
# every two minutes instead of one per sentence (covers a 60 s 429 window), and a
# failure that repeats -- a speaker name Sarvam rejects -- shows once in the feed
# instead of silently costing every Hindi sentence.
_SARVAM_COOLDOWN_S = 120.0


class CloudOk(str):
    """A sentence the voice bridge cleared to be voiced off this PC.

    Everything else -- announcements, the confirm prompt, health checks, the rest
    of a reply once it has read a file or run a command -- is a plain str and
    stays on Kokoro.
    Fail-closed on purpose: forgetting to mark something keeps it local.
    """

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
        voice_en: str = "af_bella",
        voice_hi: str = "hf_beta",
        speed: float = 1.05,
        cpu_threads: int = 4,
        sarvam_speaker: str = "priya",
        on_status=None,
    ) -> None:
        self.model_path = Path(model_path)
        self.voices_path = Path(voices_path)
        self.voice_en = voice_en
        self.voice_hi = voice_hi
        self.speed = speed
        self.cpu_threads = cpu_threads
        self.sarvam_speaker = sarvam_speaker
        self.on_status = on_status  # activity-feed line when Sarvam is switched off
        self._kokoro = None
        self._client = None
        self._sarvam_down_until = 0.0

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
        cloud = isinstance(sentence, CloudOk)  # before strip_markdown returns a plain str
        sentence = strip_markdown(sentence)
        if not sentence:  # pure-markdown chunk (e.g. a lone "**")
            return np.zeros(0, dtype=np.int16), SAMPLE_RATE
        voice, lang = pick_voice(sentence, self.voice_en, self.voice_hi)
        if cloud and lang == "hi":
            got = self._sarvam(sentence)
            if got is not None:
                return got
        samples, sample_rate = self._kokoro.create(
            sentence, voice=voice, speed=self.speed, lang=lang
        )
        pcm16 = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
        return pcm16, sample_rate

    def _sarvam(self, text: str):
        """Hindi via Sarvam, or None to fall back to Kokoro. Never raises."""
        key = (os.environ.get("SARVAM_API_KEY") or "").strip()
        if not key or len(text) > _SARVAM_MAX_CHARS or time.monotonic() < self._sarvam_down_until:
            return None
        if self._client is None:
            import httpx

            self._client = httpx.Client(timeout=_SARVAM_WAIT_S)
        # The request runs on a daemon thread so the wait is a real wall clock; an
        # abandoned request finishes on its own (the breaker keeps the next one off).
        result: queue.Queue = queue.Queue(maxsize=1)
        threading.Thread(
            target=self._sarvam_call, args=(key, text, result), daemon=True
        ).start()
        try:
            ok, value = result.get(timeout=_SARVAM_WAIT_S)
        except queue.Empty:
            ok, value = False, "timeout"
        if ok:
            return value
        self._sarvam_down_until = time.monotonic() + _SARVAM_COOLDOWN_S
        if self.on_status is not None:
            self.on_status(f"voice: Sarvam unavailable ({value}); local Hindi voice for 2 min")
        # Only the failure's type or HTTP status: an exception's text can carry the
        # request headers, and so the key.
        logging.getLogger(__name__).warning("sarvam tts failed (%s); Kokoro for 2 min", value)
        return None

    def _sarvam_call(self, key: str, text: str, result: queue.Queue) -> None:
        import base64
        import io

        import numpy as np

        from core import keys

        spec = keys.spec("SARVAM_API_KEY")  # one source for the URL and auth header
        try:
            r = self._client.post(
                spec.probe_url,
                headers={spec.auth_header: key},
                json={
                    "text": text,
                    "language_code": "hi-IN",
                    "model": "bulbul:v3",
                    "speaker": self.sarvam_speaker,
                    "speech_sample_rate": SAMPLE_RATE,
                    "output_audio_codec": "wav",
                },
            )
            if r.status_code != 200:
                result.put((False, f"HTTP {r.status_code}"))
                return
            wav_bytes = base64.b64decode("".join(r.json()["audios"]))  # as documented
            with wave.open(io.BytesIO(wav_bytes)) as w:
                if w.getnchannels() != 1 or w.getsampwidth() != 2:
                    raise ValueError("not mono 16-bit")
                rate = w.getframerate()
                pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
            if not len(pcm):
                raise ValueError("empty audio")
            result.put((True, (pcm, rate)))
        except Exception as exc:  # noqa: BLE001 -- any failure means "use Kokoro"
            result.put((False, type(exc).__name__))

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
