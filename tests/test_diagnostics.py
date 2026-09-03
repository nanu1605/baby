"""v6 W5: the diagnostics report must be safe to paste into a public issue.

The collector is the easy half. These tests are about the scrubber, because the
failure mode is a user helpfully attaching their API keys and their Windows
username to a GitHub issue. Each layer is tested for what only it can catch:
exact .env values (a key of unfamiliar shape), key SHAPES (an old key still in a
log line, no longer in .env), and personal identifiers.
"""

from __future__ import annotations

import pytest

from core import diagnostics, keys, paths

_KEY = "sk-or-v1-notarealkey-tail"
_ODD_KEY = "totally-unusual-shape-9911"  # no vendor prefix at all


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("BABY_HOME", str(tmp_path))
    monkeypatch.setenv("USERNAME", "tanishq")
    for k in keys.KEYS:
        monkeypatch.delenv(k.env, raising=False)
    return tmp_path


# --- layer 1: exact known secrets -------------------------------------------


def test_exact_env_values_are_redacted_whatever_their_shape(home):
    """The only layer that can catch a key with no recognisable prefix."""
    keys.write_keys({"OPENROUTER_API_KEY": _ODD_KEY})
    out = diagnostics.scrub(f"provider said: {_ODD_KEY} rejected")
    assert _ODD_KEY not in out
    assert "[redacted]" in out


def test_longest_secret_is_replaced_first(home):
    """Two secrets sharing a prefix: replacing the short one first would leave
    the tail of the long one exposed."""
    short, long = "sk-or-abcdefgh", "sk-or-abcdefgh-plus-more-tail"
    keys.write_keys({"OPENROUTER_API_KEY": long, "GEMINI_API_KEY": short})
    out = diagnostics.scrub(f"used {long} today")
    assert long not in out and "plus-more-tail" not in out


def test_short_env_values_are_not_treated_as_secrets(home):
    """Redacting every short value would destroy the report."""
    (home / ".env").write_text("DEBUG=1\nMODE=on\n", encoding="utf-8")
    out = diagnostics.scrub("mode is on and debug is 1")
    assert out == "mode is on and debug is 1"


# --- layer 2: key shapes ----------------------------------------------------


def test_key_shapes_are_caught_even_when_not_in_env(home):
    """An old key in a weeks-old log line is the realistic case: it is not in
    .env any more, so only the shape layer can catch it."""
    assert not keys.has_key("OPENROUTER_API_KEY")
    samples = [
        "sk-or-v1-abcdefghijklmnopqrst",
        "nvapi-abcdefghijklmnopqrstuv",
        "AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
        "ghp_abcdefghijklmnopqrstuvwxyz1234",
        "Authorization: Bearer abcdefghijklmnopqrst",
    ]
    for s in samples:
        out = diagnostics.scrub(f"log line with {s} in it")
        assert "[redacted]" in out, s
        # The distinctive tail must not survive.
        assert s.split()[-1] not in out, s


def test_ordinary_text_survives_scrubbing(home):
    """An over-eager scrubber that redacts the whole log is useless."""
    text = "2026-09-01 12:00 INFO router picked daily (qwen3.5:9b) in 412ms"
    assert diagnostics.scrub(text) == text


# --- layer 3: personal identifiers ------------------------------------------


def test_username_is_replaced_including_inside_paths(home):
    out = diagnostics.scrub(r"C:\Users\tanishq\AppData\Local\baby\logs\baby.log")
    assert "tanishq" not in out.lower()
    assert "[user]" in out


def test_owner_name_and_city_from_config_are_replaced(home):
    (home / "config.yaml").write_text(
        "owner:\n  name: Jaskaran\n  city: Ludhiana\n", encoding="utf-8"
    )
    out = diagnostics.scrub("greeting for Jaskaran in Ludhiana")
    assert "Jaskaran" not in out and "Ludhiana" not in out


def test_emails_are_replaced(home):
    out = diagnostics.scrub("contact someone@example.com about it")
    assert "someone@example.com" not in out


