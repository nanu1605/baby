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
    sentences, rest = split_sentences("Dr. Sharma is here. Ask e.g. about fees.", final=True)
    assert sentences == ["Dr. Sharma is here.", "Ask e.g. about fees."]
    assert rest == ""


def test_split_keeps_decimals_versions_domains_whole():
    """A full stop inside a token is not a sentence end. The old splitter said
    "The price is 12." and then "5 dollars." as two sentences."""
    text = "The price is 12.5 dollars. Baby v6.0.2 is out. See example.com for it. Wait...what? "
    sentences, rest = split_sentences(text)
    assert sentences == [
        "The price is 12.5 dollars.",
        "Baby v6.0.2 is out.",
        "See example.com for it.",
        "Wait...what?",
    ]
    assert rest == ""


def test_split_trailing_period_waits_for_next_char():
    """Mid-stream, "12." could be the start of "12.5": only the next character
    can tell, so a full stop at the end of the buffer is not a boundary yet."""
    sentences, rest = split_sentences("It costs 12.")
    assert (sentences, rest) == ([], "It costs 12.")
    sentences, rest = split_sentences(rest + "5 dollars. ")
    assert (sentences, rest) == (["It costs 12.5 dollars."], "")
    # '?', '!' and the danda are never part of a token: no waiting for those.
    assert split_sentences("Really?")[0] == ["Really?"]
    assert split_sentences("यह ठीक है।अब चलो।")[0] == ["यह ठीक है।", "अब चलो।"]


def test_split_closing_quote_stays_with_its_sentence():
    assert split_sentences('He said "stop." Then left. ')[0] == ['He said "stop."', "Then left."]
    assert split_sentences("He said “stop.” Then left. ")[0] == ["He said “stop.”", "Then left."]


def test_split_newline_is_a_boundary():
    """A list has no full stops. Each finished line is spoken as it completes,
    instead of the whole list arriving as one chunk at the end of the turn."""
    sentences, rest = split_sentences("Here you go:\n- milk\n- eggs\n- bre")
    assert sentences == ["Here you go:", "- milk", "- eggs"]
    assert rest == "- bre"
    assert split_sentences("1. First thing\n2. Second thing\n")[0] == [
        "1. First thing",
        "2. Second thing",
    ]
    assert split_sentences("Done.\n\nNext: Want more?")[0] == ["Done.", "Next: Want more?"]


def test_split_drops_lone_punctuation():
    """A streamed "Really?" then "!" used to hand Kokoro a bare "!"."""
    assert split_sentences("!") == ([], "")
    assert split_sentences("... ", final=True) == ([], "")
    assert split_sentences("-\n•\n**\n") == ([], "")
    assert split_sentences("ठीक। ", final=True)[0] == ["ठीक।"]  # Devanagari is speech
    # A symbol is speech ("at", "percent"): only bare punctuation is dropped.
    assert split_sentences("The symbol is:\n@\nor:\n%\n")[0] == ["The symbol is:", "@", "or:", "%"]


def test_split_sees_the_end_of_a_sentence_wrapped_in_markdown():
    """A model writes "**Yes.** Then..." The full stop is followed by the closing
    markers, not by a space, and the sentence must still end there."""
    text = "**Yes.** The longer one. *No.* `ls.` End. "
    assert split_sentences(text)[0] == ["**Yes.**", "The longer one.", "*No.*", "`ls.`", "End."]


def test_split_time_of_day_is_not_a_sentence_end():
    assert split_sentences("Meet at 5 p.m. today. Fine. ")[0] == ["Meet at 5 p.m. today.", "Fine."]
    assert split_sentences("I am. You are. ")[0] == ["I am.", "You are."]  # "am" is a word


def test_split_abbreviation_after_a_bracket_and_rupees():
    assert split_sentences("Add fruit (e.g. apples) to it. ")[0] == [
        "Add fruit (e.g. apples) to it."
    ]
    assert split_sentences("Pay Rs. 500 today. ")[0] == ["Pay Rs. 500 today."]


def test_split_keeps_a_list_number_with_its_item():
    """"1." survived by luck (one character reads as an initial); "10." did not."""
    assert split_sentences("10. Tenth thing\n11. Eleventh thing\n")[0] == [
        "10. Tenth thing",
        "11. Eleventh thing",
    ]
    assert split_sentences("The answer is 10. Done. ")[0] == ["The answer is 10.", "Done."]


def test_split_is_linear_on_dots_and_long_lines():
    """It runs on the asyncio loop once per token. The first version of the new
    pattern was cubic on a run of dots: 1600 of them took seven seconds."""
    started = time.perf_counter()
    split_sentences("." * 20_000)
    split_sentences("ab.cd," * 3_000)
    split_sentences("word " * 4_000)
    rest = ""
    for piece in ["Loa", "din", "g.."] + ["..."] * 400:
        _, rest = split_sentences(rest + piece)
    assert time.perf_counter() - started < 1.0


def test_ends_sentence():
    from voice.tts import ends_sentence

    assert ends_sentence("Let me check.")
    assert ends_sentence('He said "stop." ')
    assert ends_sentence("**Let me check.**")
    assert ends_sentence("ठीक है।")
    assert ends_sentence("The answer is 5.")  # a digit is an answer, not an initial
    assert not ends_sentence("half a sent")
    assert not ends_sentence("Ask Dr.")
    assert not ends_sentence("")


def test_speech_says_point_and_dot_inside_a_token():
    """Kokoro's phonemizer cuts at every full stop, so keeping "12.5" in one
    sentence was not enough: it still said "twelve", paused, "five"."""
    from voice.tts import strip_markdown

    assert strip_markdown("The price is 12.5 dollars.") == "The price is 12 point 5 dollars."
    assert strip_markdown("Baby v6.0.2 is out.") == "Baby v6 point 0 point 2 is out."
    assert strip_markdown("See example.com or a.pdf.") == "See example dot com or a dot pdf."
    for untouched in (
        "Ask e.g. about fees.",
        "Meet at 5 p.m. today.",
        "Dr. Sharma lives in the U.S.A. now.",
        "1. First thing",
        "Done. Next one.",
        "Let me check.It is noon.",  # two generations glued: a pause, never "dot"
    ):
        assert strip_markdown(untouched) == untouched


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
import contextlib  # noqa: E402
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

    def play(self, samples, samplerate, stop, on_level=None):
        self.play_calls += 1
        for _ in range(20):  # ~200ms of chunked playback
            if stop.is_set():
                return False
            if on_level is not None:
                on_level(0.0)
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
        # Classic one-shot path by default; conversation-mode tests opt in.
        "conversation": {"enabled": False},
    }
    cfg.update(over)
    return cfg


async def _make_pipeline(db, script, *, stt_text="what time is it", frames=None, cfg_over=None):
    provider = FakeProvider(script)
    conv_id = await db.create_conversation("voice")
    bus = EventBus()
    agent = AgentCore(provider, db, conv_id, channel="voice", bus=bus)
    pipeline = VoicePipeline(
        asyncio.get_running_loop(),
        agent,
        bus,
        _cfg(**(cfg_over or {})),
        audio=FakeAudio(frames),
        wake=FakeWake(),
        vad=FakeVAD(),
        stt=FakeSTT(stt_text),
        tts=FakeTTS(),
    )
    return pipeline, provider, bus


# -- 3b. a failed load must not leave the mic open ------------------------------


class _CountingAudio(FakeAudio):
    """FakeAudio that records start/close, so a leak is visible."""

    def __init__(self):
        super().__init__()
        self.started = 0
        self.closed = 0

    def start(self):
        self.started += 1

    def close(self):
        self.closed += 1


class _BrokenWake:
    """What a first install really hits: the wake-word model is not downloaded yet."""

    def load(self):
        raise OSError(
            "NoSuchFile: [ONNXRuntimeError] : 3 : NO_SUCHFILE : Load model from "
            "hey_jarvis_v0.1.onnx failed. File doesn't exist"
        )


async def test_a_failed_load_closes_the_mic_it_already_opened(db):
    """The mic loads FIRST and opens a PortAudio stream with a cffi callback; the
    wake word loads second and, on a fresh install, raises. load() used to return
    right then, run_ui dropped the pipeline, and the still-running stream was left
    to the garbage collector -- PortAudio calling into a callback being freed under
    it. That is a 0xC0000005 in a thread with no Python frame, and it landed minutes
    later during the embedder load, so it never looked like voice. Twice."""
    pipeline, _, _ = await _make_pipeline(db, [])
    audio = _CountingAudio()
    pipeline.audio = audio
    pipeline.wake = _BrokenWake()

    ok, notes = pipeline.load()

    assert ok is False, "a missing wake-word model must still fail soft"
    assert any("voice unavailable" in n for n in notes)
    assert audio.started == 1, "test premise: the mic was opened before the failure"
    assert audio.closed == 1, "the mic stream was left open for the GC to race"


class _LoadableStage:
    """Every stage after the mic is loaded by calling .load() on it."""

    def load(self):
        return ""


async def test_a_successful_load_leaves_the_mic_running(db):
    """The unwind must fire only on failure -- closing a good stream would make
    voice silently deaf instead of crashing, which is not an improvement."""
    pipeline, _, _ = await _make_pipeline(db, [], cfg_over={"speaker_verify": {"enabled": False}})
    audio = _CountingAudio()
    pipeline.audio = audio
    for stage in ("wake", "vad", "stt", "tts"):
        setattr(pipeline, stage, _LoadableStage())

    ok, notes = pipeline.load()

    assert ok is True, notes
    assert audio.started == 1 and audio.closed == 0


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


# -- 6b. conversation mode (P3): wake-free follow-ups --------------------------

_CONV = {"conversation": {"enabled": True, "cue": True, "window_s": 60, "end_phrases": ["bas"]}}


async def test_conversation_mode_opens_followup_after_reply(db):
    frames = [WAKE]
    pipeline, provider, _ = await _make_pipeline(db, ["It is noon."], frames=frames, cfg_over=_CONV)
    pipeline.audio.frames += [SPEECH, SPEECH, SILENCE, SILENCE]
    bridge = asyncio.create_task(pipeline._bridge())
    await asyncio.sleep(0)
    from voice.audio_io import FrameBuffer

    fb = FrameBuffer()
    await asyncio.to_thread(pipeline._idle, fb)
    await asyncio.to_thread(pipeline._listen, fb)
    await asyncio.sleep(0.1)
    await asyncio.to_thread(pipeline._respond, fb)
    bridge.cancel()
    # Instead of going IDLE, the mic re-opens for a wake-free follow-up.
    assert pipeline.state == LISTENING
    assert pipeline._listen_source == "followup"


async def test_followup_end_phrase_closes_session(db):
    pipeline, provider, _ = await _make_pipeline(
        db, ["never"], stt_text="bas", cfg_over=_CONV
    )
    pipeline._listen_source = "followup"  # we are in a follow-up window
    pipeline.audio.frames += [SPEECH, SILENCE, SILENCE]
    from voice.audio_io import FrameBuffer

    await asyncio.to_thread(pipeline._listen, FrameBuffer())
    assert pipeline.state == "idle"  # end phrase closes the session
    assert provider.requests == []  # and does NOT start a new turn


async def test_followup_normal_speech_submits_without_wake(db):
    pipeline, provider, _ = await _make_pipeline(
        db, ["Sure."], stt_text="what about tomorrow", cfg_over=_CONV
    )
    pipeline._listen_source = "followup"
    pipeline.audio.frames += [SPEECH, SPEECH, SILENCE, SILENCE]
    from voice.audio_io import FrameBuffer

    await asyncio.to_thread(pipeline._listen, FrameBuffer())
    assert pipeline.state == "responding"  # a new turn, no wake word needed
    await asyncio.sleep(0.1)
    assert provider.requests


async def test_ptt_path_enters_listening(db):
    pipeline, _, _ = await _make_pipeline(db, [])
    pipeline._ptt.set()
    from voice.audio_io import FrameBuffer

    await asyncio.to_thread(pipeline._idle, FrameBuffer())
    assert pipeline.state == LISTENING
    assert pipeline.audio.beeped == 1


async def test_silent_capture_skips_stt(db):
    """False wake / user stays quiet: give up after the grace, never call STT."""
    pipeline, provider, _ = await _make_pipeline(db, ["never"], cfg_over={"listen_grace_s": 0.2})
    pipeline.audio.frames += [SILENCE] * 6  # no speech ever → grace elapses
    from voice.audio_io import FrameBuffer

    await asyncio.to_thread(pipeline._listen, FrameBuffer())
    assert pipeline.state == "idle"
    assert pipeline.stt.calls == 0
    assert provider.requests == []


