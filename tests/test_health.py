"""v6 W3: the first-run functional health check. These tests cover the runner and
mode-composition with fake checks -- the real wheel/model probes are exercised on a
provisioned machine (dev box CLI + the owner's clean-VM matrix), not in unit tests
(they'd load multi-GB models). The load-bearing logic here is: a check that raises
becomes a fail (never crashes the probe), required-gating decides overall_ok, and
the Ollama checks appear only in Full mode."""

from __future__ import annotations

from core import health
from core.health import Result, overall_ok, run


def _pass():
    return "pass", "ok"


def _fail():
    return "fail", "broke"


def _skip():
    return "skip", "n/a"


def _raise():
    raise RuntimeError("boom\nsecond line")


def test_run_maps_status_to_result():
    results = run([("a", True, _pass), ("b", False, _skip)])
    assert results[0] == Result("a", True, True, "pass", "ok")
    assert results[1].status == "skip" and results[1].ok is True


def test_a_raising_check_becomes_a_one_line_fail_not_a_crash():
    (r,) = run([("x", True, _raise)])
    assert r.status == "fail" and r.ok is False
    assert "\n" not in r.detail  # collapsed to one line for the user
    assert "boom" in r.detail


def test_overall_ok_gates_on_required_only():
    # A failed OPTIONAL check does not sink readiness; a failed REQUIRED one does.
    soft_fail = run([("req", True, _pass), ("opt", False, _fail)])
    assert overall_ok(soft_fail) is True
    hard_fail = run([("req", True, _fail), ("opt", False, _pass)])
    assert overall_ok(hard_fail) is False


def test_skip_never_counts_as_failure():
    assert overall_ok(run([("req", True, _skip)])) is True


def test_model_checks_gate_ollama_by_mode():
    full = {name for name, _, _ in health.model_checks("full")}
    cloud = {name for name, _, _ in health.model_checks("cloud_only")}
    assert {"ollama", "ollama-model"} <= full
    assert not ({"ollama", "ollama-model"} & cloud)
    # Voice/memory model loads are checked in both modes.
    assert {"whisper", "kokoro", "embedder", "wakeword"} <= cloud


def test_check_tables_pin_required_flags():
    # overall_ok() gates on these flags -- a flipped bool in the real tables would ship
    # a broken REQUIRED component green, so pin them (not just the names).
    wheel = {name: req for name, req, _ in health.wheel_checks()}
    assert wheel["torch"] and wheel["onnxruntime"] and wheel["vcredist"]
    assert wheel["sqlite-vec"] is False  # soft: brute-force BLOB fallback
    model = {name: req for name, req, _ in health.model_checks("full")}
    assert model["whisper"] and model["kokoro"] and model["embedder"] and model["wakeword"]
    assert model["ollama"] and model["ollama-model"]
    assert model["speaker"] is False  # optional: off until enrollment


def test_wheels_level_skips_model_checks():
    # level="wheels" runs post-sync, before any model is downloaded -- so it must not
    # include the model-load checks (which would fail on a model-less box).
    names = {name for name, _, _ in health.wheel_checks()}
    assert "torch" in names and "onnxruntime" in names and "vcredist" in names
    assert "whisper" not in names and "kokoro-tts" not in names


def test_readiness_summary_names_broken_deps():
    results = run([("torch", True, _pass), ("kokoro", True, _fail)])
    msg = health.readiness_summary(results)
    assert "kokoro" in msg and "broke" in msg
    ready = health.readiness_summary(run([("torch", True, _pass)]))
    assert "ready" in ready.lower()


def test_vcredist_skips_off_windows(monkeypatch):
    monkeypatch.setattr(health.platform, "system", lambda: "Linux")
    status, detail = health.check_vcredist()
    assert status == "skip"


