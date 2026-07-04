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
      if (!streamingBubble.textContent) streamingBubble.textContent = msg.reply || "…";
      streamingBubble = null;
    }
    setTurnRunning(false);
  } else if (msg.type === "busy") {
    setTurnRunning(true);
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
});

/* ---------- header gauges + kill switch ---------- */
function setGauge(prefix, used, total, percent) {
  const pct = percent ?? (total ? (used / total) * 100 : 0);
  $(`${prefix}-bar`).style.width = `${Math.min(100, pct)}%`;
  $(`${prefix}-bar`).style.background = pct > 85 ? "var(--red)" : pct > 65 ? "var(--amber)" : "var(--accent)";
  $(`${prefix}-val`).textContent = total ? `${used.toFixed(1)}/${total.toFixed(0)}G` : `${Math.round(pct)}%`;
}

async function pollStats() {
  try {
    const s = await (await fetch("/stats")).json();
    $("model-badge").textContent = s.model;
    setGauge("cpu", 0, 0, s.cpu_percent);
    setGauge("ram", s.ram.used_gb, s.ram.total_gb, s.ram.percent);
    if (s.gpu) setGauge("vram", s.gpu.vram_used_gb, s.gpu.vram_total_gb);
    else $("vram-val").textContent = "n/a";
    if (!streamingBubble) setTurnRunning(s.turn_running);
  } catch { /* server briefly away — reconnect handles it */ }
}
pollStats();
setInterval(pollStats, 5000);

$("kill-btn").addEventListener("click", () => fetch("/kill", { method: "POST" }).catch(() => {}));
