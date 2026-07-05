"""Voice pipeline: pure text helpers, bridge, state machine — all offline.

No test here touches audio hardware, model weights, or onnxruntime: heavy
imports live inside load() methods and every pipeline stage is injectable.
"""

from __future__ import annotations

import pytest

from voice.pipeline import is_kill_phrase, normalize_transcript
from voice.tts import pick_voice, split_sentences

KILL = ("baby stop", "baby ruk ja")


# -- split_sentences ----------------------------------------------------------


def test_split_basic_terminators():
    sentences, rest = split_sentences("Hello there. How are you? Great!")
    assert sentences == ["Hello there.", "How are you?", "Great!"]
    assert rest == ""


def test_split_hindi_danda():
    sentences, rest = split_sentences("मेरा जिम सोमवार को है। ठीक है।")
    assert sentences == ["मेरा जिम सोमवार को है।", "ठीक है।"]
    assert rest == ""


def test_split_streaming_partial():
    sentences, rest = split_sentences("First sentence. And then this keeps goi")
    assert sentences == ["First sentence."]
    assert rest == "And then this keeps goi"


def test_split_abbreviations_not_split():
    sentences, rest = split_sentences("Dr. Sharma is here. Ask e.g. about fees.")
    assert sentences == ["Dr. Sharma is here.", "Ask e.g. about fees."]
    assert rest == ""


def test_split_ellipsis():
    sentences, _ = split_sentences("Well… maybe. Fine!")
    assert sentences == ["Well…", "maybe.", "Fine!"]


def test_split_final_flush():
    sentences, rest = split_sentences("no terminator here", final=True)
    assert sentences == ["no terminator here"]
    assert rest == ""


def test_split_empty():
    assert split_sentences("") == ([], "")
    assert split_sentences("", final=True) == ([], "")


def test_split_carries_remainder_across_calls():
    s1, rest = split_sentences("The answer is 42. But wai")
    s2, rest2 = split_sentences(rest + "t, there is more!")
    assert s1 == ["The answer is 42."]
    assert s2 == ["But wait, there is more!"]
    assert rest2 == ""


# -- pick_voice ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("sentence", "expected_voice", "expected_lang"),
    [
        ("Hello, how are you?", "af_heart", "en-us"),
        ("Kaisa hai bhai, sab theek?", "af_heart", "en-us"),  # Roman Hinglish → EN voice
        ("मेरा जिम सोमवार को है।", "hf_beta", "hi"),
        ("Your gym is on सोमवार this week.", "hf_beta", "hi"),  # any Devanagari → HI
        ("42.", "af_heart", "en-us"),
    ],
)
def test_pick_voice(sentence, expected_voice, expected_lang):
    assert pick_voice(sentence, "af_heart", "hf_beta") == (expected_voice, expected_lang)


# -- kill phrase --------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "baby stop",
        "Baby, stop!",
        "BABY STOP.",
        "baby ruk ja",
        "Baby ruk ja।",
        "ok baby stop now",
        "hey baby stop",
    ],
)
def test_kill_phrase_matches(text):
    assert is_kill_phrase(text, KILL) is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        "baby stopwatch",
        "tell baby to never stop believing in the project",
        "write a note saying baby stop is a kill phrase for the system",
        "ruk ja",
        "stop",
    ],
)
def test_kill_phrase_rejects(text):
    assert is_kill_phrase(text, KILL) is False


def test_normalize_transcript():
    assert normalize_transcript("  Baby,   RUK ja।! ") == "baby ruk ja"


# -- fakes for pipeline tests ---------------------------------------------------

import asyncio  # noqa: E402
import concurrent.futures  # noqa: E402
import queue as queue_mod  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

import numpy as np  # noqa: E402

from core.agent import AgentCore  # noqa: E402
from core.bus import EventBus  # noqa: E402
from tests.conftest import FakeProvider  # noqa: E402
from voice.pipeline import CONFIRM_SENTENCE, LISTENING, VoicePipeline  # noqa: E402

SPEECH = np.full(512, 1000, dtype=np.int16)
SILENCE = np.zeros(512, dtype=np.int16)
WAKE = np.full(1280, 7777, dtype=np.int16)


