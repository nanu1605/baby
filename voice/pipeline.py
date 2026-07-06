"""Voice pipeline: wake → listen → transcribe → agent → speak, with barge-in.

Runs on a dedicated daemon thread (the one allowed exception to the single
asyncio process — audio I/O blocks). All crossings into the asyncio world go
through exactly three primitives:

  submit turn:   asyncio.run_coroutine_threadsafe(agent.run_turn(text), loop)
  publish event: loop.call_soon_threadsafe(bus.publish, ...)
  read reply:    stdlib queue.Queue fed by a bridge coroutine on the loop

EventBus.publish is put_nowait on asyncio queues — loop-thread only; never
call it directly from the voice thread.

Kill-phrase matching lives here as a pure function — it is the single funnel
every transcript passes (wake capture, push-to-talk, and barge-in capture).
"""

from __future__ import annotations

import asyncio
import functools
import queue
import re
import threading
import time

from voice.tts import split_sentences

_PUNCT_RE = re.compile(r"[^\w\s]|[_।]", re.UNICODE)

# Kill phrases match only in SHORT transcripts: "baby stop" inside a long
# dictated sentence must not halt the assistant.
_MAX_KILL_WORDS = 5

CONFIRM_SENTENCE = "I need a confirmation for that — check the screen."

IDLE, LISTENING, RESPONDING = "idle", "listening", "responding"

_VAD_FRAME = 512
_WAKE_CHUNK = 1280
_BARGE_IN_FRAMES = 6  # ~200 ms of sustained speech


def normalize_transcript(text: str) -> str:
    """Lowercase, strip punctuation (incl. danda), collapse whitespace."""
    return " ".join(_PUNCT_RE.sub(" ", text.lower()).split())


def is_kill_phrase(text: str, phrases: tuple[str, ...] | list[str]) -> bool:
    """True when a short utterance contains a configured kill phrase."""
    normalized = normalize_transcript(text)
    if not normalized or len(normalized.split()) > _MAX_KILL_WORDS:
        return False
    padded = f" {normalized} "
    return any(f" {normalize_transcript(p)} " in padded for p in phrases)


