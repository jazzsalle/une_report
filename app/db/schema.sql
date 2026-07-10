-- DESIGN.md §7 SQLite 스키마
CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  account       TEXT UNIQUE NOT NULL,
  user_name     TEXT,
  created_at    TEXT NOT NULL,
  last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
  id            TEXT PRIMARY KEY,
  user_id       INTEGER NOT NULL REFERENCES users(id),
  document_id   TEXT REFERENCES documents(id),
  title         TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id    TEXT NOT NULL REFERENCES sessions(id),
  role          TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
  content       TEXT NOT NULL,
  intent        TEXT,
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
  id            TEXT PRIMARY KEY,
  user_id       INTEGER NOT NULL REFERENCES users(id),
  title         TEXT,
  source_type   TEXT NOT NULL CHECK(source_type IN ('template','upload','generated')),
  original_path TEXT NOT NULL,
  current_version INTEGER NOT NULL DEFAULT 0,
  page_count    INTEGER,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS document_versions (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id   TEXT NOT NULL REFERENCES documents(id),
  version       INTEGER NOT NULL,
  xml_path      TEXT,
  html_path     TEXT,
  edit_summary  TEXT,
  edits_json    TEXT,
  message_id    INTEGER REFERENCES messages(id),
  created_at    TEXT NOT NULL,
  UNIQUE(document_id, version)
);

CREATE TABLE IF NOT EXISTS auth_tokens (
  user_id       INTEGER PRIMARY KEY REFERENCES users(id),
  rag_jwt       TEXT,
  issued_at     TEXT
);

-- 도구 자체 세션 토큰 (브라우저 Bearer)
CREATE TABLE IF NOT EXISTS app_tokens (
  token         TEXT PRIMARY KEY,
  user_id       INTEGER NOT NULL REFERENCES users(id),
  created_at    TEXT NOT NULL
);
