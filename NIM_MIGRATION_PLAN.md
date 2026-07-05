# CHANGE SPEC — NIM Cloud-Primary Brain Migration
## Branch: `feature/nim-cloud-primary-router` → PR → `master`

> **Repo:** https://github.com/nanu1605/baby
> **Audience:** Claude Code (executor) + Tanishq (reviewer & merge authority).
> **Read fully before touching code. This spec extends `BABY_PROJECT_PLAN.md`; everything not changed here stays as-is.**

---

## 0. Summary of the Change

Baby's brain hierarchy changes from **local-primary** to **connectivity-aware cloud-primary**:

| | Before | After |
|---|--------|-------|
| Primary brain | Local Qwen3.5 9B | **NVIDIA NIM** (winner of Phase N1 bench) |
| Heavy brain | Local Qwen3.6 35B-A3B | **NIM big model** (Kimi/GLM/Nemotron class) |
| Offline / overflow / pinned | — | **Local Qwen3.5 9B (kept warm)** |
| Backstop | Gemini Flash | Gemini Flash (unchanged) |
| Local 35B heavy | exists | **removed** (frees ~20 GB RAM headroom + disk) |

Key mechanics being added: health-based router state machine, client-side rate limiting, per-request fallback, privacy & language pins, and game mode. The old behavior is preserved behind `router.mode: local_primary` — **rollback is one config line**, which keeps the merge low-risk.

---

## 1. Git Workflow (non-negotiable)

1. **Branch bootstrap is Claude Code's job — fully automatic, no human git commands.** Before touching ANY file, run this exact sequence:
   - `git fetch --all --prune`
   - Detect the default branch via `git remote show origin` (HEAD branch). This doc assumes `master`; if it's `main`, substitute everywhere.
   - `git checkout <default> && git pull`
   - If `feature/nim-cloud-primary-router` exists (locally or on origin) → `git checkout feature/nim-cloud-primary-router` and pull the remote branch if present. If it doesn't exist → `git checkout -b feature/nim-cloud-primary-router`.
   - `git push -u origin feature/nim-cloud-primary-router` so the branch is tracked from minute one.
2. **Branch guard — automatic, at the start of every phase and every work session:** check `git branch --show-current`. If it isn't `feature/nim-cloud-primary-router`, switch to it automatically (bootstrapping per step 1 if needed). If uncommitted changes are sitting on the wrong branch, `git stash` → switch → `git stash pop` so the work lands on the feature branch. **Zero direct commits to master** — if one is ever detected in local history, stop, do not push, and alert Tanishq.
3. After Phase N0, push the branch and **open a DRAFT pull request** titled *"NIM cloud-primary router"* with this spec linked in the body — so every subsequent phase is reviewable as it lands.
4. **One or more conventional commits per phase**, pushed at phase completion:
   - `feat(provider): add NVIDIA NIM provider + config schema (N0)`
   - `feat(bench): pick_nim_model.py shootout harness (N1)`
   - `feat(router): health state machine, token bucket, fallback (N2)`
   - `feat(router): privacy/language pins + game mode (N3)`
   - `feat(ui): brain badges, switch log, game-mode toggle; docs (N4)`
   - `chore(release): soak results, cleanup, PR ready (N5)`
5. Do **not** advance to the next phase until the current phase's acceptance criteria pass and its commit is pushed.
6. The existing test suite — **especially `tests/test_safety.py` — must stay green after every phase.** A red safety test blocks everything.
7. The PR is marked **Ready for Review** only at the end of Phase N5. **Tanishq merges — Claude Code never merges.** Prefer a merge commit (keeps per-phase history); squash is acceptable if Tanishq chooses it at merge time.
8. Never commit `.env` or any `nvapi-` key. New secret: `NVIDIA_API_KEY` goes in `.env` + `.env.example` (empty).

---

## 2. Target Design (recap, authoritative)

### 2.1 Routing ladder — per turn

```
Turn arrives
├─ PINNED (privacy or language pin)          → local 9B only
├─ state == OFFLINE                          → local 9B only
├─ rate bucket empty (overflow)              → local 9B (skip cloud entirely; no queueing)
├─ normal turn                               → NIM primary → (fail) Gemini → (fail) local 9B
├─ heavy turn (planning/orchestration)       → NIM heavy → (fail) NIM primary → (fail) local 9B
└─ game mode ON                              → NIM only (local unloaded); if offline: honest
                                               "text-only degraded" announcement, no local
```

Per-request fallback happens **mid-agent-loop**: both brains speak OpenAI format, so on failure the identical `messages` array is resent to the next rung and the loop continues — no restart, no lost tool results.