async def test_wake_grace_survives_long_pre_speech_pause(db):
    """A wake listen must NOT close at the VAD's short pre-speech timeout: the
    user can pause well past it (here 7 silent frames > wait_frames_done=5)
    before starting to talk, and the utterance is still captured + submitted."""
    pipeline, provider, _ = await _make_pipeline(db, ["Reply."])  # grace default 10s
    pipeline.audio.frames += [SILENCE] * 7 + [SPEECH, SPEECH, SILENCE, SILENCE]
    from voice.audio_io import FrameBuffer

    await asyncio.to_thread(pipeline._listen, FrameBuffer())
    assert pipeline.state == "responding"
    await asyncio.sleep(0.1)
    assert provider.requests  # captured despite the long initial silence


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


async def test_kill_switch_stops_playback_and_goes_idle(db):
    """UI /kill during a spoken reply: playback halts, the turn is cancelled,
    queued speech is dropped, and Baby goes IDLE — NOT into a follow-up window,
    even with conversation mode on (observed live: Baby kept talking after
    /kill because only the turn future was cancelled, not playback)."""
    pipeline, _, _ = await _make_pipeline(db, [], cfg_over=_CONV)
    pipeline.sentence_q.put("A long sentence still being spoken right now.")
    fake_turn = concurrent.futures.Future()
    pipeline._turn_future = fake_turn

    def trip():
        time.sleep(0.03)
        pipeline.cancel_turn()  # the UI kill switch

    threading.Thread(target=trip, daemon=True).start()
    from voice.audio_io import FrameBuffer

    await asyncio.to_thread(pipeline._respond, FrameBuffer())
    assert pipeline.state == "idle"          # hard stop, not a follow-up window
    assert fake_turn.cancelled()
    assert pipeline.sentence_q.empty()
    assert not pipeline._interrupt.is_set()  # flag consumed


async def test_kill_switch_while_idle_does_not_abort_next_turn(db):
    """A kill pressed while nothing is speaking must not leak into and abort the
    next real turn: the flag is cleared when a fresh turn is submitted."""
    pipeline, provider, _ = await _make_pipeline(db, ["It is noon."])
    pipeline.cancel_turn()  # kill while idle — interrupt now set
    assert pipeline._interrupt.is_set()
    pipeline.audio.frames += [SPEECH, SPEECH, SILENCE, SILENCE]
    from voice.audio_io import FrameBuffer

    await asyncio.to_thread(pipeline._listen, FrameBuffer())
    assert pipeline.state == "responding"      # fresh turn runs normally
    assert not pipeline._interrupt.is_set()     # stale flag cleared on submit
    await asyncio.sleep(0.1)
    assert provider.requests


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


def _isolate_wake_models(monkeypatch, tmp_path):
    """Point BABY_HOME and openWakeWord's package dir at empty tmp dirs, so a test
    sees neither a provisioned durable copy nor the dev venv's files."""
    from voice import wakeword

    monkeypatch.setenv("BABY_HOME", str(tmp_path / "home"))
    empty = tmp_path / "site-packages"
    empty.mkdir(exist_ok=True)
    monkeypatch.setattr(wakeword, "_package_dir", lambda: empty)


def test_wakeword_falls_back_to_builtin(tmp_path, monkeypatch):
    # BABY_HOME here so the one-time lift out of site-packages lands in tmp, not the
    # checkout. This is a REAL load: it proves the durable copy actually runs.
    monkeypatch.setenv("BABY_HOME", str(tmp_path))
    from voice.wakeword import WakeWord

    ww = WakeWord(model_path=tmp_path / "jarvis.onnx")  # absent
    assert ww.load() == "hey_jarvis"
    assert ww.active_model == "hey_jarvis"


def test_wakeword_loads_custom_alongside_builtin(tmp_path, monkeypatch):
    import openwakeword.model as owm

    from voice.wakeword import WakeWord

    captured = {}

    class _FakeModel:
        def __init__(self, wakeword_models, inference_framework="onnx", **kwargs):
            captured["models"] = list(wakeword_models)
            captured["kwargs"] = dict(kwargs)

        def predict(self, chunk):
            return {}

        def reset(self):
            pass

    _isolate_wake_models(monkeypatch, tmp_path)
    monkeypatch.setattr(owm, "Model", _FakeModel)
    custom = tmp_path / "jarvis.onnx"
    custom.write_bytes(b"stub")  # load() only checks existence before handing to Model
    ww = WakeWord(model_path=custom)
    name = ww.load()
    # Both the custom model AND the pretrained fallback are loaded and scored.
    assert str(custom) in captured["models"]
    assert "hey_jarvis" in captured["models"]
    assert name == "jarvis+hey_jarvis"


def test_wakeword_empty_fallback_never_loads_all(tmp_path, monkeypatch):
    # A blanked fallback with no custom model must NOT hand Model([]) — that
    # loads EVERY bundled wake word (alexa, timer…). Fall back to hey_jarvis.
    import openwakeword.model as owm

    from voice.wakeword import WakeWord

    captured = {}

    class _FakeModel:
        def __init__(self, wakeword_models, inference_framework="onnx", **kwargs):
            captured["models"] = list(wakeword_models)
            captured["kwargs"] = dict(kwargs)

        def predict(self, chunk):
            return {}

        def reset(self):
            pass

    _isolate_wake_models(monkeypatch, tmp_path)
    monkeypatch.setattr(owm, "Model", _FakeModel)
    ww = WakeWord(model_path=tmp_path / "absent.onnx", builtin_fallback="")
    ww.load()
    assert captured["models"] == ["hey_jarvis"]  # never [] (which loads all)


# -- 10b. the wake models outlive the venv ------------------------------------------
# openWakeWord's downloader defaults to writing its weights INSIDE site-packages.
# An upgrade's `uv sync` deleted them, and because setup was already marked complete
# the wizard never re-ran -- the install went permanently deaf with no message. The
# files now live under models_dir(), which a venv rebuild does not touch.


def _fake_openwakeword_dir(root):
    """A stand-in for site-packages/openwakeword/resources/models."""
    root.mkdir(parents=True, exist_ok=True)
    for name in (
        "hey_jarvis_v0.1.onnx",
        "melspectrogram.onnx",
        "embedding_model.onnx",
        "alexa_v0.1.onnx",
    ):
        (root / name).write_bytes(b"stub-" + name.encode())
    (root / "hey_jarvis_v0.1.tflite").write_bytes(b"tflite")  # we never load these
    return root


def test_the_wake_models_are_lifted_out_of_the_disposable_venv_copy(tmp_path, monkeypatch):
    from core import paths
    from voice import wakeword

    monkeypatch.setenv("BABY_HOME", str(tmp_path))
    pkg = _fake_openwakeword_dir(tmp_path / "site-packages")
    monkeypatch.setattr(wakeword, "_package_dir", lambda: pkg)

    assert wakeword.adopt_package_models() == 4  # the .onnx files, not the .tflite
    durable = paths.wakeword_dir()
    assert (durable / "hey_jarvis_v0.1.onnx").read_bytes() == b"stub-hey_jarvis_v0.1.onnx"
    assert not (durable / "hey_jarvis_v0.1.tflite").exists()
    # Idempotent, and never clobbers a file already there.
    (durable / "alexa_v0.1.onnx").write_bytes(b"mine")
    assert wakeword.adopt_package_models() == 0
    assert (durable / "alexa_v0.1.onnx").read_bytes() == b"mine"


def test_load_lifts_the_venv_copy_before_it_resolves_anything(tmp_path, monkeypatch):
    """An install provisioned by an older build has its files only in site-packages.
    load() must move them across BEFORE resolving, or the next `uv sync` is the last
    time this machine ever hears its wake word."""
    import openwakeword.model as owm

    from core import paths
    from voice import wakeword
    from voice.wakeword import WakeWord

    monkeypatch.setenv("BABY_HOME", str(tmp_path))
    pkg = _fake_openwakeword_dir(tmp_path / "site-packages")
    monkeypatch.setattr(wakeword, "_package_dir", lambda: pkg)

    captured = {}

    class _FakeModel:
        def __init__(self, wakeword_models, inference_framework="onnx", **kwargs):
            captured["models"] = list(wakeword_models)
            captured["kwargs"] = dict(kwargs)

        def predict(self, chunk):
            return {}

        def reset(self):
            pass

    monkeypatch.setattr(owm, "Model", _FakeModel)
    WakeWord(model_path=tmp_path / "absent.onnx").load()

    durable = paths.wakeword_dir()
    assert (durable / "hey_jarvis_v0.1.onnx").exists()
    # And the lift happened first, so this load already used the durable copy.
    assert captured["models"] == [str(durable / "hey_jarvis_v0.1.onnx")]
    assert captured["kwargs"]["melspec_model_path"] == str(durable / "melspectrogram.onnx")


def test_a_venv_rebuild_cannot_take_the_wake_models_with_it(tmp_path, monkeypatch):
    """The regression: with the venv copy GONE, load() still resolves real files."""
    import openwakeword.model as owm

    from core import paths
    from voice import wakeword
    from voice.wakeword import WakeWord

    monkeypatch.setenv("BABY_HOME", str(tmp_path))
    # What first-run wrote, under models_dir().
    durable = paths.wakeword_dir()
    durable.mkdir(parents=True, exist_ok=True)
    for name in ("hey_jarvis_v0.1.onnx", "melspectrogram.onnx", "embedding_model.onnx"):
        (durable / name).write_bytes(b"stub")
    # What `uv sync` left behind: an empty package dir.
    empty = tmp_path / "site-packages"
    empty.mkdir()
    monkeypatch.setattr(wakeword, "_package_dir", lambda: empty)

    captured = {}

    class _FakeModel:
        def __init__(self, wakeword_models, inference_framework="onnx", **kwargs):
            captured["models"] = list(wakeword_models)
            captured["kwargs"] = dict(kwargs)

        def predict(self, chunk):
            return {}

        def reset(self):
            pass

    monkeypatch.setattr(owm, "Model", _FakeModel)
    assert WakeWord(model_path=tmp_path / "absent.onnx").load() == "hey_jarvis"
    # Loaded BY PATH out of models_dir -- not by the bare name, which would send
    # openWakeWord back to the site-packages dir the rebuild just emptied.
    assert captured["models"] == [str(durable / "hey_jarvis_v0.1.onnx")]
    assert captured["kwargs"] == {
        "melspec_model_path": str(durable / "melspectrogram.onnx"),
        "embedding_model_path": str(durable / "embedding_model.onnx"),
    }


def test_an_unprovisioned_checkout_still_uses_openwakewords_own_copy(tmp_path, monkeypatch):
    """No durable files yet: hand over the bare name and no feature paths, so
    openWakeWord resolves inside its own package exactly as it always did."""
    import openwakeword.model as owm

    from voice.wakeword import WakeWord

    _isolate_wake_models(monkeypatch, tmp_path)

    captured = {}

    class _FakeModel:
        def __init__(self, wakeword_models, inference_framework="onnx", **kwargs):
            captured["models"] = list(wakeword_models)
            captured["kwargs"] = dict(kwargs)

        def predict(self, chunk):
            return {}

        def reset(self):
            pass

    monkeypatch.setattr(owm, "Model", _FakeModel)
    WakeWord(model_path=tmp_path / "absent.onnx").load()
    assert captured["models"] == ["hey_jarvis"]
    assert captured["kwargs"] == {}


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


def test_strip_markdown_drops_leaked_think_tags():
    from voice.tts import strip_markdown

    assert strip_markdown("</think>The answer is 42.") == "The answer is 42."
    assert strip_markdown("<think> hm </think> okay") == "hm okay"


def test_strip_markdown_drops_emoji():
    from voice.tts import strip_markdown

    assert strip_markdown("Done 👍") == "Done"
    assert strip_markdown("Ha, nice one 😄!") == "Ha, nice one !"
    assert strip_markdown("Sunny ☀️ today") == "Sunny today"
    assert strip_markdown("हो गया ✅") == "हो गया"
    for said, spoken in (("Top pick ⭐", "Top pick"), ("Alarm ⏰ set", "Alarm set"),
                         ("Play ▶️ now", "Play now"), ("India 🇮🇳 wins", "India wins"),
                         ("Family 👨‍👩‍👧 trip", "Family trip"),
                         ("Press 1️⃣", "Press 1"), ("Thumbs 👍🏽", "Thumbs")):
        assert strip_markdown(said) == spoken, said
    # Not emoji: kept, because espeak reads them right.
    assert strip_markdown("It is 25°C and costs ₹500.") == "It is 25°C and costs ₹500."


def test_synth_strips_markdown_before_kokoro():
    import numpy as np

    from voice.tts import TextToSpeech

    spoken = []

    class FakeTokenizer:
        def phonemize(self, text, lang):
            return text

    class FakeKokoro:
        tokenizer = FakeTokenizer()

        def create(self, phonemes, voice, speed, is_phonemes):
            assert is_phonemes
            spoken.append(phonemes)
            return np.zeros(10, dtype=np.float32), 24000

    tts = TextToSpeech()
    tts._kokoro = FakeKokoro()
    tts.synth("**Nexon EV** wins.")
    assert spoken == ["Nexon EV wins."]


