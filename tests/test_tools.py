"""Tool unit tests — no network, external I/O mocked or local-only."""

from __future__ import annotations

import sys

import pytest

from tools import files as files_mod
from tools import web as web_mod
from tools.files import file_search, read_file, write_file
from tools.shell import run_shell
from tools.web import fetch_page, web_search

# --- web_search -----------------------------------------------------------------


class _FakeDDGS:
    def __init__(self, hits=None, exc=None):
        self._hits = hits or []
        self._exc = exc

    def text(self, query, max_results=6):
        if self._exc:
            raise self._exc
        return self._hits


def test_web_search_maps_results(monkeypatch):
    hits = [{"title": "INR rate", "href": "https://x.example", "body": "83.2"}]
    monkeypatch.setattr("ddgs.DDGS", lambda: _FakeDDGS(hits))
    out = web_search("usd to inr")
    assert out["results"][0]["url"] == "https://x.example"
    assert out["results"][0]["snippet"] == "83.2"


def test_web_search_exception_becomes_error(monkeypatch):
    monkeypatch.setitem(
        web_mod._ENGINES, "ddg", lambda q, n: _FakeDDGS(exc=RuntimeError("429")).text(q, n)
    )
    assert "error" in web_search("anything")


# --- fetch_page -----------------------------------------------------------------


def test_fetch_page_refuses_non_http():
    assert "error" in fetch_page("file:///C:/Windows/win.ini")
    assert "error" in fetch_page("ftp://example.com/x")


def test_fetch_page_extracts_and_truncates(monkeypatch):
    big_text = "word " * 3000

    class FakeResponse:
        url = "https://x.example/final"
        text = f"<html><head><title>T</title></head><body><p>{big_text}</p></body></html>"

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def get(self, url):
            return FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "Client", FakeClient)
    out = fetch_page("https://x.example")
    assert out["title"] == "T"
    assert out["truncated"] is True
    assert len(out["text"]) <= 6 * 1024


# --- read_file / write_file -------------------------------------------------------


def test_read_file_missing():
    assert "error" in read_file("Z:\\nope\\missing.txt")


def test_read_file_binary_refused(tmp_path):
    p = tmp_path / "blob.bin"
    p.write_bytes(b"abc\x00def")
    assert "refusing" in read_file(str(p))["error"]


def test_read_file_truncates(tmp_path):
    p = tmp_path / "big.txt"
    p.write_text("x" * 4096, encoding="utf-8")
    out = read_file(str(p), max_kb=1)
    assert out["truncated"] is True
    assert "[truncated]" in out["text"]


def test_write_file_modes(tmp_path, monkeypatch):
    monkeypatch.setattr(files_mod.Path, "home", staticmethod(lambda: tmp_path))
    target = tmp_path / "notes.txt"
    assert write_file(str(target), "one")["mode"] == "create"
    assert "already exists" in write_file(str(target), "two")["error"]
    write_file(str(target), " more", mode="append")
    assert target.read_text(encoding="utf-8") == "one more"
    write_file(str(target), "fresh", mode="overwrite")
    assert target.read_text(encoding="utf-8") == "fresh"


def test_write_file_outside_home_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(files_mod.Path, "home", staticmethod(lambda: tmp_path / "home"))
    (tmp_path / "home").mkdir()
    out = write_file(str(tmp_path / "outside.txt"), "x")
    assert "restricted" in out["error"]


# --- file_search fallback index ----------------------------------------------------


def test_file_search_fallback_index(tmp_path, monkeypatch):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "invoice_march.pdf").write_text("x")
    (tmp_path / "docs" / "notes.txt").write_text("x")
    monkeypatch.setattr(files_mod, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr("tools._everything.search", lambda q, n: None)

    files_mod.configure(index_root=tmp_path)
    try:
        out = file_search("invoice")
        assert out["engine"] == "index"
        assert any("invoice_march" in r["path"] for r in out["results"])
    finally:
        files_mod.configure()


# --- run_shell (local PowerShell, no network) ---------------------------------------


pytestmark_shell = pytest.mark.skipif(sys.platform != "win32", reason="windows only")


@pytestmark_shell
async def test_run_shell_captures_output():
    out = await run_shell("Write-Output baby-test-123")
    assert out["exit_code"] == 0
    assert "baby-test-123" in out["stdout"]


@pytestmark_shell
async def test_run_shell_timeout_kills():
    out = await run_shell("Start-Sleep -Seconds 10", timeout_s=1)
    assert "timed out" in out["error"]


@pytestmark_shell
async def test_run_shell_bad_cwd():
    out = await run_shell("Write-Output hi", cwd="Z:\\definitely\\missing")
    assert "cwd does not exist" in out["error"]
