"""Phase 4 stage 5: browser_act dispatch + per-domain safety (offline, fake page)."""

from __future__ import annotations

import json

import pytest

import tools.browser as browser
from core.bus import EventBus
from core.safety import SafetyClass, SafetyConfig, SafetyGate, SafetySession, classify_tool


class FakePage:
    def __init__(self, url="https://ollama.com/library"):
        self.url = url
        self.clicked: list[str] = []
        self.filled: list[tuple[str, str]] = []
        self.shot_paths: list[str] = []

    def is_closed(self):
        return False

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
    assert "error" in json.loads(await browser.browser_act("type"))


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