def test_synth_drops_espeak_language_switch_marks():
    """espeak writes "(en)...(hi)" round the English words of a Hindi sentence;
    Kokoro would say those letters. Real phonemizer, no model needed."""
    import numpy as np

    from voice.tts import TextToSpeech

    spoken = []

    class FakeKokoro:
        from kokoro_onnx.tokenizer import Tokenizer

        tokenizer = Tokenizer()

        def create(self, phonemes, voice, speed, is_phonemes):
            spoken.append((phonemes, voice))
            return np.zeros(10, dtype=np.float32), 24000

    tts = TextToSpeech()
    tts._kokoro = FakeKokoro()
    tts.synth("Chrome खोल दिया।")
    (phonemes, voice), = spoken
    assert voice == "hf_beta"
    assert "(" not in phonemes and ")" not in phonemes
    assert "kɹ" in phonemes  # "Chrome" still gets English sounds


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


# -- 12b. voice CPU: one encoder pass, capped thread pools (DECISIONS #167) ----------


class _Seg:
    def __init__(self, text):
        self.text = text


class _RecordingWhisper:
    """Records the kwargs transcribe() was called with and echoes the language hint
    back as info.language -- which is exactly what faster-whisper does."""

    def __init__(self, segments=("hello",)):
        self.kwargs = {}
        self.segments = segments

    def transcribe(self, audio, **kwargs):
        self.kwargs = kwargs

        class Info:
            language = kwargs.get("language")

        return [_Seg(t) for t in self.segments], Info()


def test_stt_runs_the_encoder_once():
    """With language=None, faster-whisper 1.2.1 encodes the audio for language
    detection, drops that output, and encodes it again to transcribe. The encoder
    is the whole cost, so voice turns held 8 cores for ~6 s instead of ~3. Both
    halves of the fix are load-bearing: multilingual=True alone still takes the
    detect_language() pass, and a language hint alone pins decoding to English."""
    import numpy as np

    from voice.stt import SpeechToText

    stt = SpeechToText()
    stt._model = _RecordingWhisper()
    stt.transcribe(np.zeros(16000, dtype=np.int16))

    assert stt._model.kwargs.get("multilingual") is True, (
        "without multilingual the hint pins decoding: Hindi would come back as English"
    )
    assert stt._model.kwargs.get("language") is not None, (
        "language=None sends faster-whisper through detect_language(): two encoder passes"
    )


def test_stt_never_reports_the_hint_as_the_language():
    """The hint comes straight back as info.language, so reporting it would label a
    Hindi question "en". Nothing downstream may be handed a language that was never
    detected."""
    import numpy as np

    from voice.stt import SpeechToText

    stt = SpeechToText()
    stt._model = _RecordingWhisper(segments=("कल मौसम कैसा रहेगा?",))
    text, lang = stt.transcribe(np.zeros(16000, dtype=np.int16))
    assert text == "कल मौसम कैसा रहेगा?"
    assert lang == ""

    stt._model = _RecordingWhisper(segments=("Thank you.",))  # hallucination path
    assert stt.transcribe(np.zeros(16000, dtype=np.int16)) == ("", "")


def test_voice_models_default_to_four_threads():
    """Eight Whisper threads bought ~13% speed for ~75% more CPU; onnxruntime's
    default gave Kokoro every core. Four is the default everywhere a stranger's
    install could pick one up."""
    from voice.stt import SpeechToText
    from voice.tts import TextToSpeech

    assert SpeechToText().cpu_threads == 4
    assert TextToSpeech().cpu_threads == 4


async def test_the_pipeline_falls_back_to_four_threads_and_honours_config(db, monkeypatch):
    """A config with no cpu_threads must get 4, and a config that names one must get
    that -- for both stages. The pipeline is what actually builds them."""
    from core import paths
    from voice.stt import SpeechToText
    from voice.tts import TextToSpeech

    monkeypatch.setattr(SpeechToText, "load", lambda self: None)
    monkeypatch.setattr(TextToSpeech, "load", lambda self: None)
    monkeypatch.setattr(paths, "resolve_model", lambda p: p)

    for over, want_stt, want_tts in (
        ({}, 4, 4),
        ({"stt": {"cpu_threads": 6}, "tts": {"cpu_threads": 2}}, 6, 2),
    ):
        pipeline, _, _ = await _make_pipeline(db, [], cfg_over=over)
        pipeline.stt = None
        pipeline.tts = None
        pipeline._load_stt()
        pipeline._load_tts()
        assert pipeline.stt.cpu_threads == want_stt, over
        assert pipeline.tts.cpu_threads == want_tts, over


def _fake_onnx_stack(monkeypatch):
    """Stand-ins for onnxruntime and kokoro_onnx, so this file keeps its promise of
    never importing onnxruntime. Records what load() asked for."""
    import sys
    import types

    seen = {}

    class SessionOptions:
        def __init__(self):
            self.intra_op_num_threads = 0
            self.entries = {}

        def add_session_config_entry(self, key, value):
            self.entries[key] = value

    def InferenceSession(path, sess_options=None, providers=None):  # noqa: N802
        seen["session"] = {"path": path, "options": sess_options, "providers": providers}
        return "the-session"

    class Kokoro:
        def __init__(self, *args, **kwargs):
            seen["default_constructor"] = True

        @classmethod
        def from_session(cls, session, voices_path):
            seen["from_session"] = (session, voices_path)
            return cls.__new__(cls)

    class KoKoroConfig:
        def __init__(self, model_path, voices_path, espeak_config=None):
            self.paths = (model_path, voices_path)

        def validate(self):
            from pathlib import Path

            for p in self.paths:
                if not Path(p).exists():
                    raise FileNotFoundError(p)

    ort = types.ModuleType("onnxruntime")
    ort.SessionOptions = SessionOptions
    ort.InferenceSession = InferenceSession
    kokoro = types.ModuleType("kokoro_onnx")
    kokoro.Kokoro = Kokoro
    kokoro_config = types.ModuleType("kokoro_onnx.config")
    kokoro_config.KoKoroConfig = KoKoroConfig
    kokoro.config = kokoro_config
    monkeypatch.setitem(sys.modules, "onnxruntime", ort)
    monkeypatch.setitem(sys.modules, "kokoro_onnx", kokoro)
    monkeypatch.setitem(sys.modules, "kokoro_onnx.config", kokoro_config)
    return seen


def test_tts_session_is_capped_and_does_not_spin(monkeypatch, tmp_path):
    """Kokoro(...) builds its own session with onnxruntime defaults: a worker per
    physical core, spin-waiting between ops -- 7.1 cores for every spoken sentence.
    load() must build the session itself, capped and not spinning, and hand it to
    Kokoro rather than letting Kokoro build a second one."""
    from voice.tts import TextToSpeech

    model = tmp_path / "kokoro.onnx"
    voices = tmp_path / "voices.bin"
    model.write_bytes(b"")
    voices.write_bytes(b"")
    seen = _fake_onnx_stack(monkeypatch)

    tts = TextToSpeech(model_path=model, voices_path=voices, cpu_threads=3)
    tts.load()

    options = seen["session"]["options"]
    assert options is not None, "session built with onnxruntime's default options"
    assert options.intra_op_num_threads == 3
    assert options.entries.get("session.intra_op.allow_spinning") == "0"
    assert seen["session"]["providers"] == ["CPUExecutionProvider"]
    assert seen["session"]["path"] == str(model)
    assert seen["from_session"] == ("the-session", str(voices))
    assert "default_constructor" not in seen, "Kokoro() would build an uncapped session"


def test_tts_missing_download_still_raises_file_not_found(monkeypatch, tmp_path):
    """health.check_kokoro and first-run setup report a missing model as
    FileNotFoundError. Building the session ourselves must not turn that into
    onnxruntime's NoSuchFile -- the check has to run before the session does."""
    from voice.tts import TextToSpeech

    seen = _fake_onnx_stack(monkeypatch)
    tts = TextToSpeech(model_path=tmp_path / "absent.onnx", voices_path=tmp_path / "absent.bin")

    with pytest.raises(FileNotFoundError):
        tts.load()
    assert "session" not in seen, "the session was built before the files were checked"


