# B6 manual acceptance — speaker verification v2

Owner-run, needs a mic + `--voice`. B6 redesigns v1 (which false-rejected natural
speech and shipped OFF): natural multi-position enrollment → multiple centroids in
the new `speaker_profiles` DB table, scored by **max cosine**; per-utterance scores
feed a **session-trust smoother** (optimistic-demote) that writes the existing
binary gate flag. **No safety-gate logic changed.** Ships **OFF by default**; this
checklist is mostly the B7 soak setup. Automated coverage: `tests/test_speaker.py`
(multi-centroid + fallback), `tests/test_session_trust.py`, `tests/test_speaker_profiles.py`,
`tests/test_voice.py` (observe / demote / audit), `tests/test_safety.py` (gate unchanged).

Prereq: `scripts/setup.ps1` has downloaded the bench models (CAM++, ERes2Net,
TitaNet-large, SpeakerNet). Back up `baby.db` first (done by the build:
`backups/baby-b6.db`).

---

## §1 — Enrollment (guided single-sitting)

- [ ] `uv run python scripts/enroll_voice_v2.py` walks near / normal / far / turned
  positions, records natural speech, and stores centroids. It prints an
  intra-speaker cosine matrix + a suggested accept/reject band.
- [ ] Re-run with `--append` → extra centroids are added, existing ones kept.
- [ ] Bench a candidate: `--model models/nemo_en_titanet_large.onnx` stores
  model-scoped centroids (does not clobber the CAM++ profile).
- [ ] A non-owner test profile: `--label guest1` records under a different label.

## §2 — Boot + node status (OFF by default)

- [ ] With `voice.speaker_verify.enabled: false`, boot `--voice`: the boot log +
  the `speaker_verify` graph node read **off / disabled** — nothing gates.
- [ ] Set `enabled: true`, `mode: observe`, restart: the node shows
  **`on (N centroids …) · trust trusted`**; the tier + smoothed score update as you speak.

## §3 — Observe mode (the B7 soak mode: score + log, never gate)

- [ ] `mode: observe`, `enabled: true`. Speak several turns. Every turn runs normally
  (tools never blocked) — observe never enforces.
- [ ] `uv run python scripts/speaker_report.py --since <today>` prints a per-model
  FRR table from the logged utterances (thin but non-empty).
- [ ] Have someone else speak during a marked window; run with
  `--far-since <t> --far-until <t>` → a FAR column appears for that window.

## §4 — Session trust (optimistic-demote), only when enforcing

- [ ] `mode: chat_only`, `enabled: true`. A fresh wake session starts **trusted** —
  one clean owner turn keeps full tools; one borderline utterance does NOT lock you out.
- [ ] Sustained non-owner speech (a stranger, several turns) demotes the session to
  **chat-only**: tools denied at the gate, chat still answers. The status line names
  the tier (`trust unknown … — chat only`).
- [ ] The owner speaking clearly again recovers trust within the session.
- [ ] **PTT is always trusted** — hold the hotkey, tools work regardless of voice.
- [ ] **"baby stop" always works** — it cancels mid-turn even from an unknown speaker
  (checked before verification).

## §5 — Honest fallback + safety

- [ ] Delete the DB centroids (or use a v1-only setup): the verifier falls back to
  `models/owner_voice.json` (v1 mean) with no crash.
- [ ] A missing model file / broken profile → the node reads a fail-soft "off (…)"
  note; voice keeps working text-only.
- [ ] Another person's "delete X" is refused (chat-only) while your identical command
  runs — capture this for the B7 FAR/FRR report.

## §6 — Gates

- [ ] `uv run pytest -q` green; `uv run pytest tests/test_safety.py -q` green
  (gate behavior unchanged); `uv run ruff check .` clean.
- [ ] `npm --prefix ui/app run build` + `npm --prefix ui/app test` green (FE untouched
  beyond the node status string).
- [ ] `http://127.0.0.1:8765/classic` still works. `git branch --show-current` =
  `feature/v3-brain-ui`; zero commits on master.

---

**Restore after:** set `voice.speaker_verify.enabled: false` to disable. Clear a
profile with `enroll_voice_v2.py` (a non-append run wipes that model's centroids
first) or leave the DB rows — they're inert while disabled.
