/* Baby web UI — vanilla JS, no build step. */
"use strict";

const $ = (id) => document.getElementById(id);

/* ---------- reconnecting websocket helper ---------- */
function reconnectingSocket(path, onMessage) {
  let ws = null;
  let delay = 500;
  function connect() {
    ws = new WebSocket(`ws://${location.host}${path}`);
    ws.onopen = () => { delay = 500; };
    ws.onmessage = (e) => onMessage(JSON.parse(e.data));
    ws.onclose = () => {
      setTimeout(connect, delay);
      delay = Math.min(delay * 2, 8000);
    };
  }
  connect();
  return { send: (obj) => ws && ws.readyState === 1 && ws.send(JSON.stringify(obj)) };
}

/* ---------- chat pane ---------- */
const messages = $("messages");
let streamingBubble = null;

function addBubble(role, text) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text;
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
  return div;
}

/* Which brain wrote this answer (spec N4): local / NIM / Gemini chip,
   model name + routing reason on hover. */
const BRAIN_LABELS = {
  daily: ["local", "local"],
  // Tier name is historical; the primary slot can serve from any
  // OpenAI-compatible host (OpenRouter since 2026-07-06) — the tooltip
  // carries the exact model.
  nim_primary: ["cloud", "nim"],
  nim_heavy: ["NIM heavy", "nim"],
  backstop: ["Gemini", "gemini"],
};
function addBrainBadge(bubble, brain) {
  if (!brain || !brain.tier || !BRAIN_LABELS[brain.tier]) return;
  const [label, cls] = BRAIN_LABELS[brain.tier];
  const span = document.createElement("span");
  span.className = `brain-badge ${cls}`;
  span.textContent = label;
  span.title = `${brain.model || ""}${brain.reason ? " — " + brain.reason : ""}`;
  bubble.appendChild(span);
}

function setTurnRunning(running) {
  $("send-btn").disabled = running;
  const ind = $("turn-indicator");
  ind.textContent = running ? "thinking…" : "idle";
  ind.className = running ? "running" : "idle";
}

const chat = reconnectingSocket("/ws/chat", (msg) => {
  if (msg.type === "turn_start") {
    streamingBubble = addBubble("assistant streaming", "");
    setTurnRunning(true);
  } else if (msg.type === "token") {
    if (!streamingBubble) streamingBubble = addBubble("assistant streaming", "");
    streamingBubble.textContent += msg.text;
    messages.scrollTop = messages.scrollHeight;
  } else if (msg.type === "turn_end") {
    if (streamingBubble) {
      streamingBubble.classList.remove("streaming");
      // The final reply is the scrubbed truth: raw streamed tokens can carry
      // leaked <think> reasoning, and the "Next:" suggestion arrives after
      // the stream — prefer the server's reply over the accumulated text.
      if (msg.reply) streamingBubble.textContent = msg.reply;
      else if (!streamingBubble.textContent) streamingBubble.textContent = "…";
      addBrainBadge(streamingBubble, msg.brain);
      streamingBubble = null;
    }
    setTurnRunning(false);
  } else if (msg.type === "busy") {
    setTurnRunning(true);
    // Silence here read as "assistant is dead" (observed) — say why.
    addBubble("assistant system-note",
      "Still working on the previous request — press ■ Stop to cancel it.");
  }
});

$("chat-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = $("chat-input");
  const text = input.value.trim();
  if (!text) return;
  addBubble("user", text);
  chat.send({ type: "user_message", text });
  input.value = "";
});

/* history backfill */
fetch("/history").then((r) => r.json()).then((rows) => {
  for (const row of rows) addBubble(row.role, row.content);
}).catch(() => {});

/* ---------- activity feed ---------- */
const feed = $("feed");
const entries = new Map(); // call_id -> element

const ICONS = {
  run_shell: "⌨", app_control: "🗔", file_search: "🔍", read_file: "📄",
  write_file: "✏", web_search: "🌐", fetch_page: "🌐", get_system_stats: "📊",
  get_time: "🕐",
};

function addFeedEntry(msg) {
  const div = document.createElement("div");
  div.className = `entry ${msg.safety_class}`;
  const argsJson = JSON.stringify(msg.args, null, 2);
  div.innerHTML = `
    <div class="head">
      <span>${ICONS[msg.tool] || "⚙"}</span>
      <span class="tool-name">${msg.tool}</span>
      <span class="state">⏳</span>
    </div>
    <details><summary>args</summary><pre></pre></details>
    <div class="result"></div>`;
  div.querySelector("pre").textContent = argsJson;
  entries.set(msg.call_id, div);
  feed.appendChild(div);
  feed.scrollTop = feed.scrollHeight;
}

function closeFeedEntry(msg) {
  const div = entries.get(msg.call_id);
  if (!div) return;
  const state = div.querySelector(".state");
  const marks = { ok: "✓", dry_run: "✓ (dry)", error: "✗", denied: "⛔", refused: "🚫", timeout: "🚫 (timeout)" };
  state.textContent = marks[msg.status] || msg.status;
  div.querySelector(".result").textContent = msg.result_summary || "";
}

