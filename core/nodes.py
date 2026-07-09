"""Graph topology for the v3 "Brain" UI (B1).

`build_graph(config)` returns the `{nodes, edges}` the frontend renders as Baby's
mind. It is **derived** for the parts that drift and hand-authored for the parts
that are stable architecture:

- **tool nodes** come from the live tool registry — add a `@tool` and a node
  appears, no edit here (`tools/registry.py::schemas()`).
- **brain nodes** come from the config `models` block + the router role map, so a
  model swap in `config.yaml` moves through automatically.
- **fixed subsystems** (router, safety gate, memory chain, voice chain, task queue,
  scheduler, integrations) and the **static call-path edges** are declared here —
  they are real, stable parts of the architecture, not guesses.

Pure and read-only: no DB, no I/O, no import of the running app. Live stats and
state come from `/api/nodes/{id}/stats` and `/ws/state`; this module is only the
shape + the "what is this / why it matters" blurbs the inspectors show.
"""

from __future__ import annotations

from typing import Any

from tools import registry

# Node type → coarse layout group (B2 pins these to stable screen regions).
# core = center, voice = west, brains = center, tools = east, memory = south,
# infra = north band.

# -- fixed subsystem nodes -----------------------------------------------------
# (id, type, group, label, role, blurb)
_SUBSYSTEMS: list[tuple[str, str, str, str, str, str]] = [
    (
        "baby_core", "core", "core", "Baby",
        "The mind at the center — the live status gauge.",
        "The core of Baby. Its state (idle / listening / thinking / speaking / "
        "executing) is the pulse of the whole system, driven live by /ws/state.",
    ),
    (
        "router", "router", "core", "Router",
        "Chooses which brain answers each turn.",
        "The cloud-primary router. Picks a brain per turn (primary / heavy / "
        "backstop / local), tracks health (cloud / degraded / offline), and fails "
        "over without dropping the reply.",
    ),
    (
        "safety_gate", "safety", "core", "Safety Gate",
        "Classifies every tool call: allow / confirm / deny.",
        "Baby's conscience. Every tool call passes through here and is classified "
        "allow, confirm (asks you first), or deny. It cannot be bypassed or "
        "disabled — that is enforced in code and covered by a test.",
    ),
    (
        "mem_facts", "memory", "memory", "Facts",
        "Long-term facts Baby has learned about you.",
        "The fact store — durable things worth remembering, embedded for semantic "
        "recall and injected into each turn.",
    ),
    (
        "mem_rag", "memory", "memory", "Conversation RAG",
        "Cross-session recall of past messages.",
        "Past conversations embedded for retrieval, so Baby can recall something "
        "you discussed days ago by meaning, not just this session.",
    ),
    (
        "mem_summaries", "memory", "memory", "Summaries",
        "Rolling summaries that keep context small.",
        "Rolling per-conversation summaries. They let old turns fall out of the "
        "prompt while their gist stays available — the budget trimmer leans on them.",
    ),
    (
        "voice_wake", "voice", "voice", "Wake Word",
        "Listens for 'jarvis' / 'hey jarvis'.",
        "The always-on wake-word detector. Runs a list of models and wakes on the "
        "highest score; cheap enough to run continuously on CPU.",
    ),
    (
        "voice_vad", "voice", "voice", "VAD",
        "Detects when you start and stop speaking.",
        "Voice-activity detection. Marks speech boundaries so Baby captures a full "
        "utterance and knows when you've finished.",
    ),
    (
        "voice_stt", "voice", "voice", "STT",
        "Transcribes your speech to text (Whisper).",
        "Speech-to-text (faster-whisper, CPU). Turns the captured audio into the "
        "text a turn runs on; biased toward names Baby hears often.",
    ),
    (
        "voice_tts", "voice", "voice", "TTS",
        "Speaks Baby's reply aloud (Kokoro).",
        "Text-to-speech (Kokoro). Streams Baby's reply as audio, sentence by "
        "sentence, so it starts talking before the whole reply is ready.",
    ),
    (
        "speaker_verify", "voice", "voice", "Speaker Verify",
        "Recognises whether it's really you speaking.",
        "Speaker verification. Scores whether a voice is the owner's and feeds the "
        "gate a trust level; ships off by default until it clears its accuracy bar.",
    ),
    (
        "task_queue", "infra", "infra", "Task Queue",
        "Runs background jobs off the main turn.",
        "The background worker pool. Long jobs run here without blocking chat; each "
        "shows up as a task you can watch and cancel.",
    ),
    (
        "scheduler", "infra", "infra", "Scheduler",
        "Fires cron jobs (briefing, nightly upkeep).",
        "The cron scheduler. Runs recurring jobs — the morning briefing, nightly "
        "memory reconciliation, and any schedules you add.",
    ),
    (
        "telegram", "infra", "infra", "Telegram",
        "Chat with Baby from your phone.",
        "The Telegram bridge. Lets you talk to Baby remotely; it answers only your "
        "chat id and funnels through the same turn loop as every other surface.",
    ),
    (
        "browser", "infra", "infra", "Browser",
        "Drives a real Chromium for web tasks.",
        "A headless Chromium (Playwright) Baby can drive to read and act on pages — "
        "the muscle behind the browser tool.",
    ),
    (
        "screen", "infra", "infra", "Screen",
        "Looks at what's on your screen when asked.",
        "Screen awareness. On request, Baby captures and describes what's on your "
        "display — never continuously, only when you ask.",
    ),
]

