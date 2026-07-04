# Phase 3 Manual Acceptance Checklist

Run `uv run python run.py --voice`. Speaker + mic required.
All of these must pass before Phase 4 starts (spec Section 16, Phase 3).
Interim wake word is **"hey jarvis"** until `models/hey_baby.onnx` lands
(see scripts/wakeword_training.md).

## 1. Ready cue

- [ ] Launch → audible **"Baby ready"** (cached WAV) the moment the stack is live.
- [ ] Boot log shows per-subsystem timings: mic, wake word (model name), vad, stt, tts.
- [ ] Kill Ollama, launch → NO cue; "Baby could not start" toast (never announce
      ready when the model is down).
- [ ] Unplug/disable the mic, launch → chime + "Baby ready (text only)"; web UI
      still works.

## 2. Wake word

- [ ] Say the wake phrase → short beep → Baby listens. 5 tries, quiet room:
      ≥4 should wake.
- [ ] Random speech / TV for one minute → no false wake (threshold 0.55; tune
      per scripts/wakeword_training.md).

## 3. Full loop, three languages

- [ ] English: "What time is it?" → English voice (af_heart) reply.
- [ ] Hindi: "मेरा जिम कब है?" → Hindi voice (hf_beta) reply.
- [ ] Hinglish: "kaisa hai Baby?" → reply spoken (Roman Hinglish uses the
      English voice — correct per spec).
- [ ] Transcript appears in the activity feed as `voice: heard '...'`.

## 4. Barge-in

- [ ] Ask something with a long answer; talk over the reply → playback stops
      within a beat and Baby listens to the interruption.
- [ ] On open speakers: if Baby interrupts ITSELF (echo), raise
      `voice.barge_in_threshold` (0.6 → 0.75) or test with a headset.
- [ ] Short cough/click during playback → does NOT interrupt.

## 5. Kill phrases + push-to-talk

- [ ] Mid-reply say **"Baby stop"** → everything halts; feed shows
      `turn_end status=cancelled` + `voice: stopped by kill phrase`.
- [ ] **"Baby ruk ja"** works the same.
- [ ] Set `voice.wakeword_threshold: 0.99` (wake effectively off), restart:
      **ctrl+alt+b** still triggers listening. Restore threshold after.

## 6. Safety via voice

- [ ] Ask Baby by voice to do something gated (e.g. "delete test.txt from my
      desktop") → Baby SAYS "check the screen"; confirm modal appears in the
      web UI; approve/deny there; outcome lands in audit log.

## 7. Resources

- [ ] During a voice turn run `nvidia-smi`: VRAM unchanged (~7.99 GB, all
      Ollama — voice adds 0; owner-approved amendment of the spec's ≤7.5 GB
      line, which the 9B alone already exceeds).
- [ ] STT latency: utterance → `voice: heard` in ~1–3 s (CPU whisper turbo).
      If too slow for taste: `voice.stt.model: small` (worse Hindi).

## 8. Voice A/B (taste)

- [ ] Compare `voice_hi: hf_alpha` vs `hf_beta` on a few Hindi replies; keep
      the better one in config.yaml.

Sign-off: Tanishq — Phase 3 confirmed → begin Phase 4 (autonomy, notifications,
Telegram, autostart).