class VoicePipeline:
    """State machine IDLE → LISTENING → RESPONDING on its own thread."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        agent,
        bus,
        cfg: dict,
        *,
        audio=None,
        wake=None,
        vad=None,
        stt=None,
        tts=None,
        verifier=None,
    ) -> None:
        self.loop = loop
        self.agent = agent
        self.bus = bus
        self.cfg = cfg
        self.audio = audio
        self.wake = wake
        self.vad = vad
        self.stt = stt
        self.tts = tts
        self.verifier = verifier  # voice.speaker.SpeakerVerifier (Phase 5)
        sv_cfg = cfg.get("speaker_verify", {})
        self.sv_mode = str(sv_cfg.get("mode", "chat_only"))
        self._listen_source = "wake"  # wake | ptt — PTT bypasses verification

        self.state = IDLE
        self.sentence_q: queue.Queue = queue.Queue()
        # Out-of-band announcements (task done, briefing). Spoken only while
        # IDLE — a live conversation always wins; overflow gets dropped.
        self.announce_q: queue.Queue = queue.Queue(maxsize=5)
        self.kill_phrases = tuple(cfg.get("kill_phrases", ("baby stop", "baby ruk ja")))
        self.max_utterance_s = float(cfg.get("max_utterance_s", 30))
        self.barge_in = bool(cfg.get("barge_in", True))
        self.barge_in_threshold = float(cfg.get("barge_in_threshold", 0.6))

        self._text_buf = ""
        self._turn_future = None
        self._stopping = threading.Event()
        self._ptt = threading.Event()
        self._thread: threading.Thread | None = None
        self._bridge_future = None
        self._hotkey = None

    # -- lifecycle ------------------------------------------------------------

    def load(self) -> tuple[bool, list[str]]:
        """Construct + load every un-injected stage; per-subsystem timings."""
        notes: list[str] = []
        try:
            steps = [
                ("mic", self._load_audio),
                ("wake word", self._load_wake),
                ("vad", self._load_vad),
                ("stt", self._load_stt),
                ("tts", self._load_tts),
                ("speaker verify", self._load_verifier),
            ]
            for name, loader in steps:
                started = time.monotonic()
                detail = loader()
                elapsed = time.monotonic() - started
                notes.append(f"{name} ready in {elapsed:.1f}s" + (f" ({detail})" if detail else ""))
        except Exception as exc:  # noqa: BLE001 — voice must fail soft to text-only
            notes.append(f"voice unavailable: {type(exc).__name__}: {exc}")
            return False, notes
        return True, notes

    def _load_audio(self) -> str:
        if self.audio is None:
            from voice.audio_io import AudioIO

            self.audio = AudioIO()
        self.audio.start()
        return ""

    def _load_wake(self) -> str:
        if self.wake is None:
            from voice.wakeword import WakeWord

            self.wake = WakeWord(
                model_path=self.cfg.get("wakeword_model", "models/hey_baby.onnx"),
                threshold=float(self.cfg.get("wakeword_threshold", 0.55)),
                builtin_fallback=self.cfg.get("wakeword_builtin_fallback", "hey_jarvis"),
            )
        return self.wake.load() or ""

    def _load_vad(self) -> str:
        if self.vad is None:
            from voice.vad import VoiceDetector

            self.vad = VoiceDetector(
                silence_ms=int(self.cfg.get("vad_silence_ms", 400)),
                speech_wait_ms=int(self.cfg.get("vad_speech_wait_ms", 5000)),
            )
        self.vad.load()
        return ""

    def _load_stt(self) -> str:
        if self.stt is None:
            from voice.stt import SpeechToText

            stt_cfg = self.cfg.get("stt", {})
            self.stt = SpeechToText(
                model=stt_cfg.get("model", "large-v3-turbo"),
                device=stt_cfg.get("device", "cpu"),
                compute_type=stt_cfg.get("compute_type", "int8"),
                cpu_threads=int(stt_cfg.get("cpu_threads", 8)),
                beam_size=int(stt_cfg.get("beam_size", 1)),
                hotwords=str(stt_cfg.get("hotwords", "")),
            )
        self.stt.load()
        return ""

    def _load_tts(self) -> str:
        if self.tts is None:
            from voice.tts import TextToSpeech

            tts_cfg = self.cfg.get("tts", {})
            self.tts = TextToSpeech(
                model_path=tts_cfg.get("model", "models/kokoro-v1.0.onnx"),
                voices_path=tts_cfg.get("voices", "models/voices-v1.0.bin"),
                voice_en=tts_cfg.get("voice_en", "af_heart"),
                voice_hi=tts_cfg.get("voice_hi", "hf_beta"),
                speed=float(tts_cfg.get("speed", 1.05)),
            )
        self.tts.load()
        return ""

    def _load_verifier(self) -> str:
        sv_cfg = self.cfg.get("speaker_verify", {})
        if not sv_cfg.get("enabled", True):
            return "disabled in config"
        if self.verifier is None:
            from voice.speaker import SpeakerVerifier

            self.verifier = SpeakerVerifier(
                model_path=sv_cfg.get("model", "models/wespeaker_en_voxceleb_CAM++.onnx"),
                profile_path=sv_cfg.get("profile", "models/owner_voice.json"),
                threshold=float(sv_cfg.get("threshold", 0.5)),
            )
        if not self.verifier.enabled:
            return self.verifier.load()
        return self.verifier.note

    def start(self) -> None:
        """Bridge coroutine on the loop + state-machine thread + hotkey."""
        self._bridge_future = asyncio.run_coroutine_threadsafe(self._bridge(), self.loop)
        self._thread = threading.Thread(target=self.run, name="baby-voice", daemon=True)
        self._thread.start()
        combo = self.cfg.get("push_to_talk_hotkey", "ctrl+alt+b")
        if combo:
            from voice.hotkey import PushToTalk

            self._hotkey = PushToTalk(combo, self._ptt.set)
            if not self._hotkey.start():
                self._publish("status", text=f"voice: hotkey {combo} unavailable (in use?)")

    def stop(self) -> None:
        self._stopping.set()
        if self._hotkey is not None:
            self._hotkey.stop()
        if self._bridge_future is not None:
            self._bridge_future.cancel()
        if self.audio is not None:
            self.audio.close()

    # -- cross-thread primitives ------------------------------------------------

    def _publish(self, kind: str, **payload) -> None:
        self.loop.call_soon_threadsafe(
            functools.partial(self.bus.publish, kind, "voice", **payload)
        )

    def _submit(self, text: str):
        return asyncio.run_coroutine_threadsafe(self.agent.run_turn(text), self.loop)

    def _cancel_turn(self) -> None:
        if self._turn_future is not None and not self._turn_future.done():
            self._turn_future.cancel()

    def _toggle_game_mode(self, on: bool) -> None:
        """Deterministic voice toggle — runs even when every brain is down."""
        provider = getattr(self.agent, "provider", None)
        if not hasattr(provider, "set_game_mode"):
            self.announce("Game mode needs the cloud router.")
            return
        future = asyncio.run_coroutine_threadsafe(provider.set_game_mode(on), self.loop)
        try:
            line = future.result(timeout=30)
        except Exception as exc:  # noqa: BLE001 — a failed toggle must be spoken, not silent
            line = f"game mode toggle failed: {type(exc).__name__}"
        self._publish("status", text=f"voice: {line}")
        self.announce(
            "Game mode on. The cloud answers now."
            if on else "Game mode off. Reloading my local brain."
        )
        self._turn_future = None

    def _drain_sentences(self) -> None:
        while True:
            try:
                self.sentence_q.get_nowait()
            except queue.Empty:
                return

    # -- bridge (runs on the asyncio loop) ---------------------------------------

    async def _bridge(self) -> None:
        """Sole producer of sentence_q: voice-channel bus events → sentences."""
        q = self.bus.subscribe()
        try:
            while True:
                event = await q.get()
                if event.channel != "voice":
                    continue
                if event.kind == "token":
                    self._text_buf += event.payload.get("text", "")
                    sentences, self._text_buf = split_sentences(self._text_buf)
                    for sentence in sentences:
                        self.sentence_q.put(sentence)
                elif event.kind == "confirm_request":
                    self.sentence_q.put(CONFIRM_SENTENCE)
                elif event.kind == "turn_end":
                    if event.payload.get("status") == "ok":
                        sentences, self._text_buf = split_sentences(self._text_buf, final=True)
                        for sentence in sentences:
                            self.sentence_q.put(sentence)
                    else:  # cancelled/error: never speak a stale buffer
                        self._text_buf = ""
                    self.sentence_q.put(None)
        except asyncio.CancelledError:
            self.bus.unsubscribe(q)
            raise

    # -- state machine (runs on the voice thread) ---------------------------------

    def run(self) -> None:
        from voice.audio_io import FrameBuffer

        frames = FrameBuffer()
        while not self._stopping.is_set():
            try:
                if self.state == IDLE:
                    self._idle(frames)
                elif self.state == LISTENING:
                    self._listen(frames)
                elif self.state == RESPONDING:
                    self._respond(frames)
            except Exception as exc:  # noqa: BLE001 — pipeline must survive device hiccups
                self._publish("error", text=f"voice pipeline: {type(exc).__name__}: {exc}")
                time.sleep(1.0)
                self.state = IDLE

    def announce(self, text: str) -> None:
        """Thread-safe: queue text to be spoken next time the pipeline is idle."""
        try:
            self.announce_q.put_nowait(text)
        except queue.Full:
            self._publish("status", text="voice: announcement dropped (queue full)")

    def _play_announcement(self) -> bool:
        """Speak one queued announcement (IDLE only). True if one played."""
        try:
            text = self.announce_q.get_nowait()
        except queue.Empty:
            return False
        try:
            pcm, sample_rate = self.tts.synth(text)
            self.audio.play(pcm, sample_rate, threading.Event())
            self._publish("status", text=f"voice: announced {text!r}")
        except Exception as exc:  # noqa: BLE001 — a failed announcement must not kill the loop
            self._publish("error", text=f"voice announce: {type(exc).__name__}: {exc}")
        return True

    def _idle(self, frames) -> None:
        if self._play_announcement():
            self.audio.drain()  # mic heard the announcement — don't wake on it
            frames.clear()
            return
        if self._ptt.is_set():
            self._ptt.clear()
            self._enter_listening(frames, source="ptt")
            return
        frame = self.audio.read(timeout=0.2)
        if frame is None:
            return
        frames.push(frame)
        while (chunk := frames.pop(_WAKE_CHUNK)) is not None:
            if self.wake.detected(chunk):
                self._enter_listening(frames)
                return

    def _enter_listening(self, frames, source: str = "wake") -> None:
        # PTT bypasses speaker verification: whoever pressed the hotkey is at
        # the keyboard and already owns the PC.
        self._listen_source = source
        self.audio.beep()
        self.audio.drain()
        frames.clear()
        self.vad.reset()
        self.state = LISTENING
        self._publish("status", text="voice: listening")

    def _listen(self, frames) -> None:
        import numpy as np

        recorded: list = []
        deadline = time.monotonic() + self.max_utterance_s
        while not self._stopping.is_set():
            if time.monotonic() > deadline:
                break
            frame = self.audio.read(timeout=0.5)
            if frame is None:
                continue
            frames.push(frame)
            done = False
            while (chunk := frames.pop(_VAD_FRAME)) is not None:
                recorded.append(chunk)
                if self.vad.utterance_done(chunk):
                    done = True
                    break
            if done:
                break

        self.state = IDLE
        # No speech at all (false wake, or the user stayed quiet): skip the
        # expensive STT call on pure silence and say so in the feed.
        if not recorded or not getattr(self.vad, "speech_started", True):
            self._publish("status", text="voice: heard nothing")
            return
        pcm = np.concatenate(recorded)
        text, lang = self.stt.transcribe(pcm)
        if not text:
            self._publish("status", text="voice: heard nothing")
            return
        if is_kill_phrase(text, self.kill_phrases):
            self._cancel_turn()
            self._drain_sentences()
            self._publish("status", text="voice: stopped by kill phrase")
            return
        # Game-mode escape hatch: a bare "game mode on/off" toggles directly,
        # no model in the loop — in game mode with the cloud down there is NO
        # brain left to call the set_game_mode tool (observed live deadlock).
        from tools.game import parse_game_command

        game = parse_game_command(text)
        if game is not None:
            self._toggle_game_mode(game)
            return
        # Speaker verification (Phase 5) — AFTER the kill phrase (anyone may
        # stop Baby) and never for PTT. chat_only turns get their tools denied
        # at the gate; ignore mode drops the utterance entirely.
        verified = True
        if (
            self.verifier is not None
            and getattr(self.verifier, "enabled", False)
            and self._listen_source != "ptt"
        ):
            try:
                verified, similarity = self.verifier.verify(pcm)
            except Exception as exc:  # noqa: BLE001 — a broken check must not lock the owner out
                self._publish(
                    "error",
                    text=f"voice: speaker check failed ({type(exc).__name__}: {exc})",
                )
                verified = True
            else:
                if not verified:
                    if self.sv_mode == "ignore":
                        self._publish(
                            "status",
                            text=f"voice: unknown speaker ignored "
                            f"(similarity {similarity:.2f})",
                        )
                        return
                    self._publish(
                        "status",
                        text=f"voice: speaker not recognized "
                        f"(similarity {similarity:.2f}) — chat only",
                    )
        self._set_verified(verified)
        self._publish("status", text=f"voice: heard {text!r}")
        self._turn_future = self._submit(text)
        self.state = RESPONDING

    def _set_verified(self, verified: bool) -> None:
        """Flag the voice channel before the turn starts (same loop-FIFO as
        _submit, so the gate always sees the flag first)."""
        gate = getattr(self.agent, "gate", None)
        if gate is None:
            return
        channel = getattr(self.agent, "channel", "voice")
        self.loop.call_soon_threadsafe(gate.set_voice_verified, channel, verified)

    def _respond(self, frames) -> None:
        stop_playback = threading.Event()
        speech_frames = 0
        while not self._stopping.is_set():
            try:
                sentence = self.sentence_q.get(timeout=0.1)
            except queue.Empty:
                sentence = _NOTHING
            if sentence is None:  # end-of-turn sentinel
                self._turn_future = None
                self.state = IDLE
                return
            if sentence is not _NOTHING:
                pcm, sample_rate = self.tts.synth(sentence)
                player = threading.Thread(
                    target=self.audio.play,
                    args=(pcm, sample_rate, stop_playback),
                    daemon=True,
                )
                player.start()
                while player.is_alive():
                    speech_frames = self._barge_in_step(frames, speech_frames, stop_playback)
                    if stop_playback.is_set():
                        player.join(timeout=1.0)
                        self._cancel_turn()
                        self._drain_sentences()
                        self._publish("status", text="voice: interrupted")
                        self._enter_listening(frames)
                        return
                continue
            # queue empty: keep monitoring the mic between sentences
            speech_frames = self._barge_in_step(frames, speech_frames, stop_playback)
            if stop_playback.is_set():
                self._cancel_turn()
                self._drain_sentences()
                self._publish("status", text="voice: interrupted")
                self._enter_listening(frames)
                return

    def _barge_in_step(self, frames, speech_frames: int, stop_playback) -> int:
        """One mic poll during playback; sets stop on sustained speech."""
        if not self.barge_in:
            return 0
        frame = self.audio.read(timeout=0.05)
        if frame is None:
            return speech_frames
        frames.push(frame)
        while (chunk := frames.pop(_VAD_FRAME)) is not None:
            if self.vad.is_speech(chunk, self.barge_in_threshold):
                speech_frames += 1
                if speech_frames >= _BARGE_IN_FRAMES:
                    stop_playback.set()
                    return speech_frames
            else:
                speech_frames = 0
        return speech_frames


class _Nothing:
    """Sentinel distinct from None (None = end-of-turn)."""


_NOTHING = _Nothing()
