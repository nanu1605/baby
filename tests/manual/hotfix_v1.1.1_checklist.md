# v1.1.1 Hotfix — Manual Acceptance Checklist

Ships two bug fixes ahead of the v2 features: dead sensors + `"(no response)"`
(#6) and DB poison that broke model calls (#7). Tick every box before merging
PR #3 and tagging `v1.1.1`. Branch: `hotfix/v1.1.1`.

Prereq once: re-run `powershell -ExecutionPolicy Bypass -File scripts\setup.ps1`
so LibreHardwareMonitor is installed + autostarted, then in LHM → Options enable
**Run on Windows startup**, **Minimize to tray**, and **Remote Web Server** (Run,
port 8085), and **run LHM as administrator** so its sensor driver populates.
(LHM dropped WMI in 0.9.x — Baby now reads the web server's `/data.json`. Set
`LHM_URL` if you run it on another port.)

## 0. Automated gate (must be green)

- [ ] `uv run pytest -q` → all pass (expect **482 passed**).
- [ ] `uv run pytest tests/test_safety.py -q` → all pass (85). Safety gate is the
      hard gate — red here blocks the merge.
- [ ] `uv run ruff check .` → `All checks passed!`.
- [ ] `uv run python -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"`
      → `1.1.1`.

## 1. Sensors — real temps (#6, P1)

Start Baby: `uv run python run.py --all` (or `--ui`).

- [ ] LHM running (elevated, Remote Web Server on) → open
      `http://127.0.0.1:8085/data.json` in a browser first — it should return the
      sensor JSON tree. Then ask "Baby, what's my CPU temperature?" → real Ryzen
      temps spoken/typed (a number in °C, e.g. "CPU around 52°C"), not a guess.
- [ ] Activity feed shows a `get_sensors` tool call with a result (temps list).
- [ ] `uv run python -c "import json,tools.sensors as s; print(json.dumps(s.get_sensors(detail=True),indent=2))"`
      → JSON with `temperatures_c`, `hottest`, `fans_rpm`, `voltages_v`.

## 2. Sensors — graceful failure, never silent (#6, P1)

- [ ] Close LibreHardwareMonitor (right-click tray → Exit). Ask the CPU-temp
      question again → a clear spoken/typed explanation that the sensor source
      (LibreHardwareMonitor) is not running and how to start it — **not** silence
      and **not** `"(no response)"`.
- [ ] `uv run python -c "import json,tools.sensors as s; print(json.dumps(s.get_sensors()))"`
      with LHM closed → `{"error": "sensor source unavailable...", "hint": "...setup.ps1..."}`.
- [ ] Restart LHM; temps work again.

## 3. No more "(no response)" / empty tool result (#6, P1)

- [ ] Normal chatting for a few turns (mix of chat + a tool action) → never see
      the literal string `(no response)` in any reply.
- [ ] (If you can force an empty model turn) the reply is an honest line like
      "I hit a snag generating a response — mind trying that once more?" and the
      activity/audit shows a `generation` entry. Covered by
      `tests/test_loop_guard.py` if you can't reproduce live.

## 4. DB hygiene — migration on the real DB (#7, P2)

- [ ] Stop Baby first (so no turn is mid-flight).
- [ ] Dry-run (touches only a backup copy, live DB untouched):
      `uv run python scripts/migrate_v2_db.py --dry-run` → prints a verified
      backup path under `backups\` and a report (`empty rows`, `failed`,
      `quarantined` counts). Live `baby.db` unchanged.
- [ ] Real run: `uv run python scripts/migrate_v2_db.py` → prints
      "note: stop Baby before migrating…", makes a WAL-safe backup, adds columns,
      quarantines any existing poison, prints "migration complete."
- [ ] `backups\` holds a `baby-<stamp>.db` and it opens
      (`uv run python -c "import sqlite3;print(sqlite3.connect(r'backups/<file>').execute('select count(*) from messages').fetchone())"`).

## 5. DB hygiene — pull the plug mid-turn (#7, P2)

- [ ] Start `run.py --all`. Ask something that runs a tool (e.g. "search the web
      for X"). While it's mid-turn, **hard-kill** the process (close the terminal
      / Task Manager end task).
- [ ] Restart Baby. Ask a fresh question → answers normally (no provider error,
      no hang).
- [ ] The broken turn does not reappear in context: continue the conversation and
      confirm Baby doesn't reference or re-answer the interrupted request.

## 6. DB hygiene — long conversation stays clean (#7, P2)

- [ ] Hold a ~20-turn back-and-forth (mix chat + tool calls + a couple of
      cancels via "baby stop") → zero context/provider errors, no turn silently
      dropped, no `(no response)`.
- [ ] Cancel a turn ("baby stop") mid-answer → later turns don't re-answer the
      cancelled request (closure marker preserved — cancelled turns are NOT
      quarantined, by design).

## 7. No regressions (v1.1.0 still works)

- [ ] Cloud-primary routing: a normal turn is answered by the cloud brain (badge
      shows `cloud`); "use the big brain" escalates; pull the Wi-Fi → local 9B
      answers offline.
- [ ] Game mode toggle still frees VRAM; privacy pin (ask about a `read_file`
      result) stays local.
- [ ] Safety gate: a destructive command is refused, a mutating one asks Yes/No.

---

When 0–7 are all ticked: merge PR #3 and tag `v1.1.1` (commands in the PR / my
message). These same fixes continue on the v2 branch for P3–P5.