def test_the_shipped_config_caps_both_voice_models():
    """installer/config.default.yaml is what a fresh install runs with; a stranger
    gets 4 threads for Whisper and Kokoro, not 8 and every core."""
    from pathlib import Path

    import yaml

    cfg = yaml.safe_load(
        (Path(__file__).resolve().parent.parent / "installer" / "config.default.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert cfg["voice"]["stt"]["cpu_threads"] == 4
    assert cfg["voice"]["tts"]["cpu_threads"] == 4


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
    from voice.speaker import SessionTrust

    pipeline, provider, _ = await _make_pipeline(db, ["Sorry, chat only."])
    pipeline.verifier = FakeVerifier(ok=False, similarity=0.2)
    pipeline.session_trust = SessionTrust(demote_after=1)  # demote on a single low
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
    from voice.speaker import SessionTrust

    pipeline, provider, bus = await _make_pipeline(db, ["never"])
    pipeline.verifier = FakeVerifier(ok=False, similarity=0.1)
    pipeline.sv_mode = "ignore"
    pipeline.session_trust = SessionTrust(demote_after=1)  # confident-unknown -> drop
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


# -- 13b. session-trust smoothing (B6) -----------------------------------------------


async def test_single_low_utterance_does_not_demote(db):
    """Optimistic-demote: one shaky utterance never locks the owner out."""
    pipeline, provider, _ = await _make_pipeline(db, ["ok"])
    pipeline.verifier = FakeVerifier(ok=False, similarity=0.2)  # default demote_after=2
    pipeline.audio.frames += [SPEECH, SILENCE, SILENCE]
    from voice.audio_io import FrameBuffer

    await asyncio.to_thread(pipeline._listen, FrameBuffer())
    await asyncio.sleep(0.1)
    assert provider.requests  # turn ran
    assert "voice" not in pipeline.agent.gate.session.unverified_channels  # not denied
    assert pipeline.session_trust.tier == "uncertain"


async def test_sustained_unknown_demotes_and_denies(db):
    """Two consecutive confident-low utterances demote to chat-only (defaults)."""
    from core.safety import SafetyClass
    from voice.audio_io import FrameBuffer

    pipeline, _, _ = await _make_pipeline(db, ["one", "two"])
    pipeline.verifier = FakeVerifier(ok=False, similarity=0.15)  # demote_after=2
    for _ in range(2):
        pipeline.audio.frames += [SPEECH, SILENCE, SILENCE]
        await asyncio.to_thread(pipeline._listen, FrameBuffer())
        await asyncio.sleep(0.05)
    gate = pipeline.agent.gate
    assert "voice" in gate.session.unverified_channels
    assert gate.classify("get_time", {}, channel="voice").klass is SafetyClass.DENY


async def test_observe_mode_scores_logs_but_never_enforces(db):
    """observe = the B7 soak mode: score + log every utterance, never gate."""
    from voice.speaker import SessionTrust

    pipeline, provider, _ = await _make_pipeline(db, ["ok"])
    pipeline.verifier = FakeVerifier(ok=False, similarity=0.1)
    pipeline.sv_mode = "observe"
    pipeline.session_trust = SessionTrust(demote_after=1)  # would demote if enforcing
    pipeline.audio.frames += [SPEECH, SILENCE, SILENCE]
    from voice.audio_io import FrameBuffer

    await asyncio.to_thread(pipeline._listen, FrameBuffer())
    await asyncio.sleep(0.1)
    assert provider.requests  # turn ran
    assert "voice" not in pipeline.agent.gate.session.unverified_channels  # never denied


async def test_scored_utterance_is_audit_logged(db):
    """Every scored utterance lands an audit row — the FAR/FRR data source."""
    import json as _json

    pipeline, _, _ = await _make_pipeline(db, ["ok"])
    pipeline.verifier = FakeVerifier(ok=True, similarity=0.77)
    pipeline.audio.frames += [SPEECH, SILENCE, SILENCE]
    from voice.audio_io import FrameBuffer

    await asyncio.to_thread(pipeline._listen, FrameBuffer())
    await asyncio.sleep(0.1)
    rows = await db._fetchall(
        "SELECT args FROM audit_log WHERE tool = 'speaker_verify' ORDER BY id"
    )
    assert rows
    payload = _json.loads(rows[-1]["args"])
    assert payload["score"] == pytest.approx(0.77, abs=1e-3)
    assert payload["tier"] == "trusted"
    assert "model" in payload


# -- 14. per-turn timing, and the conversation block reaching voice (DECISIONS #168) --


def _shipped_config():
    from pathlib import Path

    import yaml

    path = Path(__file__).resolve().parent.parent / "installer" / "config.default.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _timing_lines(feed):
    lines = []
    while not feed.empty():
        event = feed.get_nowait()
        text = str(event.payload.get("text", ""))
        if event.kind == "status" and text.startswith("voice: timing "):
            lines.append(text)
    return lines


def _timing_labels(line):
    return [part.split()[0] for part in line.removeprefix("voice: timing ").split(" · ")]


async def _speak_one_turn(db, script):
    """Listen -> submit -> respond on the fakes; returns (pipeline, feed queue)."""
    from voice.audio_io import FrameBuffer

    pipeline, _, bus = await _make_pipeline(db, script)
    feed = bus.subscribe()
    watch = bus.subscribe()
    pipeline.audio.frames += [SPEECH, SPEECH, SILENCE, SILENCE]
    bridge = asyncio.create_task(pipeline._bridge())
    await asyncio.sleep(0)
    fb = FrameBuffer()
    await asyncio.to_thread(pipeline._listen, fb)
    await asyncio.sleep(0.1)  # let the turn reach the provider and stream back
    await asyncio.to_thread(pipeline._respond, fb)
    await asyncio.sleep(0.1)  # let the feed line and the audit row land
    bridge.cancel()
    # A healthy turn ends on its own end marker. The watchdog would end it too,
    # a second late, and every test above would still pass.
    bus.unsubscribe(watch)
    assert not any("without its end marker" in line for line in _status_lines(watch))
    return pipeline, feed


async def test_voice_turn_publishes_one_timing_line(db):
    """Each spoken turn says where its wait went: one line, every stage, in order."""
    _, feed = await _speak_one_turn(db, ["It is noon. Enjoy!"])
    lines = _timing_lines(feed)
    assert len(lines) == 1
    assert _timing_labels(lines[0]) == [
        "stt", "request", "token", "sentence", "tts", "audio", "end"
    ]
    # ui/server.py moves the orb on these words in a voice status line, the graph
    # pulses on "heard", and the activity socket raises a toast on the rest; a
    # timing line must do none of it.
    for word in (
        "listening", "stopped", "interrupted", "heard", "ended",
        "wiped", "kill", "error", "cancelled", "offline", "degraded",
    ):
        assert word not in lines[0]


async def test_voice_turn_timing_is_audited(db):
    """The same figures land in audit_log, so a soak can be read back later."""
    import json as _json

    await _speak_one_turn(db, ["It is noon. Enjoy!"])
    rows = await db._fetchall("SELECT args FROM audit_log WHERE tool = 'voice_timing'")
    assert len(rows) == 1
    figures = _json.loads(rows[0]["args"])
    assert list(figures) == ["stt", "request", "token", "sentence", "tts", "audio", "end"]
    seconds = list(figures.values())
    assert seconds == sorted(seconds), "each stage is measured from the same start"
    assert all(value >= 0 for value in seconds)


async def test_timing_starts_when_the_user_stopped_not_when_the_vad_noticed(db):
    """The VAD reports the end only after its silence window, so the wait the
    user feels began that long ago -- a longer window must show up as wait."""
    pipeline, _, _ = await _make_pipeline(db, ["ok"], cfg_over={"vad_silence_ms": 5000})
    pipeline.audio.frames += [SPEECH, SILENCE, SILENCE]
    from voice.audio_io import FrameBuffer

    await asyncio.to_thread(pipeline._listen, FrameBuffer())
    timing = pipeline._timing
    assert timing["stt_done"] - timing["speech_end"] >= 5.0


async def test_an_interrupted_turn_reports_how_fast_it_went_quiet(db):
    pipeline, _, bus = await _make_pipeline(db, [])
    feed = bus.subscribe()
    now = time.monotonic()
    pipeline._timing = {"speech_end": now, "stt_done": now, "submitted": now}
    pipeline.sentence_q.put("A very long sentence being spoken right now.")
    pipeline.audio.frames += [SPEECH] * 10
    pipeline._turn_future = concurrent.futures.Future()

    await _respond_once(pipeline)
    await asyncio.sleep(0.05)
    lines = _timing_lines(feed)
    assert len(lines) == 1
    labels = _timing_labels(lines[0])
    assert labels[-1] == "cut" and "end" not in labels


async def test_a_finished_turn_is_reported_once(db):
    """A later end-of-turn marker with no turn behind it (a stale sentinel, a
    kill phrase that submitted nothing) must not replay the last turn's line."""

    pipeline, feed = await _speak_one_turn(db, ["It is noon."])
    pipeline.sentence_q.put(None)
    await _respond_once(pipeline)
    await asyncio.sleep(0.05)
    assert len(_timing_lines(feed)) == 1


async def test_audio_is_stamped_when_sound_leaves_not_when_it_is_queued(db):
    """"audio" is the first block written to the speaker. A sentence that was
    synthesised but never sounded must not count as Baby starting to speak."""

    class SilentAudio(FakeAudio):
        def play(self, samples, samplerate, stop, on_level=None):
            return False  # stopped before the first block was written

    pipeline, _, bus = await _make_pipeline(db, [])
    pipeline.audio = SilentAudio()
    feed = bus.subscribe()
    now = time.monotonic()
    pipeline._timing = {"speech_end": now, "stt_done": now, "submitted": now}
    pipeline.sentence_q.put("Synthesised, never heard.")
    pipeline.sentence_q.put(None)

    await _respond_once(pipeline)
    await asyncio.sleep(0.05)
    labels = _timing_labels(_timing_lines(feed)[0])
    assert "tts" in labels and "audio" not in labels


@pytest.mark.parametrize("canned", ["confirm", "apology_en", "apology_hi"])
async def test_the_check_the_screen_line_does_not_take_the_replys_stamps(db, canned):
    """A confirmation is spoken before the model has written anything, and an
    apology instead of anything it wrote. If either canned line took the
    tts/audio stamps, the timing line would read as a reply spoken."""
    from voice.pipeline import APOLOGY_EN, APOLOGY_HI

    line = {"confirm": CONFIRM_SENTENCE, "apology_en": APOLOGY_EN, "apology_hi": APOLOGY_HI}[
        canned
    ]
    pipeline, _, bus = await _make_pipeline(db, [])
    feed = bus.subscribe()
    now = time.monotonic()
    pipeline._timing = {"speech_end": now, "stt_done": now, "submitted": now}
    pipeline.sentence_q.put(line)
    pipeline.sentence_q.put(None)

    await _respond_once(pipeline)
    await asyncio.sleep(0.05)
    assert pipeline.tts.synthed == [line]  # still spoken
    labels = _timing_labels(_timing_lines(feed)[0])
    assert "tts" not in labels and "audio" not in labels


async def test_a_killed_turn_reports_nothing_and_leaves_nothing_behind(db):
    pipeline, _, bus = await _make_pipeline(db, [])
    feed = bus.subscribe()
    now = time.monotonic()
    pipeline._timing = {"speech_end": now, "stt_done": now, "submitted": now}
    pipeline.sentence_q.put("A long sentence still being spoken right now.")
    pipeline._turn_future = concurrent.futures.Future()

    def trip():
        time.sleep(0.03)
        pipeline.cancel_turn()

    threading.Thread(target=trip, daemon=True).start()

    await _respond_once(pipeline)
    await asyncio.sleep(0.05)
    assert _timing_lines(feed) == []
    assert pipeline._timing == {}


@pytest.mark.parametrize(
    "config",
    [
        {"voice": {}, "conversation": None},
        {"voice": None, "conversation": {"end_phrases": None}},
        {"conversation": {"window_s": None, "enabled": None, "cue": None}},
        {},
    ],
)
def test_an_empty_conversation_key_means_the_default_not_a_crash(config):
    """`conversation:` or `end_phrases:` with nothing under it is None in YAML.
    While the block was ignored that was harmless; now it must still be, because
    the pipeline is built outside voice's fail-soft load and a raise there stops
    Baby starting at all."""
    from ui import server

    pipeline = VoicePipeline(None, None, None, server._voice_config(config))
    assert pipeline.conversation_enabled is True
    assert pipeline.conversation_window_s == 60
    assert "stop listening" in pipeline.end_phrases


def test_the_server_hands_conversation_settings_to_voice():
    """`conversation:` is top-level in config.yaml; the pipeline only reads the
    dict it is given. Handing it `voice:` alone left follow-up mode on code
    defaults and ignored every configured end phrase."""
    import inspect

    from ui import server

    config = {
        "voice": {"barge_in": False},
        "conversation": {"enabled": False, "window_s": 7, "end_phrases": ["enough"]},
    }
    voice = server._voice_config(config)
    assert voice["barge_in"] is False
    assert "conversation" not in config["voice"], "the caller's config is not edited"

    pipeline = VoicePipeline(None, None, None, voice)
    assert pipeline.conversation_enabled is False
    assert pipeline.conversation_window_s == 7
    assert pipeline.end_phrases == ("enough",)
    assert "_build_voice(" in inspect.getsource(server.run_ui)
    assert "_voice_config(config)" in inspect.getsource(server._build_voice)


@pytest.mark.parametrize(
    "config",
    [{"voice": {"vad_silence_ms": "700ms"}}, {"voice": {"vad_silence_ms": None}},
     {"conversation": {"window_s": "10s"}}],
)
async def test_a_mistyped_voice_setting_costs_voice_not_the_app(config):
    """These are read when the pipeline is built, outside load()'s fail-soft
    path: a hand-edit typo used to stop Baby starting at all."""
    from ui import server

    pipeline, ok, notes = await server._build_voice(None, None, None, config)
    assert pipeline is None and ok is False
    assert notes[0].startswith("voice unavailable: bad voice setting")


async def test_a_good_voice_config_is_built_and_loaded(monkeypatch):
    from ui import server

    monkeypatch.setattr(VoicePipeline, "load", lambda self: (True, ["loaded"]))
    pipeline, ok, notes = await server._build_voice(None, None, None, {"voice": {}})
    assert isinstance(pipeline, VoicePipeline) and ok is True and notes == ["loaded"]


def test_a_single_end_phrase_written_as_a_string_is_one_phrase():
    """`end_phrases: bas` is one phrase, not the letters b, a and s -- which
    would end the conversation on any short follow-up with an "a" in it."""
    pipeline = VoicePipeline(None, None, None, {"conversation": {"end_phrases": "bas"}})
    assert pipeline.end_phrases == ("bas",)


# -- 15. the stream going quiet (DECISIONS #169) ---------------------------------


def _queued(pipeline):
    got = []
    while True:
        try:
            got.append(pipeline.sentence_q.get_nowait())
        except queue_mod.Empty:
            return got


async def test_bridge_flushes_when_the_stream_goes_quiet(db):
    """"Let me check." before a tool call: no next character ever proves the
    full stop ended the sentence, so the bridge says it once the model has been
    quiet for a moment instead of holding it until the tool returns."""
    pipeline, _, bus = await _make_pipeline(db, [])
    task = asyncio.create_task(pipeline._bridge())
    await asyncio.sleep(0)
    bus.publish("token", "voice", text="Let me check.")
    await asyncio.sleep(0.05)
    assert _queued(pipeline) == [], "flushed before the model had time to continue"
    await asyncio.sleep(0.4)
    assert _queued(pipeline) == ["Let me check."]
    bus.publish("token", "voice", text=" The weather is fine.")
    bus.publish("turn_end", "voice", reply="", status="ok")
    await asyncio.sleep(0.05)
    task.cancel()
    assert _queued(pipeline) == ["The weather is fine.", None]


async def test_bridge_does_not_flush_an_unfinished_sentence(db):
    pipeline, _, bus = await _make_pipeline(db, [])
    task = asyncio.create_task(pipeline._bridge())
    await asyncio.sleep(0)
    bus.publish("token", "voice", text="half a sentence and then Dr.")
    await asyncio.sleep(0.4)
    assert not task.done(), "a crashed bridge would pass this by queueing nothing"
    task.cancel()
    assert _queued(pipeline) == []


async def test_other_events_do_not_postpone_the_flush(db):
    """tts_rms arrives every 100 ms while Baby speaks. Counting the quiet from
    the last event would keep the held sentence waiting for as long as the
    previous one is playing."""
    pipeline, _, bus = await _make_pipeline(db, [])
    task = asyncio.create_task(pipeline._bridge())
    await asyncio.sleep(0)
    bus.publish("token", "voice", text="Let me check.")
    await asyncio.sleep(0.06)
    armed = pipeline._token_at
    for _ in range(7):
        bus.publish("tts_rms", "voice", rms=0.1)
        await asyncio.sleep(0.06)
    task.cancel()
    # The deadline itself must not move: on a loaded machine one long gap
    # between the sleeps above would let a last-event timer expire too.
    assert pipeline._token_at == armed
    assert _queued(pipeline) == ["Let me check."]


async def test_a_tool_call_releases_what_was_said_before_it(db):
    """tool_start is published once the generation that asked for the tool has
    finished, so the buffered line is complete. Waiting for the quiet timer glued
    it to the answer whenever the tool was quick: "Let me check.It is noon."."""
    pipeline, _, bus = await _make_pipeline(db, [])
    task = asyncio.create_task(pipeline._bridge())
    await asyncio.sleep(0)
    bus.publish("token", "voice", text="Let me")
    bus.publish("token", "voice", text=" check.")
    bus.publish("tool_start", "voice", tool="get_time", call_id="c1")
    await asyncio.sleep(0.05)  # well inside the quiet timer
    assert _queued(pipeline) == ["Let me check."]
    bus.publish("token", "voice", text="It")
    bus.publish("token", "voice", text=" is noon.")
    bus.publish("turn_end", "voice", reply="", status="ok")
    await asyncio.sleep(0.05)
    task.cancel()
    assert _queued(pipeline) == ["It is noon.", None]


async def test_the_confirm_line_follows_the_sentence_that_introduces_it(db):
    pipeline, _, bus = await _make_pipeline(db, [])
    task = asyncio.create_task(pipeline._bridge())
    await asyncio.sleep(0)
    bus.publish("token", "voice", text="Okay, closing Notepad.")
    bus.publish("confirm_request", "voice", confirm_id="c1", command="x")
    await asyncio.sleep(0.05)
    task.cancel()
    assert _queued(pipeline) == ["Okay, closing Notepad.", CONFIRM_SENTENCE]


async def test_a_waiting_token_is_read_before_the_flush(db):
    """The token that turns "12." into "12.5" can already be in the queue when
    the quiet deadline passes (the loop was busy). Flushing without looking
    would split the very number the timer exists to protect."""
    pipeline, _, bus = await _make_pipeline(db, [])
    task = asyncio.create_task(pipeline._bridge())
    await asyncio.sleep(0)
    bus.publish("token", "voice", text="It costs 12.")
    await asyncio.sleep(0.01)  # the bridge arms its timer
    time.sleep(0.3)  # the loop is busy past the deadline
    bus.publish("tts_rms", "voice", rms=0.1)
    bus.publish("token", "voice", text="5 dollars. ")
    await asyncio.sleep(0.05)
    task.cancel()
    assert _queued(pipeline) == ["It costs 12.5 dollars."]


async def test_an_event_arriving_as_the_deadline_fires_is_read_first(db, monkeypatch):
    """The narrow half of the same race: the timer fires, and the completing
    token is published before the bridge gets to run. It must not flush."""
    import voice.pipeline as pipeline_module

    pipeline, _, bus = await _make_pipeline(db, [])
    real_wait_for = asyncio.wait_for
    raced = []

    async def racing_wait_for(awaitable, timeout):
        if timeout is None or raced:
            return await real_wait_for(awaitable, timeout)
        raced.append(True)
        awaitable.close()
        bus.publish("token", "voice", text="5 dollars. ")
        raise TimeoutError

    monkeypatch.setattr(pipeline_module.asyncio, "wait_for", racing_wait_for)
    task = asyncio.create_task(pipeline._bridge())
    await asyncio.sleep(0)
    bus.publish("token", "voice", text="It costs 12.")
    await asyncio.sleep(0.05)
    task.cancel()
    assert raced
    assert _queued(pipeline) == ["It costs 12.5 dollars."]


# -- 16. a queue per turn, prefetch, and turns that end badly (DECISIONS #170) ----


class GatedAudio(FakeAudio):
    """Playback blocks until the gate opens (or it is stopped)."""

    def __init__(self, frames=None):
        super().__init__(frames)
        self.gate = threading.Event()

    def play(self, samples, samplerate, stop, on_level=None):
        self.play_calls += 1
        while not self.gate.is_set():
            if stop.is_set():
                return False
            time.sleep(0.005)
        return True


class GatedTTS(FakeTTS):
    """Synthesis blocks until the gate opens."""

    def __init__(self):
        super().__init__()
        self.gate = threading.Event()

    def synth(self, sentence):
        self.synthed.append(sentence)
        self.gate.wait(timeout=5)
        return np.zeros(2400, dtype=np.int16), 24000


async def _until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition never became true"
        await asyncio.sleep(0.01)


def _let_respond_out(pipeline):
    """Make a still-running _respond return: its thread lives in the loop's
    executor, and a test that fails while it runs would hang at loop close
    instead of reporting the failure."""
    pipeline._stopping.set()
    for stage in (pipeline.audio, pipeline.tts):
        gate = getattr(stage, "gate", None)
        if gate is not None:
            gate.set()


@contextlib.asynccontextmanager
async def _responding(pipeline, frames=None):
    """_respond running on a worker thread for the length of the block."""
    from voice.audio_io import FrameBuffer

    frames = FrameBuffer() if frames is None else frames
    task = asyncio.ensure_future(asyncio.to_thread(pipeline._respond, frames))
    try:
        yield task
    finally:
        if not task.done():
            _let_respond_out(pipeline)
        await asyncio.wait({task}, timeout=5)


async def _finish(respond, timeout=5.0):
    """Await a _responding task with a bound. Not wait_for: on timeout it would
    cancel the task, _responding would then see it done and leave the thread."""
    done, _ = await asyncio.wait({respond}, timeout=timeout)
    assert done, "_respond did not return"
    respond.result()


async def _respond_once(pipeline, timeout=5.0):
    """Run _respond to its return; fail (not hang) if it never returns."""
    async with _responding(pipeline) as task:
        done, _ = await asyncio.wait({task}, timeout=timeout)
        assert done, "_respond did not return"
        task.result()


def _status_lines(feed):
    lines = []
    while not feed.empty():
        event = feed.get_nowait()
        if event.kind == "status":
            lines.append(str(event.payload.get("text", "")))
    return lines


async def test_next_sentence_synthesized_during_playback(db):
    """Prefetch: sentence two is made while sentence one is still playing. It
    used to start only once one had finished, a synth-length gap at every stop."""
    pipeline, _, _ = await _make_pipeline(db, [])
    pipeline.audio = GatedAudio()
    for item in ("One.", "Two.", None):
        pipeline.sentence_q.put(item)
    async with _responding(pipeline) as respond:
        await _until(
            lambda: pipeline.tts.synthed == ["One.", "Two."] and pipeline.audio.play_calls == 1
        )
        assert pipeline.audio.play_calls == 1  # still on the first sentence
        pipeline.audio.gate.set()
        await _finish(respond)
    assert pipeline.audio.play_calls == 2
    assert pipeline.state == "idle"


async def test_prefetch_holds_a_bounded_amount_of_audio(db):
    """A very long reply must not be synthesised whole into memory: one playing,
    two ready, the rest stay as text until there is room."""
    pipeline, _, _ = await _make_pipeline(db, [])
    pipeline.audio = GatedAudio()
    for n in range(10):
        pipeline.sentence_q.put(f"Sentence {n}.")
    pipeline.sentence_q.put(None)
    async with _responding(pipeline) as respond:
        await _until(lambda: len(pipeline.tts.synthed) == 3)
        await asyncio.sleep(0.15)
        assert len(pipeline.tts.synthed) == 3
        pipeline.audio.gate.set()
        await _finish(respond)
    assert pipeline.tts.synthed == [f"Sentence {n}." for n in range(10)]  # all, in order


async def test_prefetch_off_makes_one_sentence_at_a_time(db):
    pipeline, _, _ = await _make_pipeline(db, [], cfg_over={"tts": {"prefetch": False}})
    pipeline.audio = GatedAudio()
    for item in ("One.", "Two.", None):
        pipeline.sentence_q.put(item)
    async with _responding(pipeline) as respond:
        await _until(lambda: pipeline.audio.play_calls == 1)
        await asyncio.sleep(0.15)
        assert pipeline.tts.synthed == ["One."]
        pipeline.audio.gate.set()
        await _finish(respond)
    assert pipeline.tts.synthed == ["One.", "Two."]


async def test_barge_in_detected_while_synth_runs(db):
    """Kokoro takes over half a second per sentence, and the mic was not read
    for any of it. Speaking over Baby while it is about to talk must land."""
    pipeline, _, _ = await _make_pipeline(db, [])
    pipeline.tts = GatedTTS()
    pipeline.sentence_q.put("A sentence that is still being synthesised.")
    pipeline.audio.frames += [SPEECH] * 10
    fake_turn = concurrent.futures.Future()
    pipeline._turn_future = fake_turn
    await _respond_once(pipeline, timeout=3)
    assert pipeline.state == LISTENING
    assert fake_turn.cancelled()
    assert pipeline.audio.play_calls == 0  # it never got to speak
    pipeline.tts.gate.set()


async def test_failed_sentence_is_skipped_rest_is_spoken(db):
    """One Kokoro error used to raise out of the pipeline and drop the rest of
    the reply. It now costs that sentence only, and says so in the feed."""

    class FlakyTTS(FakeTTS):
        def synth(self, sentence):
            if "two" in sentence:
                raise RuntimeError("kokoro fell over")
            return super().synth(sentence)

    pipeline, _, bus = await _make_pipeline(db, [])
    pipeline.tts = FlakyTTS()
    feed = bus.subscribe()
    for item in ("Sentence one.", "Sentence two.", "Sentence three.", None):
        pipeline.sentence_q.put(item)
    await _respond_once(pipeline)
    await asyncio.sleep(0.05)
    assert pipeline.audio.play_calls == 2
    assert pipeline.tts.synthed == ["Sentence one.", "Sentence three."]
    assert any("skipped a sentence" in line for line in _status_lines(feed))


async def test_a_sentence_that_cannot_be_played_does_not_end_the_reply(db):

    class FlakyAudio(FakeAudio):
        def play(self, samples, samplerate, stop, on_level=None):
            self.play_calls += 1
            if self.play_calls == 1:
                raise OSError("device went away")
            return True

    pipeline, _, bus = await _make_pipeline(db, [])
    pipeline.audio = FlakyAudio()
    feed = bus.subscribe()
    for item in ("One.", "Two.", None):
        pipeline.sentence_q.put(item)
    await _respond_once(pipeline)
    await asyncio.sleep(0.05)
    assert pipeline.audio.play_calls == 2
    assert pipeline.state == "idle"
    assert any("could not play a sentence" in line for line in _status_lines(feed))


async def test_stale_sentinel_lands_in_the_old_queue(db):
    """The point of a queue per turn: whatever a cut-off turn still publishes
    goes to the queue it was given, which nobody is reading any more."""
    pipeline, _, bus = await _make_pipeline(db, [])
    bridge = asyncio.create_task(pipeline._bridge())
    await asyncio.sleep(0)
    old_q = pipeline.sentence_q
    bus.publish("turn_start", "voice", turn_id=1)
    bus.publish("token", "voice", text="Old words. ")
    await asyncio.sleep(0.01)  # turn 1 is well under way before the next is submitted

    new_q = queue_mod.Queue()  # what _submit and _run_turn do for the next turn
    pipeline.sentence_q = pipeline._next_q = new_q
    bus.publish("token", "voice", text="Still old. ")
    bus.publish("turn_end", "voice", turn_id=1, status="cancelled", reply="")
    bus.publish("turn_start", "voice", turn_id=2)
    bus.publish("token", "voice", text="New words. ")
    bus.publish("turn_end", "voice", turn_id=2, status="ok", reply="")
    await asyncio.sleep(0.05)
    bridge.cancel()

    assert _queued(pipeline) == ["New words.", None]
    old = []
    while not old_q.empty():
        old.append(old_q.get_nowait())
    assert old == ["Old words.", "Still old.", None]


async def test_turn_end_of_another_turn_is_ignored(db):
    """A cut-off turn can finish unwinding after the next one has started. Its
    end must not be taken for the end of the turn now speaking."""
    pipeline, _, bus = await _make_pipeline(db, [])
    bridge = asyncio.create_task(pipeline._bridge())
    await asyncio.sleep(0)
    bus.publish("turn_start", "voice", turn_id=2)
    bus.publish("token", "voice", text="Still talking. ")
    bus.publish("turn_end", "voice", turn_id=1, status="cancelled", reply="")
    await asyncio.sleep(0.05)
    assert _queued(pipeline) == ["Still talking."]  # no end marker
    bus.publish("turn_end", "voice", turn_id=2, status="ok", reply="")
    await asyncio.sleep(0.05)
    bridge.cancel()
    assert _queued(pipeline) == [None]


async def test_new_turn_waits_for_cancelled_turn_to_unwind(db):
    """The previous turn's last events must all be published before the next
    turn's turn_start, or they would be read as the next turn's."""

    class SlowToStop:
        def __init__(self):
            self.log = []

        async def run_turn(self, text):
            self.log.append(f"start {text}")
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                await asyncio.sleep(0.2)  # writing the closure marker, publishing turn_end
                self.log.append(f"unwound {text}")
                raise

    pipeline, _, _ = await _make_pipeline(db, [])
    pipeline.agent = SlowToStop()
    first = pipeline._submit("one")
    await asyncio.sleep(0.05)
    first.cancel()
    second = pipeline._submit("two")
    await _until(lambda: "start two" in pipeline.agent.log)
    assert pipeline.agent.log == ["start one", "unwound one", "start two"]
    second.cancel()
    await asyncio.sleep(0.3)


async def test_a_turn_that_never_unwinds_does_not_block_voice_forever(db, monkeypatch):
    """The wait for the previous turn is capped. Without the cap, one turn that
    swallowed its cancellation would hold every later voice turn for good."""
    import voice.pipeline as pipeline_module

    class IgnoresCancellation:
        def __init__(self):
            self.log = []
            self.let_go = False

        async def run_turn(self, text):
            self.log.append(f"start {text}")
            while not self.let_go:
                try:
                    await asyncio.sleep(0.02)
                except asyncio.CancelledError:
                    continue  # swallowed: this turn will not stop when told to

    monkeypatch.setattr(pipeline_module, "_UNWIND_WAIT_S", 0.05)
    pipeline, _, _ = await _make_pipeline(db, [])
    agent = pipeline.agent = IgnoresCancellation()
    first = pipeline._submit("one")
    await asyncio.sleep(0.05)
    first.cancel()
    second = pipeline._submit("two")
    try:
        await _until(lambda: "start two" in agent.log, timeout=1.0)
    finally:
        agent.let_go = True  # both turns return, so the loop can close
        await asyncio.wait(
            {asyncio.wrap_future(first), asyncio.wrap_future(second)}, timeout=1.0
        )


async def test_the_kill_switch_lands_during_synthesis(db):
    """The voice thread no longer sits inside Kokoro, so /kill does not wait
    for the sentence to be made first -- and nothing of it is played."""
    pipeline, _, _ = await _make_pipeline(db, [])
    pipeline.tts = GatedTTS()  # holds every synthesis for 5 s unless released
    pipeline.sentence_q.put("A sentence Kokoro is still making.")
    pipeline._turn_future = concurrent.futures.Future()
    pipeline.state = "responding"

    def trip():
        time.sleep(0.05)
        pipeline.cancel_turn()

    threading.Thread(target=trip, daemon=True).start()
    started = time.monotonic()
    await _respond_once(pipeline, timeout=4)
    assert time.monotonic() - started < 2.5, "waited for the synthesis to finish"
    assert pipeline.state == "idle"
    assert pipeline.audio.play_calls == 0
    pipeline.tts.gate.set()


async def test_a_cut_off_turn_lands_in_its_own_queue_end_to_end(db):
    """Through the real _submit, _run_turn, agent and bridge: a turn cut off
    mid-reply, and the next one started at once. Nothing of the first reaches
    the second's queue."""
    from core.providers.base import Chunk

    class SlowFirstTurn:
        name = "fake"
        calls = 0

        async def chat(self, messages, tools=None, **opts):
            SlowFirstTurn.calls += 1
            if SlowFirstTurn.calls == 1:
                yield Chunk(delta="First turn words. And more")
                await asyncio.sleep(10)
            yield Chunk(delta="Second turn.")
            yield Chunk(done=True)

        async def healthy(self):
            return True

    pipeline, _, _ = await _make_pipeline(db, [])
    pipeline.agent.provider = SlowFirstTurn()
    bridge = asyncio.create_task(pipeline._bridge())
    await asyncio.sleep(0)
    first = pipeline._turn_future = pipeline._submit("one")
    first_q = pipeline.sentence_q
    await _until(lambda: not first_q.empty())
    pipeline._cancel_turn()
    second = pipeline._submit("two")
    second_q = pipeline.sentence_q
    await asyncio.wait({asyncio.wrap_future(second)}, timeout=2)
    await asyncio.sleep(0.05)
    bridge.cancel()
    assert first.cancelled()
    spoken_second = []
    while not second_q.empty():
        spoken_second.append(second_q.get_nowait())
    assert spoken_second == ["Second turn.", None]
    spoken_first = []
    while not first_q.empty():
        spoken_first.append(first_q.get_nowait())
    assert spoken_first == ["First turn words.", None]  # the half sentence is dropped


async def test_a_failing_model_is_apologised_for_with_its_real_turn(db):
    from voice.pipeline import APOLOGY_EN

    class Broken:
        name = "fake"

        async def chat(self, messages, tools=None, **opts):
            raise RuntimeError("provider down")
            yield  # pragma: no cover

        async def healthy(self):
            return True

    pipeline, _, _ = await _make_pipeline(db, [])
    pipeline.agent.provider = Broken()
    bridge = asyncio.create_task(pipeline._bridge())
    await asyncio.sleep(0)
    future = pipeline._submit("anything")
    await asyncio.wait({asyncio.wrap_future(future)}, timeout=2)
    await asyncio.sleep(0.05)
    bridge.cancel()
    assert _queued(pipeline) == [APOLOGY_EN, None]


async def test_each_turn_gets_its_own_queue(db):
    pipeline, _, bus = await _make_pipeline(db, ["First reply.", "Second reply."])
    bridge = asyncio.create_task(pipeline._bridge())
    await asyncio.sleep(0)
    before = pipeline.sentence_q
    await asyncio.wrap_future(pipeline._submit("one"))
    await asyncio.sleep(0.05)
    first = pipeline.sentence_q
    assert first is not before
    assert _queued(pipeline) == ["First reply.", None]
    await asyncio.wrap_future(pipeline._submit("two"))
    await asyncio.sleep(0.05)
    bridge.cancel()
    assert pipeline.sentence_q is not first
    assert _queued(pipeline) == ["Second reply.", None]
    assert first.empty()


async def test_respond_ends_when_turn_died_silently(db):
    """A turn can die without publishing its end (it raised between turn_start
    and its try block). _respond used to wait there until someone spoke."""

    from voice.pipeline import APOLOGY_EN

    pipeline, _, bus = await _make_pipeline(db, [])
    feed = bus.subscribe()
    dead = concurrent.futures.Future()
    dead.set_exception(RuntimeError("died before its try block"))
    pipeline._turn_future = dead
    pipeline.state = "responding"
    started = time.monotonic()
    await _respond_once(pipeline, timeout=4)
    assert 0.9 < time.monotonic() - started < 3.0
    assert pipeline.state == "idle"
    assert pipeline.tts.synthed == [APOLOGY_EN]  # it raised: say so
    await asyncio.sleep(0.05)
    assert any("without its end marker" in line for line in _status_lines(feed))


async def test_a_turn_that_ended_quietly_is_not_apologised_for(db):
    pipeline, _, _ = await _make_pipeline(db, [])
    finished = concurrent.futures.Future()
    finished.set_result("a reply whose end marker the bus dropped")
    pipeline._turn_future = finished
    await _respond_once(pipeline, timeout=4)
    assert pipeline.state == "idle"
    assert pipeline.tts.synthed == []


async def test_a_running_turn_is_not_given_up_on(db, monkeypatch):
    """The watchdog is for a turn that is OVER. A slow tool is not that."""
    import voice.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "_DEAD_TURN_S", 0.1)
    pipeline, _, _ = await _make_pipeline(db, [])
    pipeline._turn_future = concurrent.futures.Future()  # still running
    async with _responding(pipeline) as respond:
        await asyncio.sleep(0.8)  # eight watchdog periods
        assert not respond.done()
        pipeline.sentence_q.put(None)
        await _finish(respond)


async def test_a_synthesis_of_the_wrong_shape_costs_one_sentence(db):
    class OddTTS(FakeTTS):
        def synth(self, sentence):
            if "two" in sentence:
                return (np.zeros(10, dtype=np.int16),)  # not (pcm, rate)
            return super().synth(sentence)

    pipeline, _, _ = await _make_pipeline(db, [])
    pipeline.tts = OddTTS()
    for item in ("Sentence one.", "Sentence two.", "Sentence three.", None):
        pipeline.sentence_q.put(item)
    await _respond_once(pipeline)
    assert pipeline.audio.play_calls == 2
    assert pipeline.state == "idle"


async def test_the_synth_worker_never_holds_the_process_open(db):
    """A pool worker is joined at interpreter exit, so a shutdown landing mid-
    sentence waited for Kokoro -- forever, if a call hung."""
    pipeline, _, _ = await _make_pipeline(db, [])
    await asyncio.to_thread(pipeline._synth, "Hello.")
    assert pipeline._synth_thread.name == "baby-tts"
    assert pipeline._synth_thread.daemon


async def test_error_and_capped_turns_are_spoken(db):
    """Both used to end in silence and a follow-up beep: an error cleared the
    buffer, and the tool-limit message is never streamed as tokens at all."""
    from voice.pipeline import APOLOGY_EN, APOLOGY_HI

    pipeline, _, bus = await _make_pipeline(db, [])
    bridge = asyncio.create_task(pipeline._bridge())
    await asyncio.sleep(0)
    bus.publish("token", "voice", text="Half a sentence that never fini")
    bus.publish("turn_end", "voice", status="error", reply="")
    await asyncio.sleep(0.05)
    assert _queued(pipeline) == [APOLOGY_EN, None]

    pipeline._heard_hindi = True
    bus.publish("turn_end", "voice", status="error", reply="")
    await asyncio.sleep(0.05)
    assert _queued(pipeline) == [APOLOGY_HI, None]

    bus.publish("turn_end", "voice", status="capped", reply="I hit my tool-step limit. Continue?")
    await asyncio.sleep(0.05)
    assert _queued(pipeline) == ["I hit my tool-step limit.", "Continue?", None]

    bus.publish("token", "voice", text="Half a sentence that never fini")
    bus.publish("turn_end", "voice", status="cancelled", reply="(stopped)")
    await asyncio.sleep(0.05)
    bridge.cancel()
    assert _queued(pipeline) == [None]  # a cancelled turn stays silent


async def test_bridge_survives_a_raising_event(db):
    """The bridge is the only thing feeding speech. One malformed event used to
    kill it, and Baby went silent for good with nothing in the feed."""
    pipeline, _, bus = await _make_pipeline(db, [])
    feed = bus.subscribe()
    bridge = asyncio.create_task(pipeline._bridge())
    await asyncio.sleep(0)
    bus.publish("token", "voice", text=None)  # "" + None raises
    bus.publish("token", "voice", text="Still here. ")
    await asyncio.sleep(0.05)
    assert not bridge.done()
    bridge.cancel()
    assert _queued(pipeline) == ["Still here."]
    assert any("could not queue speech" in line for line in _status_lines(feed))


async def test_a_new_turn_starts_with_an_empty_buffer(db):
    """Text left over from a turn that never ended must not be glued to the
    front of the next turn's first sentence."""
    pipeline, _, bus = await _make_pipeline(db, [])
    bridge = asyncio.create_task(pipeline._bridge())
    await asyncio.sleep(0)
    bus.publish("token", "voice", text="Leftover half of a")
    bus.publish("turn_start", "voice", turn_id=7)
    bus.publish("token", "voice", text="Fresh. ")
    await asyncio.sleep(0.05)
    bridge.cancel()
    assert _queued(pipeline) == ["Fresh."]


@pytest.mark.parametrize(
    ("heard", "hindi"), [("आज मौसम कैसा है", True), ("how is the weather", False)]
)
async def test_the_apology_language_follows_what_was_heard(db, heard, hindi):
    from voice.audio_io import FrameBuffer

    pipeline, _, _ = await _make_pipeline(db, ["ok"], stt_text=heard)
    pipeline._heard_hindi = not hindi
    pipeline.audio.frames += [SPEECH, SILENCE, SILENCE]
    await asyncio.to_thread(pipeline._listen, FrameBuffer())
    await asyncio.sleep(0.1)
    assert pipeline._heard_hindi is hindi


async def test_disabled_barge_in_still_paces_on_the_mic(db):
    """With barge-in off the mic poll returned before reading, so the playback
    loop span a whole core. It must still wait on the mic, and never judge it."""

    class CountingAudio(FakeAudio):
        reads = 0

        def read(self, timeout=None):
            self.reads += 1
            return super().read(timeout)

    class NeverAskedVAD(FakeVAD):
        def is_speech(self, chunk, threshold=None):
            raise AssertionError("barge-in is off: the mic must not be judged")

    pipeline, _, _ = await _make_pipeline(db, [], cfg_over={"barge_in": False})
    pipeline.audio = CountingAudio([SPEECH] * 10)
    pipeline.vad = NeverAskedVAD()
    pipeline.sentence_q.put("A sentence.")
    pipeline.sentence_q.put(None)
    await _respond_once(pipeline)
    assert pipeline.state == "idle"
    assert pipeline.audio.play_calls == 1  # played through
    assert 0 < pipeline.audio.reads < 200  # paced by the mic, not spinning


async def test_an_announcement_is_synthesised_on_the_synth_worker(db):
    pipeline, _, _ = await _make_pipeline(db, [])
    seen = []
    real = pipeline.tts.synth

    def synth(text):
        seen.append(threading.current_thread().name)
        return real(text)

    pipeline.tts.synth = synth
    pipeline.announce("Task done.")
    from voice.audio_io import FrameBuffer

    await asyncio.to_thread(pipeline._idle, FrameBuffer())
    assert seen and seen[0].startswith("baby-tts")


# -- 17. barge-in: stop at once, keep the words (DECISIONS #171) -------------------


def test_play_aborts_the_output_stream_on_stop(monkeypatch):
    """Leaving the `with` calls the stream's stop(), which plays out whatever the
    device still has queued -- Baby kept talking for a beat after being cut off."""
    import sys
    import types

    from voice.audio_io import AudioIO

    calls = []
    stop = threading.Event()

    class FakeStream:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            calls.append("exit")

        def write(self, block):
            calls.append("write")
            stop.set()  # the user speaks during the first block

        def abort(self):
            calls.append("abort")

    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(OutputStream=FakeStream))
    played = AudioIO().play(np.zeros(24000, dtype=np.int16), 24000, stop)
    assert played is False
    assert calls == ["write", "abort", "exit"]


