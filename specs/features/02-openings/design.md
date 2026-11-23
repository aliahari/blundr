# Phase 2 — Openings: Design

Requirements: [requirements.md](requirements.md) · Tasks: [tasks.md](tasks.md)

## Where things stand after Phase 1

- **Card model:** `ReviewCard` has `kind`, a nullable source column per kind (`CARD_SOURCE_COLUMNS`),
  `step` and `created_at`. The review queue groups cards by `(kind, source id)`. A new kind adds a
  source column and an entry in `CARD_SOURCE_COLUMNS`.
- **Move data:** `AnalyzedGame.positions` holds `{san, uci, eval_cp, best_uci}` per ply, for games
  analyzed since Phase 1. For the 293 production games analyzed before that, it is NULL.
- **Game phases:** `game_phase()` labels positions, and `progress_service` holds progress measures.
- **Lichess client:** `get_user_games` and `get_game_by_id` exist. There's no bulk export by ID yet.
- **No Anthropic dependency or key** in the app yet.

## External sources checked (2026-11-23)

- **Lichess opening names** ([lichess-org/chess-openings](https://github.com/lichess-org/chess-openings)):
  about 3,500 named openings with ECO codes. The Hugging Face mirror is CC0. Confirm the GitHub
  repo's licence before shipping its file (task 4).
- **Lichess opening explorer:** [now requires an OAuth token per request](https://lichess.org/@/thibault/blog/the-opening-explorer-now-requires-authentication/FSWh9Zg3),
  and there's [a report of it returning only 429s from February 2026](https://github.com/lichess-org/lila/issues/19610).
  Not used in Phase 2.

---

## D1 — Fetching old games' moves

Lichess's bulk export takes up to 300 game IDs per request (`POST /api/games/export/_ids`, body:
comma-separated IDs, NDJSON response). Confirm the exact endpoint and limits against the Lichess API
docs in task 1. Add `LichessClient.export_games_by_ids(ids)`, reusing the existing User-Agent and
the existing rate-limit delay.

A background job per user (`MoveBackfillJob`, tracked like `AnalysisJob` in `app.state`):

1. Select the user's `AnalyzedGame` rows with `positions IS NULL`.
2. Fetch them in batches of 300 and replay each move list with python-chess.
3. Write `positions` with `eval_cp: null` and `best_uci: null`, plus `user_moves` and `speed`.
   `initial_eval_cp` stays NULL.
4. Leave games Lichess doesn't return as NULL, and log them.

It starts automatically after a sync when the user has games without `positions`. Because it only
picks games with `positions IS NULL`, running it again does nothing (R1.2).

**Contract change for readers of `positions`:** `eval_cp` and `best_uci` may be null. Today only
`progress_service` reads `positions`, and it only uses `uci`. Update the docstring on
`AnalyzedGame.positions` and the note in CLAUDE.md.

---

## D2 — Data model

### Position key

Positions are keyed by the first four FEN fields: piece placement, side to move, castling rights
and en-passant square. Move counters are left out, so transpositions share a key. Use python-chess's
`board.epd()`, which gives exactly these fields. Note that it only includes the en-passant square
when a legal en-passant capture exists, which is the behaviour we want.

### New tables

```
repertoire_moves                       -- the user's chosen move in a position (their turn)
  id              INTEGER PK
  user_id         INTEGER FK users, indexed
  color           VARCHAR(5)          -- the repertoire it belongs to: "white" | "black"
  position_key    VARCHAR(100)        -- board.epd() before the move
  move_uci        VARCHAR(8)
  move_san        VARCHAR(12)
  parent_key      VARCHAR(100) NULL   -- position_key of the user's previous repertoire position
                                      -- in the line it was accepted from; NULL at the root.
                                      -- Used for line order (R5.3) and removal (R3.1).
  source          VARCHAR(10)         -- "games" (accepted suggestion) | "manual"
  created_at      DATETIME
  UNIQUE (user_id, color, position_key)

opening_explanations                   -- shared across users
  id              INTEGER PK
  position_key    VARCHAR(100)
  move_uci        VARCHAR(8)
  source          VARCHAR(10)         -- "claude" | "owner"
  body            TEXT                -- JSON: {plan, why_this_move, pawn_structure|null}
  model           VARCHAR(40) NULL    -- e.g. "claude-opus-5-5"; NULL for owner-written
  prompt_version  INTEGER NULL
  report_count    INTEGER DEFAULT 0
  created_at, updated_at DATETIME
  UNIQUE (position_key, move_uci, source)

explanation_reports                    -- who reported what, so one user counts once
  id, explanation_id FK, user_id FK, note TEXT NULL, created_at
  UNIQUE (explanation_id, user_id)

repertoire_deviations                  -- R6
  id, user_id FK, game_id FK analyzed_games, ply INTEGER,
  side VARCHAR(8)                      -- "user" | "opponent"
  position_key VARCHAR(100), expected_uci VARCHAR(8) NULL, played_uci VARCHAR(8),
  punish_uci VARCHAR(8) NULL           -- engine's best reply when the opponent's move was a blunder
  created_at
  UNIQUE (game_id)                     -- one per game: the first deviation only

opening_names                          -- loaded from the Lichess dataset at startup
  position_key VARCHAR(100) PK, eco VARCHAR(3), name VARCHAR(200)
```

`AnalyzedGame` gets `repertoire_exit_ply INTEGER NULL` for R7. It holds the ply of the first
deviation, or NULL if the game never left the repertoire. A separate `repertoire_checked_at` column
tells "never left" apart from "not checked yet".

`User` gets `is_owner BOOLEAN DEFAULT false`. It's set by hand for the owner's account, which can
then write explanations and see reports. There's no sign-up path to it.

Only the opponent's side of the tree is derived from games: positions where it's the opponent's
turn are never stored, because the repertoire has no move to choose there.

### Review cards

- `CARD_SOURCE_COLUMNS["opening"] = "repertoire_move_id"`. Add a nullable foreign key
  `review_cards.repertoire_move_id` using the ADD COLUMN pattern, plus a
  `UNIQUE (repertoire_move_id, card_type, step)` index. Phase 1 made this a single column.
- One card per accepted position: `kind="opening"`, `card_type="play"`, `step=0`. Each position is
  its own item, so it uses one slot of the daily new-item limit.
- **Line order (R5.3):** new cards are created in tree order (parent before child) when positions
  are accepted. The queue's "oldest first by card id" then serves them in line order, with no queue
  change needed.
- Removing a position deletes its card and review history, through the ORM cascade, as with
  blunders.

---

## D3 — Building and suggesting the repertoire

A pure function in a new `app/services/repertoire_service.py`, tested on hand-made games:

```
build_tree(games: list[GameMoves-like], color) -> dict[position_key, Node]
  Node: {key, user_to_move, opening_name?, moves: {uci: {san, count, wins, draws, losses,
         child_key, eval_loss_cp?}}}

suggest(tree, color, min_games=3, max_ply=30) -> list[Suggestion]
  Starts at the root and follows edges played in >= min_games games:
  - at the user's turn: the most-played move (ties broken by better results)
  - at the opponent's turn: every reply with >= min_games games
  Stops at max_ply, or when no edge reaches min_games.
```

**Clearly worse (R2.3):** when stored evaluations exist, for each user move compute the average
`win_prob` drop compared with the engine's best move in that position. If the usual move averages a
drop of at least `REPERTOIRE_WORSE_WINPROB` (default 10 points), the suggestion carries
`better_uci`: the most common `best_uci`. Games without evaluations don't contribute.

The tree is built on each request from the user's games. That's about 300 games × 30 plies, which
replays in well under a second. Cache it per user, invalidated on sync, only if that's measured to
be slow.

**Endpoints** (`routes/repertoire.py`):

- `GET /repertoire/{color}`: the tree with suggestions, and which positions are accepted
- `POST /repertoire/{color}/accept`: accept a line, given as a list of position keys or a node and
  "everything below it"
- `PUT /repertoire/{color}/position`: set or replace the move at a position, including adding one
  by hand
- `DELETE /repertoire/{color}/position`: remove a position and the positions below it that are no
  longer reachable from the root through accepted moves
- `GET /repertoire/deviations`: R6.4

---

## D4 — Explanations with Claude

### Approaches considered

| | Approach | Trade-offs |
|---|---|---|
| **A** *(chosen)* | **Claude writes them, the owner's notes win.** Generated once per (position, move) and shared. | Covers every position from day one. Costs a little per new position, and an explanation can be wrong, which the override and the report button handle. |
| B | Owner-written only | Best quality, but most positions would have no explanation, against "understand first". |
| C | Data only (name, engine line, statistics) | No API cost, but it doesn't explain *why*. That's the point of the phase. |
| D | A chess-fine-tuned language model | Research has found these weaker than strong general models: the ChessGPT-style fine-tune of Feng et al. (2023) understood chess worse than GPT-4o. The better-tested approach is a general model given engine analysis and chess concepts, as in Kim et al., "[Concept-guided Chess Commentary](https://aclanthology.org/2025.naacl-long.481)", NAACL 2025. That's what A does: we give Claude the engine evaluation, the main line and the opening name. |

### The call

- **SDK:** `anthropic` Python SDK. The key comes from the environment (`ANTHROPIC_API_KEY`) through
  `Settings`, optional: unset means generation is off (R4.6).
- **Model:** `claude-opus-5-5`, the current default. The owner can choose a cheaper model later via
  a `EXPLANATION_MODEL` setting. That's a cost-versus-quality decision, not made here.
- **Effort:** `output_config={"effort": "medium"}`, set explicitly (it's also this model's default).
  Thinking is always on with this model, so `thinking` is left out.
- **Output:** structured output through `client.messages.parse()` with a schema
  `{plan: str, why_this_move: str, pawn_structure: str | null}`, so there's no string parsing.
- **Refusals:** include the server-side fallback (`betas=["server-side-fallback-2026-07-01"]`,
  `fallbacks="default"`), as the SDK guidance recommends for this model. Check
  `stop_reason == "refusal"` before reading the content, and store nothing in that case.
- **What it's given** (all from our own data, so the explanation is grounded):
  - the opening name and ECO code
  - the moves from the start position in SAN
  - the FEN
  - the move to explain
  - a Stockfish evaluation before and after the move, plus the engine's principal variation: one
    `analyse` call at the normal analysis depth
  - the replies the user's opponents have played, from the tree
- **Prompt rules:**
  - explain plans and ideas, not move lists
  - write for a 1200–1800 player, under about 150 words
  - don't refer to named games or players
  - if the position is unusual, say less rather than guess

  The system prompt is fixed, so it can be cached; the position data goes in the user turn. Store
  `prompt_version` so explanations can be regenerated when the prompt changes.

### When it runs

An `ExplanationJob` in the background, like the analysis job, with two calls at a time. When
positions are accepted, it queues every (position, move) that has no explanation yet. The review UI
shows "explanation coming" until one exists. A failed call is logged and retried the next time a
position is accepted. A flood of acceptances could later move to the Batches API at half the cost;
that's not needed now.

**Cost, rough:** about 1.5K input tokens and about 1K output tokens (including thinking) per
explanation, at $4/$20 per million tokens on Claude Opus 5.5. That's around $0.02 per explanation,
so a full repertoire of 150–300 positions is around $3–6 once. Positions shared with other users
cost nothing more. Measure on the first real run before relying on this.

### Owner override and reports

- `PUT /openings/explanations` (owner only) writes `source="owner"` for a (position, move). The
  lookup always prefers `owner` over `claude`.
- `POST /openings/explanations/{id}/report` (any user) adds a report. Same-user repeats are ignored.
- `GET /openings/explanations/reported` (owner only) lists reports, most reported first.

---

## D5 — Reviewing an opening card

`ReviewCardResponse` gets an `opening` payload for `kind == "opening"`:

```jsonc
"opening": {
  "color": "white",
  "line": ["e4", "e5", "Nf3", "Nc6", "Bb5"],   // SAN from the start position to the card's position
  "fen": "...",                                  // the position to play from
  "answer_uci": "...", "answer_san": "...",
  "opening_name": "Ruy Lopez", "eco": "C60",
  "explanation": {"id": 12, "plan": "...", "why_this_move": "...", "pawn_structure": null,
                  "source": "claude"} | null
}
```

The `line` is the shortest path in the user's accepted repertoire from the start position to the
card's position. It's built from `parent_key` links, so a transposition shows the line it was
accepted from.

**UI (`ReviewPanel`):** an opening card replays `line` on the board, quickly, with the moves the user
isn't tested on played for them. Then the user is asked for their move.

- **First exposure** (`repetitions == 0 && lapses == 0`): the explanation is shown before the
  question.
- **Later reviews:** it's shown after the answer.
- **Grading:** a correct move reveals the grade buttons, as blunder cards do today. A wrong move
  shows the right one and grades "again".
- **Reporting:** a "Report explanation" link sits next to the text.

A new top-level view, `'repertoire'` (in `App.tsx`'s `useState`, with no router, as today), has:

- a tab for White and one for Black
- the line tree as an indented move list, with a board for the selected position
- accept / replace / remove controls
- the opening name
- the explanation, editable for the owner
- the recent-deviations list (R6.4)

---

## D6 — Checking games against the repertoire

A pure function in `repertoire_service`:
`first_deviation(positions, color, repertoire: dict[key, uci]) -> Deviation | None`. It replays the
game:

- at the user's turn, if the position is in the repertoire and the move differs, that's a user
  deviation
- at the user's turn, if the position isn't in the repertoire, the game is past the prepared part:
  stop, with no deviation
- at the opponent's turn, if the resulting position (after the opponent's move) is a user-to-move
  position not in the repertoire, but the position before it had a repertoire child, that's an
  opponent deviation

It runs from `run_analysis_job` after each game is saved, from the D1 job after each batch, and once
for all of a user's games when they first accept positions. It writes `repertoire_deviations` and
`repertoire_exit_ply`.

- **User deviation (R6.2):** set that position's card `due_at = now`, without touching ease,
  interval or repetitions. Missing it in the review then grades it properly.
- **Opponent deviation (R6.3):** if the game has stored evaluations and the opponent's move passes
  the same two win-probability gates `detect_blunders` uses (seen from the user's side as a gain),
  store `punish_uci` = the stored `best_uci` of the next position.

---

## D7 — Measuring it

`GET /stats/repertoire-depth?weeks=12` returns, per week (Monday-based, like the blunder rate):
the games checked, the average `repertoire_exit_ply` (counting "never left" as the game's last
repertoire ply), and the share of games that left the repertoire. It goes in `progress_service`,
with a dashboard chart in the same style as the blunder rate, and the same "not enough games" rule.

---

## Open questions

1. **Is the explanation model the owner's choice to downgrade?** It starts on `claude-opus-5-5`.
   After the first 50 explanations, compare their quality and cost with `claude-sonnet-5-5` on the
   same positions before changing `EXPLANATION_MODEL`.
2. **Should opponent deviations that were blunders also create a "punish" card**, like blunder
   cards' punish side? Phase 2 only flags them. Decide after seeing how often they happen.
3. **Lichess explorer as an optional extra** ("what players at your level play here"), once its
   availability is clear. It would need a server-side Lichess token.

## What's left to the builder

- The exact tree JSON shape, and how much of the tree one request returns.
- How the repertoire view lays out the tree and board, within the existing styles.
- The prompt wording, within the rules above. Keep it in one constant with its `prompt_version`.
