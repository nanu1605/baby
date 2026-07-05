"""Phase N0: NvidiaProvider unit tests — streaming, tools, cooldown, probe.

All offline: the AsyncOpenAI client and httpx are faked; no NIM quota burned.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError, APIStatusError

from core.providers.nvidia import NIM_OPENAI_URL, NvidiaProvider
from core.router import build_nim_providers, build_provider

pytestmark = pytest.mark.asyncio


def _event(content=None, tool_calls=None, finish=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)])


def _tc(index=0, id=None, name=None, args=None):
    fn = SimpleNamespace(name=name, arguments=args)
    return SimpleNamespace(index=index, id=id, function=fn)


async def _stream(events):
    for event in events:
        yield event


class FakeCompletions:
    """Stands in for client.chat.completions: scripted stream or exception."""

    def __init__(self, events=None, error=None):
        self.events = events or []
        self.error = error
        self.requests: list[dict] = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        if self.error is not None:
            raise self.error
        if kwargs.get("stream"):
            return _stream(self.events)
        return SimpleNamespace(choices=[])


def make_provider(events=None, error=None, model="test/model", key="nvapi-x", **kw):
    provider = NvidiaProvider(model=model, api_key=key, **kw)
    fake = FakeCompletions(events, error)
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    return provider, fake


def _status_error(code):
    request = httpx.Request("POST", NIM_OPENAI_URL)
    response = httpx.Response(code, request=request)
    return APIStatusError("boom", response=response, body=None)


async def collect(provider, **opts):
    chunks = []
    async for chunk in provider.chat([{"role": "user", "content": "hi"}], **opts):
        chunks.append(chunk)
    return chunks


async def test_streams_text_and_passes_model_params():
    provider, fake = make_provider([_event("Hel"), _event("lo"), _event(finish="stop")])
    chunks = await collect(provider, max_tokens=64)
    assert "".join(c.delta for c in chunks) == "Hello"
    req = fake.requests[0]
    assert req["model"] == "test/model"
    assert req["max_tokens"] == 64
    assert req["temperature"] == 0.7
    assert req["stream"] is True


async def test_tool_calls_reassembled_across_events():
    events = [
        _event(tool_calls=[_tc(0, id="c1", name="file_search", args='{"que')]),
        _event(tool_calls=[_tc(0, args='ry": "x"}')]),
        _event(finish="tool_calls"),
    ]
    provider, fake = make_provider(events)
    tools = [{"type": "function", "function": {"name": "file_search"}}]
    chunks = await collect(provider, tools=tools)
    final = chunks[-1]
    assert final.done and len(final.tool_calls) == 1
    call = final.tool_calls[0]
    assert (call.name, call.arguments) == ("file_search", '{"query": "x"}')
    assert fake.requests[0]["tools"] == tools


async def test_429_starts_cooldown_and_healthy_recovers():
    provider, _ = make_provider(error=_status_error(429), cooldown_s=0.01)
    assert await provider.healthy()
    with pytest.raises(APIStatusError):
        await collect(provider)
    assert not await provider.healthy()
    provider.unhealthy_until = 0.0  # cooldown elapsed
    assert await provider.healthy()


async def test_500_and_connection_errors_start_cooldown():
    for error in (_status_error(503), APIConnectionError(request=httpx.Request("POST", "http://x"))):
        provider, _ = make_provider(error=error)
        with pytest.raises((APIStatusError, APIConnectionError)):
            await collect(provider)
        assert not await provider.healthy()


async def test_400_does_not_cooldown():
    provider, _ = make_provider(error=_status_error(400))
    with pytest.raises(APIStatusError):
        await collect(provider)
    assert await provider.healthy()


async def test_missing_key_is_unhealthy():
    provider, _ = make_provider(key="")
    assert not await provider.healthy()


class FakeHttpClient:
    """Async-context httpx.AsyncClient double returning a scripted status."""

    status = 200
    last_request: tuple | None = None

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None):
        FakeHttpClient.last_request = (url, headers)
        if isinstance(FakeHttpClient.status, Exception):
            raise FakeHttpClient.status
        return SimpleNamespace(status_code=FakeHttpClient.status)


@pytest.fixture
def fake_http(monkeypatch):
    monkeypatch.setattr("core.providers.nvidia.httpx.AsyncClient", FakeHttpClient)
    FakeHttpClient.status = 200
    FakeHttpClient.last_request = None
    return FakeHttpClient


async def test_probe_models_list_ok(fake_http):
    provider, fake = make_provider()
    assert await provider.probe()
    url, headers = fake_http.last_request
    assert url == f"{NIM_OPENAI_URL}/models"
    assert headers["Authorization"] == "Bearer nvapi-x"
    assert fake.requests == []  # no generation quota burned


async def test_probe_fails_on_bad_status_and_network_error(fake_http):
    provider, _ = make_provider()
    fake_http.status = 401
    assert not await provider.probe()
    fake_http.status = httpx.ConnectError("dns down")
    assert not await provider.probe()


async def test_probe_generation_ping(fake_http):
    provider, fake = make_provider([_event("x", finish="stop")])
    assert await provider.probe(generation=True)
    assert fake.requests[0]["max_tokens"] == 1
    assert fake.requests[0]["stream"] is False


async def test_probe_generation_needs_model_and_survives_api_error(fake_http):
    provider, _ = make_provider(model="")
    assert not await provider.probe(generation=True)
    provider, _ = make_provider(error=_status_error(500))
    assert not await provider.probe(generation=True)


# -- config wiring -----------------------------------------------------------------

NIM_MODELS = {
    "nim_primary": {"provider": "nvidia", "model": "org/primary", "temperature": 0.7},
    "nim_heavy": {"provider": "nvidia", "model": "org/heavy", "temperature": 0.5},
}


def test_build_nim_providers_needs_key(monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    slots = build_nim_providers({"models": NIM_MODELS})
    assert slots == {"nim_primary": None, "nim_heavy": None}


def test_build_nim_providers_builds_configured_slots(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
    config = {
        "models": NIM_MODELS,
        "router": {"health": {"cooldown_429_s": 42}},
    }
    slots = build_nim_providers(config)
    assert slots["nim_primary"].model == "org/primary"
    assert slots["nim_heavy"].model == "org/heavy"
    assert slots["nim_primary"].base_url == NIM_OPENAI_URL
    assert slots["nim_primary"].cooldown_s == 42.0


def test_build_nim_providers_skips_empty_model(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
    models = {"nim_primary": {"model": ""}, "nim_heavy": {"model": "org/heavy"}}
    slots = build_nim_providers({"models": models})
    assert slots["nim_primary"] is None
    assert slots["nim_heavy"].model == "org/heavy"


def test_build_provider_rejects_cloud_primary_until_n2():
    config = {
        "models": {"daily": {"model": "qwen3.5:9b-q4_K_M"}},
        "router": {"mode": "cloud_primary"},
    }
    with pytest.raises(ValueError, match="cloud_primary"):
        build_provider(config)