# Which model tiers Baby considers "brains" (config.models has non-brain keys too).
_NON_BRAIN_MODEL_KEYS = {"embedder"}

# Coarse, best-effort safety class per tool for the graph — the real class is
# decided at call time in core/safety.py::classify_tool (args-dependent), so this
# is a heuristic label only, never the enforcement. Anything unlisted is "allow".
_TOOL_CLASS_HINT = {
    "run_shell": "confirm/deny (command-dependent)",
    "write_file": "confirm",
    "browser_act": "confirm",
    "app_control": "confirm",
    "describe_screen": "confirm",
    "set_game_mode": "confirm",
    "start_background_task": "confirm",
    "start_project": "confirm",
}


def _brain_nodes(config: dict) -> list[dict]:
    models = config.get("models", {}) or {}
    router = config.get("router", {}) or {}
    role_of = {
        router.get("primary"): "primary — first choice each turn",
        router.get("heavy"): "heavy — deep planning, background only",
        router.get("backstop"): "backstop — cloud safety net",
        router.get("offline_fallback"): "offline fallback — the local brain",
    }
    nodes: list[dict] = []
    for tier, spec in models.items():
        if tier in _NON_BRAIN_MODEL_KEYS or not isinstance(spec, dict):
            continue
        model = spec.get("model", tier)
        provider = spec.get("provider", "?")
        role = role_of.get(tier, "brain")
        nodes.append({
            "id": f"brain:{tier}",
            "type": "brain",
            "group": "brains",
            "label": model,
            "role": role,
            "blurb": (
                f"The '{tier}' brain — {provider} serving {model}. {role.capitalize()}."
            ),
            "tier": tier,
            "provider": provider,
            "model": model,
        })
    return nodes


def _tool_nodes(tool_schemas: list[dict]) -> list[dict]:
    nodes: list[dict] = []
    for schema in tool_schemas:
        fn = schema.get("function", {})
        name = fn.get("name")
        if not name:
            continue
        nodes.append({
            "id": f"tool:{name}",
            "type": "tool",
            "group": "tools",
            "label": name,
            "role": fn.get("description", ""),
            "blurb": fn.get("description", ""),
            "safety_class": _TOOL_CLASS_HINT.get(name, "allow"),
        })
    return nodes


def _edges(brain_ids: list[str], tool_ids: list[str]) -> list[dict]:
    """Static call-path edges. Real routes, not decoration."""
    edges: list[dict] = []

    def link(src: str, tgt: str) -> None:
        edges.append({"source": src, "target": tgt})

    # voice input chain → router
    link("voice_wake", "voice_vad")
    link("voice_vad", "voice_stt")
    link("voice_stt", "router")
    link("speaker_verify", "safety_gate")  # trust level feeds the gate
    # the core sits between input and the router; reply goes back out via TTS
    link("baby_core", "router")
    link("baby_core", "voice_tts")
    # router picks a brain; every brain answers through the gate before tools
    for bid in brain_ids:
        link("router", bid)
        link(bid, "safety_gate")
        link(bid, "mem_facts")
        link(bid, "mem_rag")
        link(bid, "mem_summaries")
    # gate → each tool (the enforced path for every tool call)
    for tid in tool_ids:
        link("safety_gate", tid)
    # specific tool → subsystem resources
    if "tool:browser_act" in tool_ids:
        link("tool:browser_act", "browser")
    if "tool:describe_screen" in tool_ids:
        link("tool:describe_screen", "screen")
    # background + remote surfaces funnel into the same turn loop
    link("scheduler", "task_queue")
    link("task_queue", "baby_core")
    link("telegram", "baby_core")
    return edges


def build_graph(config: dict, tool_schemas: list[dict] | None = None) -> dict[str, Any]:
    """Assemble the full topology. `tool_schemas` defaults to the live registry
    (`register_all()` must have run); tests may inject a list."""
    if tool_schemas is None:
        tool_schemas = registry.schemas()

    subsystem_nodes = [
        {"id": i, "type": t, "group": g, "label": lbl, "role": role, "blurb": blurb}
        for (i, t, g, lbl, role, blurb) in _SUBSYSTEMS
    ]
    brain_nodes = _brain_nodes(config)
    tool_nodes = _tool_nodes(tool_schemas)

    nodes = subsystem_nodes + brain_nodes + tool_nodes
    edges = _edges([n["id"] for n in brain_nodes], [n["id"] for n in tool_nodes])
    return {"nodes": nodes, "edges": edges}