class FakeAudio:
    def __init__(self, frames=None):
        self.frames = list(frames or [])
        self.beeped = 0
        self.play_calls = 0

    def start(self):
        pass

    def read(self, timeout=None):
        if self.frames:
            return self.frames.pop(0)
        time.sleep(0.005)
        return None

    def drain(self):
        pass

    def play(self, samples, samplerate, stop):
        self.play_calls += 1
        for _ in range(20):  # ~200ms of chunked playback
            if stop.is_set():
                return False
            time.sleep(0.01)
        return True

    def beep(self):
        self.beeped += 1

    def close(self):
        pass


class FakeWake:
    def detected(self, chunk):
        return bool(len(chunk) and chunk[0] == 7777)


class FakeVAD:
    def __init__(self, silence_frames_done: int = 2, wait_frames_done: int = 5):
        self.silence_frames_done = silence_frames_done
        self.wait_frames_done = wait_frames_done
        self._quiet = 0
        self.speech_started = False

    def is_speech(self, chunk, threshold=None):
        return bool(abs(chunk.astype(np.int32)).mean() > 500)

    def utterance_done(self, chunk):
        if self.is_speech(chunk):
            self.speech_started = True
            self._quiet = 0
            return False
        self._quiet += 1
        if not self.speech_started:
            return self._quiet >= self.wait_frames_done
        return self._quiet >= self.silence_frames_done

    def reset(self):
        self._quiet = 0
        self.speech_started = False


class FakeSTT:
    def __init__(self, text: str, lang: str = "en"):
        self.text = text
        self.lang = lang
        self.calls = 0

    def transcribe(self, pcm):
        self.calls += 1
        return self.text, self.lang


class FakeTTS:
    def __init__(self):
        self.synthed: list[str] = []

    def synth(self, sentence):
        self.synthed.append(sentence)
        return np.zeros(2400, dtype=np.int16), 24000


def _cfg(**over):
    cfg = {
        "kill_phrases": ["baby stop", "baby ruk ja"],
        "max_utterance_s": 2,
        "barge_in": True,
        "barge_in_threshold": 0.6,
        "push_to_talk_hotkey": "",
    }
    cfg.update(over)
    return cfg


async def _make_pipeline(db, script, *, stt_text="what time is it", frames=None):
    provider = FakeProvider(script)
    conv_id = await db.create_conversation("voice")
    bus = EventBus()
    agent = AgentCore(provider, db, conv_id, channel="voice", bus=bus)
    pipeline = VoicePipeline(
        asyncio.get_running_loop(),
        agent,
        bus,
        _cfg(),
        audio=FakeAudio(frames),
        wake=FakeWake(),
        vad=FakeVAD(),
        stt=FakeSTT(stt_text),
        tts=FakeTTS(),
    )
    return pipeline, provider, bus


# -- 4. bridge -----------------------------------------------------------------


async def test_bridge_assembles_voice_sentences_in_order(db):
    pipeline, _, bus = await _make_pipeline(db, [])
    task = asyncio.create_task(pipeline._bridge())
    await asyncio.sleep(0)
    bus.publish("token", "voice", text="First part ")
    bus.publish("token", "voice", text="done. Second one")
    bus.publish("token", "ui", text="MUST NOT APPEAR. ")
    bus.publish("turn_end", "voice", reply="", status="ok")
    await asyncio.sleep(0.05)
    task.cancel()

    got = []
    while True:
        try:
            got.append(pipeline.sentence_q.get_nowait())
        except queue_mod.Empty:
            break
    assert got == ["First part done.", "Second one", None]


async def test_bridge_confirm_request_speaks_check_screen(db):
    pipeline, _, bus = await _make_pipeline(db, [])
    task = asyncio.create_task(pipeline._bridge())
    await asyncio.sleep(0)
    bus.publish("confirm_request", "voice", confirm_id="c1", command="x")
    bus.publish("confirm_request", "ui", confirm_id="c2", command="y")
    await asyncio.sleep(0.05)
    task.cancel()
    assert pipeline.sentence_q.get_nowait() == CONFIRM_SENTENCE
    with pytest.raises(queue_mod.Empty):
        pipeline.sentence_q.get_nowait()


