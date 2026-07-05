"""Screen awareness: screenshot → multimodal model → description (+screen).

The default vision brain is the RESIDENT daily model (qwen3.5:9b is
multimodal), so the common path costs no eviction and no reload. Setting
screen.model switches to a dedicated vision model (e.g. qwen3-vl:2b-instruct)
sent with keep_alive "0s" so it unloads immediately after the call — that
path evicts the 9B and the next daily turn pays the reload (owner-accepted).

Deliberately NOT routed through RouterProvider.chat: its failover would try
the 24 GB heavy model on a vision failure. The chain here is explicit:
local → Gemini (router's own GeminiProvider instance, shared cooldown state,
only when allow_cloud_fallback and a key exist) → error dict. A screenshot
leaving the machine is never silent — a status event announces the fallback.
"""

from __future__ import annotations

import asyncio
import base64
import io


class VisionService:
    """Capture + encode + ask, with an explicit local→cloud chain."""

    def __init__(self, config: dict, provider, bus=None, *, grab_fn=None) -> None:
        screen_cfg = config.get("screen", {})
        self.enabled = bool(screen_cfg.get("enabled", True))
        self.model = str(screen_cfg.get("model", "") or "")
        self.all_screens = bool(screen_cfg.get("all_screens", False))
        self.max_side = int(screen_cfg.get("max_side", 1280))
        self.jpeg_quality = int(screen_cfg.get("jpeg_quality", 80))
        self.allow_cloud = bool(screen_cfg.get("allow_cloud_fallback", True))
        self.daily_model = config.get("models", {}).get("daily", {}).get("model", "")
        self.bus = bus
        self._grab_fn = grab_fn  # test seam
        self._local = None  # OllamaProvider, built lazily
        # The router's GeminiProvider (None on bare-daily setups / no key):
        # reusing the instance shares its 5-minute 429 cooldown state.
        self._cloud = getattr(provider, "cloud", None)

    # -- capture + encode ----------------------------------------------------------

    def _grab(self):
        if self._grab_fn is not None:
            return self._grab_fn()
        import ctypes

        from PIL import ImageGrab

        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001 — DPI awareness is best-effort
            pass
        return ImageGrab.grab(all_screens=self.all_screens)

    def _encode(self, image) -> str:
        """PIL image → base64 JPEG data URI, longest side capped at max_side."""
        width, height = image.size
        scale = self.max_side / max(width, height)
        if scale < 1:
            image = image.resize((max(1, int(width * scale)), max(1, int(height * scale))))
        if image.mode != "RGB":
            image = image.convert("RGB")
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=self.jpeg_quality)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

    # -- providers -------------------------------------------------------------------

    def _local_provider(self):
        if self._local is None:
            from core.providers.ollama import OllamaProvider

            if self.model:
                # Dedicated vision model: unload right after the call so the
                # 9B comes back as soon as possible.
                self._local = OllamaProvider(model=self.model, keep_alive="0s")
            else:
                self._local = OllamaProvider(model=self.daily_model)
        return self._local

    async def _ask(self, provider, messages: list[dict]) -> str:
        parts: list[str] = []
        async for chunk in provider.chat(
            messages, tools=None, max_tokens=500, reasoning_effort="none"
        ):
            if chunk.delta:
                parts.append(chunk.delta)
        return "".join(parts).strip()

    # -- public ------------------------------------------------------------------------

    async def describe(self, question: str = "") -> dict:
        if not self.enabled:
            return {"error": "screen awareness is disabled in config"}
        try:
            image = await asyncio.to_thread(self._grab)
            data_uri = await asyncio.to_thread(self._encode, image)
        except Exception as exc:  # noqa: BLE001 — capture failures become tool errors
            return {"error": f"screen capture failed: {exc}"}

        prompt = question.strip() or (
            "Describe what is on this screen, concisely: the app in focus, "
            "the visible content, anything notable."
        )
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }
        ]

        try:
            text = await self._ask(self._local_provider(), messages)
            if text:
                return {"description": text, "via": "local"}
            raise RuntimeError("local vision returned empty text")
        except Exception as exc:  # noqa: BLE001 — the chain continues to cloud
            local_error = exc

        if not self.allow_cloud or self._cloud is None:
            return {
                "error": f"local vision failed ({local_error}); "
                "cloud fallback is disabled or not configured"
            }
        if self.bus is not None:
            self.bus.publish(
                "status", "screen",
                text="screen: local vision failed — sending the screenshot to Gemini",
            )
        try:
            text = await self._ask(self._cloud, messages)
        except Exception as exc:  # noqa: BLE001 — end of the chain
            return {"error": f"vision failed locally ({local_error}) and on gemini ({exc})"}
        if not text:
            return {"error": "gemini vision returned empty text"}
        return {"description": text, "via": "gemini"}
