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

- **Lichess opening names** ([lichess-org/chess-openings](https://github.com/lichess-org/chess-openings)),
  **CC0** (confirmed on the repo, 2026-11-23): about 3,500 named openings in `a.tsv`…`e.tsv`. Each
  has an ECO code, a name, and a `pgn` line: "a well known sequence of moves, or the most common
  moves to reach the opening position based on master games". The generated `dist/` adds `uci`
  and `epd`. So it gives the main theoretical line *into* each named position, not move
  statistics for every position. Blundr uses it for names (R2.4) and for marking moves as theory
  (R2.5).
- **Lichess opening explorer:** [now requires an OAuth token per request](https://lichess.org/@/thibault/blog/the-opening-explorer-now-requires-authentication/FSWh9Zg3),
  and there's [a report of it returning only 429s from February 2026](https://github.com/lichess-org/lila/issues/19610).
  Used only as an optional extra (D8).

---

## D1 — Fetching old games' moves

Lichess's bulk export takes up to 300 game IDs per request. Confirmed in task 1 against Lichess's
own API spec ([`api-games-export-_ids.yaml`](https://github.com/lichess-org/api/blob/master/doc/specs/tags/games/api-games-export-_ids.yaml)):
`POST /api/games/export/_ids`, with a `text/plain` body of comma-separated IDs, NDJSON back when
`Accept: application/x-ndjson`, and `moves` on by default. The spec gives no separate rate limit,
so the client keeps its usual delay between requests and backs off on 429s, waiting what
`Retry-After` asks.

`LichessClient.export_games_by_ids(ids)` (task 1, done) de-duplicates the IDs, sends batches of
300, and returns the games Lichess knows. Missing games are simply absent, and unreadable lines
are logged and skipped. It keeps the shared client's custom User-Agent.

`backfill_service.backfill_moves()` (task 2, done):

1. Select the user's `AnalyzedGame` rows with `positions IS NULL`.
2. Fetch them in batches of 300 and replay each move list with python-chess.
3. Write `positions` with `eval_cp: null` and `best_uci: null`, plus `user_moves` and `speed`,
   saving after each batch. `initial_eval_cp` stays NULL.
4. Leave games Lichess doesn't return as NULL, log them, and ask again on the next run.

**Built differently from the first draft:** it isn't a separate job. It runs at the tail of every
sync's background task, after the analysis, in the same `AnalysisJob`, whose status now reports
`backfill_total` and `backfill_done`. The reason is that Lichess allows only one request at a time
per caller. A separate job could overlap with a later sync's game fetch, while the sync endpoint
already refuses to start a second sync while one is running. That check now also counts the
backfill (`_sync_in_progress`). A backfill error is logged and never fails the sync; the next sync
retries it. Because it only picks games with `positions IS NULL`, it costs nothing once done
(R1.2).

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
  played_was_theory BOOLEAN            -- R6.4: was the move that left the repertoire still theory?
  created_at
  UNIQUE (game_id)                     -- one per game: the first deviation only

opening_names                          -- loaded from the Lichess dataset at startup
  position_key VARCHAR(100) PK, eco VARCHAR(3), name VARCHAR(200)

app_meta                               -- key/value facts about the app's data; holds the hash
  key VARCHAR(50) PK, value TEXT       -- of the loaded opening dataset, so startup reloads only
                                       -- when the files change (task 4)

theory_moves                           -- also from the dataset: every move along every named line
  position_key VARCHAR(100), move_uci VARCHAR(8),
  line_name VARCHAR(200)               -- the shortest named line the move is on
  PRIMARY KEY (position_key, move_uci)

explanation_batches                    -- Message Batches in flight (D4), so a restart resumes them
  id INTEGER PK, batch_id VARCHAR(100) UNIQUE, model VARCHAR(40),
  status VARCHAR(20), created_at, ended_at NULL

explorer_cache                         -- optional Lichess explorer results (D8)
  position_key VARCHAR(100), rating_band VARCHAR(20), body TEXT, fetched_at DATETIME
  PRIMARY KEY (position_key, rating_band)
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
  (Task 3, done: the new kinds' unique keys are *named* indexes, `ux_review_cards_repertoire_move`
  and `ux_review_cards_repertoire_deviation`, not inline constraints, so the ADD COLUMN migration
  and `create_all` produce the same schema. `ReviewCard.source_id` now reads the column from
  `CARD_SOURCE_COLUMNS`, so a new kind only adds its column and its entry.)
- One card per accepted position: `kind="opening"`, `card_type="play"`, `step=0`. Each position is
  its own item, so it uses one slot of the daily new-item limit.
- **Line order (R5.3):** new cards are created in tree order (parent before child) when positions
  are accepted. The queue's "oldest first by card id" then serves them in line order, with no queue
  change needed.
- Removing a position deletes its card and review history, through the ORM cascade, as with
  blunders.
- **Punish cards for opponent slips (R6.5):** a second new kind, `deviation`.
  `CARD_SOURCE_COLUMNS["deviation"] = "repertoire_deviation_id"`, with a nullable foreign key on
  `review_cards` and its unique index. One card per flagged deviation: `card_type="punish"`,
  `step=0`. It's created only when `punish_uci` is set.

---

## D3 — Building and suggesting the repertoire

A pure function in a new `app/services/repertoire_service.py`, tested on hand-made games:

```
build_tree(games: list[GameMoves-like], color) -> dict[position_key, Node]
  Node: {key, user_to_move, opening_name?, moves: {uci: {san, count, wins, draws, losses,
         child_key, eval_loss_cp?, theory: bool}}}

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

**Theory (R2.5):** each edge gets the named line it's on from `theory_moves`, or none when it's
off-book. Where the user's usual move is off-book, the suggestion lists **every** theory move from
that position as `theory_alternatives`, the ones the user has played first, next to `better_uci`.
(Changed in task 5 from a single `theory_uci`: a position usually has several theory moves, for
example Bb5, Bc4 and d4 after 1.e4 e5 2.Nf3 Nc6, and picking one would be arbitrary. The user
chooses.) When the user adds a move by hand, the theory moves from that position are listed first
(R3.2).

Built in task 5 as `repertoire_service.build_tree()` and `suggest()`. Each game counts once per
edge. A position reached through two move orders is suggested once, under the line that reached it
first, which gives its `parent_key`. Opening names are inherited down a line from the last named
position.

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
- `GET /repertoire/{color}/options?position_key=`: every legal move in a position, theory moves
  first, for adding a move by hand (R3.2). Added in task 6.

Built in task 6 (`routes/repertoire.py`):
- **Accept adds only.** Accepting takes a suggested position plus the line leading to it, and
  with `include_below` everything under it. A position already in the repertoire keeps its move;
  `PUT` changes it.
- **Reachability is recomputed after every change** (`repertoire_service.reachable()`): from the
  start, follow the accepted move at the user's turn and every legal reply at the opponent's.
  Positions no longer reached are removed with their cards and review history. Survivors get the
  parent of the first line that reaches them, which handles transpositions that a single
  `parent_key` couldn't.
- **Replacing a move** gives the position a new card. The old card's history was for a different
  move.

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
- **Model:** set by `EXPLANATION_MODEL`, chosen by the benchmark below. Until the benchmark has
  run, it's `claude-opus-5-5`, the current default.
- **Effort:** `output_config={"effort": "medium"}`, set explicitly. Thinking stays at the model's
  default, so `thinking` is left out.
- **Output:** a JSON schema in `output_config.format`:
  `{plan: str, why_this_move: str, pawn_structure: str | null}`. Validate each result against it
  before storing; a result that doesn't validate is treated as failed.
- **Refusals:** check `stop_reason == "refusal"` before reading the content, and store nothing in
  that case. The server-side fallback parameter isn't accepted on the Batches API, so a refused
  batch item is retried once as a direct call with `betas=["server-side-fallback-2026-07-01"]` and
  `fallbacks="default"`. If that refuses too, the position stays without an explanation and is
  listed for the owner.
- **What it's given** (all from our own data, so the explanation is grounded):
  - the opening name and ECO code
  - the moves from the start position in SAN
  - the FEN
  - the move to explain
  - a Stockfish evaluation before and after the move, plus the engine's principal variation: one
    `analyse` call at the normal analysis depth
  - the replies the user's opponents have played, from the tree
  - whether the move is theory, and the named line it belongs to (`theory_moves`)
- **Prompt rules:**
  - explain plans and ideas, not move lists
  - write for a 1200–1800 player, under about 150 words
  - don't refer to named games or players
  - if the position is unusual, say less rather than guess

  The system prompt is fixed, so it can be cached; the position data goes in the user turn. Store
  `prompt_version` so explanations can be regenerated when the prompt changes.

### When it runs: Message Batches

Nobody waits on an explanation (the review shows "explanation coming" until it exists), so all
generation goes through the **Message Batches API**, at half the normal price.

- **Submitting:** an `ExplanationJob` collects every accepted (position, move) without an
  explanation and submits them as one batch (`client.messages.batches.create`). Each item's
  `custom_id` is derived from the position key and move, made safe for the API's `custom_id` rules,
  with a lookup kept alongside. It records the batch in `explanation_batches`. It runs after
  positions are accepted, at most one batch every 10 minutes, so acceptances in a row end up in the
  same batch.
- **Collecting:** a background poll every 5 minutes checks the open batches
  (`batches.retrieve`). When one has ended, it streams the results (`batches.results`), keyed by
  `custom_id` (results come back in any order), and stores the ones that succeeded. It puts
  `errored` and `expired` items back in the queue for the next batch, and handles refused ones as
  described above.
- **Restarts:** open batches are in `explanation_batches`, so a deploy or restart picks up where it
  left off.

Batches usually finish within minutes, and at most within 24 hours. Confirm in task 7 that
structured outputs (`output_config.format`) are accepted in batch requests; if not, ask for JSON in
the prompt and validate it the same way.

### Choosing the model: benchmark first

The owner decided to compare cheaper models before settling on one. A script,
`scripts/benchmark_explanations.py`:

1. Takes 50 positions from the owner's accepted repertoire, a mix of openings and depths.
2. Generates explanations for each with `claude-opus-5-5`, `claude-sonnet-5-5` and
   `claude-haiku-5-5`, all at effort `medium`, through one batch.
3. Writes a blind comparison sheet: the three explanations per position in shuffled order, model
   names hidden, plus the cost per model from each response's `usage`.
4. Stores nothing in `opening_explanations`.

The owner rates the sheet (correct / useful / wrong per explanation). The cheapest model whose
explanations are about as often correct and useful as Opus's becomes `EXPLANATION_MODEL`. The
benchmark costs real money (about $1 at the estimate below), so it runs only with the owner's
go-ahead.

**Cost, rough, before measuring:** about 1.5K input and 1K output tokens per explanation,
including thinking. At batch prices (half the list price) that's about $0.013 per explanation on
Claude Opus 5.5, $0.007 on Claude Sonnet 5.5 and $0.0003 on Claude Haiku 5.5. A repertoire of
150–300 positions costs from about $0.05 to about $4 once, depending on the model. Positions
shared with other users cost nothing more.

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
  store `punish_uci` = the stored `best_uci` of the next position, and create the `deviation`
  punish card (R6.5). It's created once per deviation and never recreated after the user removes
  it.
- **Theory flag (R6.4):** `played_was_theory = (position_key, played_uci) in theory_moves`.

The punish card's review payload mirrors a blunder punish card's: the line from the start of the
game to the opponent's move, the FEN after it, and `punish_uci` as the answer, plus the stored
evaluations, so `ReviewPanel` can reuse its blunder-punish presentation.

---

## D7 — Measuring it

`GET /stats/repertoire-depth?weeks=12` returns, per week (Monday-based, like the blunder rate):
the games checked, the average `repertoire_exit_ply` (counting "never left" as the game's last
repertoire ply), and the share of games that left the repertoire. It goes in `progress_service`,
with a dashboard chart in the same style as the blunder rate, and the same "not enough games" rule.

---

## D8 — Lichess explorer, optional

Only when `LICHESS_API_TOKEN` is set: a personal token from the owner's Lichess account, kept in
the VPS `.env` and never committed.

- `LichessClient.explorer(fen, ratings, speeds)` calls the explorer's `/lichess` endpoint with the
  token as a Bearer header and the usual User-Agent, with a short timeout (3 s).
- The **rating band** is the bucket around the user's current Lichess rating in the speed they play
  most, from Lichess's public user endpoint, cached for a day.
- Results are cached in `explorer_cache` per (position, rating band) for 7 days. Nothing is
  pre-fetched; a position is fetched the first time someone opens it in the repertoire view.
- Any failure (no token, timeout, 401, 429) returns "no data", the panel is hidden, and it's
  logged once per hour at most. Nothing else in Phase 2 reads explorer data, so an outage only
  hides the panel.

**Later, not in Phase 2:** the same data can prepare the user for the common non-theory replies
played at their level and a bit above. That's on the roadmap's "Later / maybe" list. Keep the
explorer client and cache general enough to serve it.

---

## Open questions

All three questions from the first draft were settled by the owner on 2026-11-23 (benchmark
cheaper models and use batches; punish cards for opponent slips; the explorer as an optional
extra). Still open:

1. **Which explanation model**, settled by the benchmark in D4.

## What's left to the builder

- The exact tree JSON shape, and how much of the tree one request returns.
- How the repertoire view lays out the tree and board, within the existing styles.
- The prompt wording, within the rules above. Keep it in one constant with its `prompt_version`.
