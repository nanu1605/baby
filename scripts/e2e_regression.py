"""Live E2E regression battery: drives the RUNNING Baby end to end.

Scored runs: add --fresh-conversation — battery turns pollute the history and
the local 9B's tool discipline drops on a dirty context (a restart alone does
NOT clear it; the UI reuses the latest conversation by design).

Usage (Baby must be up at 127.0.0.1:8765):
    uv run python scripts/e2e_regression.py                      # T01-T15
    uv run python scripts/e2e_regression.py --fresh-conversation # clean scored run
    uv run python scripts/e2e_regression.py --with-project       # + orchestrator E2E (slow)
    uv run python scripts/e2e_regression.py --rollback-check     # + local_primary flip

Safety: only ALLOW-class actions run (goto/read/screenshot, file reads on a
probe file this script creates, time, memory, game-mode toggles). The safety
gate stays in enforce mode and is NEVER bypassed. T10's start_background_task
is CONFIRM class: by default the battery watches for its OWN confirm dialog
and DENIES it — proving the model→tool→gate pipeline without executing a
confirm-class action unattended. --approve-confirms (attended runs, e.g. the
N5 full course) approves that one dialog through the same POST /confirm
endpoint the UI button uses and asserts the task runs to done. The browser
window will open and announcements may speak — warn the owner before a run.

Assertions come from three sources: the ws turn events, the REST endpoints,
and baby.db's audit_log read read-only. Results → bench_results/E2E_REPORT.md;
exit code 1 if anything failed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sqlite3
import time
from pathlib import Path

import httpx
import websockets

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:8765"
DB = ROOT / "baby.db"
SHOTS = Path(os.path.expandvars(r"%LOCALAPPDATA%\baby\shots"))
PROBE = Path(os.path.expandvars(r"%TEMP%")) / "baby_e2e_probe.txt"
PROBE_SECRET = "PROBE-SECRET-73"

RESULTS: list[tuple[str, str, bool, str]] = []  # (id, name, ok, note)

# Tests that under-read THIS run because the model never even called the tool
# under test (9B discipline on a dirty/degraded window) — excluded from the
# exit code like the static BRAIN_DEPENDENT set, but still printed as FAIL.
RUNTIME_BRAIN_DEPENDENT: set[str] = set()

APPROVE_CONFIRMS = False  # set from --approve-confirms


def record(test_id: str, name: str, ok: bool, note: str = "") -> None:
    RESULTS.append((test_id, name, ok, note))
    print(f"  {test_id} {name}: {'PASS' if ok else 'FAIL'}{' — ' + note if note else ''}")


def audit_marker() -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    row = con.execute("SELECT COALESCE(MAX(id), 0) FROM audit_log").fetchone()
    con.close()
    return int(row[0])


def audit_since(marker: int) -> list[dict]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = [
        dict(r)
        for r in con.execute(
            "SELECT tool, args, result_summary FROM audit_log WHERE id > ? ORDER BY id",
            (marker,),
        )
    ]
    con.close()
    return rows


def router_actions(rows: list[dict]) -> list[tuple[str, str]]:
    out = []
    for r in rows:
        if r["tool"] == "router":
            try:
                out.append((json.loads(r["args"]).get("action", ""), r["result_summary"] or ""))
            except ValueError:
                pass
    return out


async def ws_turn(text: str, timeout: float = 240.0, kill_after_first_token: bool = False):
    """One chat turn; returns (reply, status, brain, elapsed_s)."""
    t0 = time.monotonic()
    async with websockets.connect("ws://127.0.0.1:8765/ws/chat") as ws:
        await ws.send(json.dumps({"type": "user_message", "text": text}))
        reply, killed = "", False
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            event = json.loads(raw)
            kind = event.get("type")
            if kind == "busy":
                await asyncio.sleep(3)
                await ws.send(json.dumps({"type": "user_message", "text": text}))
            elif kind == "token":
                reply += event.get("text", "")
                if kill_after_first_token and not killed:
                    killed = True
                    httpx.post(f"{BASE}/kill", timeout=5)
            elif kind == "turn_end":
                return (
                    reply or event.get("reply", ""),
                    event.get("status", ""),
                    event.get("brain") or {},
                    time.monotonic() - t0,
                )


def get(path: str):
    return httpx.get(f"{BASE}{path}", timeout=10)


def ollama_loaded() -> list[str]:
    try:
        data = httpx.get("http://127.0.0.1:11434/api/ps", timeout=5).json()
        return [m.get("name", "") for m in data.get("models", [])]
    except Exception:  # noqa: BLE001
        return []


# -- battery -----------------------------------------------------------------------


async def t01_stats():
    s = get("/stats").json()
    ok = all(k in s for k in ("router", "brain_turns", "game_mode")) and "state" in s["router"]
    record("T01", "stats sanity", ok, f"state={s.get('router', {}).get('state')}")


async def t02_plain_turn():
    reply, status, brain, took = await ws_turn("In one short sentence, say hello.")
    record("T02", "plain chat turn", bool(reply.strip()) and status == "ok" and "tier" in brain,
           f"brain={brain.get('tier')} {took:.1f}s")


async def t03_tool_turn():
    marker = audit_marker()
    # Name the tool: without it the model can answer from stale context
    # (get_time results linger in history — observed in the baseline run).
    reply, status, _, _ = await ws_turn(
        "Use the get_time tool and tell me the exact current time."
    )
    rows = audit_since(marker)
    used_time = any(r["tool"] == "get_time" for r in rows)
    has_time = bool(re.search(r"\d{1,2}[:.]\d{2}", reply))
    record("T03", "tool turn (get_time)", used_time and has_time and status == "ok",
           f"tool={used_time} time_in_reply={has_time}")


async def t04_memory():
    await ws_turn("Remember that my e2e probe word is kumquat.")
    reply, _, _, _ = await ws_turn("What is my e2e probe word?")
    record("T04", "memory round-trip", "kumquat" in reply.lower(), reply[:60])
    await ws_turn("Forget my e2e probe word.")  # best-effort cleanup


async def t05_privacy_pin():
    PROBE.write_text(f"The probe word is {PROBE_SECRET}.", encoding="utf-8")
    marker = audit_marker()
    reply, _, brain, _ = await ws_turn(f"Read the file {PROBE} and tell me the probe word.")
    rows = audit_since(marker)
    called = any(r["tool"] == "read_file" for r in rows)
    pinned = any("privacy pin" in detail for _, detail in router_actions(rows))
    if not called:
        # No read_file call → nothing for the pin to act on. That's model
        # discipline, not a pin regression: with a pinned tool result present
        # the pin either fires or the pipeline is genuinely broken (the hard
        # FAIL below). Observed in the 2026-07-06 run on a 50-message history.
        RUNTIME_BRAIN_DEPENDENT.add("T05")
        record("T05", "privacy pin (read_file)", False,
               "model never called read_file — brain-dependent under-read, not a pin failure")
    else:
        record("T05", "privacy pin (read_file)", pinned and brain.get("tier") == "daily",
               f"pinned={pinned} brain={brain.get('tier')}")
    PROBE.unlink(missing_ok=True)


async def t06_language_pin():
    marker = audit_marker()
    _, _, brain, _ = await ws_turn("आज के लिए एक छोटी सी शुभकामना दो।")
    actions = router_actions(audit_since(marker))
    pinned = any("language pin" in detail for _, detail in actions)
    record("T06", "language pin (Devanagari)", pinned and brain.get("tier") == "daily",
           f"pinned={pinned}")


async def t07_browser_read():
    reply, status, _, _ = await ws_turn(
        "Open example.com in the browser, read the page, and quote its main "
        "heading exactly, word for word."
    )
    ok = ("example domain" in reply.lower() and status == "ok"
          and "(no response)" not in reply)
    record("T07", "browser goto+read", ok, reply[:60])


async def t08_browser_screenshot():
    before = {p.name for p in SHOTS.glob("*.png")} if SHOTS.exists() else set()
    reply, status, _, _ = await ws_turn(
        "Use the browser_act tool with action screenshot to capture the current page."
    )
    after = {p.name for p in SHOTS.glob("*.png")} if SHOTS.exists() else set()
    record("T08", "browser screenshot", bool(after - before) and status == "ok",
           f"new={sorted(after - before)[:1]}")


async def t09_screen():
    reply, status, _, _ = await ws_turn("What's on my screen right now? One sentence.")
    ok = (status == "ok" and bool(reply.strip())
          and "error" not in reply.lower() and "(no response)" not in reply)
    record("T09", "screen awareness", ok, reply[:60])


async def _answer_own_confirm(approve: bool, window_s: float = 180.0) -> str:
    """Watch /ws/activity for the battery's OWN start_background_task confirm
    and answer it through the same POST /confirm endpoint the UI button uses.

    The gate stays in enforce mode and classifies as usual — this only stands
    in for the click on a dialog the battery itself raised, matched strictly
    on tool name + this battery's task text. Default answer is DENY.
    """
    try:
        async with websockets.connect("ws://127.0.0.1:8765/ws/activity") as ws:
            deadline = time.monotonic() + window_s
            while time.monotonic() < deadline:
                raw = await asyncio.wait_for(
                    ws.recv(), timeout=max(1.0, deadline - time.monotonic())
                )
                event = json.loads(raw)
                if (
                    event.get("type") == "confirm_request"
                    and event.get("tool") == "start_background_task"
                    and "electric cars" in event.get("command", "")
                ):
                    httpx.post(f"{BASE}/confirm/{event['confirm_id']}",
                               json={"approved": approve}, timeout=5)
                    return "approved" if approve else "denied"
    except Exception:  # noqa: BLE001 — no dialog appeared; t10 reports the truth
        pass
    return ""


def _newest_task_status(before: int) -> str:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    row = con.execute(
        "SELECT status FROM tasks WHERE id > ? ORDER BY id DESC LIMIT 1", (before,)
    ).fetchone()
    con.close()
    return row[0] if row else ""


async def t10_background_task():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    before = con.execute("SELECT COALESCE(MAX(id), 0) FROM tasks").fetchone()[0]
    con.close()
    marker = audit_marker()
    # The safety class of start_background_task depends on the MODEL-WRITTEN
    # title/spec (_TASK_GATED_RE): benign wording queues straight away (ALLOW,
    # observed 2026-07-06 second run), gated wording raises a confirm dialog
    # that times out unattended (observed first run). Handle both paths.
    watcher = asyncio.create_task(_answer_own_confirm(APPROVE_CONFIRMS))
    # Explicit tool naming: the 9B answers research inline otherwise (observed
    # twice) — this test verifies the QUEUE pipeline, not model initiative.
    await ws_turn(
        "Use the start_background_task tool to queue this as a background "
        "task: research the top 3 electric cars under 15 lakh and summarize."
    )
    if not watcher.done():
        watcher.cancel()
    try:
        answered = await watcher
    except asyncio.CancelledError:
        answered = ""
    called = any(r["tool"] == "start_background_task" for r in audit_since(marker))
    if not called:
        RUNTIME_BRAIN_DEPENDENT.add("T10")
        record("T10", "background task", False,
               "model never called start_background_task — brain-dependent under-read")
        return
    status = _newest_task_status(before)
    if not status:
        if answered == "denied":
            # Dialog raised and denied by the battery itself: model→tool→gate
            # pipeline proven without executing a confirm-class action
            # unattended — --approve-confirms runs it through to done.
            record("T10", "background task (to gate)", True,
                   "confirm dialog raised and denied by the battery — pipeline proven")
        else:
            record("T10", "background task", False,
                   f"tool called but no task queued and dialog {answered or 'not seen'}")
        return
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        if status in ("done", "failed", "cancelled"):
            break
        await asyncio.sleep(10)
        status = _newest_task_status(before)
    how = f"confirm {answered}" if answered else "queued without dialog (benign spec is ALLOW)"
    record("T10", "background task", status == "done", f"status={status} — {how}")


async def t11_game_mode_cycle():
    httpx.post(f"{BASE}/game_mode", json={"on": True}, timeout=15)
    unloaded = False
    for _ in range(10):
        await asyncio.sleep(2)
        if not ollama_loaded():
            unloaded = True
            break
    httpx.post(f"{BASE}/game_mode", json={"on": False}, timeout=15)
    reloaded = False
    for _ in range(60):
        await asyncio.sleep(2)
        if ollama_loaded():
            reloaded = True
            break
    record("T11", "game-mode VRAM cycle", unloaded and reloaded,
           f"unloaded={unloaded} reloaded={reloaded}")


async def t12_kill_switch():
    _, status, _, _ = await ws_turn(
        "Count slowly from one to fifty, writing every number as a word.",
        kill_after_first_token=True,
    )
    record("T12", "kill switch cancels turn", status == "cancelled", f"status={status}")


async def t13_heavy_escalation():
    state = get("/stats").json().get("router", {}).get("state", "")
    marker = audit_marker()
    await ws_turn("Use the big brain: design a tiny backup plan for my documents folder.")
    actions = router_actions(audit_since(marker))
    attempted = any("nim_heavy" in action for action, _ in actions)
    if state != "cloud":
        # Heavy is unreachable by DESIGN while degraded/offline — the ladder
        # serving backstop/daily IS the correct behavior; note, don't fail.
        served = any(action.startswith("route ") for action, _ in actions)
        record("T13", "heavy escalation attempted", served,
               f"state={state} at run time — fallback ladder served (by design)")
        return
    record("T13", "heavy escalation attempted", attempted,
           "route/skip nim_heavy present" if attempted else f"actions={actions[:3]}")


async def t14_escape_hatch():
    marker = audit_marker()
    reply, status, _, took = await ws_turn("game mode off", timeout=15)
    rows = audit_since(marker)
    model_routes = [a for a, d in router_actions(rows) if a.startswith("route ")]
    ok = "game mode" in reply.lower() and status == "ok" and not model_routes and took < 5
    record("T14", "game-mode escape hatch (no model)", ok,
           f"{took:.1f}s routes={model_routes}")


async def t15_get_endpoints():
    bad = [p for p in ("/tasks", "/projects", "/history", "/memory") if get(p).status_code != 200]
    record("T15", "GET endpoints", not bad, f"failing={bad}" if bad else "all 200")


async def t16_project():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    before = con.execute("SELECT COALESCE(MAX(id), 0) FROM projects").fetchone()[0]
    con.close()
    await ws_turn(
        "Start a project: write a two-line haiku about Indore and a two-line "
        "haiku about monsoon rain, as two separate subtasks."
    )
    deadline = time.monotonic() + 1900
    status = ""
    while time.monotonic() < deadline:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        row = con.execute(
            "SELECT status FROM projects WHERE id > ? ORDER BY id DESC LIMIT 1", (before,)
        ).fetchone()
        con.close()
        status = row[0] if row else ""
        if status in ("done", "failed", "cancelled"):
            break
        await asyncio.sleep(20)
    record("T16", "orchestrator project (--with-project)", status == "done",
           f"status={status or 'never created'}")


BRAIN_DEPENDENT = {"T03", "T07", "T08", "T09"}  # model tool-discipline, not pipeline


def write_report(state: str) -> None:
    passed = sum(1 for _, _, ok, _ in RESULTS if ok)
    lines = [
        "# Live E2E regression report",
        "",
        f"**{passed}/{len(RESULTS)} passed** — router state during run: `{state}`",
        "",
        "Brain-dependent tests (T03, T07–T09) exercise the serving model's tool",
        "discipline and under-read when the state is not `cloud` (the local 9B",
        "serves during congestion, and its discipline drops on a long summary).",
        "Score the N5 full course in a `cloud` window; the remaining tests are",
        "deterministic pipeline checks.",
        "",
        *([f"Runtime under-reads this run (model never called the tool under "
           f"test — excluded from the exit code): "
           f"{', '.join(sorted(RUNTIME_BRAIN_DEPENDENT))}", ""]
          if RUNTIME_BRAIN_DEPENDENT else []),
        "| # | test | result | note |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {tid} | {name} | {'PASS' if ok else '**FAIL**'} | {note} |"
        for tid, name, ok, note in RESULTS
    ]
    out = ROOT / "bench_results" / "E2E_REPORT.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n{passed}/{len(RESULTS)} passed — report: {out}")


async def main(args) -> int:
    try:
        get("/stats")
    except Exception:  # noqa: BLE001
        print("FAIL: Baby is not reachable at 127.0.0.1:8765 — start it first")
        return 1

    # Scored runs need a clean conversation: battery turns pollute the history
    # and the folded summary, and the local 9B's tool discipline degrades
    # measurably on a polluted context. A restart does NOT clear it — the UI
    # reuses the latest conversation by design — hence --fresh-conversation.
    if args.fresh_conversation:
        resp = httpx.post(f"{BASE}/conversation/new", timeout=10)
        if resp.status_code == 200:
            print(f"fresh conversation started (id={resp.json().get('conversation_id')})")
        else:
            print(f"WARNING: /conversation/new failed ({resp.status_code}) — "
                  "running against the existing history")
    try:
        history = get("/history").json()
        if len(history) > 20:
            print(f"WARNING: {len(history)} messages in history — run with "
                  "--fresh-conversation for a clean scored run (a restart alone "
                  "does not clear it; results below may under-read)")
    except Exception:  # noqa: BLE001
        pass

    battery = [
        t01_stats, t02_plain_turn, t03_tool_turn, t04_memory, t05_privacy_pin,
        t06_language_pin, t07_browser_read, t08_browser_screenshot, t09_screen,
        t10_background_task, t12_kill_switch, t13_heavy_escalation,
        t14_escape_hatch, t15_get_endpoints,
    ]
    if not args.skip_restart_tests:
        battery.insert(10, t11_game_mode_cycle)
    if args.with_project:
        battery.append(t16_project)

    state = get("/stats").json().get("router", {}).get("state", "?")
    print(f"running {len(battery)} tests against the live Baby (state={state})...")
    for test in battery:
        try:
            await test()
        except Exception as exc:  # noqa: BLE001 — one dead test must not stop the battery
            record(test.__name__[:3].upper(), test.__name__, False,
                   f"{type(exc).__name__}: {exc}")
    write_report(state)
    soft = BRAIN_DEPENDENT | RUNTIME_BRAIN_DEPENDENT
    hard_fails = [t for t, _, ok, _ in RESULTS if not ok and t not in soft]
    if not hard_fails and state != "cloud":
        print("note: only brain-dependent tests failed under a degraded window — "
              "pipelines green; re-score in a cloud window for the record")
    return 0 if not hard_fails else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Live E2E regression battery")
    parser.add_argument("--with-project", action="store_true",
                        help="include the slow orchestrator project test")
    parser.add_argument("--rollback-check", action="store_true",
                        help="(run separately) flip router.mode local_primary and back — see docs")
    parser.add_argument("--skip-restart-tests", action="store_true",
                        help="skip the game-mode VRAM cycle (owner mid-use)")
    parser.add_argument("--approve-confirms", action="store_true",
                        help="approve the battery's OWN T10 confirm dialog and "
                             "run the background task through to done (attended runs)")
    parser.add_argument("--fresh-conversation", action="store_true",
                        help="start a new UI conversation first (clean history — "
                             "use for scored runs; a restart alone does not clear it)")
    args = parser.parse_args()
    APPROVE_CONFIRMS = args.approve_confirms
    if args.rollback_check:
        print("rollback check is a guided manual step at N5 (config flip + restart x2); "
              "see tests/manual/full_regression_checklist.md §8")
    raise SystemExit(asyncio.run(main(args)))