def test_frame_buffer_prepend_puts_chunks_back_in_order():
    from voice.audio_io import FrameBuffer

    buf = FrameBuffer()
    buf.push(np.array([5, 6], dtype=np.int16))
    buf.prepend([np.array([1, 2], dtype=np.int16), np.array([3, 4], dtype=np.int16)])
    assert buf.pop(6).tolist() == [1, 2, 3, 4, 5, 6]
    buf.prepend([])
    assert buf.pop(1) is None


class RecordingSTT(FakeSTT):
    def __init__(self, text="stop, I meant the other one"):
        super().__init__(text)
        self.heard = []

    def transcribe(self, pcm):
        self.heard.append(pcm)
        return super().transcribe(pcm)


def _speech_chunks(pcm):
    return int((pcm == SPEECH[0]).sum()) // len(SPEECH)


_KEEP = {"barge_in_keep_audio": True}


async def _barge_in_then_listen(db, frames_after_trigger, cfg_over=None, stt=None):
    """Baby speaks; the user talks over it (6 speech frames trip barge-in), then
    `frames_after_trigger`. Keep-audio on unless cfg_over says otherwise.
    Returns the pipeline after the capture."""
    from voice.audio_io import FrameBuffer

    pipeline, _, _ = await _make_pipeline(db, [], cfg_over={**_KEEP, **(cfg_over or {})})
    pipeline.stt = stt or RecordingSTT()
    pipeline.sentence_q.put("A very long sentence being spoken right now.")
    pipeline.audio.frames += [SPEECH] * 6 + list(frames_after_trigger)
    pipeline._turn_future = concurrent.futures.Future()
    fb = FrameBuffer()
    async with _responding(pipeline, fb) as respond:
        await _finish(respond)
    assert pipeline.state == LISTENING
    await asyncio.to_thread(pipeline._listen, fb)
    return pipeline