async def test_bridge_cancelled_turn_never_speaks_stale_buffer(db):
    pipeline, _, bus = await _make_pipeline(db, [])
    task = asyncio.create_task(pipeline._bridge())
    await asyncio.sleep(0)
    bus.publish("token", "voice", text="half a sentence that never fini")
    bus.publish("turn_end", "voice", reply="(cancelled)", status="cancelled")
    await asyncio.sleep(0.05)
    task.cancel()
    assert pipeline.sentence_q.get_nowait() is None  # sentinel only, no speech
    with pytest.raises(queue_mod.Empty):
        pipeline.sentence_q.get_nowait()


# -- 5. cross-thread ------------------------------------------------------------


async def test_publish_from_worker_thread_reaches_subscriber(db):
    bus = EventBus()
    q = bus.subscribe()
    loop = asyncio.get_running_loop()

    import functools

    def worker():
        # call_soon_threadsafe takes no kwargs — partial is mandatory
        loop.call_soon_threadsafe(
            functools.partial(bus.publish, "status", "voice", text="from thread")
        )

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    event = await asyncio.wait_for(q.get(), timeout=2)
    assert event.payload["text"] == "from thread"


async def test_submit_and_cancel_from_thread_yields_cancelled_turn_end(db):
    class SlowProvider(FakeProvider):
        async def chat(self, messages, tools=None, **opts):
            self.requests.append(list(messages))
            await asyncio.sleep(30)
            yield  # pragma: no cover

    provider = SlowProvider([])
    conv_id = await db.create_conversation("voice")
    bus = EventBus()
    q = bus.subscribe()
    agent = AgentCore(provider, db, conv_id, channel="voice", bus=bus)
    loop = asyncio.get_running_loop()

    holder = {}

    def worker():
        holder["fut"] = asyncio.run_coroutine_threadsafe(agent.run_turn("hi"), loop)

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    await asyncio.sleep(0.1)  # let the turn start
    holder["fut"].cancel()

    while True:
        event = await asyncio.wait_for(q.get(), timeout=2)
        if event.kind == "turn_end":
            assert event.payload["status"] == "cancelled"
            break


# -- 6. state machine -----------------------------------------------------------


async def test_full_voice_turn_wake_to_spoken_reply(db):
    frames = [WAKE]  # idle sees the wake chunk
    pipeline, provider, _ = await _make_pipeline(db, ["It is noon. Enjoy!"], frames=frames)
    pipeline.audio.frames += [SPEECH, SPEECH, SILENCE, SILENCE]  # the utterance
    bridge = asyncio.create_task(pipeline._bridge())
    await asyncio.sleep(0)

    from voice.audio_io import FrameBuffer

    fb = FrameBuffer()
    await asyncio.to_thread(pipeline._idle, fb)
    assert pipeline.state == LISTENING
    assert pipeline.audio.beeped == 1

    await asyncio.to_thread(pipeline._listen, fb)
    assert pipeline.state == "responding"
    await asyncio.sleep(0.1)  # let the scheduled run_turn reach the provider
    assert provider.requests  # turn submitted

    await asyncio.to_thread(pipeline._respond, fb)
    bridge.cancel()
    assert pipeline.state == "idle"
    assert pipeline.tts.synthed == ["It is noon.", "Enjoy!"]


async def test_empty_transcript_never_submits(db):
    pipeline, provider, _ = await _make_pipeline(db, ["never"], stt_text="")
    pipeline.audio.frames += [SPEECH, SILENCE, SILENCE]
    from voice.audio_io import FrameBuffer

    await asyncio.to_thread(pipeline._listen, FrameBuffer())
    assert pipeline.state == "idle"
    assert provider.requests == []


async def test_ptt_path_enters_listening(db):
    pipeline, _, _ = await _make_pipeline(db, [])
    pipeline._ptt.set()
    from voice.audio_io import FrameBuffer

    await asyncio.to_thread(pipeline._idle, FrameBuffer())
    assert pipeline.state == LISTENING
    assert pipeline.audio.beeped == 1


async def test_silent_capture_skips_stt(db):
    """False wake / user stays quiet: give up after the wait, never call STT."""
    pipeline, provider, _ = await _make_pipeline(db, ["never"])
    pipeline.audio.frames += [SILENCE] * 6  # wait_frames_done=5 → gives up
    from voice.audio_io import FrameBuffer

    await asyncio.to_thread(pipeline._listen, FrameBuffer())
    assert pipeline.state == "idle"
    assert pipeline.stt.calls == 0
    assert provider.requests == []


