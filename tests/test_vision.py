"""Phase 5 stage 4: screen awareness (offline — fake grab, fake providers)."""

from __future__ import annotations

import base64
import io
import types

import pytest

from core.bus import EventBus
from core.safety import SafetyClass, SafetyConfig, classify_tool
from core.vision import VisionService
from tests.conftest import FakeProvider

pytestmark = pytest.mark.asyncio


def _image(width=200, height=100):
    from PIL import Image

    return Image.new("RGB", (width, height), (30, 60, 90))


def make_service(
    *,
    local=None,
    cloud=None,
    grab=None,
    screen_cfg=None,
    bus=None,
):
    config = {
        "screen": {"enabled": True, "allow_cloud_fallback": True, **(screen_cfg or {})},
        "models": {"daily": {"model": "daily-model"}},
    }
    router_stub = types.SimpleNamespace(cloud=cloud)
    service = VisionService(
        config, router_stub, bus, grab_fn=grab or (lambda: _image())
    )
    if local is not None:
        service._local = local
    return service


# -- capture + encode -----------------------------------------------------------------


async def test_encode_downscales_longest_side():
    from PIL import Image

    service = make_service(screen_cfg={"max_side": 128})
    data_uri = service._encode(_image(1000, 400))
    raw = base64.b64decode(data_uri.split(",", 1)[1])
    reopened = Image.open(io.BytesIO(raw))
    assert max(reopened.size) == 128
    assert reopened.size[0] > reopened.size[1]  # aspect kept


async def test_small_image_not_upscaled():
    from PIL import Image

    service = make_service(screen_cfg={"max_side": 1280})
    raw = base64.b64decode(service._encode(_image(200, 100)).split(",", 1)[1])
    assert Image.open(io.BytesIO(raw)).size == (200, 100)


async def test_capture_failure_becomes_error():
    def boom():
        raise OSError("no display")

    service = make_service(local=FakeProvider(["never"]), grab=boom)
    result = await service.describe()
    assert "screen capture failed" in result["error"]


# -- provider chain ------------------------------------------------------------------


async def test_local_success_with_data_uri_message():
    local = FakeProvider(["A code editor is open."])
    service = make_service(local=local)
    result = await service.describe("what app is focused?")
    assert result == {"description": "A code editor is open.", "via": "local"}
    content = local.requests[0][0]["content"]
    assert content[0]["text"] == "what app is focused?"
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


class ExplodingProvider(FakeProvider):
    async def chat(self, messages, tools=None, **opts):
        self.requests.append(messages)
        raise RuntimeError("no vision support")
        yield  # pragma: no cover


async def test_local_failure_falls_back_to_gemini_with_status():
    bus = EventBus()
    events = bus.subscribe()
    cloud = FakeProvider(["Cloud says: a browser."])
    service = make_service(local=ExplodingProvider([]), cloud=cloud, bus=bus)
    result = await service.describe()
    assert result == {"description": "Cloud says: a browser.", "via": "gemini"}
    event = events.get_nowait()
    assert "sending the screenshot to Gemini" in event.payload["text"]


async def test_empty_local_reply_also_falls_back():
    cloud = FakeProvider(["cloud desc"])
    service = make_service(local=FakeProvider([""]), cloud=cloud)
    result = await service.describe()
    assert result["via"] == "gemini"


async def test_fallback_disabled_returns_error():
    service = make_service(
        local=ExplodingProvider([]),
        cloud=FakeProvider(["never"]),
        screen_cfg={"allow_cloud_fallback": False},
    )
    result = await service.describe()
    assert "cloud fallback is disabled" in result["error"]


async def test_no_cloud_configured_returns_error():
    service = make_service(local=ExplodingProvider([]), cloud=None)
    result = await service.describe()
    assert "local vision failed" in result["error"]


async def test_both_paths_failing_reports_both():
    service = make_service(local=ExplodingProvider([]), cloud=ExplodingProvider([]))
    result = await service.describe()
    assert "locally" in result["error"] and "gemini" in result["error"]


async def test_disabled_service_short_circuits():
    service = make_service(screen_cfg={"enabled": False})
    result = await service.describe()
    assert "disabled" in result["error"]


# -- local provider construction ------------------------------------------------------


async def test_dedicated_model_unloads_immediately():
    service = make_service(screen_cfg={"model": "qwen3-vl:2b-instruct"})
    provider = service._local_provider()
    assert provider.model == "qwen3-vl:2b-instruct"
    assert provider.keep_alive == "0s"


async def test_default_uses_resident_daily_model():
    service = make_service()
    provider = service._local_provider()
    assert provider.model == "daily-model"
    assert provider.keep_alive == "24h"


# -- tool + safety ---------------------------------------------------------------------


async def test_describe_screen_tool_registered():
    from tools import register_all, registry

    register_all()
    names = [s["function"]["name"] for s in registry.schemas()]
    assert "describe_screen" in names


async def test_describe_screen_is_allow_class():
    verdict = classify_tool("describe_screen", {"question": "x"}, SafetyConfig())
    assert verdict.klass is SafetyClass.ALLOW
    assert "read-only" in verdict.reason