async def test_barge_in_keeps_the_interrupting_words(db):
    """The words that interrupted Baby were thrown away and a beep played over
    the user, who had to say it all again. They are now the start of the capture."""
    pipeline = await _barge_in_then_listen(db, [SPEECH] * 4 + [SILENCE] * 3)
    assert pipeline.audio.beeped == 0
    assert len(pipeline.stt.heard) == 1
    assert _speech_chunks(pipeline.stt.heard[0]) == 10  # the 6 that tripped it, then 4


async def test_short_interjection_is_transcribed(db):
    """"Stop!" is about as long as the barge-in streak itself. Replaying the
    streak through the normal capture means it is heard and endpointed like any
    other speech, instead of the capture starting on the silence after it."""
    pipeline = await _barge_in_then_listen(db, [SILENCE] * 3)
    assert len(pipeline.stt.heard) == 1
    assert _speech_chunks(pipeline.stt.heard[0]) == 6


async def test_keep_audio_off_beeps_and_discards(db):
    pipeline = await _barge_in_then_listen(
        db,
        [SPEECH] * 4 + [SILENCE] * 3,
        # The follow-up cue off: the beep here is the wake beep, not the cue.
        cfg_over={"barge_in_keep_audio": False, "conversation": {"enabled": False, "cue": False}},
    )
    assert pipeline.audio.beeped == 1
    assert _speech_chunks(pipeline.stt.heard[0]) == 4  # only what came after the beep