function addSystemLine(text) {
  const div = document.createElement("div");
  div.className = "entry system-line";
  div.textContent = text;
  feed.appendChild(div);
  feed.scrollTop = feed.scrollHeight;
}

/* ---------- confirmation modal ---------- */
const dialog = $("confirm-dialog");
let activeConfirmId = null;
let countdownTimer = null;

function openConfirm(msg) {
  activeConfirmId = msg.confirm_id;
  $("confirm-command").textContent = msg.command;
  $("confirm-explanation").textContent = msg.explanation;
  let remaining = Math.floor(msg.timeout_s);
  $("confirm-countdown").textContent = remaining;
  clearInterval(countdownTimer);
  countdownTimer = setInterval(() => {
    remaining -= 1;
    $("confirm-countdown").textContent = Math.max(0, remaining);
    if (remaining <= 0) clearInterval(countdownTimer);
  }, 1000);
  dialog.showModal();
}

function closeConfirm() {
  clearInterval(countdownTimer);
  activeConfirmId = null;
  if (dialog.open) dialog.close();
}

async function answerConfirm(approved) {
  if (!activeConfirmId) return;
  await fetch(`/confirm/${activeConfirmId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approved }),
  }).catch(() => {});
  closeConfirm();
}

$("confirm-yes").addEventListener("click", () => answerConfirm(true));
$("confirm-no").addEventListener("click", () => answerConfirm(false));
dialog.addEventListener("cancel", (e) => { e.preventDefault(); answerConfirm(false); });

const TASK_LABEL = { task_queued: "queued", task_started: "started", task_done: "finished" };

reconnectingSocket("/ws/activity", (msg) => {
  if (msg.type === "tool_start") addFeedEntry(msg);
  else if (msg.type === "tool_end") closeFeedEntry(msg);
  else if (msg.type === "confirm_request") openConfirm(msg);
  else if (msg.type === "confirm_resolved" && msg.confirm_id === activeConfirmId) closeConfirm();
  else if (msg.type === "status") addSystemLine(msg.text);
  else if (msg.type in TASK_LABEL) {
    const tail = msg.type === "task_done"
      ? ` (${msg.status}${msg.result_summary ? ": " + msg.result_summary : ""})`
      : "";
    addSystemLine(`task #${msg.task_id} ${TASK_LABEL[msg.type]}: ${msg.title}${tail}`);
  }
  else if (msg.type === "project_started") {
    addSystemLine(`project #${msg.project_id} started: ${msg.title} (${msg.subtasks} subtasks)`);
  }
  else if (msg.type === "project_done") {
    const tail = msg.result_summary ? ": " + msg.result_summary : "";
    addSystemLine(`project #${msg.project_id} ${msg.status}: ${msg.title}${tail}`);
  }
});

/* ---------- header gauges + kill switch ---------- */
function setGauge(prefix, used, total, percent) {
  const pct = percent ?? (total ? (used / total) * 100 : 0);
  $(`${prefix}-bar`).style.width = `${Math.min(100, pct)}%`;
  $(`${prefix}-bar`).style.background = pct > 85 ? "var(--red)" : pct > 65 ? "var(--amber)" : "var(--accent)";
  $(`${prefix}-val`).textContent = total ? `${used.toFixed(1)}/${total.toFixed(0)}G` : `${Math.round(pct)}%`;
}

let gameModeOn = false;

const ROUTER_LABEL = {
  cloud: "cloud", degraded: "cloud degraded", offline: "cloud offline", unknown: "cloud —",
};
function setRouterState(s) {
  const el = $("router-state");
  if (!el) return;
  // Cloud health is ALWAYS on the header — never hidden, and never masked by
  // "game mode" (game state lives on the 🎮 button, not this badge).
  const state = (s.router && s.router.state) || "unknown";
  el.style.display = "";
  el.className = `badge ${state}`;
  $("router-state-word").textContent = ROUTER_LABEL[state] || state;
  el.title = s.game_mode
    ? "cloud router state — game mode on (all turns cloud)"
    : "cloud router state";
  gameModeOn = !!s.game_mode;
  const btn = $("game-btn");
  btn.classList.toggle("on", gameModeOn);
  btn.textContent = gameModeOn ? "🎮 on" : "🎮";
}

async function pollStats() {
  try {
    const s = await (await fetch("/stats")).json();
    $("model-badge").textContent = s.model;
    setRouterState(s);
    setGauge("cpu", 0, 0, s.cpu_percent);
    setGauge("ram", s.ram.used_gb, s.ram.total_gb, s.ram.percent);
    if (s.gpu) setGauge("vram", s.gpu.vram_used_gb, s.gpu.vram_total_gb);
    else $("vram-val").textContent = "n/a";
    if (!streamingBubble) setTurnRunning(s.turn_running);
  } catch { /* server briefly away — reconnect handles it */ }
}
pollStats();
setInterval(pollStats, 5000);

$("game-btn").addEventListener("click", async () => {
  await fetch("/game_mode", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ on: !gameModeOn }),
  }).catch(() => {});
  pollStats();
});

$("kill-btn").addEventListener("click", () => fetch("/kill", { method: "POST" }).catch(() => {}));