async def test_pause_before_speaking_still_captured(db):
    """Owner bug: 'replied only the first time' — the pause between beep and
    first word must not end the capture (silence counted from frame zero)."""
    pipeline, provider, _ = await _make_pipeline(db, ["Reply."])
    # 3 silent frames (> silence_frames_done=2) BEFORE any speech, then talk.
    pipeline.audio.frames += [SILENCE, SILENCE, SILENCE, SPEECH, SPEECH, SILENCE, SILENCE]
    from voice.audio_io import FrameBuffer

    await asyncio.to_thread(pipeline._listen, FrameBuffer())
    assert pipeline.state == "responding"
    await asyncio.sleep(0.1)
    assert provider.requests  # the turn was submitted despite the initial pause


# -- 6b. VoiceDetector endpointing (no model — probability overridden) ------------


def _scripted_vad(probs, **kwargs):
    from voice.vad import VoiceDetector

    vad = VoiceDetector(**kwargs)
    seq = iter(probs)
    vad.probability = lambda chunk: next(seq)
    return vad


def test_vad_pre_speech_silence_does_not_end_utterance():
    # silence_ms=64 → 2 frames; speech_wait_ms=160 → 5 frames (32 ms frames)
    vad = _scripted_vad([0.0] * 4, silence_ms=64, speech_wait_ms=160)
    chunk = SILENCE
    assert not any(vad.utterance_done(chunk) for _ in range(4))
    assert not vad.speech_started


def test_vad_gives_up_after_speech_wait():
    vad = _scripted_vad([0.0] * 5, silence_ms=64, speech_wait_ms=160)
    results = [vad.utterance_done(SILENCE) for _ in range(5)]
    assert results == [False, False, False, False, True]
    assert not vad.speech_started


def test_vad_endpoints_on_silence_after_speech():
    vad = _scripted_vad([0.9, 0.0, 0.0], silence_ms=64, speech_wait_ms=160)
    assert not vad.utterance_done(SPEECH)  # speech
    assert vad.speech_started
    assert not vad.utterance_done(SILENCE)  # 1 silent frame
    assert vad.utterance_done(SILENCE)  # 2 silent frames = 64 ms → done


def test_vad_reset_clears_speech_flag():
    vad = _scripted_vad([0.9], silence_ms=64, speech_wait_ms=160)
    vad.utterance_done(SPEECH)
    assert vad.speech_started
    vad._model = None  # reset() must not require the real model
    vad.reset()
    assert not vad.speech_started


# -- 6c. announcements (Phase 4) ---------------------------------------------------


async def test_announce_played_when_idle(db):
    pipeline, _, _ = await _make_pipeline(db, [])
    pipeline.announce("your task is done")
    from voice.audio_io import FrameBuffer

    await asyncio.to_thread(pipeline._idle, FrameBuffer())
    assert pipeline.tts.synthed == ["your task is done"]
    assert pipeline.audio.play_calls == 1
    assert pipeline.state == "idle"  # announcements never change state


async def test_announce_deferred_while_responding(db):
    pipeline, _, _ = await _make_pipeline(db, [])
    pipeline.announce("waiting my turn")
    pipeline.sentence_q.put("Reply sentence.")
    pipeline.sentence_q.put(None)
    from voice.audio_io import FrameBuffer

    fb = FrameBuffer()
    await asyncio.to_thread(pipeline._respond, fb)  # speaks the reply only
    assert pipeline.tts.synthed == ["Reply sentence."]
    await asyncio.to_thread(pipeline._idle, fb)  # then the announcement
    assert pipeline.tts.synthed == ["Reply sentence.", "waiting my turn"]


async def test_announce_overflow_dropped(db):
    pipeline, _, _ = await _make_pipeline(db, [])
    for i in range(7):  # maxsize 5
        pipeline.announce(f"announcement {i}")
    assert pipeline.announce_q.qsize() == 5


