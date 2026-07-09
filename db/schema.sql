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
  turn_id INTEGER,                     -- groups every row of one run_turn (P2)
  status TEXT DEFAULT 'ok',            -- ok | failed | quarantined (P2); only 'ok' loads to context
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
  finished_at TEXT,
  project_id INTEGER REFERENCES projects(id)  -- orchestrator subtasks (Phase 5)
);

CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  spec TEXT NOT NULL,
  status TEXT DEFAULT 'queued',        -- queued | planning | running | integrating | done | failed | cancelled
  plan TEXT,                           -- JSON subtask list the orchestrator produced
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
  result_summary TEXT,
  duration_ms REAL                     -- B1: tool exec time; NULL for denied/dry-run/pre-B1 rows
);

CREATE TABLE IF NOT EXISTS usage_log (
  id INTEGER PRIMARY KEY,
  ts TEXT DEFAULT (datetime('now')),
  conversation_id INTEGER REFERENCES conversations(id),
  turn_id INTEGER,                     -- links to messages.turn_id (P2); one row per turn
  channel TEXT,                        -- cli | ui | voice | telegram | scheduler
  brain_tier TEXT,                     -- daily | nim_primary | nim_heavy | backstop
  brain_model TEXT,                    -- exact served model (nullable)
  prompt_tokens INTEGER DEFAULT 0,
  completion_tokens INTEGER DEFAULT 0,
  total_tokens INTEGER DEFAULT 0
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

-- B4 tool enable/disable. A disabled tool's schema is hidden from the model so
-- it stops calling it; the safety gate is unaffected (it classifies every call
-- regardless — a disjoint path). Rows exist only for tools the owner toggled;
-- an absent row means enabled (the default).
CREATE TABLE IF NOT EXISTS tool_flags (
  name TEXT PRIMARY KEY,
  enabled INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- B1 search spine: external-content FTS5 mirrors of messages / tasks / audit_log.
-- External content stores only the index (no duplicate text); queries join back
-- to the base row by rowid. For messages the query ALSO joins WHERE status='ok',
-- so quarantining a turn (a status UPDATE, content unchanged) drops it from
-- search with no FTS re-sync needed. FTS5 is compiled into sqlite here.
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
  content, content='messages', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
  INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
  INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE VIRTUAL TABLE IF NOT EXISTS tasks_fts USING fts5(
  title, spec, content='tasks', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS tasks_ai AFTER INSERT ON tasks BEGIN
  INSERT INTO tasks_fts(rowid, title, spec) VALUES (new.id, new.title, new.spec);
END;
CREATE TRIGGER IF NOT EXISTS tasks_ad AFTER DELETE ON tasks BEGIN
  INSERT INTO tasks_fts(tasks_fts, rowid, title, spec) VALUES('delete', old.id, old.title, old.spec);
END;
CREATE TRIGGER IF NOT EXISTS tasks_au AFTER UPDATE ON tasks BEGIN
  INSERT INTO tasks_fts(tasks_fts, rowid, title, spec) VALUES('delete', old.id, old.title, old.spec);
  INSERT INTO tasks_fts(rowid, title, spec) VALUES (new.id, new.title, new.spec);
END;

CREATE VIRTUAL TABLE IF NOT EXISTS audit_fts USING fts5(
  tool, args, result_summary, content='audit_log', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS audit_ai AFTER INSERT ON audit_log BEGIN
  INSERT INTO audit_fts(rowid, tool, args, result_summary)
    VALUES (new.id, new.tool, new.args, new.result_summary);
END;
CREATE TRIGGER IF NOT EXISTS audit_ad AFTER DELETE ON audit_log BEGIN
  INSERT INTO audit_fts(audit_fts, rowid, tool, args, result_summary)
    VALUES('delete', old.id, old.tool, old.args, old.result_summary);
END;

-- B6 speaker verification v2: multi-centroid voice profiles. One row per centroid
-- (an enrollment segment / mic position / session), so a profile is a SET of
-- centroids scored by max-cosine — robust to the distance/energy variance that
-- single-mean v1 false-rejected. model+dim tie a centroid to its extractor (a CAM++
-- centroid can't be scored by a TitaNet extractor). Additive; no _migrate entry
-- (CREATE TABLE IF NOT EXISTS handles it), no backup step.
CREATE TABLE IF NOT EXISTS speaker_profiles (
  id INTEGER PRIMARY KEY,
  label TEXT NOT NULL DEFAULT 'owner',
  model TEXT NOT NULL,                 -- onnx filename the centroid was embedded with
  dim INTEGER NOT NULL,
  centroid BLOB NOT NULL,              -- struct.pack float32, mirror memory/store._pack
  kind TEXT,                           -- near | normal | far | turned | session2 ...
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
