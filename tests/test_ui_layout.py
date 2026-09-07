"""The top bar's height is one number, and the bar is not a scroll container.

Both halves are regressions that shipped in 6.0.1 and are invisible to every other
test in this repo, because nothing here renders CSS.

  * `.topbar` had `overflow-x: auto` and no height. Giving one axis a non-visible
    overflow computes the OTHER axis from `visible` to `auto`, so the bar became a
    vertical scroll container: content taller than the box was clipped rather than
    growing it, and the horizontal scrollbar then ate height from the inside.
  * Four fixed overlays -- the inspector, the side panel, its backdrop and the
    omnibox -- each hardcoded the bar's height as a bare `52px`/`64px`, while the
    bar itself was content-derived and sat around 42px. They started in the wrong
    place at rest and underneath the bar once the scrollbar appeared.

These are source-shape assertions, the same kind as the docs gates in
test_uninstall.py. They cannot tell you the app looks right; they can tell you
nobody quietly reintroduced the two things that made it look wrong.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_APP_CSS = _ROOT / "ui" / "app" / "src" / "styles" / "app.css"
_TOKENS_CSS = _ROOT / "ui" / "app" / "src" / "styles" / "tokens.css"

# A bare pixel offset in this band is a header height written out by hand. Real
# offsets outside it (a 4px nudge, a 200px drawer) are none of this test's business.
_SUSPECT_PX = re.compile(r"^\s*(?:top|inset)\s*:\s*(\d+)px", re.M)
_HEADER_BAND = range(36, 81)


@pytest.fixture(scope="module")
def app_css() -> str:
    return _APP_CSS.read_text(encoding="utf-8")


def test_the_topbar_height_is_a_token(app_css: str):
    assert "--topbar-h:" in _TOKENS_CSS.read_text(encoding="utf-8"), (
        "--topbar-h is gone from tokens.css; the overlays have nothing to hang off"
    )
    topbar = _rule(app_css, ".topbar")
    assert "min-height: var(--topbar-h)" in topbar, (
        "the bar no longer states its own height, so the overlays' offset is a "
        f"guess again:\n{topbar}"
    )


def test_the_topbar_is_not_a_scroll_container(app_css: str):
    topbar = _rule(app_css, ".topbar")
    assert "overflow" not in topbar, (
        "overflow on .topbar makes it a scroll container on BOTH axes and clips "
        f"the bar vertically -- compact the contents instead:\n{topbar}"
    )
    assert "flex: 0 0 auto" in topbar, (
        f"the bar can be squashed by the column flex again:\n{topbar}"
    )


def test_no_overlay_hardcodes_the_header_height(app_css: str):
    stale = sorted(
        {
            int(m)
            for m in _SUSPECT_PX.findall(app_css)
            if int(m) in _HEADER_BAND
        }
    )
    assert not stale, (
        f"app.css hardcodes header-shaped offsets {stale} instead of deriving them "
        "from var(--topbar-h). That is exactly how the inspector, side panel, "
        "backdrop and omnibox drifted out of alignment with the bar."
    )


def test_the_bar_can_actually_narrow(app_css: str):
    """Every child of the bar is nowrap and unshrinkable, so its intrinsic width
    (~1450px) exceeded the 1280px default window. Without a ladder of breakpoints
    ABOVE the old 720px cliff, the whole desktop range overflows."""
    widths = {int(w) for w in re.findall(r"@media \(max-width:\s*(\d+)px\)", app_css)}
    above_the_old_cliff = {w for w in widths if w > 720}
    assert above_the_old_cliff, (
        "the only relief is still the 720px breakpoint, which leaves every real "
        "desktop window size rendering an overflowing header"
    )
    assert max(above_the_old_cliff) >= 1280, (
        "no breakpoint reaches the default window width (1280px), so the bar "
        f"still overflows at the size it ships in: {sorted(above_the_old_cliff)}"
    )


def _rule(css: str, selector: str) -> str:
    """The declaration block for `selector` at the top level of the sheet."""
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, f"{selector} is gone from app.css"
    return m.group(1)
