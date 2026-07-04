"""System tray icon: Baby's status at a glance.

Green = ready, amber = working (a turn or background task in flight),
red = waiting on a confirmation. pystray runs its own message-loop thread
(run_detached) — a narrow exception to the single-asyncio rule, same class
as the toast helper; all state flows in from a bus-subscriber coroutine on
the loop, so the tray thread itself never touches Baby's internals.
"""

from __future__ import annotations

import asyncio
import contextlib
import webbrowser

_COLORS = {
    "ready": (46, 204, 113),
    "busy": (241, 196, 15),
    "confirm": (231, 76, 60),
}
_LABELS = {
    "ready": "ready",
    "busy": "working…",
    "confirm": "waiting for your confirmation",
}


def _dot(rgb: tuple[int, int, int]):
    """64px round status dot (Windows scales it down for the tray)."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((8, 8, 56, 56), fill=(*rgb, 255))
    return img


class TrayState:
    """Pure bus-event → status folding; unit-tested without pystray."""

    def __init__(self) -> None:
        self.turns = 0
        self.tasks = 0
        self.confirms = 0

    def apply(self, kind: str) -> str:
        if kind == "turn_start":
            self.turns += 1
        elif kind == "turn_end":
            self.turns = max(0, self.turns - 1)
        elif kind == "task_started":
            self.tasks += 1
        elif kind == "task_done":
            self.tasks = max(0, self.tasks - 1)
        elif kind == "confirm_request":
            self.confirms += 1
        elif kind == "confirm_resolved":
            self.confirms = max(0, self.confirms - 1)
        return self.status()

    def status(self) -> str:
        if self.confirms > 0:
            return "confirm"
        if self.turns > 0 or self.tasks > 0:
            return "busy"
        return "ready"


class TrayIcon:
    """pystray icon wired to the event bus; menu: Open Baby / Quit Baby."""

    def __init__(self, bus, url: str, on_quit) -> None:
        self.bus = bus
        self.url = url
        self.on_quit = on_quit
        self.state = TrayState()
        self._icon = None
        self._images: dict = {}
        self._watcher: asyncio.Task | None = None

    def start(self) -> bool:
        try:
            import pystray

            self._images = {name: _dot(rgb) for name, rgb in _COLORS.items()}
            menu = pystray.Menu(
                pystray.MenuItem(
                    "Open Baby", lambda: webbrowser.open(self.url), default=True
                ),
                pystray.MenuItem("Quit Baby", lambda: self.on_quit()),
            )
            self._icon = pystray.Icon("baby", self._images["ready"], "Baby — ready", menu)
            self._icon.run_detached()
        except Exception:  # noqa: BLE001 — no tray must never block boot
            return False
        self._watcher = asyncio.get_running_loop().create_task(
            self._watch(), name="baby-tray"
        )
        return True

    async def _watch(self) -> None:
        q = self.bus.subscribe()
        try:
            while True:
                event = await q.get()
                self._set(self.state.apply(event.kind))
        finally:
            self.bus.unsubscribe(q)

    def _set(self, status: str) -> None:
        if self._icon is None:
            return
        try:
            self._icon.icon = self._images[status]
            self._icon.title = f"Baby — {_LABELS[status]}"
        except Exception:  # noqa: BLE001 — a torn-down icon must not kill the watcher
            pass

    def stop(self) -> None:
        if self._watcher is not None:
            self._watcher.cancel()
        if self._icon is not None:
            with contextlib.suppress(Exception):
                self._icon.stop()
        self._icon = None
