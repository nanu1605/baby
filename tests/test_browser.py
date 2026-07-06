"""Phase 4 stage 5: browser_act dispatch + per-domain safety (offline, fake page)."""

from __future__ import annotations

import json

import pytest

import tools.browser as browser
from core.bus import EventBus
from core.safety import SafetyClass, SafetyConfig, SafetyGate, SafetySession, classify_tool


class _FakeKeyboard:
    def __init__(self):
        self.pressed: list[str] = []

    async def press(self, key):
        self.pressed.append(key)


class FakePage:
    def __init__(self, url="https://ollama.com/library"):
        self.url = url
        self.clicked: list[str] = []
        self.filled: list[tuple[str, str]] = []
        self.shot_paths: list[str] = []
        self.keyboard = _FakeKeyboard()
        self.selectors_present: set[str] = {'textarea[name="q"]'}

    def is_closed(self):
        return False

    async def query_selector(self, selector):
        return object() if selector in self.selectors_present else None

    async def wait_for_load_state(self, state, timeout=None):
        pass

    async def goto(self, url, timeout=None):
        self.url = url

    async def title(self):
        return "Fake Title"

    async def inner_text(self, selector, timeout=None):
        return "  headline   text\nwith   spacing  " + "x" * 5000

    async def click(self, selector, timeout=None):
        self.clicked.append(selector)

    async def fill(self, selector, value, timeout=None):
        self.filled.append((selector, value))

    async def screenshot(self, path=None):
        self.shot_paths.append(path)


@pytest.fixture
def page(monkeypatch):
    fake = FakePage()

    async def fake_ensure():
        return fake

    monkeypatch.setattr(browser, "_ensure", fake_ensure)
    monkeypatch.setattr(browser, "_page", fake)
    return fake


@pytest.mark.asyncio
async def test_goto_normalizes_scheme_and_reports_title(page):
    result = json.loads(await browser.browser_act("goto", value="ollama.com"))
    assert result["url"] == "https://ollama.com"
    assert result["title"] == "Fake Title"


@pytest.mark.asyncio
async def test_goto_accepts_url_in_selector_slot(page):
    result = json.loads(await browser.browser_act("goto", selector="https://x.dev"))
    assert result["url"] == "https://x.dev"


@pytest.mark.asyncio
async def test_goto_without_url_errors(page):
    assert "error" in json.loads(await browser.browser_act("goto"))


@pytest.mark.asyncio
async def test_read_collapses_whitespace_and_caps(page):
    result = json.loads(await browser.browser_act("read"))
    assert result["truncated"] is True
    assert len(result["text"]) == 4000
    assert "headline text with spacing" in result["text"]


@pytest.mark.asyncio
async def test_click_and_type_dispatch(page):
    await browser.browser_act("click", selector="#buy")
    await browser.browser_act("type", selector="#q", value="evs")
    assert page.clicked == ["#buy"]
    assert page.filled == [("#q", "evs")]


@pytest.mark.asyncio
async def test_click_without_selector_errors(page):
    assert "error" in json.loads(await browser.browser_act("click"))


@pytest.mark.asyncio
async def test_unknown_action_errors(page):
    assert "error" in json.loads(await browser.browser_act("hack"))


@pytest.mark.asyncio
async def test_screenshot_returns_png_path(page, tmp_path, monkeypatch):
    monkeypatch.setattr(browser, "_shots_dir", lambda: tmp_path / "shots")
    result = json.loads(await browser.browser_act("screenshot"))
    assert result["screenshot"].endswith(".png")
    assert page.shot_paths


@pytest.mark.asyncio
async def test_page_exception_becomes_error_result(page):
    async def broken(url, timeout=None):
        raise TimeoutError("net down")

    page.goto = broken
    result = json.loads(await browser.browser_act("goto", value="https://x.dev"))
    assert "failed" in result["error"]


def test_current_domain_reads_real_page(page):
    assert browser.current_domain() == "ollama.com"


# -- safety matrix ---------------------------------------------------------------


def _session(domain="ollama.com", confirmed=()):
    return SafetySession(
        confirmed_browser_domains=set(confirmed),
        browser_domain_fn=(lambda: domain),
    )


CFG = SafetyConfig()


