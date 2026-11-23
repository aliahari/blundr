# Phase 1 — Foundations: Design

Requirements: [requirements.md](requirements.md) · Tasks: [tasks.md](tasks.md)

## Where things stand today

- `ReviewCard.blunder_id` is a required foreign key, unique together with `card_type`.
  `card_type` is `"avoid" | "punish"` (`app/models/db_models.py`).
- The queue logic in `app/routes/reviews.py` groups by `blunder_id`:
  - `_blunders_started_today` counts a same-day avoid + punish pair once.
  - `_fresh_blunder_ids` separates leftover cards from brand-new ones.
  - `_fresh_cards_stmt` orders by `(blunder_id, card_type)`.
  - `/stats/overview` reuses these helpers.
- `/stats/overview` decides whether a blunder is mastered by `GROUP BY blunder_id`.
  `/stats/timeline` finds a card's creation date by joining `Blunder.created_at`.
- `detect_blunders` (`app/services/analysis_service.py`) already computes the evaluation and the
  principal variation (the engine's best line) for every position. It keeps only the blunders and
  throws the rest away.
- `AnalyzedGame` stores neither the moves, nor the number of moves the player made, nor the
  game's speed.

---

## D1 — Card model

### Approaches considered

| | Approach | Trade-offs |
|---|---|---|
| **A** *(recommended)* | **One nullable foreign-key column per kind, plus a `kind` column.** `review_cards` gets `kind` (`"blunder"`) and `blunder_id` becomes nullable. Later phases add `opening_position_id`, `endgame_position_id` and so on with the usual `ALTER TABLE ADD COLUMN`. | Keeps real foreign keys and cascade deletes, and the queries stay simple. Needs **one** table rebuild now, because SQLite can't drop NOT NULL in place. After that, each new kind is one ALTER. Each card has one column per kind, mostly empty, which costs nothing in SQLite. |
| B | **Generic `item_kind` + `item_id` with no foreign key.** | No schema change per kind. But SQLite can't enforce integrity or cascade deletes, so every delete path needs manual cleanup, and the joins need a `CASE` per kind. |
| C | **A shared `training_items` table** that every kind's row links to, with cards pointing at it. | The cleanest model on paper. But it means migrating every blunder into a new table, and every later kind needs two inserts. It's a bigger change than this phase needs. |

**Recommendation: A.** It is the smallest change that meets R1, it matches the project's manual
migration pattern, and the one risky step (the rebuild) is done now, in a phase with no other
feature work.

### Schema after this phase

```
review_cards
  id               INTEGER PK          -- preserved
  user_id          INTEGER FK users    -- indexed
  kind             VARCHAR(20) NOT NULL DEFAULT 'blunder'
  blunder_id       INTEGER FK blunders NULL      -- deletes still cascade through the ORM relationship, as today
  card_type        VARCHAR(20) NOT NULL      -- per-kind: 'avoid' | 'punish' for blunders
  step             INTEGER NOT NULL DEFAULT 0  -- new; which move of the item's line this card drills
  ease, interval_days, repetitions, lapses, due_at, last_reviewed_at   -- unchanged
  created_at       DATETIME NOT NULL   -- new; backfilled from blunders.created_at
  UNIQUE (blunder_id, card_type, step) -- widened from (blunder_id, card_type); NULLs don't collide in SQLite
```

**Why `step`:** a line (an opening line, a multi-move combination) is learned move by move, and
a player usually gets only a few of its moves wrong. Chessbook keeps a separate schedule for every
move in a repertoire, and ChessAtlas brings back lines when you're about to forget them; both use
FSRS. Giving each move its own card does the same with Blundr's scheduler. `step` is added now
because the table is being rebuilt anyway. Adding it later would need a second rebuild, since
the unique constraint changes. All existing cards become step 0.

- The app enforces that exactly one source column is set and that it matches `kind`, in one
  helper used when cards are created. It is not a CHECK constraint, because SQLite can't add or
  change a CHECK without rebuilding the table, and the list of kinds will grow.
- Each later kind adds its own `UNIQUE (<kind>_id, card_type)` index alongside its column.
- `card_type` is widened to 20 characters. SQLite doesn't enforce the length, so this only
  documents the intent.

### ORM

- `ReviewCard.blunder` becomes `Mapped[Blunder | None]`.
- New `ReviewCard.item_key` property returns `(kind, source_id)`. Everything that used to group
  by `blunder_id` groups by this instead.
- Add the `kind` and `created_at` columns.

### Migration (in `db.create_tables()`)

Detection: `PRAGMA table_info(review_cards)` has no `kind` column. Inside the existing
`engine.begin()` transaction:

1. `PRAGMA foreign_keys=OFF` (it must be set outside a transaction, so run it before `begin()`).
2. `CREATE TABLE review_cards_new (...)` using the schema above.
3. `INSERT INTO review_cards_new (id, user_id, kind, blunder_id, card_type, ease, ..., created_at)
   SELECT rc.id, rc.user_id, 'blunder', rc.blunder_id, rc.card_type, rc.ease, ...,
   b.created_at FROM review_cards rc JOIN blunders b ON b.id = rc.blunder_id`
4. Check that the row counts are equal. If not, raise, so the transaction rolls back and the old
   table stays as it was.
5. `DROP TABLE review_cards`, `ALTER TABLE review_cards_new RENAME TO review_cards`, and recreate
   the indexes (`user_id`, `due_at`, the unique constraint).
6. `PRAGMA foreign_key_check`, then `PRAGMA foreign_keys=ON`.

`review_logs.card_id` still points at the same IDs, because step 3 keeps the `id` values.

**Before deploying:** the deploy workflow restarts the backend container, and the migration runs
when the app starts. Take a manual backup of the production `blundr.db` before the first deploy
with this phase. Test the migration on a copy of that backup locally first.

---

## D2 — One review queue

The three helpers in `reviews.py` change from grouping by `blunder_id` to grouping by
`(kind, source_id)`. In SQL this is `kind` plus `COALESCE(blunder_id, ...)` over the source columns.
In Phase 1 that is just `blunder_id`. The helper builds this expression in one function, so each
later kind adds itself in one place.

| Today | After |
|---|---|
| `_blunders_started_today` | `_items_started_today`: distinct `(kind, source_id)` whose first review was today |
| `_fresh_blunder_ids` → `list[int]` | `_fresh_item_keys` → `list[tuple[str, int]]`, same leftover / brand-new split, oldest first by card `created_at` and then `id` |
| `_fresh_cards_stmt(blunder_ids)` | `_fresh_cards_stmt(item_keys)`: filters by key, orders by item, then a per-kind card order (`avoid` before `punish`), then `step` |

**How a multi-step item is reviewed** (needed from Phase 2; Phase 1 only has to allow it): the
player replays the line from its start. Moves whose cards aren't due are played for them, or
shown as a quick check that isn't graded. Only the due moves are graded, and each grade
reschedules only that move's card. So a line where move 7 keeps going wrong is shown often,
but moves 1–6 aren't graded again until they are due. The response should return the item's
due cards with their `step`, and leave it to the UI to replay the line. The exact flow is Phase
2's design.

**Response shape** (`ReviewCardResponse`):

```jsonc
{
  "card_id": 1, "kind": "blunder", "card_type": "avoid",
  "due_at": "...", "repetitions": 0, "lapses": 0,
  "blunder": { ... }          // present when kind == "blunder", else null
}
```

`kind` is a new field. `blunder` becomes optional in the schema but is always present for blunder
cards. The frontend's `ReviewCardInfo` type gets `kind: 'blunder'` and `blunder?: BlunderInfo`.
`ReviewPanel` chooses what to render by `kind`, and today only blunders exist. The frontend and
backend deploy together, so no API versioning is needed.

**Loading:** `selectinload(ReviewCard.blunder).selectinload(Blunder.game)` stays. Each later kind
adds its own `selectinload`.

**Mixing across kinds:** in Phase 1 only blunder cards exist, so no mixing policy is built. Leave
a single place (`_fresh_item_keys`) where a later phase can give each kind its share of new cards.
The 10-minute session from the mission stays as the existing `limit` on `/reviews/due` plus
`max_new_per_day`, with no new setting.

**Stats:**
- `/stats/overview`: "mastered" is grouped by item key. The response keeps `blunders_mastered`
  (filtered to `kind='blunder'`), so the dashboard doesn't change.
- `/stats/timeline`: takes `card_created` from `ReviewCard.created_at` instead of joining
  `Blunder`.

---

## D3 — Keeping the move data

### Approaches considered

| | Approach | Trade-offs |
|---|---|---|
| **A** *(recommended)* | **A JSON column on `AnalyzedGame`:** `positions TEXT`, holding one entry per move. | One write per game. It fits the existing `refutation_line` / `best_line` JSON pattern, and later phases read whole games anyway (to build opening trees and scan endgames). Can't be queried per position in SQL. |
| B | **A `game_positions` table**, one row per move. | Can be queried per position, but adds about 80 rows per game, so hundreds of thousands of rows for an active player. Nothing in Phase 1 to 5 needs SQL access to single positions yet. |

**Recommendation: A.** If a later phase needs SQL access to positions (for example "all my games
that reached this FEN"), it can add an index table built from the JSON.

### Entry shape

```jsonc
// AnalyzedGame.positions: one entry per ply, in order
[
  { "san": "e4", "uci": "e2e4", "eval_cp": 31, "best_uci": "e2e4" },
  ...
]
```

- `eval_cp`: the evaluation **after** the move, from the player's side, with mate scored as
  `MATE_SCORE`. This is the same value as `cp` in the loop.
- `best_uci`: the engine's best move in the position **before** this move (`prev_best`). It is
  `null` only if the engine returned no PV.
- FENs are not stored, because you get them by replaying from the starting position. That keeps
  each game to roughly 3–5 KB.
- A game that ends early because of a move that can't be replayed stores the moves up to that
  point. This matches the existing `break`.

`detect_blunders` returns `(blunders, positions)`, or a small dataclass holding both.
`run_analysis_job` writes `positions` with `_dump_line`-style JSON.

### New `AnalyzedGame` columns (ADD COLUMN migration, all nullable)

| Column | Source |
|---|---|
| `positions TEXT` | above |
| `user_moves INTEGER` | count of entries where the player was to move |
| `speed VARCHAR(16)` | Lichess `speed` field (`bullet/blitz/rapid/classical/correspondence`). Parse it in `_parse_game_data` if it isn't parsed already |
| `initial_eval_cp INTEGER` | evaluation of the starting position. It is needed to compute the first move's drop from `positions` alone |

Games analyzed before this phase have all four columns as NULL (R3.4).

---

## D4 — Blunder rate

**Endpoint:** `GET /stats/blunder-rate?weeks=12&phase=all|opening|middlegame|endgame&speed=...`

```jsonc
[
  { "week": "2026-11-16", "games": 14, "user_moves": 512, "blunders": 9, "rate": 1.76 },
  ...
]
```

- Week = ISO week of `AnalyzedGame.played_at`, labelled by its Monday. Weeks with no games are
  filled with zeros and `rate: null`, so the chart can leave a gap.
- `rate = blunders / user_moves × 100`, counting only games where `user_moves IS NOT NULL`.
- **Game phase** is decided per ply with one pure helper, `game_phase(board, ply)` in a new
  `app/services/game_phase.py`. It's tested on its own and later phases reuse it:
  - opening: ply ≤ 20 and at least 6 minor and major pieces still on each side
  - endgame: at most 6 minor and major pieces left in total, or no queens left and at most 2
    minor and major pieces per side
  - middlegame: everything else

  These thresholds are the builder's call. They are close to Lichess's own split between game
  phases, and the helper's docstring should say so.
- For each game, the endpoint replays `positions` to label every one of the player's moves and
  every blunder ply with its phase, then adds them up. This runs in Python on each request. A
  year of a heavy player's games is about 5,000 games, which replays in well under a second. If
  that's too slow, cache it per game later.

**Dashboard:** a third chart in `Dashboard.tsx`, reusing `ComboChart`. It shows the weekly rate
as a line and the game count as faint bars, with a filter for opening / middlegame / endgame /
all. It shows a short "not enough games yet" state until there are 4 weeks with at least 5 games
each.

---

## Open questions

1. **Filling in old games.** Their R3 data is empty, so the blunder rate begins from the day this
   ships. One option is a one-off background job that re-fetches those games by ID (the Lichess
   export endpoint takes up to 300 IDs per request) and re-analyzes them. That costs CPU on the
   VPS, and it must not create duplicate blunders or cards. **Suggestion:** ship without it. Decide
   after seeing how many active users have more than 4 weeks of history.
2. ~~Card model approach (D1).~~ **Decided 2026-11-23: A**, one nullable foreign-key column per
   kind, plus `kind` and `step`.
3. ~~Move-data storage (D3).~~ **Decided 2026-11-23: A**, a JSON `positions` column on
   `AnalyzedGame`.

## What's left to the builder

- The exact phase thresholds in `game_phase`. Keep them in one function with tests.
- The chart's styling within the existing `ComboChart` conventions.
- Naming the `detect_blunders` return type.
