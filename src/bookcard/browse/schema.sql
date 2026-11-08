CREATE TABLE IF NOT EXISTS books (
  id TEXT PRIMARY KEY,            -- book.fp12; if absent, sha1(category.name)[:12]
  title TEXT NOT NULL,
  subtitle TEXT NOT NULL DEFAULT '', -- category.description
  language TEXT NOT NULL DEFAULT '',
  visibility TEXT NOT NULL,       -- 'public' | 'private'
  scene_mode TEXT NOT NULL,       -- 'text' | 'image' | 'none' (contract-v1 §7.1)
  sort_order INTEGER NOT NULL DEFAULT 0,
  manifest_meta TEXT NOT NULL,    -- manifest JSON with category removed (book/provenance/quality/license)
  imported_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS libraries (
  id TEXT PRIMARY KEY,            -- library.slug
  book_id TEXT NOT NULL REFERENCES books(id),
  name TEXT NOT NULL,
  sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS cards (
  id TEXT PRIMARY KEY,            -- source card_id; if absent, <library_id>:<sha1(front)[:8]>
  library_id TEXT NOT NULL REFERENCES libraries(id),
  front TEXT NOT NULL,
  back TEXT NOT NULL DEFAULT '',
  tags TEXT NOT NULL DEFAULT '[]',   -- JSON array
  sort_order INTEGER NOT NULL DEFAULT 0,
  source TEXT,                    -- the package's card.source as-is (JSON)
  scene TEXT,                     -- §3 (JSON), null = no scene
  edited INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS card_state (
  card_id TEXT PRIMARY KEY REFERENCES cards(id),
  repetition INTEGER NOT NULL DEFAULT 0,
  ease REAL NOT NULL DEFAULT 1.8,     -- App sm2.dart:77
  interval_days INTEGER NOT NULL DEFAULT 0,
  next_review_at INTEGER,         -- ms
  last_reviewed_at INTEGER,       -- ms
  reviews INTEGER NOT NULL DEFAULT 0,
  lapses INTEGER NOT NULL DEFAULT 0,
  ignored INTEGER NOT NULL DEFAULT 0,
  mastered INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS review_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  card_id TEXT NOT NULL REFERENCES cards(id),
  rating TEXT NOT NULL,           -- 'unclear' | 'forgot' | 'good' | 'easy' (UI value)
  sm2 TEXT NOT NULL,              -- 'again' | 'good'
  reviewed_at INTEGER NOT NULL,
  interval_days INTEGER NOT NULL
);
