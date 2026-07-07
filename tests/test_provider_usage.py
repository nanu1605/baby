"""P5 token telemetry: usage capture through the shared provider seam.

`accumulate_stream` is the one path every OpenAI-wire brain shares, so the
usage-trailer handling is tested there directly (offline, no quota). Provider
wiring tests confirm stream_options.include_usage is sent only when emit_usage.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from core.providers import base as base_mod
from core.providers.base import Chunk, _usage_dict, accumulate_stream
from core.providers.nvidia import NvidiaProvider

pytestmark = pytest.mark.asyncio


def _event(content=None, tool_calls=None, finish=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)])


def _tc(index=0, id=None, name=None, args=None):
    fn = SimpleNamespace(name=name, arguments=args)
    return SimpleNamespace(index=index, id=id, function=fn)


def _usage_event(prompt, completion, total=None):
    """The include_usage trailer: empty choices, usage populated (real wire shape)."""
    usage = SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion if total is None else total,
    )
    return SimpleNamespace(choices=[], usage=usage)


async def _stream(events):
    for event in events:
        yield event


async def _collect(events):
    return [c async for c in accumulate_stream(_stream(events))]


# -- accumulate_stream usage capture -----------------------------------------


async def test_captures_usage_from_trailing_chunk():
    chunks = await _collect(
        [_event("Hel"), _event("lo"), _event(finish="stop"), _usage_event(12, 5)]
    )
    assert "".join(c.delta for c in chunks) == "Hello"
    done = chunks[-1]
    assert done.done and done.usage == {
        "prompt_tokens": 12,
        "completion_tokens": 5,
        "total_tokens": 17,
    }


async def test_usage_none_when_host_omits_it():
    # No trailer: old behavior preserved, done chunk carries usage=None.
    chunks = await _collect([_event("hi"), _event(finish="stop")])
    assert chunks[-1].done and chunks[-1].usage is None


async def test_exactly_one_done_chunk():
    # The done chunk is now emitted at stream end, never twice (deferred-done).
    chunks = await _collect([_event("x"), _event(finish="stop"), _usage_event(1, 1)])
    assert sum(1 for c in chunks if c.done) == 1


async def test_tool_calls_intact_with_usage_trailer():
    events = [
        _event(tool_calls=[_tc(0, id="c1", name="file_search", args='{"que')]),
        _event(tool_calls=[_tc(0, args='ry": "x"}')]),
        _event(finish="tool_calls"),
        _usage_event(30, 8),
    ]
    chunks = await _collect(events)
    done = chunks[-1]
    assert done.done and len(done.tool_calls) == 1
    call = done.tool_calls[0]
    assert (call.name, call.arguments) == ("file_search", '{"query": "x"}')
    assert done.usage["total_tokens"] == 38


class _StallAfterFinish:
    """Streams content + finish_reason, then hangs forever (no usage trailer)."""

    def __init__(self):
        self._events = [_event("hi"), _event(finish="stop")]
        self._i = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._i < len(self._events):
            ev = self._events[self._i]
            self._i += 1
            return ev
        await asyncio.sleep(3600)  # stall well past the trailer timeout


class _ErrorAfterFinish:
    """Streams content + finish_reason, then the connection errors (no trailer)."""

    def __init__(self):
        self._events = [_event("hi"), _event(finish="stop")]
        self._i = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._i < len(self._events):
            ev = self._events[self._i]
            self._i += 1
            return ev
        raise RuntimeError("connection reset before usage trailer")


async def test_done_delivered_when_trailer_stalls(monkeypatch):
    # Review #1 regression: a host that stalls AFTER finish_reason must not delay
    # or fail the terminating chunk — the wait is bounded and gives up cleanly.
    monkeypatch.setattr(base_mod, "_TRAILER_TIMEOUT_S", 0.05)
    chunks = await asyncio.wait_for(
        _collect_iter(_StallAfterFinish()), timeout=2.0
    )
    assert chunks[-1].done and chunks[-1].usage is None


async def test_done_delivered_when_trailer_errors():
    # Review #2 regression: an exception on the post-finish drain is swallowed;
    # the done chunk (with any tool_calls) is still delivered, turn never fails.
    chunks = await _collect_iter(_ErrorAfterFinish())
    assert chunks[-1].done and chunks[-1].usage is None
    assert "".join(c.delta for c in chunks) == "hi"


async def _collect_iter(stream):
    return [c async for c in accumulate_stream(stream)]


async def test_usage_dict_derives_missing_total():
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=4, total_tokens=None)
    assert _usage_dict(usage) == {
        "prompt_tokens": 10,
        "completion_tokens": 4,
        "total_tokens": 14,
    }
    assert _usage_dict(None) is None


# -- provider wiring: stream_options gated by emit_usage ----------------------


def _fake_nvidia(emit_usage=True):
    provider = NvidiaProvider(model="m", api_key="nvapi-x", emit_usage=emit_usage)
    requests: list[dict] = []

    async def create(**kwargs):
        requests.append(kwargs)
        return _stream([_event("ok", finish="stop"), _usage_event(3, 2)])

    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    return provider, requests


async def test_provider_sends_include_usage_by_default():
    provider, requests = _fake_nvidia(emit_usage=True)
    got = [c async for c in provider.chat([{"role": "user", "content": "hi"}])]
    assert requests[0]["stream_options"] == {"include_usage": True}
    assert got[-1].usage == {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}


async def test_provider_omits_stream_options_when_disabled():
    provider, requests = _fake_nvidia(emit_usage=False)
    _ = [c async for c in provider.chat([{"role": "user", "content": "hi"}])]
    assert "stream_options" not in requests[0]


async def test_chunk_has_usage_field():
    assert Chunk(done=True, usage={"total_tokens": 1}).usage == {"total_tokens": 1}
    assert Chunk().usage is None