async def test_an_end_phrase_said_over_baby_ends_the_conversation(db):
    """In a follow-up window "bas" closes the conversation. Said over Baby it
    used to be lost with the beep; kept, it must close it too -- not be sent to
    the model as a question."""
    pipeline = await _barge_in_then_listen(
        db,
        [SILENCE] * 3,
        cfg_over={"conversation": {"enabled": True, "end_phrases": ["bas"]}},
        stt=RecordingSTT("bas"),
    )
    assert pipeline.state == "idle"
    assert pipeline._turn_future is None or pipeline._turn_future.cancelled()


async def test_a_partial_frame_is_not_lost_at_the_switch(db):
    """Mic blocks do not line up with 512-sample chunks, so the capture buffer
    holds a remainder when barge-in trips. It belongs after the replayed chunks."""
    louder = np.full(768, 2000, dtype=np.int16)  # 1.5 chunks, told apart by value
    from voice.audio_io import FrameBuffer

    pipeline, _, _ = await _make_pipeline(db, [], cfg_over=_KEEP)
    pipeline.stt = RecordingSTT()
    pipeline.sentence_q.put("A very long sentence being spoken right now.")
    pipeline.audio.frames += [SPEECH] * 5 + [louder] + [SILENCE] * 3
    pipeline._turn_future = concurrent.futures.Future()
    fb = FrameBuffer()
    async with _responding(pipeline, fb) as respond:
        await _finish(respond)
    await asyncio.to_thread(pipeline._listen, fb)
    pcm = pipeline.stt.heard[0]
    assert (pcm[: 5 * 512] == SPEECH[0]).all()
    assert (pcm[5 * 512 : 5 * 512 + 768] == 2000).all()  # the whole frame, in order


async def test_only_this_replys_audio_is_replayed(db):
    """Audio kept during an earlier reply must not come back in front of a later
    interruption."""
    from voice.audio_io import FrameBuffer

    pipeline, _, _ = await _make_pipeline(db, [], cfg_over=_KEEP)
    pipeline.stt = RecordingSTT()
    stale = np.full(512, 4242, dtype=np.int16)
    pipeline._heard.extend([stale] * 5)
    pipeline.sentence_q.put("A very long sentence being spoken right now.")
    pipeline.audio.frames += [SPEECH] * 6 + [SILENCE] * 3
    pipeline._turn_future = concurrent.futures.Future()
    fb = FrameBuffer()
    async with _responding(pipeline, fb) as respond:
        await _finish(respond)
    await asyncio.to_thread(pipeline._listen, fb)
    assert not (pipeline.stt.heard[0] == 4242).any()


@pytest.mark.parametrize("keep", [True, False])
async def test_barge_in_does_not_reset_session_trust(db, keep):
    """A fresh wake starts a new session with optimistic speaker trust. An
    interruption is the same session, whether or not its audio is kept: an
    unknown speaker must not win back trust by talking over Baby."""
    from voice.audio_io import FrameBuffer

    pipeline, _, _ = await _make_pipeline(db, [], cfg_over={"barge_in_keep_audio": keep})
    resets = []
    pipeline.session_trust.reset = lambda: resets.append(True)
    pipeline.sentence_q.put("A very long sentence being spoken right now.")
    pipeline.audio.frames += [SPEECH] * 10
    pipeline._turn_future = concurrent.futures.Future()
    fb = FrameBuffer()
    async with _responding(pipeline, fb) as respond:
        await _finish(respond)
    assert pipeline.state == LISTENING
    assert resets == []
    pipeline._enter_listening(fb)  # a wake word still starts a new session
    assert resets == [True]


def test_play_aborts_a_stop_that_lands_on_the_last_block(monkeypatch):
    """The last block is written but still queued for the output latency, and
    leaving the `with` waits for all of it. A stop arriving then must abort too."""
    import sys
    import types

    from voice.audio_io import AudioIO

    calls = []
    stop = threading.Event()

    class FakeStream:
        latency = 0.2

        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            calls.append("exit")

        def write(self, block):
            calls.append("write")

        def abort(self):
            calls.append("abort")

    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(OutputStream=FakeStream))
    threading.Timer(0.05, stop.set).start()  # after the only block is written
    played = AudioIO().play(np.zeros(1200, dtype=np.int16), 24000, stop)
    assert played is False
    assert calls == ["write", "abort", "exit"]


async def test_interrupting_closes_the_provider_stream(db):
    """Cancelling the voice turn reaches the model's stream through the real
    router, which is what stops the generation (and the billing), not just the
    speech."""
    from core.providers.base import Chunk
    from core.router import CloudRouter

    class Streaming:
        name = "fake"
        closed = False
        started = None

        async def chat(self, messages, tools=None, **opts):
            try:
                yield Chunk(delta="Once upon a time ")
                Streaming.started = True
                await asyncio.sleep(30)
                yield Chunk(done=True)
            finally:
                Streaming.closed = True

        async def healthy(self):
            return True

    pipeline, _, _ = await _make_pipeline(db, [])
    pipeline.agent.provider = CloudRouter(Streaming())
    pipeline._turn_future = pipeline._submit("tell me a story")
    await _until(lambda: Streaming.started)
    pipeline._cancel_turn()
    await _until(lambda: Streaming.closed)


def test_the_shipped_config_names_the_new_voice_switches():
    """Prefetch is on for everyone. Keeping the interruption is off for a fresh
    install -- on open speakers Baby can trip barge-in on its own voice and would
    then answer itself -- and on in a headset owner's own config."""
    voice = _shipped_config()["voice"]
    assert voice["tts"]["prefetch"] is True
    assert voice["barge_in_keep_audio"] is False
    pipeline = VoicePipeline(None, None, None, voice)
    assert pipeline.prefetch is True and pipeline.barge_keep_audio is False
    # The code agrees, for an upgraded install whose config has neither key.
    bare = VoicePipeline(None, None, None, {})
    assert bare.prefetch is True and bare.barge_keep_audio is False


# -- 18. thinking aloud, the pause, and Whisper's language (DECISIONS #173) ---------


async def test_filler_keeps_listening_without_a_turn(db):
    """"Uh... let me think" used to go to the model, which answered it. It is
    not a request: no model call, no beep, and the mic stays open for the real one."""
    from voice.audio_io import FrameBuffer

    pipeline, provider, bus = await _make_pipeline(db, ["ok"])
    said = ["Uh... let me think.", "what time is it"]
    pipeline.stt.transcribe = lambda pcm: (said.pop(0), "en")
    # The question is already in the mic queue while the filler is transcribed;
    # a drain there would throw its start away.
    pipeline.audio.drain = pipeline.audio.frames.clear
    events = bus.subscribe()
    frames = FrameBuffer()
    pipeline.audio.frames += [SPEECH, SILENCE, SILENCE]
    pipeline.audio.frames += [SILENCE] + [SPEECH] * 3 + [SILENCE, SILENCE]
    await asyncio.to_thread(pipeline._listen, frames)
    await asyncio.sleep(0.1)
    assert pipeline.state == LISTENING
    assert provider.requests == []
    assert pipeline.audio.beeped == 0
    statuses = []
    while not events.empty():
        event = events.get_nowait()
        if event.kind == "status":
            statuses.append(event.payload["text"])
    # The feed shows what was set aside, without "heard" (the UI would light the
    # path to the model, and nothing went there).
    assert statuses == ["voice: listening (thinking aloud: 'Uh... let me think.')"]
    # The VAD starts over, so the real question is captured whole.
    assert pipeline.vad.speech_started is False
    await asyncio.to_thread(pipeline._listen, frames)
    await asyncio.sleep(0.1)
    assert provider.requests
    # That capture was read from audio queued during the filler's transcription,
    # so its end-of-speech time is unknown: no timing line rather than a wrong one.
    assert "speech_end" not in pipeline._timing


async def test_a_filler_is_not_scored_as_a_speaker(db):
    """Thinking aloud never reaches speaker verification, so a "hmm" cannot move
    the session's trust either way."""
    from voice.audio_io import FrameBuffer

    pipeline, provider, _ = await _make_pipeline(db, ["never"], stt_text="hmm")
    pipeline.verifier = FakeVerifier(ok=False, similarity=0.1)
    pipeline.audio.frames += [SPEECH, SILENCE, SILENCE]
    await asyncio.to_thread(pipeline._listen, FrameBuffer())
    assert pipeline.verifier.calls == 0
    assert not pipeline.session_trust._scores
    assert provider.requests == []


