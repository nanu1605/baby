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
import collections
import concurrent.futures
import functools
import json
import queue
import re
import threading
import time
from pathlib import Path

from core.intents import is_end_phrase
from core.prompts import detect_language
from voice.amplitude import Throttle, quantize_level, rms_int16
from voice.tts import ends_sentence, split_sentences

_PUNCT_RE = re.compile(r"[^\w\s]|[_।]", re.UNICODE)

# Kill phrases match only in SHORT transcripts: "baby stop" inside a long
# dictated sentence must not halt the assistant.
_MAX_KILL_WORDS = 5

CONFIRM_SENTENCE = "I need a confirmation for that — check the screen."

IDLE, LISTENING, RESPONDING = "idle", "listening", "responding"

_VAD_FRAME = 512
_WAKE_CHUNK = 1280
_BARGE_IN_FRAMES = 6  # ~200 ms of sustained speech
# ponytail: a guess. Long enough that a model mid-"12.5" has sent the "5"
# (tokens arrive tens of ms apart), short enough not to be heard as a pause.
_QUIET_FLUSH_S = 0.25
# A new turn waits this long for the one before it to finish unwinding.
# ponytail: a turn that ignores cancellation for longer can still put tokens
# into the next turn's queue. None does today (tools and the safety gate
# re-raise CancelledError); tag bus events with a turn id if one ever does.
_UNWIND_WAIT_S = 5.0
_DEAD_TURN_S = 1.0  # the turn is over and silent, but its end marker never came
_READY_AHEAD = 2  # sentences synthesised and waiting while one plays
_SYNTH_WAIT_S = 30.0  # an announcement gives up on Kokoro after this (~0.6 s is normal)

# Spoken when a turn ends in an error: silence reads as a dead assistant.
APOLOGY_EN = "Sorry, something went wrong. Please ask me again."
APOLOGY_HI = "माफ़ कीजिए, कुछ गड़बड़ हो गई। फिर से पूछिए।"