First-token timeout before falling to the next rung: **3.5 s for `channel=voice`**, 8 s for text channels.

### 2.2 Health state machine

```
CLOUD ── 1 failure (timeout/429/5xx/DNS) ──► DEGRADED ── net gone ──► OFFLINE
  ▲                                             │  ▲                    │
  └──── 3 consecutive healthy probes ◄──────────┘  └── net returns ◄────┘
```

- Background probe every **45 s**: lightweight `GET /v1/models` for connectivity; a 1-token generation ping is sent **only** when attempting the DEGRADED→CLOUD transition (proves generation actually works without burning quota constantly).
- Passive signals: every real call's outcome feeds the state machine — a failure flips state instantly; successes count toward recovery.
- Hysteresis: fall back after **one** failure; return to cloud only after **3** consecutive healthy probes. No flapping mid-conversation.
- 429 sets a **90 s cloud cooldown** regardless of state.
- Every transition → `audit_log` + activity feed, with reason (`429`, `timeout`, `dns_fail`, `recovered`).

### 2.3 Rate limiting

Client-side **token bucket** capped safely under the free tier's ~40 RPM baseline: default `36` requests/min, shared across all NIM calls (bench, probes, turns, background tasks). Bucket empty → route to local, never queue. Background tasks may consume at most 50% of the bucket so interactive turns keep headroom.

### 2.4 Pins

- **Privacy pins** (`router.privacy_pins`, default `[read_file, run_shell]`): the moment a pinned tool is about to return its result into the context, the **remainder of that turn executes on local**. On later turns, pinned tool results in history are **redacted before any cloud call** — stored fully in SQLite, but sent upward as `[local-only content redacted: file_search result, 2.1 KB]`. Private bytes never leave the PC; the cloud model still sees that *something* happened.
- **Language pin**: if the user message is Devanagari or Hindi-dominant (script-detection heuristic, ≥30% Devanagari chars), route to local Qwen (strong Hindi) or `router.hindi_model` if configured to a hosted Qwen3. Hinglish in Roman script is NOT pinned — it flows to the NIM primary.

### 2.5 Game mode

- Triggers: UI toggle, voice/text command ("Baby, game mode"), optional auto-detect of a fullscreen exclusive app (config `game_mode.auto_detect`, default false).
- Effect: local model unloaded (`keep_alive: 0` + explicit unload call) → ~5.5 GB VRAM freed; **all** routing goes NIM; Whisper stays (voice still works, ~1 GB). Exiting game mode reloads the 9B in the background and announces "Baby ready" when warm again.
- If the net drops during game mode, Baby says so plainly and offers to reload the local brain.

### 2.6 Config schema (new/changed blocks in `config.yaml`)

```yaml
models:
  daily:            # UNCHANGED — local Qwen3.5 9B; now the offline/pinned brain
    keep_alive: 24h
  # heavy: (local 35B block DELETED in N5)
  nim_primary:
    provider: nvidia
    base_url: https://integrate.api.nvidia.com/v1
    model: ""                 # set from N1 bench winner; exact ID from build.nvidia.com
    temperature: 0.7
  nim_heavy:
    provider: nvidia
    base_url: https://integrate.api.nvidia.com/v1
    model: ""                 # set from N1 bench winner (heavy slot)
    temperature: 0.5
  cloud:                      # Gemini backstop — UNCHANGED

router:
  mode: cloud_primary         # cloud_primary | local_primary  ← rollback switch
  primary: nim_primary
  heavy: nim_heavy
  offline_fallback: daily
  backstop: cloud
  local_keep_warm: true
  first_token_timeout_s: {voice: 3.5, text: 8}
  health: {probe_s: 45, recover_after: 3, cooldown_429_s: 90}
  rate_limit: {rpm: 36, background_share: 0.5}
  privacy_pins: [read_file, run_shell]
  language_pin: {enabled: true, devanagari_ratio: 0.3, target: daily}

game_mode:
  enabled: true
  auto_detect: false
```

---

## 3. What Is Explicitly Removed / Preserved

**Removed (Phase N5, after soak passes):** the local heavy brain — config block, router references, and `ollama rm qwen3.6:35b-a3b-q4_K_M` (frees ~20 GB disk). Any llama.cpp MoE-offload notes in docs get marked historical.