async def test_with_the_filter_off_filler_is_a_turn(db):
    from voice.audio_io import FrameBuffer

    pipeline, provider, _ = await _make_pipeline(
        db, ["ok"], stt_text="Uh... let me think.", cfg_over={"filler_filter": False}
    )
    pipeline.audio.frames += [SPEECH, SILENCE, SILENCE]
    await asyncio.to_thread(pipeline._listen, FrameBuffer())
    await asyncio.sleep(0.1)
    assert pipeline.state == "responding"
    assert provider.requests


async def test_a_short_yes_is_never_taken_for_filler(db):
    from voice.audio_io import FrameBuffer

    pipeline, provider, _ = await _make_pipeline(db, ["ok"], stt_text="haan")
    pipeline.audio.frames += [SPEECH, SILENCE, SILENCE]
    await asyncio.to_thread(pipeline._listen, FrameBuffer())
    await asyncio.sleep(0.1)
    assert provider.requests


async def test_thinking_aloud_cannot_hold_the_mic_open_for_ever(db):
    """Three fillers in a row only re-open the mic; the fourth is a turn, so a
    wake word cannot be stretched into an open mic by "hmm" every few seconds."""
    from voice.audio_io import FrameBuffer
    from voice.pipeline import _MAX_FILLERS

    assert _MAX_FILLERS == 3  # what DECISIONS #173 promises
    pipeline, provider, _ = await _make_pipeline(db, ["ok"], stt_text="hmm")
    frames = FrameBuffer()

    async def say_hmm():
        pipeline.audio.frames += [SPEECH, SILENCE, SILENCE]
        await asyncio.to_thread(pipeline._listen, frames)

    for _ in range(3):
        await say_hmm()
        assert pipeline.state == LISTENING
    # A new wake word starts the count again: three more are set aside.
    pipeline._enter_listening(frames)
    assert pipeline._fillers == 0
    for _ in range(3):
        await say_hmm()
        assert pipeline.state == LISTENING
    assert provider.requests == []
    await say_hmm()
    await asyncio.sleep(0.1)
    assert provider.requests


async def test_push_to_talk_pass_covers_one_capture(db):
    """PTT skips speaker verification for what is said after the hotkey -- not for
    whoever speaks after a filler, however long later."""
    from voice.audio_io import FrameBuffer

    pipeline, provider, _ = await _make_pipeline(db, ["ok"], stt_text="uh")
    pipeline.verifier = FakeVerifier(ok=True, similarity=0.9)
    frames = FrameBuffer()
    pipeline._enter_listening(frames, source="ptt")
    pipeline.audio.frames += [SPEECH, SILENCE, SILENCE]
    await asyncio.to_thread(pipeline._listen, frames)
    assert pipeline.verifier.calls == 0
    assert pipeline._listen_source == "wake"
    pipeline.stt.text = "what time is it"
    pipeline.audio.frames += [SPEECH, SILENCE, SILENCE]
    await asyncio.to_thread(pipeline._listen, frames)
    await asyncio.sleep(0.1)
    assert pipeline.verifier.calls == 1
    assert provider.requests


def test_the_pause_that_ends_an_utterance_is_longer():
    voice = _shipped_config()["voice"]
    assert voice["vad_silence_ms"] == 700
    # A config that does not name the key; every installed one names 400.
    assert VoicePipeline(None, None, None, {})._vad_silence_s == 0.7
    assert voice["filler_filter"] is True
    assert voice["stt"]["languages"] == []  # unlocked unless the owner chooses


@pytest.mark.parametrize(("cfg", "ms"), [({}, 700), ({"vad_silence_ms": 400}, 400)])
def test_the_vad_waits_as_long_as_the_timing_line_says(monkeypatch, cfg, ms):
    import voice.vad as vad_module

    monkeypatch.setattr(vad_module.VoiceDetector, "load", lambda self: None)
    pipeline = VoicePipeline(None, None, None, cfg)
    pipeline._load_vad()
    assert pipeline.vad.silence_ms == ms
    assert pipeline._vad_silence_s == ms / 1000


class _FakeCT2Whisper:
    """Stands in for ctranslate2.models.Whisper: detection sorted by likelihood."""

    is_multilingual = True

    def __init__(self, ranked):
        self.ranked = ranked

    def detect_language(self, encoder_output):
        return [list(self.ranked)]

    def encode(self, features, to_cpu=False):
        return ("encoded", features)


def test_language_lock_prefers_best_allowed():
    """Hindi heard as Urdu: with [en, hi] allowed, Hindi comes first, and the
    allowed ones keep their likelihood order."""
    from voice.stt import _LanguageLock

    inner = _FakeCT2Whisper([("<|ur|>", 0.5), ("<|hi|>", 0.3), ("<|en|>", 0.1), ("<|pa|>", 0.1)])
    locked = _LanguageLock(inner, ["en", "hi"])
    assert [code for code, _ in locked.detect_language("enc")[0]] == [
        "<|hi|>", "<|en|>", "<|ur|>", "<|pa|>",
    ]


def test_language_lock_delegates_the_rest():
    from voice.stt import _LanguageLock

    inner = _FakeCT2Whisper([("<|en|>", 1.0)])
    locked = _LanguageLock(inner, ["en"])
    assert locked.is_multilingual is True
    assert locked.encode("f") == ("encoded", "f")


class _FakeWhisperModel:
    supported_languages = ["en", "hi", "ur", "pa"]

    def __init__(self, *args, **kwargs):
        self.model = _FakeCT2Whisper([("<|ur|>", 0.6), ("<|hi|>", 0.4)])


def _load_stt(monkeypatch, languages):
    import faster_whisper

    from voice.stt import SpeechToText

    monkeypatch.setattr(faster_whisper, "WhisperModel", _FakeWhisperModel)
    stt = SpeechToText(languages=languages)
    stt.load()
    return stt


def test_unlocked_default_leaves_model_untouched(monkeypatch):
    from voice.stt import _LanguageLock

    assert not isinstance(_load_stt(monkeypatch, ())._model.model, _LanguageLock)
    # A code Whisper does not know is ignored, not a lock on every language.
    assert not isinstance(_load_stt(monkeypatch, ("xx",))._model.model, _LanguageLock)


@pytest.mark.parametrize(
    ("written", "read"),
    [("hi", ("hi",)), ("en, hi", ("en", "hi")), ("[en, hi]", ("en", "hi")),
     (["EN", "HI"], ("en", "hi")), ((" hi ",), ("hi",))],
)
def test_hand_written_languages_still_lock(monkeypatch, written, read):
    stt = _load_stt(monkeypatch, written)
    assert stt.languages == read
    assert stt._model.model.detect_language("enc")[0][0][0] == "<|hi|>"


@pytest.mark.parametrize("written", [True, 1, None])
def test_odd_language_values_lock_nothing_and_do_not_crash(monkeypatch, written):
    from voice.stt import _LanguageLock

    assert not isinstance(_load_stt(monkeypatch, written or ())._model.model, _LanguageLock)


def test_configured_languages_lock_detection(monkeypatch):
    stt = _load_stt(monkeypatch, ("en", "hi"))
    assert stt._model.model.detect_language("enc")[0][0][0] == "<|hi|>"


async def test_the_pipeline_hands_the_languages_to_whisper(db, monkeypatch):
    import voice.stt as stt_module

    seen = {}

    class Recording(stt_module.SpeechToText):
        def __init__(self, **kwargs):
            seen.update(kwargs)
            super().__init__(**kwargs)

        def load(self):
            pass

    monkeypatch.setattr(stt_module, "SpeechToText", Recording)
    # Written by hand as a string rather than a list: still two languages.
    pipeline, _, _ = await _make_pipeline(db, [], cfg_over={"stt": {"languages": "en, hi"}})
    pipeline.stt = None
    pipeline._load_stt()
    assert "languages" in seen
    assert pipeline.stt.languages == ("en", "hi")


def test_the_shipped_end_phrases_are_the_ones_the_pipeline_uses():
    from ui import server

    config = _shipped_config()
    pipeline = VoicePipeline(None, None, None, server._voice_config(config))
    assert pipeline.end_phrases == tuple(config["conversation"]["end_phrases"])


# -- 19. end to end: stream, speak, interrupt, carry on (spec Phase 8) ----------


class _StreamingProvider(FakeProvider):
    """Streams each scripted reply a word at a time, pausing after every sentence
    the way a real model's tokens trickle in. Knows whether it is mid-stream."""

    def __init__(self, script, pause=0.5):
        super().__init__(script)
        self.pause = pause
        self.streaming = False
        self.cancelled = 0

    async def chat(self, messages, tools=None, **opts):
        from core.providers.base import Chunk

        self.requests.append([dict(m) for m in messages])
        words = self.script.pop(0).split(" ")
        self.streaming = True
        try:
            for n, word in enumerate(words):
                yield Chunk(delta=word if n == 0 else f" {word}")
                await asyncio.sleep(self.pause if word.endswith(".") else 0.01)
            yield Chunk(done=True)
        except (asyncio.CancelledError, GeneratorExit):
            self.cancelled += 1
            raise
        finally:
            self.streaming = False


class _NumberedTTS(FakeTTS):
    """Every clip carries the number of the sentence it was made from."""

    def synth(self, sentence):
        self.synthed.append(sentence)
        return np.full(2400, len(self.synthed), dtype=np.int16), 24000


class _ListeningAudio(FakeAudio):
    """Records which sentence each play() was and whether the model was still
    writing at the time; the user starts talking over the second sentence."""

    def __init__(self, tts, provider, talk_over):
        super().__init__()
        self.tts, self.provider, self.talk_over = tts, provider, talk_over
        self.played = []  # (sentence, model still streaming, stopped early)

    def play(self, samples, samplerate, stop, on_level=None):
        sentence = self.tts.synthed[int(samples[0]) - 1]
        streaming = self.provider.streaming
        if len(self.played) == 1 and self.talk_over:
            # The user talks over sentence two, which plays until Baby stops it
            # (bounded): however slow the machine, the cut lands in this sentence.
            self.play_calls += 1
            self.frames += self.talk_over
            self.talk_over = None
            finished = not stop.wait(5)
        else:
            finished = super().play(samples, samplerate, stop, on_level)
        self.played.append((sentence, streaming, not finished))
        return finished


async def test_a_conversation_end_to_end(db):
    """Wake, ask, and the answer starts playing while the model is still writing;
    sentences play in order; talking over Baby stops it mid-sentence and closes the
    model's stream; nothing from the old answer plays after that; and the words
    that interrupted are the start of the next question, which gets its answer."""
    from voice.audio_io import FrameBuffer

    first = "It's sunny today. Twenty eight degrees. Light wind from the west. Carry water."
    provider = _StreamingProvider([first, "Tomorrow brings rain."])
    pipeline, _, bus = await _make_pipeline(db, [], frames=[WAKE], cfg_over=_KEEP)
    pipeline.agent.provider = provider
    pipeline.tts = _NumberedTTS()
    said = ["what's the weather", "and tomorrow?"]
    pipeline.stt = RecordingSTT()
    pipeline.stt.transcribe = lambda pcm: (pipeline.stt.heard.append(pcm), (said.pop(0), "en"))[1]
    talk_over = [SPEECH] * 6 + [SPEECH] * 4 + [SILENCE] * 3
    pipeline.audio = _ListeningAudio(pipeline.tts, provider, talk_over)
    pipeline.audio.frames += [WAKE, SPEECH, SPEECH, SILENCE, SILENCE]
    bridge = asyncio.create_task(pipeline._bridge())
    await asyncio.sleep(0)
    fb = FrameBuffer()
    try:
        await asyncio.to_thread(pipeline._idle, fb)
        await asyncio.to_thread(pipeline._listen, fb)
        assert pipeline.state == "responding"
        async with _responding(pipeline, fb) as respond:
            await _finish(respond, timeout=10)
        assert pipeline.state == LISTENING  # straight back to listening, no beep
        beeps_after_wake = pipeline.audio.beeped

        played = [sentence for sentence, _, _ in pipeline.audio.played]
        # First audio while the model was still writing the answer.
        assert pipeline.audio.played[0][1] is True
        # In order, and cut off during the sentence the user talked over.
        assert played == ["It's sunny today.", "Twenty eight degrees."]
        assert pipeline.audio.played[1][2] is True
        await _until(lambda: provider.cancelled == 1, timeout=5)

        # The words that interrupted begin the next capture.
        await asyncio.to_thread(pipeline._listen, fb)
        assert _speech_chunks(pipeline.stt.heard[1]) == 10
        assert pipeline.audio.beeped == beeps_after_wake
        async with _responding(pipeline, fb) as respond:
            await _finish(respond, timeout=10)
    finally:
        bridge.cancel()
    # Nothing from the first answer played after the interruption.
    later = [sentence for sentence, _, _ in pipeline.audio.played[2:]]
    assert later == ["Tomorrow brings rain."]
    assert pipeline.state == "idle"
