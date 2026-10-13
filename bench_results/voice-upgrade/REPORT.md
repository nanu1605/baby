# OpenRouter model bench — voice upgrade (DECISIONS #174)

N=5 runs per test per model, run 2026-10-13 10:30–10:37 IST (morning, off-peak) through OpenRouter, Baby's real tool schemas from tools/registry.py. T4 was rescored offline after the honesty check learned typographic apostrophes; the replies are unchanged.
The script recommends; **Tanishq picks the winners** (config.yaml + DECISIONS.md).

| model | primary_score | heavy_score | T1_action | T2_chain | T3_args | T4_recovery | T5_discipline | T6_hindi | T7_hinglish | T8_json | T9_plan | arg_validity | first_token_p50 | first_token_p95 | count_429 | stream_errors | reasoning_effort |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| openai/gpt-4o-mini | 96.0 | None | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | None | 100.0 | 1.01 | 2.86 | 0 | 0 | accepted |
| openai/gpt-5.4-mini | 95.2 | None | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | None | 100.0 | 1.2 | 2.85 | 0 | 0 | accepted |
| openai/gpt-5.4-nano | 95.0 | None | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | None | 100.0 | 1.25 | 2.57 | 0 | 0 | accepted |

**Recommended primary:** `openai/gpt-4o-mini` (score 96.0)


Column notes: T-columns are pass-% over runs; arg_validity is parseable-JSON tool args over all calls; first_token in seconds; reasoning_effort is whether the model accepted the extra_body knob (T0 probe — informs the N2 router). Transcripts (T7 human judgment) in bench_results/voice-upgrade/transcripts/.