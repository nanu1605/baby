"""File tools: search (Everything → scandir fallback), read, write."""

from __future__ import annotations

import gzip
import json
import os
import time
from datetime import datetime
from pathlib import Path

from tools.registry import tool

CACHE_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "baby"
_INDEX_SKIP = {
    "AppData",
    ".git",
    "node_modules",
    ".venv",
    "__pycache__",
    "$Recycle.Bin",
    ".cache",
    ".gradle",
    ".nuget",
}
_DOC_SUFFIXES = {".pdf", ".docx", ".pptx", ".xlsx"}

# Injectable for tests.
_index_root: Path | None = None
_index_ttl_hours: float = 24.0


def configure(index_root: Path | None = None, index_ttl_hours: float = 24.0) -> None:
    global _index_root, _index_ttl_hours
    _index_root = index_root
    _index_ttl_hours = index_ttl_hours


def _index_path() -> Path:
    return CACHE_DIR / "file_index.json.gz"


def _build_index(root: Path) -> list[tuple[str, str, int, str]]:
    entries: list[tuple[str, str, int, str]] = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    if entry.name in _INDEX_SKIP or entry.name.startswith("$"):
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            st = entry.stat()
                            entries.append(
                                (
                                    entry.name.lower(),
                                    entry.path,
                                    st.st_size,
                                    datetime.fromtimestamp(st.st_mtime).isoformat(
                                        timespec="seconds"
                                    ),
                                )
                            )
                    except OSError:
                        continue
        except OSError:
            continue
    return entries


def _load_or_build_index() -> list[tuple[str, str, int, str]]:
    root = _index_root or Path.home()
    path = _index_path()
    if path.exists():
        age_hours = (time.time() - path.stat().st_mtime) / 3600
        if age_hours < _index_ttl_hours:
            try:
                with gzip.open(path, "rt", encoding="utf-8") as f:
                    return [tuple(e) for e in json.load(f)]
            except (OSError, ValueError):
                pass
    entries = _build_index(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(entries, f)
    return entries


def _fallback_search(query: str, max_results: int) -> list[dict]:
    needle = query.lower()
    hits = [e for e in _load_or_build_index() if needle in e[0]]
    hits.sort(key=lambda e: e[3], reverse=True)  # newest first
    return [
        {"path": path, "size": size, "modified": mtime}
        for _, path, size, mtime in hits[:max_results]
    ]


@tool
def file_search(query: str, max_results: int = 20) -> dict:
    """Search local files by name; instant via Everything, else cached index."""
    max_results = max(1, min(int(max_results), 100))
    from tools import _everything

    results = _everything.search(query, max_results)
    if results is not None:
        return {"engine": "everything", "results": results}
    return {"engine": "index", "results": _fallback_search(query, max_results)}


@tool
def read_file(path: str, max_kb: int = 256) -> dict:
    """Read a text/code file, or pdf/docx/pptx/xlsx as markdown."""
    p = Path(path).expanduser()
    if not p.exists():
        return {"error": f"not found: {p}"}
    if not p.is_file():
        return {"error": f"not a file: {p}"}
    max_bytes = max(1, min(int(max_kb), 1024)) * 1024

    if p.suffix.lower() in _DOC_SUFFIXES:
        try:
            from markitdown import MarkItDown

            text = MarkItDown().convert(str(p)).text_content
        except Exception as exc:  # noqa: BLE001 — conversion failure is a result
            return {"error": f"could not convert {p.suffix} file: {exc}"}
    else:
        with open(p, "rb") as f:
            head = f.read(8192)
        if b"\x00" in head:
            return {"error": "binary file — refusing to read"}
        text = p.read_text(encoding="utf-8", errors="replace")

    truncated = len(text.encode("utf-8", errors="replace")) > max_bytes
    if truncated:
        text = text[:max_bytes] + "\n...[truncated]"
    return {"path": str(p), "text": text, "truncated": truncated}


@tool
def write_file(path: str, content: str, mode: str = "create") -> dict:
    """Write a file under the user profile; create, overwrite, or append."""
    if mode not in ("create", "overwrite", "append"):
        return {"error": f"invalid mode: {mode}"}
    p = Path(path).expanduser().resolve()
    # Defense in depth: the gate already enforces this, but the tool re-checks.
    if not p.is_relative_to(Path.home()):
        return {"error": "writes are restricted to your user profile"}
    if mode == "create" and p.exists():
        return {"error": f"already exists (use overwrite or append): {p}"}
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a" if mode == "append" else "w", encoding="utf-8") as f:
        f.write(content)
    return {"path": str(p), "bytes_written": len(content.encode("utf-8")), "mode": mode}