def test_vcredist_fails_when_a_runtime_dll_is_missing(monkeypatch):
    monkeypatch.setattr(health.platform, "system", lambda: "Windows")
    # Report vcruntime140_1.dll (the VS2019+ addition many wheels need) as missing.
    monkeypatch.setattr(
        health.os.path, "exists", lambda p: "vcruntime140_1.dll" not in str(p)
    )
    status, detail = health.check_vcredist()
    assert status == "fail" and "vcruntime140_1.dll" in detail


def test_vcredist_passes_when_all_dlls_present(monkeypatch):
    monkeypatch.setattr(health.platform, "system", lambda: "Windows")
    monkeypatch.setattr(health.os.path, "exists", lambda p: True)
    status, _ = health.check_vcredist()
    assert status == "pass"


# --- the local brain is not installed by anything we ship --------------------
# On a public Full install nothing puts Ollama on the machine, so an unreachable
# daemon is the ORDINARY first-run outcome, not an exceptional one. A clean VM
# showed a user two lines of `ConnectError: [WinError 10061] No connection could
# be made because the target machine actively refused it` and a Retry button that
# re-ran the same check forever.


class _Refused(Exception):
    """Stands in for httpx.ConnectError without importing httpx into the test."""


def _patch_httpx(monkeypatch, get_raises):
    """Give core.health a fake httpx whose .get raises what the test asks for."""
    import sys
    import types

    fake = types.ModuleType("httpx")
    fake.TransportError = _Refused

    def _get(*_a, **_k):
        raise get_raises

    fake.get = _get
    monkeypatch.setitem(sys.modules, "httpx", fake)


def test_an_unreachable_ollama_reads_as_a_sentence_not_a_winerror(monkeypatch):
    _patch_httpx(monkeypatch, _Refused("[WinError 10061] actively refused it"))
    status, detail = health.check_ollama()
    assert status == "fail"
    assert "WinError" not in detail and "ConnectError" not in detail
    assert "ollama.com/download" in detail
    # It must not STATE the software is absent -- a refused connection cannot tell
    # "not installed" from "installed but stopped", and sending someone to
    # reinstall what they already have is its own dead end. Conditional only.
    assert "If it isn't installed" in detail


def test_the_model_check_blames_the_daemon_not_the_weights(monkeypatch):
    """One dead daemon used to produce two different diagnoses, the second wrong."""
    _patch_httpx(monkeypatch, _Refused("refused"))
    status, detail = health.check_ollama_model()
    assert status == "fail"
    assert "not pulled" not in detail
    assert detail == health.check_ollama()[1]


def test_a_local_brain_only_failure_is_recognised():
    results = [
        Result("torch", True, True, "pass", "ok"),
        Result("ollama", True, False, "fail", "no daemon"),
        Result("ollama-model", True, False, "fail", "no daemon"),
    ]
    assert health.local_brain_only_failure(results)


def test_any_other_failure_disqualifies_the_cloud_fallback():
    """Switching modes must only be offered when it would actually fix the run."""
    results = [
        Result("whisper", True, False, "fail", "model missing"),
        Result("ollama", True, False, "fail", "no daemon"),
    ]
    assert not health.local_brain_only_failure(results)


def test_a_healthy_run_is_not_a_local_brain_failure():
    assert not health.local_brain_only_failure([Result("torch", True, True, "pass", "ok")])


def test_an_optional_local_brain_failure_still_counts_as_clean():
    """Only REQUIRED failures gate, matching overall_ok."""
    results = [Result("ollama", False, False, "fail", "no daemon")]
    assert not health.local_brain_only_failure(results)


def test_one_dead_daemon_is_reported_once_not_twice():
    """Both local-brain checks fail with the same sentence; the banner said it
    twice and read as two separate problems."""
    same = "Baby can't reach Ollama at http://x"
    results = [
        Result("ollama", True, False, "fail", same),
        Result("ollama-model", True, False, "fail", same),
    ]
    msg = health.readiness_summary(results)
    assert msg.count(same) == 1
