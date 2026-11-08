# Browse-lite v1: the lightweight browse page that ships with the skill + local SQLite

Set by the lead on 2026-11-06, per (data storage option A, SM-2 scheduling, tier filter in the header). This file is the only spec for the execution issues; to change a field, change it here first.

## 0. Purpose and boundaries

- With the extraction skill, the user can **lightly browse and edit** the cards extracted from their book without installing the App: run `bookcard browse serve`, and the browser opens a page.
- Visuals **copy the App's Playful Dark skin directly**. Structure and interaction **copy the App's Browse page**, keeping only:
  - Left panel (book → section → word list);
  - Center card (flip, page left/right);
  - Four-key rating + review history line;
  - Top-right `⋯` menu (Ignore / Skip / Edit), plus the Edit page;
  - Tier filter in the header.
- Not included: Rec, the learning scene, video, Capture, Stats, login / sync.
- Zero dependencies: the backend only uses Python's standard library (`sqlite3`, `http.server`, `json`, `zipfile`); the frontend is a single self-contained `index.html` (inline CSS/JS, no CDN, fonts use system fallback).
- Only listens on `127.0.0.1`.

## 1. Directories and CLI

```
src/bookcard/browse/
  __init__.py
  schema.sql        §2
  db.py             import, query, rating (SM-2), tiers, editing
  sm2.py            line-by-line port of App lib/core/sm2.dart + lib/core/rating.dart
  server.py         §4 HTTP API + static files
  static/index.html §5 frontend (single file)
```

```
bookcard browse import <pack.ttfc.zip | pack directory>... [--db PATH]
bookcard browse serve [--db PATH] [--port 8765] [--no-open]
```

- Default database: `~/.bookcard/browse/browse.db`; images are extracted to `~/.bookcard/browse/images/<book_id>/`.
- Importing the same package again = idempotent update: book / library / card fields refresh with the package; **the user's progress, ignore status, and edits are kept** (cards with `edited=1` do not take the package's front/back/tags).

## 2. SQLite schema (`schema.sql`)

```sql
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
```

## 3. Scene (`cards.scene`)

Decided at import time from the package (the package side is in contract-v1 §7.1):

| Condition | scene |
|---|---|
| `card.source.context` exists | `{"kind":"text","book":"<title>","page":12,"printed_page":"8","text":"<context.text>","highlights":[{"start":3,"end":7}]}` |
| otherwise, if `card.images[0]` exists | `{"kind":"image","book":"<title>","page":12,"src":"/images/<book_id>/p0012.jpg","width":1240,"height":1754,"boxes":[{"x":120,"y":300,"w":64,"h":18}]}` (`boxes` may be empty) |
| otherwise (public packages, old packages) | `null` |

## 4. HTTP API (JSON, UTF-8)

| Method | Path | Request | Returns |
|---|---|---|---|
| GET | `/` | — | `static/index.html` |
| GET | `/images/<book_id>/<file>` | — | image (path traversal must be blocked) |
| GET | `/api/tree` | — | `[{"id","title","subtitle","visibility","scene_mode","libraries":[{"id","name","count"}]}]` |
| GET | `/api/cards` | `book=` or `library=` (one of them is required); `off=T1,T6` (tiers turned off) | `{"cards":[{"id","front","back_preview","tier","library_id","sort_order"}],"tier_counts":{"T1":0,"T2":0,"T4":0,"T6":0,"T7":0}}` —— `tier_counts` is counted over the **whole scope before filtering**; `cards` are filtered and ordered by `library.sort_order, card.sort_order` |
| GET | `/api/cards/<id>` | — | `{"id","library_id","book_id","front","back","tags":[],"tier","scene","state":{repetition,ease,interval_days,next_review_at,last_reviewed_at,reviews,lapses,ignored,mastered},"edited"}` |
| POST | `/api/cards/<id>/rate` | `{"rating":"unclear|forgot|good|easy"}` | same as `GET /api/cards/<id>` (updated) |
| POST | `/api/cards/<id>/ignore` | `{"ignored":true|false}` | same as above |
| PUT | `/api/cards/<id>` | `{"front","back","tags":[],"library_id"}` | same as above; `edited=1`; front 1–500 characters, otherwise 400 |

Error = HTTP 4xx/5xx + `{"error":"..."}`. Skip is a pure frontend action (go to the next card), with no API.

### Tier (the App's `kToggleTiers`, same order and colors as the App)

Checked in order: `ignored` ⇒ **T6** · `mastered` ⇒ **T7** · `reviews == 0` ⇒ **T2** (new) · `next_review_at <= now` ⇒ **T1** (due) · otherwise **T4** (learned).

### Rating

- UI value → SM-2 value, the SM-2 algorithm, and interval / ease updates are **ported line by line from App `lib/core/sm2.dart` + `lib/core/rating.dart`** (with cross-check tests: same input, same output).
- `easy` also sets `mastered=1` (same as App ); `unclear` / `forgot` set `mastered=0`.
- Each rating writes one `review_log` row.

## 5. Frontend (`static/index.html`)

The visual standard is the App's **Playful Dark**. Values are taken from the Flutter code, not by eye:

- Colors: `lib/design/palette.dart` `playfulDark`
- Sizing: `lib/design/tokens.dart`
- Type: `lib/design/typography.dart`
- Buttons: `lib/design/skin_button.dart` (3D bottom edge)
- Tier colors: `lib/core/front_tier.dart`

| Area | Copy from (App) | Notes |
|---|---|---|
| Header | `card_browse_page.dart` top bar + `widgets/tier_toggle_bar.dart` (`autoFit`, `shortFormat`, `TierResetStyle.icon`) | Left: book / section name; right: tier pills `● T1 Due 12`, **chips with a count of 0 are hidden**; tap = turn off that tier, tap again = turn it back on; a ↺ reset appears when any tier is off |
| Left panel | `pages/card_browse/left_panel.dart` | book → section → word list, with tier color dots per word; tap a word = jump to that card; narrow screen (< 768) = slide-out drawer |
| Card | `learn_card_stage.dart` + `flip_card.dart` + `card_back_scaffold.dart` | Front: front large text; flip (tap / space) to the back: back text + scene; ← → / buttons on both sides page through; flip animation 3D rotateY |
| Scene: text | `widgets/text_scene.dart` | container, header row (book title · p.8), body 13/20, keyword yellow underline highlight + bold; long paragraphs collapse to 6 lines + `Show more` |
| Scene: image | card back image + bbox (`back_content_renderer.dart` image focus / occurrence box) | page image fills the width; bbox drawn as a semi-transparent yellow box in the App style; scale proportionally; tap to enlarge to full screen |
| Rating | `LearnActionBar` (four keys Unclear / Forgot / Good / Easy, keys 1–4); the history line sits **below** the keys (`CardHistoryData.lineText`, including `Overdue Xd`) | front shows `Show answer` |
| `⋯` menu | `CardMoreMenu` | Ignore (toggles to `Unignore` once ignored) / Skip / Edit |
| Edit page | `pages/card_create/edit_panel.dart` | fields Front (required) / Back / Tags (chip input) / Library (dropdown within the same book); Save / Cancel; after saving, return to this card; Esc = cancel |

- After rating, automatically go to the next card in the current filtered list; when the last card is done, show a short `All done in this section` empty state (copy the App's `StateView` style).
- UI copy in English.
- `?mock=1` = no server, using the built-in sample data (one text-scene book + one image-scene book + one public book) — for development and screenshots.
- The URL hash carries the state: `#/lib/<id>/card/<id>`, so a refresh does not lose your place.
