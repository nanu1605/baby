# Training the "hey baby" wake word (owner task, ~1 hour)

Baby currently wakes on the pretrained **"hey jarvis"** model. To switch to
"hey baby", train a custom openWakeWord model on Google Colab (local training
is blocked: the training tools are not Python-3.13-clean and the GPU's VRAM
is fully owned by the LLM).

## Steps

1. Open the official notebook:
   https://colab.research.google.com/github/dscripka/openWakeWord/blob/main/notebooks/automatic_model_training.ipynb
   (from the openWakeWord repo: `notebooks/automatic_model_training.ipynb`).
2. Runtime → Change runtime type → **T4 GPU** (free tier is enough).
3. In the config cell set:
   - `target_phrase: "hey baby"`
   - leave the synthetic-sample counts at their defaults (the notebook
     generates positives with piper-sample-generator and mixes in the
     provided negative datasets).
4. Run all cells. Wall-clock is typically **30–60 minutes** on a T4.
5. Download the resulting `hey_baby.onnx` from the notebook's output.
6. Drop it into this repo at **`models/hey_baby.onnx`**.
7. Restart Baby (`uv run python run.py --voice`). The readiness notes will
   show `wake word ready (hey_baby)` — no config change needed; the custom
   model is picked up automatically when the file exists.

## Tuning after the swap

- Start threshold: `voice.wakeword_threshold: 0.55` in config.yaml.
- Too many false accepts (wakes on TV/random speech) → raise toward 0.7.
- Missed wakes → lower toward 0.45.
- Watch the activity feed: every wake publishes `voice: listening`.