**Preserved untouched:** the safety gate and all its tests (cloud models' tool calls pass through the SAME gate — no exceptions), memory system, voice pipeline, UI layout, task queue, scheduler, Telegram, autostart, "Baby ready" cue. The persona prompt is shared verbatim across all providers.

---

## 4. Phase Plan

> Sequential. Each phase = build → tests green → acceptance demo → commit → push. Draft PR stays updated throughout.

### Phase N0 — Branch, Provider & Config Scaffolding *(half a day)*
**Build:**
- **Automatic branch bootstrap first** (Section 1, steps 1–2): fetch → detect default branch → create/switch to `feature/nim-cloud-primary-router` → push with tracking. No human git commands expected. Then open the draft PR.
- `core/providers/nvidia.py`: OpenAI-compat client pointed at `integrate.api.nvidia.com/v1`, auth via `NVIDIA_API_KEY` (`nvapi-` prefix), streaming + tools passthrough, and a `healthy()` implementing the probe rules (models-list check + optional 1-token ping).
- Config loader accepts the full Section 2.6 schema; `.env.example` gains `NVIDIA_API_KEY=`. **Default `router.mode` stays `local_primary`** — nothing user-facing changes yet.
- Provider registered in the router's provider map behind the flag.

**Acceptance:**
- A one-off manual call through the new provider returns a streamed completion and a valid tool call (any catalog model).
- Full existing pytest suite green; Baby behaves exactly as before with the branch checked out.
- `.env` untracked; secret absent from the diff.

### Phase N1 — `scripts/pick_nim_model.py` (the shootout) *(1–2 days)*
**Purpose:** choose `nim_primary` and `nim_heavy` empirically, with Baby's *actual* tool schemas — not reputation.

**Build — the script must:**
- Take candidates from `--models` or a `bench:` config block. Starting shortlist (**verify exact `org/model` IDs on build.nvidia.com pages first — never guess strings**): `nvidia/nemotron-3-super`, Mistral Nemotron, Kimi K2.5/K2.6, `zhipuai/glm-5.2`, MiniMax M2.7, Llama 4 Maverick, and a hosted Qwen3 (Hindi insurance candidate).
- Import Baby's real tool schemas from `tools/registry.py` (no hand-copied schemas).
- Run a fixed battery per model, playing the tool-executor role with canned results:
  - **T1** single action: "Close Chrome and tell me CPU usage" → expects correct `app_control` + `get_system_stats` calls with valid args.
  - **T2** chained loop: scripted 3-step scenario → measures whether the chain completes without derailing.
  - **T3** argument fidelity: `file_search` with specific filters → exact-arg match scoring.
  - **T4** error recovery: return `{"error": ...}` to a call → model must retry sensibly or report honestly; hallucinated success = fail.
  - **T5** no-tool discipline: casual Hinglish chat ("aur baby, kya chal raha hai?") → expects ZERO tool calls.
  - **T6** Hindi: Devanagari prompt → reply in Devanagari (script-ratio check).
  - **T7** Hinglish: Roman code-mix prompt → code-mixed reply (heuristic + transcript saved for human judgment).
  - **T8** JSON mode: structured output against a schema → parse-validity.
  - **T9** *(heavy slot only)* planning: decompose a mini-project into subtask JSON → judged on structure completeness.
- Metrics per model: tool-call validity %, correct-tool %, arg accuracy %, no-tool discipline, first-token p50/p95, full-response latency, 429 count, streaming OK. Run **N=5 per test, at Tanishq's real usage hours** (evening IST) to capture peak congestion.
- Be a good citizen: obey the shared token bucket, exponential backoff on 429, and **cache results per model in `bench_results/*.json`** so interrupted runs resume instead of re-burning quota.
- Emit `bench_results/REPORT.md`: ranked table + recommended primary & heavy.

**Acceptance:**
- Report covers the full shortlist with no crashed runs.
- **Tanishq (human) picks the two winners**, sets them in `config.yaml`, and the choice + reasoning is recorded in `DECISIONS.md`. The script recommends; the human decides.

### Phase N2 — Router v2: State Machine, Bucket, Fallback *(~1 week)*
**Build:**
- `core/router.py` rewritten to the Section 2 design: CLOUD/DEGRADED/OFFLINE state machine, background probe task, passive signals, hysteresis, 429 cooldown, token bucket with background-task share, per-request mid-loop fallback, per-channel first-token timeouts.
- Ladder per Section 2.1 (pins arrive in N3; ladder must already support pin inputs as no-ops).
- Every routing decision + transition → `audit_log` with reason.
- Flip default `router.mode: cloud_primary` **only after** tests below pass.
- `tests/test_router_v2.py` with fully mocked providers: state transitions, hysteresis (no flap on 1 good probe), cooldown, bucket exhaustion → local, mid-loop fallback preserving the messages array, voice-timeout rung-drop, `local_primary` legacy mode still works.

**Acceptance (manual, on the real machine):**
- Mid-conversation, disable Wi-Fi → next turn answers **locally, no crash, no dead air beyond the timeout**; audit shows `OFFLINE`.
- Re-enable → Baby returns to cloud only after the recovery probes; switch logged.
- Hammer 40+ requests/min artificially → overflow turns visibly route local; zero queue buildup.
- A voice turn during a simulated slow-cloud (added latency) falls to local within ~3.5 s.

### Phase N3 — Pins + Game Mode *(~3–4 days)*
**Build:**
- Privacy pins per Section 2.4 including the **history-redaction rule** (full content in SQLite; redaction placeholder in any cloud-bound messages). Unit test proves pinned content bytes never appear in captured cloud-bound payloads (use a request-capturing mock).
- Language pin with Devanagari-ratio detection.
- Game mode: toggle paths (UI/command/auto-detect), unload + reroute + reload, "Baby ready" re-announce on reload, honest offline messaging.

**Acceptance:**
- "Read me my notes file" → whole turn on local; a later cloud turn's payload contains the redaction placeholder, not file bytes (verified via capture mock).
- Devanagari message → local brain badge; Roman Hinglish → NIM badge.
- Game mode ON → `nvidia-smi` shows ~5.5 GB freed; Baby still answers (cloud). OFF → local reloads, ready cue plays.

### Phase N4 — UI, Docs & Soak *(~3 days + 2–3 day soak)*
**Build:**
- UI: per-message **brain badge** (local / NIM / Gemini + model name on hover), router state indicator (cloud/degraded/offline), game-mode toggle, switch events in the activity feed.
- Persona parity pass: identical persona prompt across providers; spot-check tone drift between brains.
- Docs: README (new architecture diagram), CHANGELOG, DECISIONS, `config.yaml` example.
- **Soak:** run Baby `cloud_primary` through 2–3 days of normal use. Collect: turns per brain, fallback count + reasons, 429s, first-token p50/p95 per brain, voice dead-air events, unhandled exceptions (target: zero).

**Acceptance:** soak summary written into the PR description; zero unhandled exceptions; Tanishq signs off that daily feel is equal-or-better than local-primary.

### Phase N5 — Cleanup, Regression & PR Ready *(1 day)*
**Build/Do:**
- Remove the local 35B heavy: config block, router refs, `ollama rm qwen3.6:35b-a3b-q4_K_M`; note historical status in docs.
- Rebase branch on latest master; resolve conflicts.
- **Full regression:** entire pytest suite (safety suite emphatically included) + re-run the ORIGINAL `BABY_PROJECT_PLAN.md` Phase 1–4 manual demos (close-Chrome demo, 3-language voice loop, background task announce, briefing, autostart ready-cue) to prove zero regressions.
- Complete the PR checklist below; mark PR **Ready for Review**. Tanishq reviews and merges; tag `v1.1.0` post-merge.

---

## 5. PR Checklist (paste into the PR description)

- [ ] All phases N0–N5 committed with conventional messages; branch rebased on master
- [ ] Full pytest suite green — including untouched, green `test_safety.py`
- [ ] `test_router_v2.py` covers: states, hysteresis, cooldown, bucket, mid-loop fallback, timeouts, legacy mode
- [ ] Privacy-pin capture test proves pinned bytes never reach cloud payloads
- [ ] Bench `REPORT.md` committed; winners set in config; rationale in `DECISIONS.md`
- [ ] Soak summary (2–3 days) attached: fallbacks, 429s, latency percentiles, zero unhandled exceptions
- [ ] Original Phase 1–4 manual demos re-verified (no regressions)
- [ ] Rollback verified: `router.mode: local_primary` restores old behavior with no code change
- [ ] Docs updated (README, CHANGELOG, DECISIONS, config example); no secrets in diff
- [ ] Local 35B removed; disk/RAM reclaim confirmed

---

## 6. Risks & Rollback

| Risk | Mitigation |
|------|-----------|
| NIM model IDs wrong/renamed | IDs verified on catalog pages in N1; config-only fix |
| Free-tier terms/limits shift mid-build | Bucket + cooldown already assume scarcity; worst case flip `router.mode: local_primary` |
| Two-brain persona drift | Shared persona prompt, temperature parity, N4 spot-check |
| Voice dead air on slow cloud | 3.5 s first-token rung-drop to warm local |
| Private data leak to cloud | Privacy pins + history redaction + capture-mock test in N3 |
| Regression sneaks into master | Everything behind the branch + flag; human-merged PR; full regression in N5 |

**Rollback at any time, even post-merge:** set `router.mode: local_primary`. That's the whole procedure.

---

*End of change spec. Branch first, bench before choosing, soak before merging — and the safety tests outrank everything.*