def test_readonly_actions_always_allowed():
    for action in ("goto", "read", "screenshot"):
        verdict = classify_tool("browser_act", {"action": action}, CFG, session=_session())
        assert verdict.klass is SafetyClass.ALLOW, action


def test_first_click_on_domain_confirms():
    verdict = classify_tool("browser_act", {"action": "click"}, CFG, session=_session())
    assert verdict.klass is SafetyClass.CONFIRM
    assert "ollama.com" in verdict.reason


def test_click_after_approval_allowed_same_domain_only():
    session = _session(confirmed={"ollama.com"})
    ok = classify_tool("browser_act", {"action": "click"}, CFG, session=session)
    assert ok.klass is SafetyClass.ALLOW
    session.browser_domain_fn = lambda: "other.com"
    other = classify_tool("browser_act", {"action": "type"}, CFG, session=session)
    assert other.klass is SafetyClass.CONFIRM


def test_click_with_no_page_confirms():
    verdict = classify_tool("browser_act", {"action": "click"}, CFG, session=_session(domain=""))
    assert verdict.klass is SafetyClass.CONFIRM
    assert "no page" in verdict.reason


def test_model_supplied_domain_is_ignored():
    # The model claims a pre-approved domain in kwargs; the REAL page differs.
    session = _session(domain="evil.com", confirmed={"trusted.com"})
    verdict = classify_tool(
        "browser_act", {"action": "click", "domain": "trusted.com"}, CFG, session=session
    )
    assert verdict.klass is SafetyClass.CONFIRM


def test_note_approval_records_domain_for_click_only():
    gate = SafetyGate(SafetyConfig(), EventBus())
    gate.session.browser_domain_fn = lambda: "ollama.com"
    gate.note_approval("browser_act", {"action": "goto"})
    assert gate.session.confirmed_browser_domains == set()
    gate.note_approval("browser_act", {"action": "click"})
    assert gate.session.confirmed_browser_domains == {"ollama.com"}
    gate.note_approval("run_shell", {"command": "dir"})
    assert gate.session.confirmed_browser_domains == {"ollama.com"}


# -- dead-context relaunch (owner closed the visible window) ------------------------


class DeadContext:
    pages: list = []

    async def new_page(self):
        raise RuntimeError("Target page, context or browser has been closed")

    async def close(self):
        pass


class LiveContext:
    def __init__(self):
        self.pages: list = []

    async def new_page(self):
        return FakePage(url="about:blank")

    async def close(self):
        pass


class FakeChromium:
    def __init__(self):
        self.launches = 0

    async def launch_persistent_context(self, profile, headless):
        self.launches += 1
        return LiveContext()


class FakePlaywright:
    def __init__(self):
        self.chromium = FakeChromium()

    async def stop(self):
        pass


class FakeStarter:
    """Stands in for playwright.async_api.async_playwright()."""

    def __init__(self, pw):
        self._pw = pw

    def __call__(self):
        return self

    async def start(self):
        return self._pw


@pytest.mark.asyncio
async def test_ensure_relaunches_after_context_closed(monkeypatch):
    pw = FakePlaywright()
    monkeypatch.setattr("playwright.async_api.async_playwright", FakeStarter(pw))
    monkeypatch.setattr(browser, "_pw", FakePlaywright())
    monkeypatch.setattr(browser, "_context", DeadContext())
    monkeypatch.setattr(browser, "_page", None)
    page = await browser._ensure()
    assert page.url == "about:blank"  # fresh page from the relaunched context
    assert pw.chromium.launches == 1
    await browser.shutdown()


@pytest.mark.asyncio
async def test_ensure_reuses_live_context_without_relaunch(monkeypatch):
    pw = FakePlaywright()
    monkeypatch.setattr("playwright.async_api.async_playwright", FakeStarter(pw))
    monkeypatch.setattr(browser, "_context", LiveContext())
    monkeypatch.setattr(browser, "_page", None)
    page = await browser._ensure()
    assert page.url == "about:blank"
    assert pw.chromium.launches == 0  # no relaunch needed
    await browser.shutdown()


