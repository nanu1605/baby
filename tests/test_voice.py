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
    def __init__(self, silence_frames_done: int = 2):
        self.silence_frames_done = silence_frames_done
        self._quiet = 0

    def is_speech(self, chunk, threshold=None):
        return bool(abs(chunk.astype(np.int32)).mean() > 500)

    def utterance_done(self, chunk):
        if self.is_speech(chunk):
            self._quiet = 0
            return False
        self._quiet += 1
        return self._quiet >= self.silence_frames_done

    def reset(self):
        self._quiet = 0


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