async def test_announce_synth_failure_recovers(db):
    pipeline, _, _ = await _make_pipeline(db, [])

    def boom(sentence):
        raise RuntimeError("tts died")

    pipeline.tts.synth = boom
    pipeline.announce("doomed")
    from voice.audio_io import FrameBuffer

    await asyncio.to_thread(pipeline._idle, FrameBuffer())  # must not raise
    assert pipeline.state == "idle"


# -- 7. barge-in ------------------------------------------------------------------


async def test_barge_in_stops_playback_and_cancels_turn(db):
    pipeline, _, _ = await _make_pipeline(db, [])
    pipeline.sentence_q.put("A very long sentence being spoken right now.")
    pipeline.audio.frames += [SPEECH] * 10  # sustained speech during playback
    fake_turn = concurrent.futures.Future()
    pipeline._turn_future = fake_turn

    from voice.audio_io import FrameBuffer

    await asyncio.to_thread(pipeline._respond, FrameBuffer())
    assert pipeline.state == LISTENING  # captures the interruption
    assert fake_turn.cancelled()
    assert pipeline.sentence_q.empty()


async def test_short_blip_does_not_barge_in(db):
    pipeline, _, _ = await _make_pipeline(db, [])
    pipeline.sentence_q.put("Short sentence.")
    pipeline.sentence_q.put(None)
    pipeline.audio.frames += [SPEECH, SILENCE, SPEECH, SILENCE]  # blips, never 6 in a row

    from voice.audio_io import FrameBuffer

    await asyncio.to_thread(pipeline._respond, FrameBuffer())
    assert pipeline.state == "idle"
    assert pipeline.audio.play_calls == 1  # played to completion


# -- 8. kill phrase mid-reply -----------------------------------------------------


async def test_kill_phrase_after_barge_in_cancels_and_never_submits(db):
    pipeline, provider, _ = await _make_pipeline(db, ["never"], stt_text="baby ruk ja")
    pipeline.audio.frames += [SPEECH, SILENCE, SILENCE]
    fake_turn = concurrent.futures.Future()
    pipeline._turn_future = fake_turn

    from voice.audio_io import FrameBuffer

    await asyncio.to_thread(pipeline._listen, FrameBuffer())
    assert pipeline.state == "idle"
    assert provider.requests == []
    assert fake_turn.cancelled()


# -- 9. ready cue -----------------------------------------------------------------


def _cue_cfg(tmp_path, **over):
    cfg = {
        "ready_announce": {
            "enabled": True,
            "sound_file": str(tmp_path / "ready.wav"),
            "min_interval_s": 60,
            **over,
        }
    }
    return cfg


def test_ready_cue_full_plays_wav(tmp_path, monkeypatch):
    from voice import readycue

    wav = tmp_path / "ready.wav"
    wav.write_bytes(b"RIFF")
    played = []
    monkeypatch.setattr("voice.audio_io.play_wav", lambda p: played.append(p) or True)
    cue = readycue.ReadyCue(_cue_cfg(tmp_path))
    assert cue.play_full() is True
    assert played == [wav]


def test_ready_cue_throttles(tmp_path, monkeypatch):
    from voice import readycue

    (tmp_path / "ready.wav").write_bytes(b"RIFF")
    monkeypatch.setattr("voice.audio_io.play_wav", lambda p: True)
    cue = readycue.ReadyCue(_cue_cfg(tmp_path))
    assert cue.play_full() is True
    assert cue.play_full() is False  # inside min_interval_s
    assert cue.play_degraded() is False  # throttle is shared


def test_ready_cue_missing_wav_degrades(tmp_path, monkeypatch):
    from voice.readycue import ReadyCue

    beeped = []
    import winsound

    monkeypatch.setattr(winsound, "MessageBeep", lambda *a: beeped.append(1))
    cue = ReadyCue(_cue_cfg(tmp_path))  # ready.wav never written
    assert cue.play_full() is True  # degraded path fired instead
    assert beeped == [1]


def test_ready_cue_disabled(tmp_path):
    from voice.readycue import ReadyCue

    cue = ReadyCue(_cue_cfg(tmp_path, enabled=False))
    assert cue.play_full() is False
    assert cue.play_degraded() is False


