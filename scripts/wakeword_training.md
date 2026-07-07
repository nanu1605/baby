# Training the "jarvis" wake word (owner task, ~1 hour)

Baby wakes on the pretrained **"hey jarvis"** model out of the box. To also wake
on a single-word **"jarvis"**, train a custom openWakeWord model on Google Colab
(local training is blocked: the training tools are not Python-3.13-clean and the
GPU's VRAM is fully owned by the LLM). The custom model runs **alongside** the
pretrained "hey jarvis" — both fire — so "Jarvis" and "Hey Jarvis" wake Baby, and
wake keeps working even before (or if) the custom model is present.

## Steps

1. Open the official notebook:
   https://colab.research.google.com/github/dscripka/openWakeWord/blob/main/notebooks/automatic_model_training.ipynb
   (from the openWakeWord repo: `notebooks/automatic_model_training.ipynb`).
2. Runtime → Change runtime type → **T4 GPU** (free tier is enough).
3. In the config cell set:
   - `target_phrase: "jarvis"`
   - A single two-syllable word carries less acoustic evidence than a phrase,
     so **increase the synthetic-positive count** (e.g. 30–50k) and add a few
     **Indian-English pronunciations** to the phrase list (piper voices +
     `custom_negative_phrases` help). Keep the provided negative datasets.
4. Run all cells. Wall-clock is typically **30–60 minutes** on a T4.
5. Download the resulting `jarvis.onnx` from the notebook's output.
6. Drop it into this repo at **`models/jarvis.onnx`** (the path in
   `voice.wakeword_model`). Extra custom models can be listed in
   `voice.wakeword_models: [..]`.
7. Restart Baby (`uv run python run.py --voice`). Readiness shows
   `wake word ready (jarvis+hey_jarvis)` — no config change needed; the custom
   model is picked up automatically when the file exists.

## Tuning after it lands

- A single word false-accepts more easily, so **start higher**:
  `voice.wakeword_threshold: 0.6` in config.yaml (shared by both models).
- Too many false accepts (wakes on TV / a "jarvis" mention in a video) → raise
  toward 0.7; missed wakes → lower toward 0.5.
- Tune from the logs: every wake publishes `voice: listening`; a false wake that
  hears nothing publishes `voice: heard nothing`. Watch the ratio.
- "Hey Baby" is deferred — the pretrained "hey jarvis" + a custom "jarvis" cover
  the interim.
