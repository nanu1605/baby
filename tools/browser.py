"""browser_act: drive a persistent Chromium via Playwright (spec §10).

One persistent profile (%LOCALAPPDATA%/baby/browser) so logins survive
restarts; the window is visible by default (owner choice — first-time logins
happen right in Baby's browser). Safety: goto/read/screenshot are ALLOW;
click/type CONFIRM once per domain per session — and the domain comes from
the REAL page url via current_domain(), never from model-supplied kwargs.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from urllib.parse import urlparse

from tools.registry import tool

_ACTION_TIMEOUT_MS = 15_000
_READ_CAP = 4000

_pw = None  # playwright driver
_context = None  # persistent BrowserContext
_page = None  # current Page
_headless = True
_profile_dir: Path | None = None


def configure(headless: bool = True, profile_dir: str = "") -> None:
    global _headless, _profile_dir
    _headless = headless
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "baby"
    _profile_dir = Path(profile_dir) if profile_dir else base / "browser"


def current_domain() -> str:
    """Domain of the page the browser is REALLY on ('' when no page)."""
    if _page is None:
        return ""
    try:
        return urlparse(_page.url).netloc
    except Exception:  # noqa: BLE001 — a torn-down page must read as "no page"
        return ""


async def _ensure():
    """Lazy-launch the persistent context; returns the active page."""
    global _pw, _context, _page
    if _page is not None and not _page.is_closed():
        return _page
    if _profile_dir is None:
        configure()
    if _context is None:
        from playwright.async_api import async_playwright

        _profile_dir.mkdir(parents=True, exist_ok=True)
        _pw = await async_playwright().start()
        _context = await _pw.chromium.launch_persistent_context(
            str(_profile_dir), headless=_headless
        )
    _page = _context.pages[0] if _context.pages else await _context.new_page()
    return _page


async def shutdown() -> None:
    global _pw, _context, _page
    try:
        if _context is not None:
            await _context.close()
        if _pw is not None:
            await _pw.stop()
    except Exception:  # noqa: BLE001 — shutdown is best-effort
        pass
    _pw = _context = _page = None


def _shots_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "baby" / "shots"


@tool
async def browser_act(action: str, selector: str = "", value: str = "") -> str:
    """Drive the browser: goto|read|click|type|screenshot (value=url for goto)."""
    action = action.lower().strip()
    if action not in ("goto", "read", "click", "type", "screenshot"):
        return json.dumps({"error": f"unknown browser action {action!r}"})
    try:
        page = await _ensure()
        if action == "goto":
            url = value or selector  # tolerate the url landing in either slot
            if not url:
                return json.dumps({"error": "goto needs a url in value"})
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            await page.goto(url, timeout=_ACTION_TIMEOUT_MS)
            return json.dumps({"url": page.url, "title": await page.title()})
        if action == "read":
            target = selector or "body"
            text = await page.inner_text(target, timeout=_ACTION_TIMEOUT_MS)
            text = " ".join(text.split())
            truncated = len(text) > _READ_CAP
            return json.dumps(
                {"url": page.url, "text": text[:_READ_CAP], "truncated": truncated},
                ensure_ascii=False,
            )
        if action == "click":
            if not selector:
                return json.dumps({"error": "click needs a selector"})
            await page.click(selector, timeout=_ACTION_TIMEOUT_MS)
            return json.dumps({"clicked": selector, "url": page.url})
        if action == "type":
            if not selector:
                return json.dumps({"error": "type needs a selector"})
            await page.fill(selector, value, timeout=_ACTION_TIMEOUT_MS)
            return json.dumps({"typed_into": selector})
        # screenshot
        shots = _shots_dir()
        shots.mkdir(parents=True, exist_ok=True)
        path = shots / f"shot_{int(time.time())}.png"
        await page.screenshot(path=str(path))
        return json.dumps({"screenshot": str(path), "url": page.url})
    except Exception as exc:  # noqa: BLE001 — tools must return errors, not raise
        return json.dumps({"error": f"browser {action} failed: {exc}"})