def test_prerender_writes_valid_riff(tmp_path, monkeypatch):
    import wave

    import numpy as np

    from voice.tts import TextToSpeech

    tts = TextToSpeech()
    monkeypatch.setattr(tts, "synth", lambda text: (np.zeros(2400, dtype=np.int16), 24000))
    out = tmp_path / "cue.wav"
    tts.prerender("Baby ready", out)
    with wave.open(str(out)) as wav:
        assert wav.getframerate() == 24000
        assert wav.getnchannels() == 1
        assert wav.getnframes() == 2400


# -- 10. wake fallback -------------------------------------------------------------


def test_wakeword_falls_back_to_builtin(tmp_path):
    from voice.wakeword import WakeWord

    ww = WakeWord(model_path=tmp_path / "hey_baby.onnx")  # absent
    assert ww.load() == "hey_jarvis"
    assert ww.active_model == "hey_jarvis"


# -- 11. markdown never reaches the speaker -----------------------------------------


def test_strip_markdown_bold_italic_code():
    from voice.tts import strip_markdown

    assert strip_markdown("**Tata Nexon EV** is *good* and `cheap`.") == (
        "Tata Nexon EV is good and cheap."
    )


def test_strip_markdown_links_headings_bullets():
    from voice.tts import strip_markdown

    text = "## Top picks\n- [Nexon](https://x.y/nexon) first\n- Punch second"
    assert strip_markdown(text) == "Top picks Nexon first Punch second"


def test_strip_markdown_unpaired_asterisks_dropped():
    from voice.tts import strip_markdown

    assert strip_markdown("** loud start") == "loud start"


def test_strip_markdown_plain_and_hindi_untouched():
    from voice.tts import strip_markdown

    assert strip_markdown("kal 8 baje uthna hai") == "kal 8 baje uthna hai"
    assert strip_markdown("**ठीक है** boss") == "ठीक है boss"


def test_synth_strips_markdown_before_kokoro():
    import numpy as np

    from voice.tts import TextToSpeech

    spoken = []

    class FakeKokoro:
        def create(self, sentence, voice, speed, lang):
            spoken.append(sentence)
            return np.zeros(10, dtype=np.float32), 24000

    tts = TextToSpeech()
    tts._kokoro = FakeKokoro()
    tts.synth("**Nexon EV** wins.")
    assert spoken == ["Nexon EV wins."]


def test_synth_pure_markdown_returns_silence():
    import numpy as np

    from voice.tts import TextToSpeech

    class ExplodingKokoro:
        def create(self, *a, **k):
            raise AssertionError("must not synth empty text")

    tts = TextToSpeech()
    tts._kokoro = ExplodingKokoro()
    pcm, rate = tts.synth("**")
    assert len(pcm) == 0 and rate == 24000
    assert pcm.dtype == np.int16


# -- 12. stt hotword bias ------------------------------------------------------------


def test_stt_passes_hotwords_to_whisper():
    import numpy as np

    from voice.stt import SpeechToText

    seen = {}

    class FakeInfo:
        language = "en"

    class FakeModel:
        def transcribe(self, audio, **kwargs):
            seen.update(kwargs)
            return [], FakeInfo()

    stt = SpeechToText(hotwords="Ollama, Chromium")
    stt._model = FakeModel()
    stt.transcribe(np.zeros(16000, dtype=np.int16))
    assert seen["hotwords"] == "Ollama, Chromium"


def test_stt_empty_hotwords_sends_none():
    import numpy as np

    from voice.stt import SpeechToText

    seen = {}

    class FakeInfo:
        language = "en"

    class FakeModel:
        def transcribe(self, audio, **kwargs):
            seen.update(kwargs)
            return [], FakeInfo()

    stt = SpeechToText()
    stt._model = FakeModel()
    stt.transcribe(np.zeros(16000, dtype=np.int16))
    assert seen["hotwords"] is None


# -- 13. speaker verification gate (Phase 5) -----------------------------------------


class FakeVerifier:
    def __init__(self, ok=True, similarity=0.9, enabled=True):
        self.ok = ok
        self.similarity = similarity
        self.enabled = enabled
        self.note = "on (threshold 0.5)"
        self.calls = 0

    def verify(self, pcm):
        self.calls += 1
        return self.ok, self.similarity


