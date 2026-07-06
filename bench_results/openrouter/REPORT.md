# NIM model shootout — Phase N1 report

N=5 runs per test per model, run at evening IST (peak congestion), Baby's real tool schemas from tools/registry.py.
The script recommends; **Tanishq picks the winners** (config.yaml + DECISIONS.md).

| model | primary_score | heavy_score | T1_action | T2_chain | T3_args | T4_recovery | T5_discipline | T6_hindi | T7_hinglish | T8_json | T9_plan | arg_validity | first_token_p50 | first_token_p95 | count_429 | stream_errors | reasoning_effort |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| openai/gpt-4o-mini | 95.2 | None | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | None | 100.0 | 1.2 | 4.01 | 0 | 0 | accepted |
| deepseek/deepseek-chat-v3.1 | 90.5 | None | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | None | 100.0 | 2.37 | 6.21 | 0 | 0 | accepted |
| qwen/qwen3-32b | 73.3 | None | 100.0 | 100.0 | 100.0 | 80.0 | 80.0 | 100.0 | 80.0 | 100.0 | None | 100.0 | 4.8 | 10.26 | 0 | 0 | accepted |
| meta-llama/llama-3.3-70b-instruct | 22.3 | None | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 0.0 | 20.0 | 0.0 | None | 100.0 | 1.29 | 7.76 | 0 | 0 | accepted |
| minimax/minimax-m2.7 | 0.0 | None | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | None | 0.0 | 0.0 | 0.0 | 0 | 200 | rejected (400) |

**Recommended primary:** `openai/gpt-4o-mini` (score 95.2)


Column notes: T-columns are pass-% over runs; arg_validity is parseable-JSON tool args over all calls; first_token in seconds; reasoning_effort is whether the model accepted the extra_body knob (T0 probe — informs the N2 router). Transcripts (T7 human judgment) in bench_results/transcripts/.