@pytest.mark.asyncio
async def test_action_on_window_closed_mid_flight_resets(monkeypatch):
    closed_page = FakePage()

    async def dying_goto(url, timeout=None):
        raise RuntimeError("Page.goto: Target page, context or browser has been closed")

    closed_page.goto = dying_goto

    async def fake_ensure():
        return closed_page

    stopped = []

    async def fake_shutdown():
        stopped.append(True)

    monkeypatch.setattr(browser, "_ensure", fake_ensure)
    monkeypatch.setattr(browser, "shutdown", fake_shutdown)
    result = json.loads(await browser.browser_act("goto", value="https://google.com"))
    assert "window was closed" in result["error"]
    assert stopped  # state reset so the retry relaunches


# -- forgiving slots + search flow (owner logs: 9B omits selectors) -----------------


@pytest.mark.asyncio
async def test_click_accepts_selector_in_value_slot(page):
    result = json.loads(await browser.browser_act("click", value="#searchbox"))
    assert result["clicked"] == "#searchbox"
    assert page.clicked == ["#searchbox"]


@pytest.mark.asyncio
async def test_type_without_selector_finds_search_box(page):
    result = json.loads(
        await browser.browser_act("type", value="fine dining places in Indore")
    )
    assert result["typed_into"] == 'textarea[name="q"]'
    assert page.filled == [('textarea[name="q"]', "fine dining places in Indore")]


@pytest.mark.asyncio
async def test_type_without_selector_and_no_field_errors(page):
    page.selectors_present = set()
    result = json.loads(await browser.browser_act("type", value="hello"))
    assert "no input field" in result["error"]


@pytest.mark.asyncio
async def test_press_defaults_to_enter(page):
    result = json.loads(await browser.browser_act("press"))
    assert result["pressed"] == "Enter"
    assert page.keyboard.pressed == ["Enter"]


@pytest.mark.asyncio
async def test_press_named_key_in_value(page):
    result = json.loads(await browser.browser_act("press", value="Tab"))
    assert result["pressed"] == "Tab"


def test_safety_press_confirms_first_time_then_allows():
    cfg = SafetyConfig()
    session = SafetySession(browser_domain_fn=lambda: "google.com")
    verdict = classify_tool("browser_act", {"action": "press"}, cfg, session=session)
    assert verdict.klass is SafetyClass.CONFIRM
    session.confirmed_browser_domains.add("google.com")
    verdict = classify_tool("browser_act", {"action": "press"}, cfg, session=session)
    assert verdict.klass is SafetyClass.ALLOW


def test_note_approval_records_domain_for_press():
    gate = SafetyGate(SafetyConfig(), EventBus())
    gate.session.browser_domain_fn = lambda: "google.com"
    gate.note_approval("browser_act", {"action": "press"})
    assert "google.com" in gate.session.confirmed_browser_domains


async def test_read_unreadable_page_returns_hint_not_error(monkeypatch):
    """DDG-style pages: inner_text times out / returns empty - the result
    must teach type+press instead of dead-ending (observed live)."""
    import json as _json

    from tools import browser

    class HostilePage:
        url = "https://duckduckgo.com/"

        async def inner_text(self, target, timeout=0):
            raise TimeoutError("Page.inner_text: Timeout exceeded")

        async def wait_for_load_state(self, state, timeout=0):
            return None

    async def fake_ensure():
        return HostilePage()

    monkeypatch.setattr(browser, "_ensure", fake_ensure)
    result = _json.loads(await browser.browser_act("read"))
    assert "error" not in result
    assert result["text"] == ""
    assert 'action="type"' in result["hint"] and 'action="press"' in result["hint"]


@pytest.mark.asyncio
async def test_read_with_query_in_value_teaches_type_press(page):
    """gpt-4o-mini passed its search query as read's value 3x in one turn
    (observed live) — read must teach the exact type+press calls, never type
    itself (type is CONFIRM-class, read is ALLOW: silent redirect = gate
    bypass)."""
    result = json.loads(await browser.browser_act(
        "read", selector="input[name='q']", value="mobile phones"
    ))
    assert "cannot type" in result["error"]
    assert 'action="type"' in result["error"]
    assert "mobile phones" in result["error"]  # their own query, ready to copy


@pytest.mark.asyncio
async def test_read_with_selector_in_value_slot_forgiven(page):
    page.selectors_present.add("h1")
    result = json.loads(await browser.browser_act("read", value="h1"))
    assert "error" not in result
    assert "headline" in result["text"]