async def test_unknown_speaker_chat_only_denies_tools(db):
    from core.safety import SafetyClass

    pipeline, provider, _ = await _make_pipeline(db, ["Sorry, chat only."])
    pipeline.verifier = FakeVerifier(ok=False, similarity=0.2)
    pipeline.audio.frames += [SPEECH, SILENCE, SILENCE]
    from voice.audio_io import FrameBuffer

    await asyncio.to_thread(pipeline._listen, FrameBuffer())
    await asyncio.sleep(0.1)
    assert provider.requests  # the turn still runs (chat is allowed)
    gate = pipeline.agent.gate
    assert "voice" in gate.session.unverified_channels
    verdict = gate.classify("run_shell", {"command": "dir"}, channel="voice")
    assert verdict.klass is SafetyClass.DENY
    verdict = gate.classify("get_time", {}, channel="voice")
    assert verdict.klass is SafetyClass.DENY  # even read-only tools


async def test_owner_speaker_clears_flag_and_submits(db):
    pipeline, provider, _ = await _make_pipeline(db, ["Done."])
    pipeline.verifier = FakeVerifier(ok=True, similarity=0.8)
    pipeline.agent.gate.session.unverified_channels.add("voice")  # stale flag
    pipeline.audio.frames += [SPEECH, SILENCE, SILENCE]
    from voice.audio_io import FrameBuffer

    await asyncio.to_thread(pipeline._listen, FrameBuffer())
    await asyncio.sleep(0.1)
    assert provider.requests
    assert "voice" not in pipeline.agent.gate.session.unverified_channels


async def test_ignore_mode_drops_unknown_voice(db):
    pipeline, provider, bus = await _make_pipeline(db, ["never"])
    pipeline.verifier = FakeVerifier(ok=False, similarity=0.1)
    pipeline.sv_mode = "ignore"
    events = bus.subscribe()
    pipeline.audio.frames += [SPEECH, SILENCE, SILENCE]
    from voice.audio_io import FrameBuffer

    await asyncio.to_thread(pipeline._listen, FrameBuffer())
    await asyncio.sleep(0.1)
    assert provider.requests == []
    assert pipeline.state == "idle"
    texts = []
    while not events.empty():
        texts.append(events.get_nowait().payload.get("text", ""))
    assert any("unknown speaker ignored" in t for t in texts)


async def test_ptt_bypasses_verification(db):
    pipeline, provider, _ = await _make_pipeline(db, ["Yes boss."])
    pipeline.verifier = FakeVerifier(ok=False)  # would fail if consulted
    pipeline._ptt.set()
    pipeline.audio.frames += [SPEECH, SILENCE, SILENCE]
    from voice.audio_io import FrameBuffer

    fb = FrameBuffer()
    await asyncio.to_thread(pipeline._idle, fb)
    assert pipeline._listen_source == "ptt"
    await asyncio.to_thread(pipeline._listen, fb)
    await asyncio.sleep(0.1)
    assert pipeline.verifier.calls == 0
    assert provider.requests
    assert "voice" not in pipeline.agent.gate.session.unverified_channels


async def test_kill_phrase_honored_for_any_voice(db):
    pipeline, provider, _ = await _make_pipeline(db, ["never"], stt_text="baby stop")
    pipeline.verifier = FakeVerifier(ok=False)
    pipeline.audio.frames += [SPEECH, SILENCE, SILENCE]
    from voice.audio_io import FrameBuffer

    await asyncio.to_thread(pipeline._listen, FrameBuffer())
    assert pipeline.verifier.calls == 0  # kill phrase checked FIRST
    assert provider.requests == []


async def test_disabled_verifier_never_blocks(db):
    pipeline, provider, _ = await _make_pipeline(db, ["Reply."])
    pipeline.verifier = FakeVerifier(ok=False, enabled=False)
    pipeline.audio.frames += [SPEECH, SILENCE, SILENCE]
    from voice.audio_io import FrameBuffer

    await asyncio.to_thread(pipeline._listen, FrameBuffer())
    await asyncio.sleep(0.1)
    assert pipeline.verifier.calls == 0
    assert provider.requests


async def test_wake_after_ptt_verifies_again(db):
    pipeline, _, _ = await _make_pipeline(db, [])
    pipeline._listen_source = "ptt"
    from voice.audio_io import FrameBuffer

    pipeline._enter_listening(FrameBuffer())  # wake/barge-in path, default source
    assert pipeline._listen_source == "wake"
