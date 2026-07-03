# BABY — Local-First Personal AI Assistant
## Complete Build Specification & Phased Implementation Plan

> **Audience:** Claude Code (autonomous executor) + Tanishq (owner/reviewer).
> **Goal:** Build "Baby", a Jarvis-style, voice-enabled, local-first personal AI assistant for Windows 11.
> **Read this entire document before writing any code.**

---

## Table of Contents

1. [How to Execute This Plan (rules for Claude Code)](#1-how-to-execute-this-plan)
2. [Product Vision & Feature List](#2-product-vision--feature-list)
3. [Target Machine, Constraints & Resource Budgets](#3-target-machine-constraints--resource-budgets)
4. [Architecture Principles](#4-architecture-principles)
5. [Locked Tech Stack](#5-locked-tech-stack)
6. [Repository Layout](#6-repository-layout)
7. [Configuration (`config.yaml` + `.env`)](#7-configuration)
8. [Database Schema (SQLite)](#8-database-schema)
9. [Model Layer: Providers & Router](#9-model-layer-providers--router)
10. [Tool Specifications](#10-tool-specifications)
11. [Safety Gate (shell-gate) Specification](#11-safety-gate-specification)
12. [Memory System Specification](#12-memory-system-specification)
13. [Voice Pipeline Specification](#13-voice-pipeline-specification)
14. [UI Specification](#14-ui-specification)
15. [Background Tasks, Scheduler & Notifications](#15-background-tasks-scheduler--notifications)
16. [Phase Plan (0 → 5) with Acceptance Criteria](#16-phase-plan)
17. [Coding Standards & Testing](#17-coding-standards--testing)
18. [Risks & Mitigations](#18-risks--mitigations)
19. [Out of Scope / Non-Goals](#19-out-of-scope--non-goals)
20. [Appendix A: Baby Persona System Prompt (draft)](#appendix-a-baby-persona-system-prompt)
21. [Appendix B: Portfolio Demo Script](#appendix-b-portfolio-demo-script)

---

## 1. How to Execute This Plan

Rules for Claude Code. These override any conflicting instinct:

1. **Work strictly phase by phase** (Section 16). Do not begin Phase N+1 until every acceptance criterion of Phase N passes and Tanishq has confirmed the demo.
2. **Runtime target is native Windows 11.** All code must run under Windows Python (PowerShell shell, Windows paths). Do NOT assume Linux/WSL2 at runtime. If you (Claude Code) are executing inside WSL, still write Windows-native code and tell Tanishq which commands to run on the Windows side.
3. **Ask before:** installing system-level software (Ollama, Everything, espeak-ng, Node), anything requiring Administrator elevation, anything that costs money (nothing in this plan should), and deleting any user file.
4. **Never bypass the safety gate** (Section 11) — not even in tests. Tests use `dry_run=True`.
5. **Never commit secrets.** `.env` is gitignored from the first commit. Provide `.env.example` instead.
6. **After each phase:** run `pytest`, run the manual demo checklist, update `README.md` + `CHANGELOG.md`, and make a git commit per phase (conventional commits, e.g. `feat(phase-1): text agent + tools + UI`).
7. **Keep tool schemas terse.** The daily model runs with an 8K context; every token of schema costs conversation room.
8. **When ambiguous, choose the simplest option that satisfies the acceptance criteria**, note the decision in `DECISIONS.md`, and move on. Only stop to ask if the choice is irreversible or user-facing.
9. **One process for v1.** Core + UI + workers run in a single asyncio process (`run.py`). The voice pipeline runs in its own thread (audio I/O is blocking) and talks to the core via thread-safe queues. Do not introduce Redis, Celery, Docker, or microservices.

---

## 2. Product Vision & Feature List

Baby is a personal AI assistant that lives on Tanishq's PC. Local models by default (privacy + zero cost), free cloud tier only as a fallback brain. It listens, talks, types, and acts.

**Numbered features (from the owner — all must exist by end of Phase 5, most by Phase 4):**

| # | Feature | Delivered in |
|---|---------|--------------|
| 1 | Talk & listen in English, Hindi, and Hinglish | Phase 3 |
| 2 | Launch itself at login | Phase 4 |
| 3 | Act on the PC — search/read files, run shell commands, query system stats, control apps & browser | Phase 1 (browser in 4) |
| 4 | Reach the web — search for current info | Phase 1 |
| 5 | Remember context across sessions | Phase 2 |
| 6 | UI to type questions and watch Baby work in real time | Phase 1 |
| 7 | Open and close apps on command | Phase 1 |
| 8 | Proactively suggest the next step after finishing a task | Phase 2 |
| 9 | Spawn multiple agents to work on projects | Phase 5 |
| 10 | Announce when a task is completed | Phase 4 |
| 11 | Friend-mode: casual chat, auto-detected from the message itself | Phase 2 |
| + | Screen awareness ("what's on my screen?") | Phase 5 |
| + | Scheduled routines / morning briefing | Phase 4 |
| + | Confirm-before-destructive safety gate | Phase 1 |
| + | Full audit log of every action | Phase 1 |
| + | Speaker verification (only Tanishq's voice commands Baby) | Phase 5 |
| + | Phone access via Telegram (+ Tailscale for UI) | Phase 4 |
| + | **"Baby ready"** audio cue whenever Baby comes online (boot, login, recovery) | Phase 3 (toast from Phase 1) |

**Explicit clarification:** Baby does NOT get kernel-level access. Everything above is achievable in user space (with occasional UAC elevation the user approves manually). Kernel access = drivers = risk with zero benefit here.

---

## 3. Target Machine, Constraints & Resource Budgets

| Component | Spec |
|-----------|------|
| OS | Windows 11 (native — no WSL2 at runtime) |
| CPU | AMD Ryzen 7 9700X (8C/16T) |
| GPU | NVIDIA RTX 5060 Ti, **8 GB VRAM** |
| RAM | 32 GB DDR5 |
| Python | 3.11+ (single venv at `.venv`, managed with `uv`) |

### 3.1 VRAM budget (hard constraint — respect it)

| Consumer | Budget |
|----------|--------|
| Qwen3.5 9B Q4_K_M weights + 8K ctx (q8_0 KV cache) | ~5.5 GB |
| faster-whisper large-v3-turbo (int8, CUDA) | ~1.0 GB |
| Windows display/compositor overhead | ~0.7 GB |
| **Total** | **~7.2 GB / 8 GB** ✅ |

Rules that follow from this:
- Daily model context is capped at **8192 tokens**. Enable KV-cache quantization and flash attention in Ollama.
- Kokoro TTS, openWakeWord, silero-VAD, and sentence-transformers embeddings run on **CPU only**. Never load them on GPU.
- If VRAM pressure appears (OOM / heavy swapping): first drop Whisper to CPU int8, then reduce ctx to 6144. Log the decision.

### 3.2 RAM budget (heavy model)

- Qwen3.6 35B-A3B Q4 needs ~20 GB when loaded (weights mostly in system RAM, MoE experts on CPU, attention on GPU).
- Loading it is allowed only when free RAM > 22 GB. The router (Section 9) checks this via `psutil` before escalating.
- `keep_alive`: daily model `24h` (always warm), heavy model `10m` (unload after idle).

---

## 4. Architecture Principles

1. **Interface-agnostic core.** Mic, web UI, and Telegram are thin clients. All of them submit `InboundMessage` objects to the same `AgentCore` and subscribe to the same event stream. The core never knows or cares where a message came from.
2. **Provider-agnostic model layer.** One `ChatProvider` protocol; Ollama and Gemini are both driven through the OpenAI-compatible chat-completions format (Ollama serves it natively; Gemini exposes an OpenAI-compat endpoint). Swapping brains = changing config.
3. **Everything is a tool.** The model can only affect the world through registered tools with JSON schemas. No `eval`, no free-form code execution outside the gated shell tool.
4. **Safety gate between model and OS.** Every shell/file-write/app-kill action passes through the classifier in Section 11. The model cannot approve its own actions.
5. **Everything is logged.** Every tool call, its arguments, safety class, approval status, and result summary goes to `audit_log`. The UI activity feed and the audit log are the same data.
6. **Text first, voice last.** Phases 0–2 build a fully working text agent. Voice (Phase 3) is a layer on top, never a dependency underneath.
7. **Fail soft.** Cloud down → local. Heavy model won't fit → daily model. Everything SDK missing → slow fallback search. TTS crash → text-only reply. Baby degrades, never dies.

---

## 5. Locked Tech Stack

| Concern | Choice | Notes |
|---------|--------|-------|
| Language | Python 3.11+, asyncio | `uv` for env + deps |
| LLM runtime | **Ollama** (Windows) | OpenAI-compat API at `http://127.0.0.1:11434/v1` |
| Daily brain | **Qwen3.5 9B instruct, Q4_K_M** | fully in VRAM, tool-calling workhorse |
| Heavy brain | **Qwen3.6 35B-A3B, Q4** | MoE; GPU+RAM split; planner/escalation |
| Cloud fallback | **Gemini Flash (free tier)** | optional; via Gemini's OpenAI-compat endpoint; handle 429 → fall back local |
| Agent loop | **Hand-rolled** (no LangChain/CrewAI) | explicit, debuggable, ~200 lines |
| STT | **faster-whisper large-v3-turbo**, int8, CUDA | auto language detect (hi/en); Hinglish passes through as mixed text |
| TTS | **Kokoro-82M** (`kokoro` pip pkg), CPU | Hindi + English voices; needs `espeak-ng` installed; verify voice IDs against the model's VOICES.md at build time |
| Wake word | **openWakeWord** + custom "hey baby" model | trained via their synthetic-data notebook; onnxruntime CPU |
| VAD | **silero-vad** | endpointing, barge-in detection |
| Embeddings | **intfloat/multilingual-e5-small** (384-dim), CPU | good Hindi/Hinglish performance, tiny |
| Vector store | **sqlite-vec** extension | inside the single `baby.db` |
| DB | **SQLite** (WAL mode) | one file: conversations, memory, tasks, audit, schedules |
| UI backend | **FastAPI + WebSockets**, bound to `127.0.0.1` | uvicorn |
| UI frontend | **Single-page vanilla JS + CSS** (no build step) | dark theme; keep it dependency-free |
| Web search | **duckduckgo-search** lib (zero-key default) | config-swappable to Brave API / SearXNG |
| Page fetch | httpx + trafilatura | readable-text extraction |
| Browser control | **Playwright** (Python, Chromium, persistent profile) | Phase 4 |
| File search | **Everything SDK** (`Everything64.dll` via ctypes) | requires Everything app running; fallback = cached `os.scandir` index |
| System stats | psutil + pynvml | CPU/RAM/disk/GPU/processes |
| Notifications | **winotify** (toasts) + TTS announce + Telegram | |
| Scheduler | **APScheduler** | cron-style routines, morning briefing |
| Task queue | SQLite-backed queue + asyncio workers | no external broker |
| Telegram | python-telegram-bot (polling) | locked to `TELEGRAM_CHAT_ID` |
| Autostart | Windows **Task Scheduler** (at logon, hidden) | `scripts/autostart.ps1` registers it |
| Lint/format/test | ruff + pytest + pytest-asyncio | CI-less; run locally each phase |

---

## 6. Repository Layout

```
baby/
├── README.md                  # what Baby is, setup, usage, demo GIFs
├── BABY_PROJECT_PLAN.md       # this file
├── DECISIONS.md               # running log of choices Claude Code made
├── CHANGELOG.md
├── pyproject.toml             # uv-managed; pinned deps
├── config.yaml                # all non-secret config (Section 7)
├── .env.example               # secret template (gitignore .env)
├── run.py                     # entrypoint: --cli | --ui | --voice | --all
│
├── core/
│   ├── agent.py               # AgentCore: the plan→tool→observe loop
│   ├── router.py              # model selection & escalation (Section 9)
│   ├── bus.py                 # async event bus: InboundMessage / AgentEvent
│   ├── prompts.py             # persona + system prompt assembly
│   ├── safety.py              # shell-gate classifier + confirmation flow
│   └── providers/
│       ├── base.py            # ChatProvider protocol (chat(), stream(), tools)
│       ├── ollama.py          # OpenAI-compat client → localhost:11434
│       └── gemini.py          # OpenAI-compat client → Gemini endpoint
│
├── tools/
│   ├── registry.py            # @tool decorator, schema export, dispatch
│   ├── system_stats.py        # get_system_stats
│   ├── apps.py                # app_control (open/close/focus/list)
│   ├── files.py               # file_search / read_file / write_file
│   ├── shell.py               # run_shell (gated PowerShell)
│   ├── web.py                 # web_search / fetch_page
│   ├── browser.py             # browser_act (Phase 4)
│   ├── memory_tools.py        # remember / recall
│   └── tasks_tools.py         # start_background_task / task_status / cancel_task
│
├── memory/
│   ├── store.py               # facts + sqlite-vec CRUD & search
│   ├── embedder.py            # e5 wrapper ("query:" / "passage:" prefixes)
│   ├── summarizer.py          # rolling conversation summaries
│   └── extractor.py           # end-of-conversation fact extraction
│
├── voice/
│   ├── pipeline.py            # thread: mic → wake → VAD → STT → core → TTS
│   ├── wakeword.py            # openWakeWord wrapper, threshold logic
│   ├── stt.py                 # faster-whisper wrapper
│   ├── tts.py                 # Kokoro wrapper + per-sentence script routing
│   └── audio_io.py            # sounddevice in/out, barge-in kill switch
│
├── ui/
│   ├── server.py              # FastAPI app: /ws/chat, /ws/activity, REST
│   └── web/                   # index.html, app.js, style.css (no build step)
│
├── workers/
│   ├── queue.py               # SQLite task queue + asyncio worker pool
│   ├── scheduler.py           # APScheduler jobs incl. morning briefing
│   └── notify.py              # toast + TTS announce + telegram push
│
├── clients/
│   ├── cli.py                 # REPL client (Phase 0 debug surface)
│   └── telegram_bot.py        # Phase 4
│
├── db/
│   ├── schema.sql             # Section 8
│   └── database.py            # aiosqlite wrapper, migrations, WAL
│
├── assets/
│   └── baby_ready.wav         # cached "Baby ready" cue, rendered by setup.ps1
│
├── scripts/
│   ├── setup.ps1              # winget installs, ollama pull, uv sync, playwright install, render ready cue
│   ├── autostart.ps1          # register/unregister Task Scheduler job
│   └── wakeword_training.md   # how to train "hey baby" (Colab notebook steps)
│
└── tests/
    ├── test_safety.py         # the most important test file in the repo
    ├── test_registry.py
    ├── test_router.py
    ├── test_memory.py
    └── test_agent_loop.py     # with a FakeProvider (scripted responses)
```

---

## 7. Configuration

### 7.1 `config.yaml` (complete example — ship this file)

```yaml
owner:
  name: Tanishq
  city: Indore
  languages: [en, hi, hinglish]

models:
  daily:
    provider: ollama
    model: qwen3.5:9b-instruct-q4_K_M      # adjust tag to what `ollama pull` actually provides
    num_ctx: 8192
    temperature: 0.7
    keep_alive: 24h
  heavy:
    provider: ollama
    model: qwen3.6:35b-a3b-q4_K_M
    num_ctx: 8192
    temperature: 0.5
    keep_alive: 10m
    min_free_ram_gb: 22
  cloud:
    provider: gemini
    model: gemini-flash-latest              # free tier; key in .env; optional
  embedder: intfloat/multilingual-e5-small

router:
  default: daily
  escalate_on: [explicit_request, planning_task, retry_after_failure, long_context]
  escalation_order: [heavy, cloud]          # falls through if unavailable

voice:
  wakeword_model: models/hey_baby.onnx
  wakeword_threshold: 0.55
  push_to_talk_hotkey: ctrl+alt+b
  stt:
    model: large-v3-turbo
    device: cuda
    compute_type: int8
  tts:
    engine: kokoro
    voice_en: af_heart                      # verify against Kokoro VOICES.md
    voice_hi: hf_beta                       # verify against Kokoro VOICES.md
    speed: 1.05
  vad_silence_ms: 400
  barge_in: true
  ready_announce:
    enabled: true
    phrase: "Baby ready"
    sound_file: assets/baby_ready.wav     # pre-rendered by setup.ps1; instant playback
    min_interval_s: 60                    # throttle so restart loops can't spam it

ui:
  host: 127.0.0.1
  port: 8765

search:
  engine: ddg                               # ddg | brave | searxng
  max_results: 6

safety:
  mode: enforce                             # enforce | dry_run
  auto_allow_app_close: [chrome, msedge, notepad, spotify, vlc]

telegram:
  enabled: false                            # flip in Phase 4

briefing:
  enabled: false                            # flip in Phase 4
  cron: "0 8 * * *"
  include: [date, weather, pending_tasks, headlines, system_health]
```

### 7.2 `.env.example`

```
GEMINI_API_KEY=            # optional; enables cloud fallback
TELEGRAM_BOT_TOKEN=        # Phase 4
TELEGRAM_CHAT_ID=          # Phase 4; Baby ONLY answers this chat id
BRAVE_API_KEY=             # optional alternative search
```

---

## 8. Database Schema

Single file `baby.db`, WAL mode, accessed via `aiosqlite`. `db/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS conversations (
  id INTEGER PRIMARY KEY,
  channel TEXT NOT NULL,               -- cli | ui | voice | telegram | scheduler
  started_at TEXT DEFAULT (datetime('now')),
  summary TEXT                         -- rolling summary lives here
);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  conversation_id INTEGER REFERENCES conversations(id),
  role TEXT NOT NULL,                  -- user | assistant | tool
  content TEXT NOT NULL,
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS facts (
  id INTEGER PRIMARY KEY,
  text TEXT NOT NULL,                  -- "Tanishq's car is a Skoda Kushaq"
  source TEXT,                         -- explicit | extracted
  created_at TEXT DEFAULT (datetime('now')),
  last_used_at TEXT,
  active INTEGER DEFAULT 1
);

-- sqlite-vec virtual table; e5-small = 384 dims
CREATE VIRTUAL TABLE IF NOT EXISTS fact_vectors USING vec0(
  fact_id INTEGER PRIMARY KEY,
  embedding float[384]
);

CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  spec TEXT NOT NULL,                  -- full natural-language task description
  status TEXT DEFAULT 'queued',        -- queued | running | done | failed | cancelled
  result TEXT,
  notify INTEGER DEFAULT 1,
  created_at TEXT DEFAULT (datetime('now')),
  started_at TEXT,
  finished_at TEXT
);

CREATE TABLE IF NOT EXISTS task_events (
  id INTEGER PRIMARY KEY,
  task_id INTEGER REFERENCES tasks(id),
  ts TEXT DEFAULT (datetime('now')),
  kind TEXT,                           -- log | tool_call | error | done
  payload TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY,
  ts TEXT DEFAULT (datetime('now')),
  channel TEXT,
  tool TEXT NOT NULL,
  args TEXT NOT NULL,                  -- JSON
  safety_class TEXT,                   -- allow | confirm | deny
  approved INTEGER,                    -- 1 approved / 0 refused-or-denied
  result_summary TEXT
);

CREATE TABLE IF NOT EXISTS schedules (
  id INTEGER PRIMARY KEY,
  cron TEXT NOT NULL,
  prompt TEXT NOT NULL,                -- what Baby should do when it fires
  enabled INTEGER DEFAULT 1,
  last_run TEXT
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);
```

---

## 9. Model Layer: Providers & Router

### 9.1 Provider protocol (`core/providers/base.py`)

```python
class ChatProvider(Protocol):
    name: str
    async def chat(self, messages: list[dict], tools: list[dict] | None,
                   stream: bool = True, **opts) -> AsyncIterator[Chunk]: ...
    async def healthy(self) -> bool: ...
```

- Both `ollama.py` and `gemini.py` use the **openai** Python client with a custom `base_url` — one code path, two brains. Tool calls arrive in OpenAI `tool_calls` format from both.
- Gemini errors `429/5xx` → provider reports unhealthy → router falls back. Never crash on cloud failure.

### 9.2 Ollama runtime settings

Set once in `scripts/setup.ps1` (user env vars) and document in README:

```
OLLAMA_FLASH_ATTENTION=1
OLLAMA_KV_CACHE_TYPE=q8_0
```

Pull commands (verify exact tags at build time from the Ollama library):

```
ollama pull qwen3.5:9b-instruct-q4_K_M
ollama pull qwen3.6:35b-a3b-q4_K_M
```

**MoE performance note:** if the 35B-A3B runs below ~8 t/s under Ollama's automatic offload, switch the heavy brain to a direct `llama.cpp` server (`llama-server`) with attention layers on GPU and MoE experts on CPU (`--n-gpu-layers 99 --override-tensor "exps=CPU"` style flags), still exposed as an OpenAI endpoint on another port. The provider layer makes this a config-only change. Record whichever path wins in `DECISIONS.md`.

### 9.3 Router logic (`core/router.py`)

```
pick_model(request):
  1. explicit override ("use the big brain" / "cloud pe pooch") → that model
  2. score task: planning keywords, >3 expected tool steps, code generation,
     long inputs (>4K tokens), OR previous attempt on daily model failed
     → escalate
  3. escalation: heavy IF psutil free RAM > min_free_ram_gb ELSE cloud IF key+online
     ELSE stay on daily and say so
  4. every routing decision → audit_log + activity feed ("thinking harder…")
```

Keep the "task score" heuristic dumb and transparent in v1 (keyword + count based). Do not build an ML classifier.

---

## 10. Tool Specifications

All tools registered via `@tool` decorator in `tools/registry.py`, which auto-generates OpenAI-format JSON schemas from type hints + docstrings. **Descriptions ≤ 25 words each** (context budget).

| Tool | Signature | Behavior | Safety class |
|------|-----------|----------|--------------|
| `get_system_stats` | `(detail: bool = False)` | CPU %, RAM, disk, GPU util/VRAM (pynvml), top 5 processes if detail | allow |
| `app_control` | `(action: "open"\|"close"\|"focus"\|"list", name: str)` | open: resolve via Start-Menu shortcut index built at boot + known paths; close: graceful `WM_CLOSE`, then `taskkill` after 5 s | open/list/focus: allow · close: allow if in `auto_allow_app_close`, else confirm · system processes: deny |
| `file_search` | `(query: str, max_results: int = 20)` | Everything SDK instant search → path, size, mtime. Fallback: cached scandir index (rebuild nightly) | allow |
| `read_file` | `(path: str, max_kb: int = 256)` | txt/md/code raw; pdf/docx via `markitdown`; refuse binaries | allow (log path) |
| `write_file` | `(path: str, content: str, mode: "create"\|"overwrite"\|"append")` | writes under user profile only; never system dirs | confirm |
| `run_shell` | `(command: str, cwd: str = "~", timeout_s: int = 60)` | PowerShell, output captured (stdout+stderr, truncated 8 KB) | classified per Section 11 |
| `web_search` | `(query: str)` | ddg lib → title/url/snippet ×6 | allow |
| `fetch_page` | `(url: str)` | httpx GET + trafilatura extract, truncate 6 KB | allow |
| `remember` | `(fact: str)` | store fact + embedding | allow |
| `recall` | `(query: str, k: int = 5)` | vector search over facts | allow |
| `start_background_task` | `(title: str, spec: str)` | enqueue task (Section 15), return task_id | confirm if spec contains gated actions, else allow |
| `task_status` | `(task_id: int \| None)` | one or all active tasks | allow |
| `cancel_task` | `(task_id: int)` | cancel queued/running task | allow |
| `browser_act` (Phase 4) | `(action: "goto"\|"click"\|"type"\|"read"\|"screenshot", selector: str = "", value: str = "")` | Playwright Chromium, persistent profile in `%LOCALAPPDATA%/baby/browser` | goto/read/screenshot: allow · click/type: confirm on first use per domain per session |

**Agent loop contract (`core/agent.py`):** max **8 tool iterations** per user turn; on hitting the cap, summarize progress and ask the user. Every tool result is appended as a `tool` message. Exceptions inside tools are caught and returned as `{"error": "..."}` so the model can react instead of the loop dying.

---

## 11. Safety Gate Specification

`core/safety.py` — the most important 200 lines in the repo. **Regex/rule based, deterministic, unit-tested. The LLM never classifies its own commands.**

### 11.1 Three classes

| Class | Meaning | Examples |
|-------|---------|----------|
| **ALLOW** | read-only, reversible, zero risk | `Get-*`, `dir`, `type`, `whoami`, `ipconfig`, `ping`, `tasklist`, `systeminfo`, `git status/log/diff`, `python --version` |
| **CONFIRM** | mutating but recoverable — requires explicit user yes | `New-Item`, `Copy-Item`, `Move-Item`, `Remove-Item` (non-system paths), `Set-*`, `pip/uv/winget install`, `git push/commit`, `taskkill`, `Compress-Archive`, any command writing outside `~` |
| **DENY** | destructive / system-critical — refused outright, never even asked | `Remove-Item -Recurse` on drive roots or `C:\Windows`/`Program Files`, `format`, `diskpart`, `bcdedit`, `reg delete HKLM`, `vssadmin delete`, `cipher /w`, `Stop-Computer`/`Restart-Computer` (unless the user's own message explicitly requested shutdown), `Set-ExecutionPolicy` machine-wide, anything targeting `System32`, killing processes: `csrss, wininit, winlogon, lsass, services, svchost` |

### 11.2 Rules

1. DENY patterns are checked **first** and always win.
2. Unknown/unclassifiable commands default to **CONFIRM** (never allow-by-default).
3. Chained commands (`;`, `&&`, `|`, backticks, `Invoke-Expression`, encoded commands `-enc`) are classified by their **most dangerous** segment; `Invoke-Expression` and `-EncodedCommand` are DENY outright.
4. Confirmation flow: UI shows a modal with the exact command + one-line plain-English explanation; voice asks *"Should I run: … — yes or no?"* and accepts `yes / haan / kar do / go ahead` (and `no / nahi / ruk` to refuse). Timeout 60 s → treated as NO.
5. Every classification and decision → `audit_log`, approved or not.
6. **Kill switch:** global hotkey `Ctrl+Alt+Shift+B` and the phrases "Baby stop / Baby ruk ja" immediately cancel the current agent turn, all running tools, and TTS playback.
7. `safety.mode: dry_run` prints what *would* run without executing — used by all tests.

### 11.3 Required tests (`tests/test_safety.py`)

At minimum 40 cases: 15 ALLOW, 15 CONFIRM, 10 DENY — including sneaky ones: `powershell -enc <b64>`, `Remove-Item -Recurse -Force C:\`, `cmd /c "del /s /q C:\Users"`, chained `Get-Date; Remove-Item x`, `Stop-Process -Name lsass`. **Phase 1 cannot pass while any safety test fails.**

---

## 12. Memory System Specification

Three layers, all inside `baby.db`:

**1. Short-term (working context).** Last N messages of the live conversation, passed verbatim to the model. When the running token estimate approaches the 8K cap, the oldest messages are folded into the rolling summary.

**2. Rolling summary (`summarizer.py`).** After every ~10 messages, a cheap daily-model call compresses older turns into `conversations.summary` (target ≤ 200 tokens). The summary is always prepended to context so long sessions never lose the thread.

**3. Long-term facts (`store.py` + `extractor.py`).**
- Explicit: user says "remember …" → `remember` tool stores the fact + e5 embedding.
- Extracted: at conversation end (or every 20 messages), `extractor.py` asks the model to pull durable, user-specific facts (preferences, names, projects, recurring tasks) as a JSON list. Deduplicate by cosine similarity > 0.9 before insert.
- Retrieval: before each user turn, embed the query (`"query: …"` prefix for e5) and pull top-k facts (k=5) above a similarity floor; inject under a `## What Baby remembers` block in the system prompt.
- Hygiene: `last_used_at` updated on hit; facts never auto-deleted in v1 (user can say "forget that" → set `active=0`).

**Embedding discipline:** e5 requires `"query: "` on searches and `"passage: "` on stored facts. Getting this wrong silently halves retrieval quality — enforce in `embedder.py`, test it.

---

## 13. Voice Pipeline Specification

`voice/pipeline.py` runs on a **dedicated thread** (audio I/O blocks; must not stall the asyncio core). Flow:

```
mic stream (sounddevice, 16 kHz mono)
  → openWakeWord scores every frame; score > threshold  ──►  wake
  → silero-VAD opens capture; records until vad_silence_ms of silence
  → faster-whisper (turbo, int8, cuda) transcribes → text + detected lang
  → submit InboundMessage(channel="voice", text, lang) to AgentCore
  → stream assistant reply sentence-by-sentence into Kokoro (CPU)
  → audio_io plays; if user speaks (VAD fires) mid-playback → barge-in: stop TTS instantly
```

**Language routing (feature #1):**
- STT auto-detects Hindi vs English. Hinglish arrives as Roman-script mixed text — pass it through untouched; the LLM handles code-mixed input natively.
- TTS routes **per sentence** by script: any Devanagari → `voice_hi`; otherwise → `voice_en`. A Hinglish reply in Roman script uses the English voice (correct — that's how people read it aloud).
- Persona instruction: Baby replies in the language the user used (English → English, Hindi → Hindi, Hinglish → Hinglish).

**Reliability details:**
- Wake-word threshold starts at 0.55; expose in config; log false-accept/false-reject events to tune it.
- Push-to-talk hotkey (`ctrl+alt+b`) bypasses wake word entirely — the reliable fallback if the wake model misbehaves.
- "hey baby" custom model is trained via openWakeWord's synthetic-speech notebook (Piper-generated positive samples + their negative sets). Steps live in `scripts/wakeword_training.md`. Ship a working `.onnx` before Phase 3 is "done".
- espeak-ng must be installed and on PATH for Kokoro's phonemizer — `scripts/setup.ps1` handles it; verify at startup and warn clearly if missing.

**Readiness announcement — "Baby ready" (owner requirement).**
- On **every** startup (manual launch, crash-restart, or the Task Scheduler login launch), `run.py` runs a readiness sequence: DB opens → daily model answers a 1-token warm-up ping (this also pre-warms Ollama) → tools registered → UI server bound → if voice is enabled: mic opened, wake word + VAD + STT + TTS loaded.
- The moment **all** checks pass, Baby plays `assets/baby_ready.wav` — the phrase "Baby ready" **pre-rendered once by Kokoro during `setup.ps1` and cached**. Using a cached WAV (not live TTS) makes the cue instant and independent of TTS cold-start.
- Degraded paths: if audio/TTS failed to load but text works → Windows chime + toast **"Baby ready (text only)"**. If the model itself isn't reachable, no ready cue is played — a "Baby could not start: …" toast appears instead. Never announce ready when Baby can't actually respond.
- The same cue replays when the voice pipeline **recovers** after an error (e.g., audio device reconnect), throttled by `min_interval_s` so a crash loop can't chant "Baby ready" endlessly.
- Every readiness event (full / text-only / failed, with timings per subsystem) → `audit_log` + activity feed.
- Pre-voice phases (0–2) implement the same readiness sequence with console log + toast; Phase 3 upgrades it to the spoken cue.

---

## 14. UI Specification

**Backend (`ui/server.py`):** FastAPI on `127.0.0.1:8765` (never `0.0.0.0` — no LAN exposure without Tailscale).
- `WS /ws/chat` — user text in; streamed assistant tokens out.
- `WS /ws/activity` — live `AgentEvent` stream (every tool call, its args, safety class, result, model-routing decisions). This IS the audit log, rendered live.
- `POST /confirm/{id}` — approve/deny a CONFIRM action from the modal.
- `GET /history`, `GET /tasks`, `GET /memory` — read views.

**Frontend (`ui/web/`, no build step):** single dark-themed page, two panes:
- **Left — Chat:** message bubbles, streaming tokens, a text box (feature #6: type instead of speak), mic-toggle button, language just flows naturally.
- **Right — Activity feed (feature #6: "see what he is doing"):** a live timeline. Each entry: icon + tool name + collapsible args + status (running → ✓/✗) + result summary. Color by safety class (green allow / amber confirm / red deny). Confirmations surface as an inline modal with the command, plain-English explanation, and Yes/No.
- Header strip: current model (daily/heavy/cloud badge), VRAM + RAM gauges (polled), running-tasks count, kill-switch button.

Keep it genuinely dependency-free (vanilla JS + CSS, maybe one tiny WS helper). No React/build pipeline — this is a personal tool and the simplicity is a feature.

---

## 15. Background Tasks, Scheduler & Notifications

**Task queue (`workers/queue.py`) — powers features #9 groundwork, #10.**
- `start_background_task(title, spec)` inserts a `tasks` row (`queued`) and returns `task_id` immediately so the foreground chat stays responsive.
- An asyncio worker pool (size 2 in v1) pulls queued tasks and runs each through a **fresh AgentCore instance** with its own tool-iteration budget (raise cap to ~15 for background work). Progress → `task_events` and the activity feed.
- On finish/fail → `notify.py` fires (feature #10): **Windows toast** (winotify) + **spoken announcement** ("Baby: your task '…' is done") + **Telegram push** if enabled. Message includes a one-line result.
- `task_status` / `cancel_task` tools + `GET /tasks` expose state.

**Scheduler (`workers/scheduler.py`).**
- APScheduler loads enabled `schedules` rows as cron jobs; each fires a prompt into a scheduler-channel AgentCore turn.
- **Morning briefing** (feature +): at `briefing.cron`, Baby assembles date, Indore weather (web), pending tasks, a few headlines, and system health, then speaks + toasts it. Off by default; enabled in Phase 4.

**Multi-agent (Phase 5, feature #9).** The heavy brain acts as **orchestrator**: it decomposes a project into subtasks, writes them to the `tasks` board, and spawns daily-model **worker** AgentCores (2–3 max, VRAM-bounded) that each own a scoped subtask. Workers report to `task_events`; the orchestrator polls, integrates, and announces completion. Strictly bounded concurrency — small local models compound errors across chained steps, so keep chains short and always leave the orchestrator on the smart brain.

---

## 16. Phase Plan

> Each phase is independently runnable and demoable. Do not advance until acceptance criteria pass **and** Tanishq confirms. One git commit (minimum) per phase.

### Phase 0 — Skeleton & Heartbeat  *(target: a weekend)*
**Build:** repo scaffold, `pyproject.toml` (uv), `config.yaml` + `.env.example`, `db/schema.sql` applied on boot (WAL), `ChatProvider` protocol + `ollama.py`, minimal `AgentCore` loop (message → model → optional tool → observe → reply), `registry.py` with ONE dummy tool (`get_time`), and `clients/cli.py` REPL. `scripts/setup.ps1` installs Ollama + pulls the daily model + `uv sync`.
**Acceptance:**
- `python run.py --cli` chats with Qwen3.5 9B locally.
- Model calls `get_time` and uses the result in its reply.
- Conversation + messages persist to `baby.db`; restart preserves history.
- `ruff` clean; `pytest` (loop test with FakeProvider) green.

### Phase 1 — Text Agent, Real Tools & UI  *(~2 weeks)*  → features #3, #4, #6, #7, audit, gate
**Build:** all Section 10 tools except `browser_act` — `get_system_stats`, `app_control`, `file_search` (Everything SDK + fallback), `read_file`, `write_file`, `run_shell`, `web_search`, `fetch_page`. Full **safety gate** (Section 11) with its test suite. `audit_log` writing on every tool call. FastAPI UI (Section 14): chat pane + **live activity feed** + confirmation modal. Everything SDK integration via ctypes (+ install check). Startup **readiness sequence** with a "Baby ready" toast once core + tools + UI are up (upgraded to the spoken cue in Phase 3).
**Acceptance:**
- `python run.py --ui` → browser UI; typing works; tokens stream.
- "Close Chrome and tell me my CPU and GPU usage" → Chrome closes, stats returned, **both actions visible in the activity feed**.
- "Search my drive for invoices from last month" → Everything results in seconds.
- "Search the web for today's USD to INR rate" → current answer with sources.
- A CONFIRM command shows the modal; a DENY command is refused with reason; both audited.
- `tests/test_safety.py` (40+ cases) fully green. `test_registry.py` green.

### Phase 2 — Memory & Personality  *(~1 week)*  → features #5, #8, #11
**Build:** `embedder.py` (e5 + prefixes), `store.py` + `fact_vectors`, `summarizer.py`, `extractor.py`; `remember`/`recall` tools; retrieval injection into the system prompt; the **Baby persona** (Appendix A) with per-message chat-vs-act detection (feature #11); **next-step suggestion** — after each completed task the orchestrator makes one extra call proposing the logical next action (feature #8).
**Acceptance:**
- Tell Baby a fact today; in a new session tomorrow it recalls and uses it.
- Casual "kaisa hai Baby?" → friendly chat, **no tools fired**; "close Spotify" → acts. Detection is automatic.
- After finishing a multi-step task, Baby proactively proposes a sensible next step.
- Hinglish fact ("mera gym Monday Wednesday Friday hai") stored and retrieved correctly. `test_memory.py` green (incl. e5 prefix + dedup tests).

### Phase 3 — Voice  *(~2 weeks)*  → feature #1
**Build:** `audio_io.py` (sounddevice + barge-in), `wakeword.py` (custom "hey baby" .onnx), `stt.py` (faster-whisper turbo int8 cuda), `tts.py` (Kokoro CPU + per-sentence script routing), `pipeline.py` thread wired to the core, push-to-talk hotkey, kill-switch phrases. Trained wake-word model shipped. VRAM re-measured against the 7.2 GB budget. **Ready cue:** `setup.ps1` pre-renders `assets/baby_ready.wav` with Kokoro; the readiness sequence plays it the moment the full stack is live (Section 13).
**Acceptance:**
- "Hey Baby" wakes reliably (tuned threshold, low false accepts in a quiet room).
- Full loop in **all three languages**: English question → English voice; Hindi question → Hindi voice; Hinglish → correct handling.
- Barge-in: talking over Baby stops playback instantly.
- Push-to-talk works with wake word disabled. "Baby ruk ja" halts everything.
- Launching Baby ends with an audible **"Baby ready"** the moment the stack is live; if audio is unavailable it degrades to chime + toast, and no cue plays if the model isn't actually reachable.
- Measured VRAM ≤ ~7.5 GB with 9B + Whisper loaded together.

### Phase 4 — Autonomy, Notifications, Reach  *(~1–2 weeks)*  → features #2, #10, +briefing, +phone
**Build:** `workers/queue.py` + worker pool; `notify.py` (toast + TTS announce + Telegram); `browser_act` (Playwright, persistent profile); `scheduler.py` + **morning briefing**; **model router escalation** live (daily → heavy → cloud) with RAM checks; `clients/telegram_bot.py` (locked to `TELEGRAM_CHAT_ID`); `scripts/autostart.ps1` registers the **Task Scheduler logon job** (do this last, once stable).
**Acceptance:**
- "In the background, research the top 3 EVs under 15 lakh and summarize" → returns a `task_id`, chat stays usable, and on completion Baby **announces + toasts + Telegrams** the result (feature #10).
- A planning-heavy request visibly escalates to the heavy/cloud brain (badge changes; logged).
- Morning briefing fires on schedule and is spoken.
- Browser task (open a site, read something back) works via Playwright.
- Telegram: messaging Baby from the phone works and is restricted to the owner chat id.
- Baby **auto-launches at Windows login** (feature #2), hidden, UI reachable — and **"Baby ready"** plays shortly after reaching the desktop with zero interaction.

### Phase 5 — Multi-Agent & Advanced Awareness  *(open-ended)*  → feature #9, +screen, +speaker-id
**Build:** orchestrator/worker multi-agent over the shared `tasks` board (Section 15, VRAM-bounded); **screen awareness** (screenshot → a small local vision model, e.g. a Qwen-VL/Gemma-vision variant that fits, OR Gemini free-tier vision fallback → "what's on my screen?"); **speaker verification** (a speaker-embedding check so only Tanishq's voice triggers actions; unknown voices get chat-only or are ignored per config); Tailscale doc so the UI is reachable from the phone securely.
**Acceptance:**
- "Baby, build me a starter FastAPI project with auth and tests" → orchestrator decomposes, workers execute scoped subtasks, progress shows per worker, integrated result announced.
- "What's on my screen?" → accurate description.
- Another person saying "Hey Baby, delete X" is not obeyed for gated actions; Tanishq's voice is.

**Portfolio checkpoint:** Phases 0–3 + a polished README and a demo video already make a strong MSc-application/AI-ML-recruiter artifact. Ship that milestone deliberately before sinking time into Phase 5.

---

## 17. Coding Standards & Testing

- **Style:** ruff (lint+format), full type hints, Google-style docstrings (they feed tool schemas — keep them accurate and short).
- **Async everywhere** in core/UI/workers; the only threads are the voice pipeline and blocking audio.
- **Errors:** tools never raise into the loop — they return `{"error": "..."}`. The core catches, logs, and lets the model recover or ask.
- **Secrets:** only via `.env` (pydantic-settings). `.env` gitignored from commit 1. Never log key values.
- **Tests per phase** (pytest + pytest-asyncio):
  - `test_safety.py` — 40+ cases, **must stay green forever** (allow/confirm/deny + injection/chain tricks).
  - `test_registry.py` — schema generation + dispatch + arg validation.
  - `test_router.py` — escalation picks (mock RAM high/low, cloud up/down).
  - `test_memory.py` — e5 prefixes, round-trip recall, dedup, Hinglish fact.
  - `test_agent_loop.py` — FakeProvider scripts multi-tool turns; asserts iteration cap + tool-message threading.
- **No network in unit tests** — mock providers, search, and Everything. A separate `tests/manual/` holds the human demo checklist per phase.
- **Docs kept live:** `README.md` (setup + usage + GIFs), `CHANGELOG.md` (per phase), `DECISIONS.md` (every non-obvious choice), `DEMO.md` (Appendix B).

---

## 18. Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| **8 GB VRAM overflow** | Hard 8K ctx cap; KV-cache q8_0; Whisper int8; TTS/wakeword/embeddings CPU-only; documented fallbacks (Whisper→CPU, ctx→6144). |
| **35B-A3B too slow via Ollama** | Fall back to direct `llama.cpp` MoE offload (experts→CPU). Config-only swap. Or lean on Gemini free tier for heavy reasoning. |
| **Small-model multi-agent errors compound** | Keep chains short; orchestrator always on smart brain; bounded worker count; every step audited. Defer to Phase 5. |
| **Dangerous shell command slips through** | Deterministic gate, DENY-first, unknown→CONFIRM, `Invoke-Expression`/`-enc` always denied, 40+ tests, kill switch. LLM never self-approves. |
| **Wake-word false triggers ("baby" is common)** | Train on "**hey** baby"; tunable threshold; push-to-talk fallback; log accept/reject to tune. |
| **Hindi/Hinglish quality on a 9B** | e5 for Hinglish embeddings; Whisper turbo handles code-mix; escalate hard multilingual tasks; Kokoro Hindi voice for TTS. |
| **Everything not installed/running** | Startup check + clear warning; automatic scandir fallback index. |
| **Gemini free-tier rate limits (429)** | Provider marks unhealthy on 429/5xx → router falls back to local. Cloud is never a hard dependency. |
| **Telegram exposed to strangers** | Bot answers ONLY `TELEGRAM_CHAT_ID`; ignore all else. UI bound to localhost; remote only via Tailscale. |
| **Autostart hides a crashing app** | Register Task Scheduler last (Phase 4), after stability; log to file; easy `autostart.ps1 -Remove`. |
| **Secret leak (happened before)** | `.env` gitignored day 1; `.env.example` only; never echo keys; setup script reminds to rotate any exposed key. |

---

## 19. Out of Scope / Non-Goals

- **Kernel drivers / true kernel access** — explicitly rejected; user space + occasional user-approved UAC covers every feature.
- Cloud LLMs as the *primary* brain (local-first is the whole point; cloud is fallback only).
- LangChain / CrewAI / AutoGen — the loop is hand-rolled for transparency.
- Docker / Redis / Celery / microservices in v1 — single process.
- Multi-user support — Baby is Tanishq's, single-owner.
- Mobile/desktop native apps — the phone surface is Telegram (+ Tailscale-served web UI).
- Fine-tuning any model — off-the-shelf weights only.

---

## Appendix A: Baby Persona System Prompt

*(Draft — assembled in `core/prompts.py`; memory + tool schemas appended at runtime.)*

```
You are Baby, Tanishq's personal AI assistant running locally on his Windows PC.

Identity & tone:
- Warm, quick, and a little witty — like a sharp friend, not a corporate bot.
- You know Tanishq (Indore; software/DevOps engineer; into AI/ML, EVs, fitness, personal finance).

Language:
- Reply in the SAME language the user used: English→English, Hindi→Hindi, Hinglish→Hinglish.
- Match their register. Keep it natural; don't over-formalize Hindi.

Two modes — pick automatically from the message, never announce which:
- ACT mode: the user wants something done (open/close apps, files, system stats, web, tasks).
  Use tools. Be concise about what you did.
- CHAT mode: the user is talking, venting, joking, or asking an opinion. Just talk. No tools.
  If a message is ambiguous, lean chat, and offer to act.

Acting rules:
- Think step by step; use tools rather than guessing about the system, files, or current facts.
- For anything that changes the system, the safety layer may ask the user to confirm — that's expected; explain briefly what a command does when asked.
- Never fabricate file contents, command output, or web facts. If a tool failed, say so and suggest a fix.
- After finishing a real task, proactively suggest the single most useful next step (one line).

Boundaries:
- You do not have kernel access and don't need it.
- Never run destructive commands; if asked, refuse and explain the safer path.
- Keep spoken (voice) replies short and clear; keep typed replies tight but complete.
```

---

## Appendix B: Portfolio Demo Script

A 2–3 minute video that shows Baby off (great for MSc applications + AI/ML recruiters):
0. Restart the PC. As the desktop settles, an unprompted **"Baby ready"** plays — Baby booted itself. *(autostart + ready cue)*
1. Say **"Hey Baby, kaise ho?"** → friendly Hinglish reply, voice, no tools. *(persona + voice + #11)*
2. **"Close Chrome and show me my CPU and GPU usage."** → watch both actions land in the activity feed. *(#3, #6, #7)*
3. **"Search the web for today's USD to INR rate."** → current answer with sources. *(#4)*
4. Type in the UI: **"Remember my gym days are Mon/Wed/Fri."** → later, **"When's my gym?"** recalls it. *(#5, #6)*
5. **"In the background, research the 3 best EVs under 15 lakh and summarize."** → get a task id, keep chatting; on completion Baby announces + toasts. *(#10, background)*
6. Ask something gnarly → badge flips to the heavy/cloud brain. *(router)*
7. Try a destructive command → Baby refuses and explains. *(safety)*
8. **"What's on my screen?"** → describes it. *(screen awareness)*

---

*End of specification. Build Baby phase by phase. Ship the Phase 0–3 milestone as a portfolio artifact before Phase 5.*
