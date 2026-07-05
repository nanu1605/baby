# NIM model shootout — Phase N1 report

N=5 runs per test per model, run at evening IST (peak congestion), Baby's real tool schemas from tools/registry.py.
The script recommends; **Tanishq picks the winners** (config.yaml + DECISIONS.md).

| model | primary_score | heavy_score | T1_action | T2_chain | T3_args | T4_recovery | T5_discipline | T6_hindi | T7_hinglish | T8_json | T9_plan | arg_validity | first_token_p50 | first_token_p95 | count_429 | stream_errors | reasoning_effort |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| minimaxai/minimax-m2.7 | 86.9 | 37.0 | 100.0 | 100.0 | 100.0 | 60.0 | 80.0 | 100.0 | 100.0 | 100.0 | 0.0 | 100.0 | 1.41 | 7.32 | 43 | 0 | rejected (400) |
| qwen/qwen3.5-122b-a10b | 78.7 | 86.0 | 100.0 | 100.0 | 80.0 | 100.0 | 80.0 | 100.0 | 100.0 | 100.0 | 80.0 | 100.0 | 4.07 | 72.33 | 0 | 0 | accepted |
| z-ai/glm-5.2 | 67.5 | 95.0 | 100.0 | 100.0 | 100.0 | 0.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 130.02 | 238.27 | 0 | 0 | accepted |
| nvidia/nemotron-3-super-120b-a12b | 66.3 | 78.0 | 100.0 | 40.0 | 100.0 | 0.0 | 60.0 | 100.0 | 100.0 | 100.0 | 80.0 | 100.0 | 2.18 | 12.46 | 0 | 2 | accepted |
| moonshotai/kimi-k2.6 | 56.5 | 24.0 | 100.0 | 100.0 | 100.0 | 60.0 | 60.0 | 60.0 | 0.0 | 0.0 | 0.0 | 100.0 | 0.88 | 3.26 | 80 | 0 | accepted |
| mistralai/mistral-nemotron | 49.0 | None | 0.0 | 0.0 | 0.0 | 80.0 | 100.0 | 100.0 | 20.0 | 100.0 | None | 0.0 | 0.24 | 0.66 | 0 | 0 | accepted |
| meta/llama-4-maverick-17b-128e-instruct | -7.5 | None | 0.0 | 0.0 | 0.0 | 0.0 | 100.0 | 0.0 | 0.0 | 0.0 | None | 0.0 | 79.1 | 456.6 | 0 | 1 | rejected (400) |

**Recommended primary:** `minimaxai/minimax-m2.7` (score 86.9)
**Recommended heavy:** `z-ai/glm-5.2` (score 95.0)

Column notes: T-columns are pass-% over runs; arg_validity is parseable-JSON tool args over all calls; first_token in seconds; reasoning_effort is whether the model accepted the extra_body knob (T0 probe — informs the N2 router). Transcripts (T7 human judgment) in bench_results/transcripts/.