def test_blank_owner_fields_do_not_scrub_everything(home):
    """The shipped template ships blank owner fields -- an empty term must not
    match every character in the report."""
    (home / "config.yaml").write_text('owner:\n  name: ""\n  city: ""\n', encoding="utf-8")
    text = "a perfectly ordinary log line"
    assert diagnostics.scrub(text) == text


def test_broken_config_does_not_break_diagnostics(home):
    (home / "config.yaml").write_text("owner: [this is not\n  valid: yaml", encoding="utf-8")
    assert diagnostics.scrub("still works") == "still works"


# --- log tail ---------------------------------------------------------------


def test_log_tail_is_scrubbed_and_bounded(home):
    log_dir = home / "logs"
    log_dir.mkdir()
    keys.write_keys({"OPENROUTER_API_KEY": _KEY})
    body = "filler line\n" * 20_000 + f"boot used {_KEY}\n"
    (log_dir / "baby.log").write_text(body, encoding="utf-8")

    tail = diagnostics.log_tail()
    assert _KEY not in tail
    assert len(tail.encode()) <= diagnostics.LOG_TAIL_BYTES
    assert "filler line" in tail  # it is still the log, not an empty string


def test_missing_log_is_not_an_error(home):
    assert diagnostics.log_tail() == ""


# --- the report -------------------------------------------------------------


def test_report_never_contains_a_key_even_masked(home):
    """Not even the masked form: the last four characters are still key material
    once the report is posted in public."""
    keys.write_keys({"OPENROUTER_API_KEY": _KEY})
    log_dir = home / "logs"
    log_dir.mkdir()
    (log_dir / "baby.log").write_text(f"startup with {_KEY}\n", encoding="utf-8")

    report = diagnostics.collect()
    text = diagnostics.render(report)
    masked = keys.mask(_KEY)  # "sk-or-...tail" -- still key material in public
    for blob in (str(report), text):
        assert _KEY not in blob
        assert masked not in blob
    # Presence is still reported -- that is the useful part.
    assert "OPENROUTER_API_KEY" in text
    assert "set" in text


def test_report_flags_a_missing_required_key(home):
    paths.write_setup({"install_mode": "cloud_only"})
    text = diagnostics.render(diagnostics.collect())
    assert "MISSING" in text


def test_report_includes_health_failures_scrubbed(home):
    keys.write_keys({"OPENROUTER_API_KEY": _KEY})
    health = {
        "ok": False,
        "summary": f"kokoro failed for {_KEY}",
        "results": [
            {"name": "kokoro", "ok": False, "detail": f"path C:\\Users\\tanishq with {_KEY}"},
            {"name": "whisper", "ok": True, "detail": "fine"},
        ],
    }
    report = diagnostics.collect(health)
    text = diagnostics.render(report)
    assert _KEY not in text and "tanishq" not in text.lower()
    assert "kokoro" in text
    assert "whisper" not in text  # only failures are listed


def test_write_report_lands_next_to_the_logs(home):
    path = diagnostics.write_report("hello")
    assert path.exists() and path.parent == home / "logs"
    assert path.read_text(encoding="utf-8") == "hello"


def test_diagnostics_endpoint_is_clean(tmp_path, monkeypatch):
    from tests.test_keys import _client, _close

    client, db = _client(tmp_path, monkeypatch)
    try:
        monkeypatch.setenv("USERNAME", "tanishq")
        keys.write_keys({"OPENROUTER_API_KEY": _KEY})
        (tmp_path / "logs").mkdir(exist_ok=True)
        (tmp_path / "logs" / "baby.log").write_text(
            f"boot {_KEY} for C:\\Users\\tanishq\n", encoding="utf-8"
        )
        r = client.get("/api/diagnostics")
        assert r.status_code == 200
        assert _KEY not in r.text
        assert "tanishq" not in r.text.lower()

        r = client.get("/api/diagnostics?save=true")
        saved = r.json()["saved_to"]
        assert saved and _KEY not in open(saved, encoding="utf-8").read()
    finally:
        _close(db)