# Per-turn timing, reported as seconds since the user stopped speaking. The
# labels are what the feed line shows, and none of them may contain a word the
# UI matches on status text, or a timing line would move the orb or raise a
# toast: "listening", "stopped", "interrupted", "heard", "conversation ended"
# (ui/server.py _StateDeriver, the graph's edge map) and "wiped", "kill",
# "error", "cancelled", "offline", "degraded" (useActivitySocket TOAST_STATUS).
_TIMING_LABELS = (
    ("stt_done", "stt"),
    ("submitted", "request"),
    ("first_token", "token"),
    ("first_sentence", "sentence"),
    ("tts_start", "tts"),
    ("first_audio", "audio"),
)


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
        self.sv_mode = str(sv_cfg.get("mode", "chat_only"))  # chat_only | ignore | observe
        # B6: per-session trust smoother over utterance scores (optimistic-demote).
        from voice.speaker import SessionTrust

        self.session_trust = SessionTrust(
            accept=float(sv_cfg.get("accept_threshold", 0.62)),
            reject=float(sv_cfg.get("reject_threshold", 0.45)),
            window=int(sv_cfg.get("smoothing_window", 5)),
            demote_after=int(sv_cfg.get("demote_after", 2)),
        )
        self._listen_source = "wake"  # wake | ptt — PTT bypasses verification

        self.state = IDLE
        # One queue PER TURN (_submit makes a new one): sentences, then None.
        # A turn that was cut off can only write into a queue nobody reads, so
        # none of its speech can play after the interruption.
        self.sentence_q: queue.Queue = queue.Queue()
        # Bridge state, asyncio-loop thread only.
        self._out_q = self.sentence_q  # the queue of the turn being read
        self._next_q: queue.Queue | None = None  # a submitted turn, not yet started
        self._live_turn = None  # turn_id the bridge adopted with _out_q
        self._turn_task: asyncio.Task | None = None  # the latest voice turn
        self._heard_hindi = False  # script of the last transcript (apology language)
        # Kokoro runs on one worker thread, not the voice thread, so the mic is
        # polled while it works and sentence N+1 is made while N plays. One
        # worker: Kokoro is never asked for two things at once. A daemon, unlike
        # a ThreadPoolExecutor's, so a shutdown never waits on a sentence.
        self.prefetch = bool((cfg.get("tts") or {}).get("prefetch", True))
        self._synth_jobs: queue.Queue = queue.Queue()
        self._synth_thread: threading.Thread | None = None
        # Out-of-band announcements (task done, briefing). Spoken only while
        # IDLE — a live conversation always wins; overflow gets dropped.
        self.announce_q: queue.Queue = queue.Queue(maxsize=5)
        self.kill_phrases = tuple(cfg.get("kill_phrases", ("baby stop", "baby ruk ja")))
        self.max_utterance_s = float(cfg.get("max_utterance_s", 30))
        # Grace to START speaking after the wake beep. The VAD's own short
        # pre-speech timeout (vad_speech_wait_ms) used to close a fresh wake
        # in ~5 s ("heard nothing" before the user finished asking) — the
        # pipeline now owns this window so wake and follow-up feel the same.
        self.listen_grace_s = float(cfg.get("listen_grace_s", 10))
        self.barge_in = bool(cfg.get("barge_in", True))
        self.barge_in_threshold = float(cfg.get("barge_in_threshold", 0.6))
        # P3 conversation mode: after Baby speaks, keep the mic hot for a
        # wake-word-free follow-up until an end phrase or window_s of silence.
        conv = cfg.get("conversation", {})
        self.conversation_enabled = bool(conv.get("enabled", True))
        self.conversation_window_s = float(conv.get("window_s", 60))
        self.conversation_cue = bool(conv.get("cue", True))
        self.end_phrases = tuple(
            conv.get(
                "end_phrases",
                ("baby stop listening", "stop listening", "bas", "bas baby", "so jao"),
            )
        )

        self._text_buf = ""
        self._token_at = 0.0  # monotonic time of the last voice token (bridge)
        self._turn_future = None
        # Monotonic stamps for the turn in flight (see _TIMING_LABELS). The voice
        # thread and the bridge write different keys; one line per turn reports them.
        self._timing: dict[str, float] = {}
        self._vad_silence_s = float(cfg.get("vad_silence_ms", 400)) / 1000.0
        self._stopping = threading.Event()
        # Kill switch: force-stop live playback + the running turn (the UI /kill
        # cancelled the turn future but Baby kept speaking the already-queued
        # sentences — observed live). Set cross-thread, honored in _respond.
        self._interrupt = threading.Event()
        self._ptt = threading.Event()
        self._thread: threading.Thread | None = None
        self._bridge_future = None
        self._hotkey = None
        # V3e honest amplitude: mic RMS during listening throttled to ~15 Hz (the
        # bus has no rate-limit) → mic_rms on /ws/activity. TTS RMS rides the
        # audio_io.play on_level callback (naturally ~10 Hz). Additive only.
        self._mic_throttle = Throttle(hz=15.0)

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
            self._unwind_partial_load()
            return False, notes
        return True, notes

    def _unwind_partial_load(self) -> None:
        """Close whatever a failed load had already opened.

        The mic is step ONE, and it opens a PortAudio InputStream whose callback is
        a cffi trampoline holding a reference to this pipeline. Any later step can
        fail -- on a first install the wake-word models have not been downloaded
        yet, so _load_wake raises NoSuchFile -- and this used to return straight to
        run_ui, which drops the pipeline on the floor. The stream was then left to
        the garbage collector, with PortAudio's real-time thread still calling a
        callback whose Python side was being freed underneath it.

        That took the whole backend down with an access violation in a thread with
        no Python frame (0xC0000005, faulting module "unknown") -- minutes later,
        during the next allocation-heavy phase, which happened to be the embedder
        load. Two fresh installs, same stack, and neither one looked like voice.

        Only the mic holds a native callback; the model stages are plain objects
        with nothing to release.
        """
        if self.audio is not None:
            try:
                self.audio.close()
            except Exception:  # noqa: BLE001 — tearing down an already-failed load
                pass

    def _load_audio(self) -> str:
        if self.audio is None:
            from voice.audio_io import AudioIO

            self.audio = AudioIO()
        self.audio.start()
        return ""

    def _load_wake(self) -> str:
        if self.wake is None:
            from core import paths
            from voice.wakeword import WakeWord

            model_path = paths.resolve_model(self.cfg.get("wakeword_model", "models/jarvis.onnx"))
            self.wake = WakeWord(
                model_path=str(model_path),
                threshold=float(self.cfg.get("wakeword_threshold", 0.55)),
                builtin_fallback=self.cfg.get("wakeword_builtin_fallback", "hey_jarvis"),
                extra_models=self.cfg.get("wakeword_models", []),
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
                cpu_threads=int(stt_cfg.get("cpu_threads", 4)),
                beam_size=int(stt_cfg.get("beam_size", 1)),
                hotwords=str(stt_cfg.get("hotwords", "")),
            )
        self.stt.load()
        return ""

    def _load_tts(self) -> str:
        if self.tts is None:
            from core import paths
            from voice.tts import TextToSpeech

            tts_cfg = self.cfg.get("tts", {})
            model_path = paths.resolve_model(tts_cfg.get("model", "models/kokoro-v1.0.onnx"))
            voices_path = paths.resolve_model(tts_cfg.get("voices", "models/voices-v1.0.bin"))
            self.tts = TextToSpeech(
                model_path=str(model_path),
                voices_path=str(voices_path),
                voice_en=tts_cfg.get("voice_en", "af_heart"),
                voice_hi=tts_cfg.get("voice_hi", "hf_beta"),
                speed=float(tts_cfg.get("speed", 1.05)),
                cpu_threads=int(tts_cfg.get("cpu_threads", 4)),
            )
        self.tts.load()
        return ""

    def _load_verifier(self) -> str:
        sv_cfg = self.cfg.get("speaker_verify", {})
        if not sv_cfg.get("enabled", True):
            return "disabled in config"
        from core import paths

        model = str(
            paths.resolve_model(sv_cfg.get("model", "models/wespeaker_en_voxceleb_CAM++.onnx"))
        )
        profile = paths.resolve_model(sv_cfg.get("profile", "models/owner_voice.json"))
        if self.verifier is None:
            from voice.speaker import SpeakerVerifier

            self.verifier = SpeakerVerifier(
                model_path=model,
                profile_path=str(profile),
                threshold=float(sv_cfg.get("threshold", 0.5)),
                centroids=self._load_db_centroids(model),
            )
        if not self.verifier.enabled:
            return self.verifier.load()
        return self.verifier.note

    def _load_db_centroids(self, model: str) -> list | None:
        """Fetch the owner's DB centroids for `model` (B6). Runs on the voice/worker
        thread (load() is dispatched via asyncio.to_thread), so bouncing the async DB
        read onto the loop is deadlock-safe. Returns None on any miss/failure so the
        verifier falls back to the v1 JSON profile."""
        db = getattr(self.agent, "db", None)
        if db is None:
            return None
        try:
            from voice.speaker import unpack_vector

            rows = asyncio.run_coroutine_threadsafe(
                db.speaker_centroids("owner", Path(model).name), self.loop
            ).result(timeout=5)
            return [unpack_vector(r["centroid"]) for r in rows] or None
        except Exception:  # noqa: BLE001 — DB miss/timeout falls back to JSON, never crashes
            return None

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

    def _emit_tts_level(self, level: float) -> None:
        """audio_io.play on_level callback (player thread) → honest tts_rms.
        _publish is thread-safe, so publishing from the player thread is fine."""
        self._publish("tts_rms", rms=quantize_level(level))

    def _emit_reply_level(self, level: float) -> None:
        """on_level for a sentence of the reply: the first call is the first
        ~100 ms block actually written to the speaker, which is what "audio"
        means in the timing line -- stamping before the player thread starts
        would leave out opening the output device."""
        self._mark("first_audio")
        self._emit_tts_level(level)

    def _submit(self, text: str):
        self.sentence_q = turn_q = queue.Queue()
        return asyncio.run_coroutine_threadsafe(self._run_turn(text, turn_q), self.loop)

    async def _run_turn(self, text: str, turn_q: queue.Queue) -> str:
        """Run one voice turn, after the one before it has finished unwinding.

        The bus is FIFO and the bridge adopts `turn_q` when it reads this
        turn's turn_start. Waiting for the previous turn means every event it
        published -- its late tokens, its cancelled turn_end -- is already
        ahead of that turn_start in the queue, and so lands in the old turn_q.
        """
        previous, self._turn_task = self._turn_task, asyncio.current_task()
        if previous is not None and not previous.done():
            await asyncio.wait({previous}, timeout=_UNWIND_WAIT_S)
        self._next_q = turn_q
        return await self.agent.run_turn(text)

    def _synth(self, text: str):
        """Kokoro through the one synth worker, so an announcement never runs
        beside a sentence still being made for a turn that was just cut off.

        Bounded: this runs on the voice thread while IDLE, and a synthesis that
        never came back would leave Baby deaf to the wake word for good.
        """
        return self._synth_later(text).result(timeout=_SYNTH_WAIT_S)

    def _synth_later(self, text: str) -> concurrent.futures.Future:
        """Queue one synthesis on the worker (started on first use)."""
        if self._synth_thread is None:
            self._synth_thread = threading.Thread(
                target=self._synth_worker, name="baby-tts", daemon=True
            )
            self._synth_thread.start()
        future: concurrent.futures.Future = concurrent.futures.Future()
        self._synth_jobs.put((future, text))
        return future

    def _synth_worker(self) -> None:
        while True:
            future, text = self._synth_jobs.get()
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(self.tts.synth(text))
            except BaseException as exc:  # noqa: BLE001 — handed to whoever waits on it
                future.set_exception(exc)

    def _cancel_turn(self) -> None:
        if self._turn_future is not None and not self._turn_future.done():
            self._turn_future.cancel()

    def cancel_turn(self) -> None:
        """Public: the UI kill switch must reach VOICE turns too (observed
        live: a stalled voice turn survived /kill because only the UI-tracked
        task was cancelled). Force-stops the running turn AND any in-flight
        speech — Baby used to keep speaking the already-queued sentences after
        /kill because only the turn future was cancelled, not playback."""
        self._interrupt.set()
        self._cancel_turn()
        self._drain_sentences()

    def turn_running(self) -> bool:
        return self._turn_future is not None and not self._turn_future.done()

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

    # -- per-turn timing ---------------------------------------------------------

    def _mark(self, key: str) -> None:
        """Stamp the first time `key` happens in the turn in flight."""
        self._timing.setdefault(key, time.monotonic())

    def _report_timing(self, cut: float | None = None) -> None:
        """One feed line and one audit row for the turn that just ended.

        Every figure is seconds since the user stopped speaking. `cut` is set
        when the user interrupted: seconds from detecting their speech to the
        speaker going quiet.
        """
        timing, self._timing = self._timing, {}
        start = timing.get("speech_end")
        if start is None:
            return
        parts = {
            label: round(timing[key] - start, 2)
            for key, label in _TIMING_LABELS
            if key in timing
        }
        if cut is None:
            parts["end"] = round(time.monotonic() - start, 2)
        else:
            parts["cut"] = round(cut, 2)
        line = " · ".join(f"{label} {value:.1f}s" for label, value in parts.items())
        self._publish("status", text=f"voice: timing {line}")
        db = getattr(self.agent, "db", None)
        if db is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                db.add_audit(
                    getattr(self.agent, "channel", "voice"),
                    "voice_timing",
                    json.dumps(parts),
                    "allow",
                    1,
                    line,
                ),
                self.loop,
            )
        except Exception:  # noqa: BLE001 — audit logging is best-effort
            pass

    # -- bridge (runs on the asyncio loop) ---------------------------------------

    async def _bridge(self) -> None:
        """Sole producer of sentence_q: voice-channel bus events → sentences."""
        q = self.bus.subscribe()
        try:
            while True:
                try:
                    # Timed only on an empty queue: once the deadline has passed
                    # wait_for(…, 0) raises without looking, and the token that
                    # completes "12." into "12.5" may already be waiting.
                    wait = self._quiet_wait() if q.empty() else None
                    event = await asyncio.wait_for(q.get(), wait)
                except TimeoutError:
                    # The model stopped on a full stop and nothing followed (the
                    # next-step call has not started): say what is there.
                    if not q.empty():
                        continue
                    event = None
                try:
                    if event is None:
                        self._queue_sentences(final=True)
                    elif event.channel == "voice":
                        self._on_voice_event(event)
                except Exception as exc:  # noqa: BLE001 — one bad event must not end voice
                    # This coroutine is the only thing feeding speech: if it
                    # dies, Baby goes silent for good and nothing says why.
                    self._text_buf = ""
                    self.bus.publish(
                        "status", "voice",
                        text=f"voice: could not queue speech ({type(exc).__name__})",
                    )
        except asyncio.CancelledError:
            self.bus.unsubscribe(q)
            raise

    def _on_voice_event(self, event) -> None:
        kind = event.kind
        if kind == "turn_start":
            if self._next_q is not None:
                self._out_q, self._next_q = self._next_q, None
            self._live_turn = event.payload.get("turn_id")
            self._text_buf = ""
        elif kind == "token":
            self._mark("first_token")
            self._text_buf += event.payload.get("text", "")
            self._token_at = time.monotonic()
            self._queue_sentences()
        elif kind == "tool_start":
            # Published once the generation that asked for the tool has
            # finished, so whatever is buffered is complete: say it now. Waiting
            # for the quiet timer instead glued "Let me check." to the answer
            # ("check.It is noon.") whenever the tool was quick, and let the
            # confirm line below overtake it.
            self._queue_sentences(final=True)
        elif kind == "confirm_request":
            self._queue_sentences(final=True)
            self._out_q.put(CONFIRM_SENTENCE)
        elif kind == "turn_end":
            if event.payload.get("turn_id") != self._live_turn:
                return  # a turn cut off earlier, finishing late: not this one's end
            status = event.payload.get("status")
            if status == "ok":
                self._queue_sentences(final=True)
            elif status == "capped":
                # The tool-limit message is never streamed as tokens.
                self._text_buf = str(event.payload.get("reply") or "")
                self._queue_sentences(final=True)
            else:  # error or cancelled: never speak a half-written buffer
                self._text_buf = ""
                if status == "error":
                    self._out_q.put(APOLOGY_HI if self._heard_hindi else APOLOGY_EN)
            self._out_q.put(None)

    def _queue_sentences(self, final: bool = False) -> None:
        sentences, self._text_buf = split_sentences(self._text_buf, final=final)
        for sentence in sentences:
            self._mark("first_sentence")
            self._out_q.put(sentence)

    def _quiet_wait(self) -> float | None:
        """How long the bridge waits before flushing the buffer; None = forever.

        A full stop only ends a sentence once the next character arrives
        (split_sentences), so text that stops on one -- "Let me check." before a
        tool call -- would be held until the tool finished. Counted from the last
        token, not the last event: tts_rms arrives every 100 ms while Baby
        speaks and would otherwise keep pushing the flush away.
        """
        if not ends_sentence(self._text_buf):
            return None
        return max(0.0, self._token_at + _QUIET_FLUSH_S - time.monotonic())

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
            pcm, sample_rate = self._synth(text)
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
        # B6: a fresh wake/PTT starts a new conversation session — reset the trust
        # smoother to optimistic. Follow-ups keep accumulating within the session.
        if source != "followup":
            self.session_trust.reset()
        # Wake/PTT always beeps; a follow-up window beeps only if the cue is on.
        if source != "followup" or self.conversation_cue:
            self.audio.beep()
        self.audio.drain()  # never transcribe Baby's own just-played speech
        frames.clear()
        self._drain_sentences()  # drop any stale sentinel from a cancelled turn
        self.vad.reset()
        self.state = LISTENING
        followup = source == "followup"
        self._publish("status", text="voice: listening" + (" (follow-up)" if followup else ""))

    def _listen(self, frames) -> None:
        import numpy as np

        recorded: list = []
        # Grace to START speaking: a follow-up waits the full conversation
        # window, a fresh wake waits listen_grace_s. Both ignore the VAD's own
        # short pre-speech timeout (which used to close a wake in ~5 s before
        # the user finished asking) — the pipeline owns the window here, and
        # only silence AFTER speech ends the capture.
        followup = self._listen_source == "followup"
        grace = self.conversation_window_s if followup else self.listen_grace_s
        speech_deadline = time.monotonic() + grace
        hard_deadline = speech_deadline + self.max_utterance_s  # cap a runaway
        done = False
        while not self._stopping.is_set():
            now = time.monotonic()
            if not getattr(self.vad, "speech_started", False) and now > speech_deadline:
                break  # nobody spoke within the grace window
            if now > hard_deadline:
                break
            frame = self.audio.read(timeout=0.5)
            if frame is None:
                continue
            frames.push(frame)
            done = False
            while (chunk := frames.pop(_VAD_FRAME)) is not None:
                recorded.append(chunk)
                # Honest listening ripple: publish the real mic RMS (~15 Hz).
                if self._mic_throttle.ready(time.monotonic()):
                    self._publish("mic_rms", rms=quantize_level(rms_int16(chunk)))
                if self.vad.utterance_done(chunk):
                    if not getattr(self.vad, "speech_started", False):
                        recorded.clear()  # was all pre-speech silence — keep waiting
                        break
                    done = True
                    break
            if done:
                break

        # The VAD only reports the end once its silence window has passed, so
        # the user actually stopped that long ago -- the wait they feel starts there.
        speech_end = time.monotonic() - (self._vad_silence_s if done else 0.0)
        self.state = IDLE
        # No speech at all (false wake, or the user stayed quiet): skip the
        # expensive STT call on pure silence and say so in the feed. In a
        # follow-up window, silence is how the conversation ends.
        if not recorded or not getattr(self.vad, "speech_started", True):
            if followup:
                self._session_cue()
                self._publish("status", text="voice: conversation ended (silence)")
            else:
                self._publish("status", text="voice: heard nothing")
            return
        pcm = np.concatenate(recorded)
        text, lang = self.stt.transcribe(pcm)
        stt_done = time.monotonic()
        if not text:
            if followup:
                self._session_cue()
                self._publish("status", text="voice: conversation ended (silence)")
            else:
                self._publish("status", text="voice: heard nothing")
            return
        # Conversation end phrase ("baby stop listening", "bas") cleanly closes
        # the follow-up window. Checked BEFORE the kill phrase so "baby stop
        # listening" ends the session as a session-close, not as a mid-turn kill
        # (the kill phrase "baby stop" would otherwise shadow it).
        if followup and is_end_phrase(text, self.end_phrases):
            self._session_cue()
            self._publish("status", text="voice: conversation ended")
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
        # Speaker verification (Phase 5 / v2 B6) — AFTER the kill phrase (anyone
        # may stop Baby) and never for PTT. Per-utterance scores feed a session
        # trust smoother (optimistic-demote): only a confident non-owner (tier
        # UNKNOWN) loses tools. chat_only enforces (tools denied at the gate),
        # ignore drops the utterance, observe scores+logs but never enforces.
        verified = True
        if (
            self.verifier is not None
            and getattr(self.verifier, "enabled", False)
            and self._listen_source != "ptt"
        ):
            from voice.speaker import SessionTrust

            try:
                _, similarity = self.verifier.verify(pcm)
            except Exception as exc:  # noqa: BLE001 — a broken check must not lock the owner out
                self._publish(
                    "error",
                    text=f"voice: speaker check failed ({type(exc).__name__}: {exc})",
                )
                verified = True
            else:
                tier = self.session_trust.update(similarity)
                enforce = self.sv_mode != "observe"
                verified = tier != SessionTrust.UNKNOWN or not enforce
                self._log_speaker(similarity, tier, verified)
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
                        f"(trust {tier}, similarity {similarity:.2f}) — chat only",
                    )
        self._set_verified(verified)
        self._publish("status", text=f"voice: heard {text!r}")
        # A kill switch pressed while idle must not abort this fresh turn, and a
        # stale sentinel from a cancelled prior turn must not end it early.
        self._interrupt.clear()
        self._drain_sentences()
        self._timing = {
            "speech_end": speech_end,
            "stt_done": stt_done,
            "submitted": time.monotonic(),
        }
        self._heard_hindi = detect_language(text) == "Hindi"
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

    def _log_speaker(self, similarity: float, tier: str, verified: bool) -> None:
        """Fire-and-forget audit row per scored utterance (B6) — the sole FAR/FRR
        data source for scripts/speaker_report.py. Mirrors the router's overloaded
        add_audit('router', ...) pattern; logging must never break the turn."""
        db = getattr(self.agent, "db", None)
        if db is None:
            return
        channel = getattr(self.agent, "channel", "voice")
        model = Path(self.cfg.get("speaker_verify", {}).get("model", "")).name
        payload = json.dumps(
            {
                "score": round(float(similarity), 4),
                "tier": tier,
                "decision": bool(verified),
                "model": model,
                "source": self._listen_source,
                "mode": self.sv_mode,
            }
        )
        try:
            asyncio.run_coroutine_threadsafe(
                db.add_audit(
                    channel,
                    "speaker_verify",
                    payload,
                    "allow",
                    1 if verified else 0,
                    f"trust={tier}",
                ),
                self.loop,
            )
        except Exception:  # noqa: BLE001 — audit logging is best-effort
            pass

    def _respond(self, frames) -> None:
        """Speak this turn's sentences as they arrive, watching the mic throughout.

        Kokoro runs on the synth worker, so sentence N+1 is being made while N
        plays, and this loop never stops polling for a barge-in or the kill
        switch -- it used to block for the length of every synthesis. All the
        prefetched audio is local to this call: returning discards it.
        """
        turn_q = self.sentence_q
        stop_playback = threading.Event()
        speech_frames = 0
        ready: collections.deque = collections.deque()  # (pcm, rate, is_reply), in order
        inflight = None  # (future, is_reply) for the sentence being synthesised
        player: threading.Thread | None = None
        ended = False  # the end-of-turn marker was read: nothing more will come
        dead_since: float | None = None
        while not self._stopping.is_set():
            # Kill switch: stop everything now — no follow-up window, unlike a
            # barge-in or a clean turn end.
            if self._interrupt.is_set():
                self._abort_speaking(stop_playback, player)
                return

            if inflight is not None and inflight[0].done():
                (future, reply), inflight = inflight, None
                try:
                    pcm, sample_rate = future.result()
                    ready.append((pcm, sample_rate, reply))
                except Exception as exc:  # noqa: BLE001 — one bad sentence must not end the reply
                    self._publish(
                        "status", text=f"voice: skipped a sentence ({type(exc).__name__})"
                    )

            # Reaped before the gate below: with prefetch off it waits for the
            # player to be gone, and a dead one would cost a whole extra pass.
            if player is not None and not player.is_alive():
                player = None

            if inflight is None and not ended and self._may_synthesise(ready, player):
                try:
                    sentence = turn_q.get_nowait()
                except queue.Empty:
                    pass
                else:
                    if sentence is None:
                        ended = True
                    else:
                        # The canned "check the screen" line is not the model's
                        # reply: if it took the tts/audio stamps they would land
                        # before the first token, and the reply's own would
                        # never be recorded.
                        reply = sentence is not CONFIRM_SENTENCE
                        if reply:
                            self._mark("tts_start")
                        inflight = (self._synth_later(sentence), reply)

            if player is None and ready:
                pcm, sample_rate, reply = ready.popleft()
                player = threading.Thread(
                    target=self._play, args=(pcm, sample_rate, stop_playback, reply), daemon=True
                )
                player.start()

            busy = inflight is not None or bool(ready) or player is not None
            if ended and not busy:
                self._end_turn(frames, stop_playback)
                return

            # One mic poll: barge-in, and the pause that paces this loop.
            speech_frames = self._barge_in_step(frames, speech_frames, stop_playback)
            if stop_playback.is_set():
                heard_at = time.monotonic()
                if player is not None:
                    player.join(timeout=1.0)
                self._cancel_turn()
                self._drain_sentences()
                self._publish("status", text="voice: interrupted")
                self._report_timing(cut=time.monotonic() - heard_at if player else 0.0)
                self._enter_listening(frames)
                return

            # A turn can die without publishing its end (it raised between
            # turn_start and its try block, or the bus dropped the event). With
            # nothing left to say, do not wait for a marker that is not coming.
            if busy or ended or not turn_q.empty() or not self._turn_is_over():
                dead_since = None
            else:
                dead_since = dead_since or time.monotonic()
                if time.monotonic() - dead_since > _DEAD_TURN_S:
                    self._publish("status", text="voice: turn finished without its end marker")
                    if self._turn_failed():  # it raised: say so, as an error turn would
                        turn_q.put(APOLOGY_HI if self._heard_hindi else APOLOGY_EN)
                    turn_q.put(None)
                    dead_since = None

    def _turn_failed(self) -> bool:
        future = self._turn_future
        # done() first: exception() on a running future waits for it to finish.
        return (
            future is not None
            and future.done()
            and not future.cancelled()
            and future.exception() is not None
        )

    def _may_synthesise(self, ready, player) -> bool:
        """Bound on audio held: one sentence playing and at most _READY_AHEAD
        more, ready or being made. With prefetch off, one at a time as it
        always was."""
        if self.prefetch:
            return len(ready) < _READY_AHEAD
        return player is None and not ready

    def _turn_is_over(self) -> bool:
        return self._turn_future is not None and self._turn_future.done()

    def _play(self, pcm, sample_rate, stop, reply: bool) -> None:
        """Player-thread body. A device error costs this sentence, not the reply."""
        try:
            self.audio.play(
                pcm,
                sample_rate,
                stop,
                on_level=self._emit_reply_level if reply else self._emit_tts_level,
            )
        except Exception as exc:  # noqa: BLE001
            self._publish(
                "status", text=f"voice: could not play a sentence ({type(exc).__name__})"
            )

    def _end_turn(self, frames, stop_playback) -> None:
        """Everything the turn had to say has been said."""
        self._turn_future = None
        # A kill switch that landed as the turn was ending must not open a
        # follow-up window (the cancelled turn still emits its end marker).
        if self._interrupt.is_set():
            self._abort_speaking(stop_playback)
            return
        self._report_timing()
        # Conversation mode: keep the mic hot for a wake-word-free follow-up
        # (opens only now that playback is fully done, so Baby never transcribes
        # its own speech). Disabled → back to IDLE.
        if self.conversation_enabled:
            self._enter_listening(frames, source="followup")
        else:
            self.state = IDLE

    def _abort_speaking(self, stop_playback, player=None) -> None:
        """Kill switch reached a live turn: halt playback, cancel the turn,
        drop queued speech, go fully idle (no follow-up window)."""
        stop_playback.set()
        if player is not None:
            player.join(timeout=1.0)
        self._cancel_turn()
        self._drain_sentences()
        self._interrupt.clear()
        self._timing = {}  # a killed turn is not a wait worth measuring
        self.state = IDLE
        self._publish("status", text="voice: stopped by kill switch")

    def _session_cue(self) -> None:
        """Soft audio cue marking a conversation window opening/closing."""
        if self.conversation_cue:
            try:
                self.audio.beep()
            except Exception:  # noqa: BLE001 — the cue is cosmetic
                pass

    def _barge_in_step(self, frames, speech_frames: int, stop_playback) -> int:
        """One mic poll during playback; sets stop on sustained speech."""
        # The read is also what paces the _respond loop, so it happens even with
        # barge-in off -- returning before it made that loop spin a whole core.
        frame = self.audio.read(timeout=0.05)
        if frame is None:
            return speech_frames
        if not self.barge_in:
            return 0
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
