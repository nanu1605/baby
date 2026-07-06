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
    """Lazy-launch the persistent context; returns the active page.

    The window is visible, so the owner can close it at any moment — a dead
    context must trigger a clean relaunch, not fail forever with
    "Target page, context or browser has been closed" (owner bug report).
    """
    global _pw, _context, _page
    if _page is not None and not _page.is_closed():
        return _page
    if _profile_dir is None:
        configure()
    if _context is not None:
        try:
            _page = _context.pages[0] if _context.pages else await _context.new_page()
            return _page
        except Exception:  # noqa: BLE001 — closed/crashed context: reset and relaunch
            await shutdown()
    from playwright.async_api import async_playwright

    _profile_dir.mkdir(parents=True, exist_ok=True)
    _pw = await async_playwright().start()
    _context = await _pw.chromium.launch_persistent_context(str(_profile_dir), headless=_headless)
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


# Where text goes when the model gives no selector — search/input boxes on
# most sites, tried in order. The 9B routinely omits selectors (owner logs
# showed 'type needs a selector' killing every search flow).
_TYPE_TARGETS = (
    'textarea[name="q"]',
    'input[name="q"]',
    'input[type="search"]',
    '[role="searchbox"]',
    'input[type="text"]',
    "textarea",
)


async def _find_typable(page) -> str:
    for candidate in _TYPE_TARGETS:
        try:
            if await page.query_selector(candidate) is not None:
                return candidate
        except Exception:  # noqa: BLE001 — a bad candidate must not end the scan
            continue
    return ""


@tool
async def browser_act(action: str, selector: str = "", value: str = "") -> str:
    """Drive the real browser window. Actions: goto (value=url), read (whole
    page, or selector), click (selector), type (value=text; finds the search
    box itself when selector is empty), press (value=key, e.g. Enter — submits
    a search), screenshot. Easiest search ON ANY site: goto its query URL —
    "https://www.google.com/search?q=your+query" or
    "https://duckduckgo.com/?q=your+query" — then read. Or: type the query
    (search box auto-found), then press Enter."""
    action = action.lower().strip()
    if action not in ("goto", "read", "click", "type", "press", "screenshot"):
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
            # value lands here with two distinct wrong-slot intents (both
            # observed live): a selector ("h1") — forgive it like goto/click
            # do — or a search query the model expects read to TYPE
            # (gpt-4o-mini, 3x in one turn). read must never silently type:
            # type is CONFIRM-class per domain while read is ALLOW, so
            # honoring that intent would bypass the safety gate. Decide by
            # asking the page: value that matches an element is a selector,
            # anything else earns the exact type+press recipe.
            if value:
                el = None
                try:
                    el = await page.query_selector(value)
                except Exception:  # noqa: BLE001 — not parseable as a selector
                    el = None
                if el is not None:
                    if not selector:
                        target = value
                else:
                    return json.dumps({
                        "error": "read only reads — it cannot type. To search "
                                 f'this page: call browser_act action="type" '
                                 f'value="{value}" (the search box is '
                                 'auto-found), then browser_act action="press" '
                                 'value="Enter"',
                    })
            # Hostile-to-automation pages (duckduckgo.com, observed live) hang
            # inner_text for the full 15 s AND serve empty bodies; a dead-end
            # error left the model with no path forward. Short reads, one
            # hydration retry, and a teaching hint instead of an error — real
            # keystrokes (type + press) still work on these pages.
            text = ""
            try:
                text = " ".join((await page.inner_text(target, timeout=5000)).split())
            except Exception:  # noqa: BLE001 — fall through to the retry/hint
                pass
            if not text:
                try:
                    await page.wait_for_load_state("networkidle", timeout=5000)
                    text = " ".join((await page.inner_text(target, timeout=5000)).split())
                except Exception:  # noqa: BLE001
                    pass
            if not text:
                # Exact call syntax: prose ("call type with your query") was
                # misread as an English verb — the model kept re-reading.
                return json.dumps({
                    "url": page.url,
                    "text": "",
                    "hint": "page text unreadable (script-heavy or blocks "
                            "automation reads) — to search here: call "
                            'browser_act action="type" value="<your query>" '
                            "(the search box is auto-found), then browser_act "
                            'action="press" value="Enter"; screenshot also works',
                })
            truncated = len(text) > _READ_CAP
            return json.dumps(
                {"url": page.url, "text": text[:_READ_CAP], "truncated": truncated},
                ensure_ascii=False,
            )
        if action == "click":
            target = selector or value  # tolerate the selector in either slot
            if not target:
                return json.dumps({"error": "click needs a selector"})
            await page.click(target, timeout=_ACTION_TIMEOUT_MS)
            return json.dumps({"clicked": target, "url": page.url})
        if action == "type":
            target = selector or await _find_typable(page)
            if not target:
                return json.dumps(
                    {"error": "type: no input field found — give a selector"}
                )
            await page.fill(target, value, timeout=_ACTION_TIMEOUT_MS)
            return json.dumps({"typed_into": target, "hint": "press Enter to submit"})
        if action == "press":
            key = value or selector or "Enter"
            await page.keyboard.press(key)
            try:
                await page.wait_for_load_state("load", timeout=_ACTION_TIMEOUT_MS)
            except Exception:  # noqa: BLE001 — no navigation happened; that's fine
                pass
            return json.dumps({"pressed": key, "url": page.url, "title": await page.title()})
        # screenshot
        shots = _shots_dir()
        shots.mkdir(parents=True, exist_ok=True)
        path = shots / f"shot_{int(time.time())}.png"
        await page.screenshot(path=str(path))
        return json.dumps({"screenshot": str(path), "url": page.url})
    except Exception as exc:  # noqa: BLE001 — tools must return errors, not raise
        if "has been closed" in str(exc):
            # Owner closed the window mid-action: reset now so the retry relaunches.
            await shutdown()
            return json.dumps(
                {"error": f"browser window was closed — retry the {action} to reopen it"}
            )
        return json.dumps({"error": f"browser {action} failed: {exc}"})
