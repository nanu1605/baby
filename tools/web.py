"""Web tools: search (ddgs) and readable-page fetch (httpx + trafilatura)."""

from __future__ import annotations

import re

from tools.registry import tool

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) baby-assistant/0.1"
_MAX_PAGE_CHARS = 6 * 1024

# Injectable config (set from config.yaml at startup).
_engine = "ddg"
_max_results = 6


def configure(engine: str = "ddg", max_results: int = 6) -> None:
    global _engine, _max_results
    _engine = engine
    _max_results = max_results


def _search_ddg(query: str, max_results: int) -> list[dict]:
    from ddgs import DDGS  # duckduckgo-search was frozen/renamed to ddgs (2025)

    hits = DDGS().text(query, max_results=max_results)
    return [
        {"title": h.get("title", ""), "url": h.get("href", ""), "snippet": h.get("body", "")}
        for h in hits
    ]


_ENGINES = {"ddg": _search_ddg}


@tool
def web_search(query: str) -> dict:
    """Search the web; returns titles, URLs and snippets."""
    engine = _ENGINES.get(_engine)
    if engine is None:
        return {"error": f"unknown search engine configured: {_engine}"}
    try:
        results = engine(query, _max_results)
    except Exception as exc:  # noqa: BLE001 — network failures are results
        return {"error": f"search failed: {exc}"}
    if not results:
        return {"error": "no results"}
    return {"results": results}


@tool
def fetch_page(url: str) -> dict:
    """Fetch a web page and return its readable text."""
    if not re.match(r"(?i)^https?://", url or ""):
        return {"error": "only http(s) URLs are allowed"}
    import httpx

    try:
        with httpx.Client(follow_redirects=True, timeout=15, headers={"User-Agent": _UA}) as c:
            resp = c.get(url)
            resp.raise_for_status()
            html = resp.text
            final_url = str(resp.url)
    except httpx.HTTPError as exc:
        return {"error": f"fetch failed: {exc}"}

    import trafilatura

    text = trafilatura.extract(html, include_comments=False) or ""
    title_match = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    title = title_match.group(1).strip() if title_match else ""
    if not text:
        text = re.sub(r"(?s)<(script|style)[^>]*>.*?</\1>", " ", html)
        text = re.sub(r"<[^>]+>", " ", text)
        text = " ".join(text.split())
    truncated = len(text) > _MAX_PAGE_CHARS
    return {
        "url": url,
        "final_url": final_url,
        "title": title,
        "text": text[:_MAX_PAGE_CHARS],
        "truncated": truncated,
    }
