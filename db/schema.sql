CREATE TABLE IF NOT EXISTS conversations (
  id INTEGER PRIMARY KEY,
  channel TEXT NOT NULL,               -- cli | ui | voice | telegram | scheduler
  started_at TEXT DEFAULT (datetime('now')),
  summary TEXT,                        -- rolling summary lives here
  summarized_upto INTEGER DEFAULT 0,   -- last message id folded into summary
  extracted_upto INTEGER DEFAULT 0     -- last message id scanned for facts
);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  conversation_id INTEGER REFERENCES conversations(id),
  role TEXT NOT NULL,                  -- user | assistant | tool
  content TEXT NOT NULL,
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS facts (
  id INTEGER PRIMARY KEY,
  text TEXT NOT NULL,
  source TEXT,                         -- explicit | extracted
  created_at TEXT DEFAULT (datetime('now')),
  last_used_at TEXT,
  active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  spec TEXT NOT NULL,
  status TEXT DEFAULT 'queued',        -- queued | running | done | failed | cancelled
  result TEXT,
  notify INTEGER DEFAULT 1,
  created_at TEXT DEFAULT (datetime('now')),
  started_at TEXT,
  finished_at TEXT
);

CREATE TABLE IF NOT EXISTS task_events (
  id INTEGER PRIMARY KEY,
  task_id INTEGER REFERENCES tasks(id),
  ts TEXT DEFAULT (datetime('now')),
  kind TEXT,                           -- log | tool_call | error | done
  payload TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY,
  ts TEXT DEFAULT (datetime('now')),
  channel TEXT,
  tool TEXT NOT NULL,
  args TEXT NOT NULL,                  -- JSON
  safety_class TEXT,                   -- allow | confirm | deny
  approved INTEGER,
  result_summary TEXT
);

CREATE TABLE IF NOT EXISTS schedules (
  id INTEGER PRIMARY KEY,
  cron TEXT NOT NULL,
  prompt TEXT NOT NULL,
  enabled INTEGER DEFAULT 1,
  last_run TEXT
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);
