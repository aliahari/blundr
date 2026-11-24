# Phase 2 — The repertoire graph (design)

Requirements: [requirements.md](requirements.md) · Design: [design.md](design.md) ·
Tasks: [tasks.md](tasks.md)

Drafted 2026-11-24 with the owner. It replaces the Repertoire tab built in task 11 (a list of
suggested moves to accept) and turns task 14 (the Lichess explorer) from optional into required.

## Why

The owner opened the Repertoire tab and didn't know what to do with it. A list of "moves you play in
3 or more games, accept them" doesn't say what to learn, why, or what comes next.

The training idea behind the new tab:

1. **Theory first.** Against best play, you should know what to play. The backbone of a repertoire
   is master theory, not whatever you happened to play.
2. **Then your games say where to go deeper.** Wherever opponents in your games leave theory, add
   how you respond. Wherever your games run past the end of your repertoire while still in theory,
   extend it there. Frequency in your own games decides what's worth the effort.
3. **Depth grows as you do.** Stronger opponents follow theory longer, so your games keep reaching
   deeper positions, and the graph keeps pointing at the next step. No separate "advanced mode".

This is the Chessbook way of building a repertoire (pick from the engine's ranked moves at your
turn, then answer your opponents' likely replies, one move deeper at a time), driven by the user's
own games instead of a generic database.

## Goals

- The tab shows the user's openings as an interactive graph: every line from their games, how often
  it happened and how they scored, and where they or their opponent left theory.
- From any position the user can see what to do next and do it in one or two clicks: pick their
  move at their turn, or pick an opponent reply to answer.
- "Theory" means what masters actually play, from the Lichess masters database.
- Everything the user picks is trained exactly as today (opening cards, explanations by Claude or
  the owner, punish cards, the task 12 checks, the task 13 depth chart).

## Non-goals

- Playing through every theory branch. The tab never asks the user to cover lines their games don't
  reach; masters' moves they haven't met are shown, not demanded.
- A general opening database or course. The graph is always "your games", with theory as the
  reference.
- Preparing against a specific opponent.
- The "club players at a rating range" explorer data. It's the natural next step (prepare one level
  up) and the client should allow it, but it isn't built here.

## Anti-goals

- **A graph too busy to read.** If the default view of a 300-game history looks like a hairball, it
  has failed even if every number is right. Rare branches fold away by default.
- **Theory marks you can't trust.** If normal moves show as "left theory" (as the openings dataset
  alone does, e.g. 2…Nc6 in the Alapin, 3…exf4 in the Vienna Gambit), users learn to ignore the
  marks. Masters data is the source; the dataset is only a fallback, and the UI says so when it's
  in use.
- **A tab that dies with Lichess.** Without a token, or while the explorer is down, the graph still
  works from the user's games and the dataset.
- **Waiting on a page.** No click should block on more than the engine's one-or-two-second run;
  masters data and explanations fill in when they arrive.

## Approaches considered

**Layout.** Three mockups were drawn from the owner's real White games: a left-to-right line graph
(A), rings / sunburst (B), and a board-first, Chessbook-style panel (C). **Chosen: A and C
together** (owner, 2026-11-24): the graph shows the shape and where to act; clicking a move opens
the C panel, where the user picks moves and steps deeper. Rings were dropped: move order and thin
slices are hard to read.

**Drawing the graph.** (a) Hand-drawn SVG with our own layout, using `d3-hierarchy` (~5 KB) for the
tidy-tree positions; (b) React Flow, which brings pan, zoom and a minimap but adds ~150 KB to a
340 KB app and is styled for flowcharts. **Chosen: a** (owner went with the recommendation): the
graph is a fixed left-to-right shape with simple interactions, and the app stays small on phones.

**Source of theory.** (a) The Lichess masters database through the explorer, with the existing
`LICHESS_TOKEN`, cached; (b) the openings dataset only. **Chosen: a, falling back to b** when there's
no token or the explorer fails: the dataset alone marks too many normal moves as off-book.

**Engine moves at the user's turn.** One way: a multi-line Stockfish run on the server, cached per
position. No API cost.

## How it works

### The graph's data

Per colour, the graph is the position graph `build_tree()` already builds from the user's games
(positions keyed by `board.epd()`, moves with counts and win/draw/loss), merged with:

- **the user's repertoire** (`repertoire_moves`): their chosen move at their turn;
- **master moves** per position, from the explorer cache (below);
- **the task 12 deviations**, for "recent deviations" links.

Each edge (a move from a position) carries:

| field | meaning |
|---|---|
| `games`, `w`, `d`, `l` | in the user's games, from the user's side |
| `master_games` | games in the masters database with this move here; null if not fetched yet |
| `theory` | `true` / `false` / `null` (unknown yet): see "Theory" |
| `repertoire` | at the user's turn: this is their chosen move |

Each node (a position) carries `user_to_move`, the opening name (inherited along the line, as
today), `games` (how many of the user's games reached it), and its **mark**, at most one:

| mark | where | when | colour in the mockup |
|---|---|---|---|
| **you left theory** | your turn | the move you play most here isn't theory, and you have no repertoire move here, or you played against your repertoire move here in a game (a task 12 user deviation) | orange |
| **add your answer** | your turn | the opponent's move into this position wasn't theory, at least `ACT_MIN_GAMES` of your games reached it, and you have no repertoire move here | red |
| **extend here** | your turn | the opponent's move into it *was* theory, the position before it is in your repertoire (you have an answer one level up), at least `ACT_MIN_GAMES` games reached it, and you have no repertoire move here | gold, dashed |

`ACT_MIN_GAMES = 2` to start (a reply seen once is noise). These marks are the graph's to-do list;
the panel and a short list above the graph ("3 replies to answer, 2 places to extend") rank them by
games.

**Folding.** By default an edge is shown when it's in the repertoire or at least `SHOW_MIN_GAMES`
(= 2) of the user's games played it; the rest of a node's moves fold into "+N more", which unfolds
on click. The graph stops at ply 30 (`MAX_PLY`, as today). The user can fold or unfold any branch.

**Transpositions.** Positions are merged, so it's a graph, not a tree. The layout uses a spanning
tree: each position hangs under the first line (by games, then move order) that reaches it, and
any other line into it is drawn as a thin dashed link to that node. Clicking either selects the same
position.

### Theory

- A move is **theory** in a position when the masters database has at least
  `THEORY_MIN_MASTER_GAMES` (= 10) games with it there, or it's on one of the dataset's named lines
  (so named gambits and sidelines masters rarely play still count). Deep in a line, where the
  position itself has fewer than 10 master games, any move masters played there counts.
  *(Builder check, my call: a fixed 10 would mark every move off-book once masters thin out.)*
- When the position's masters data isn't cached yet, `theory` comes from the dataset alone and the
  node shows as "checking theory…" until it arrives (`theory: null` on edges only the masters data
  could confirm). When the explorer is unavailable (no token, error), the dataset decides and the
  tab shows a one-line note: "Theory from named opening lines only: master moves are unavailable."

### Masters data (task 14, now required)

- `LichessClient.masters(fen)` calls `GET https://explorer.lichess.ovh/masters?fen=…&moves=12&topGames=0`
  with the existing `LICHESS_TOKEN` as a Bearer header, the usual Blundr User-Agent and a 5 s
  timeout. (D8 named the setting `LICHESS_API_TOKEN`; it's the existing `LICHESS_TOKEN`, already
  passed through `docker-compose.yml`.)
- Results go in `explorer_cache` with `rating_band = "masters"`, kept for 90 days (master games
  change slowly). One cache row per position serves every user.
- **Prefetch volume.** The owner's White graph shows about 150–300 positions by default, so a first
  prefetch takes a few minutes at one request per second, once; later opens hit the cache.
  *(Builder check, my estimate.)*
- **Prefetch.** When a user opens a colour's graph, the backend queues the masters lookup for every
  *shown* node at the opponent's or the user's turn that isn't cached, and a single background
  worker fetches them one at a time, at most one request per second, newest request first. The
  frontend polls the graph (or the open position) every few seconds while any shown node is still
  "checking theory…", and stops when none is. A 429 pauses the worker for 60 s; other failures
  mark the position as failed for an hour (dataset fallback meanwhile).
- The panel of an opened position fetches its masters data at once (jumping the queue) if missing.

### Engine moves at the user's turn

- `engine_lines(fen)`: Stockfish with `MultiPV = 4`, up to depth 20 or 1.5 s, whichever first.
  Returns up to four `{uci, san, eval_cp}` from the side to move, ranked.
- Cached in a new table `engine_lines (position_key PK, depth INTEGER, body TEXT, created_at)`,
  shared across users, kept forever (the engine version changing is not a reason to recompute).
- At most one engine run at a time across the app (an `asyncio.Semaphore(1)`), so a busy panel can't
  starve the game analysis on the VPS. A run that waits more than 5 s returns "engine busy" and the
  panel shows the user's own and the master moves without evaluations.

### Endpoints

All under `/api/repertoire`, logged-in user only.

- `GET /{color}/graph` → the whole shown graph for that colour: nodes (key, user_to_move, games,
  opening name, mark, folded-move count), edges (as above), the coverage line, and
  `theory_source: "masters" | "dataset"`. Folding is server-side (`?unfold=<key>,<key>` adds the
  folded moves of those positions) so the payload stays small. It also queues the masters prefetch.
- `GET /{color}/position?key=…` → everything the panel needs for one position: the user's moves here
  with counts and scores, master moves with counts and score, engine lines (at the user's turn),
  the repertoire move and its explanation, the line from the start (via `line_to`/first path), and
  the position's opening name.
- Existing: `PUT /{color}/position` (set or replace the move — this is "pick"), `DELETE
  /{color}/position` (remove, with what's below), the explanation and report routes. The
  reachability rule stays: a move can be added only at a position the repertoire reaches, i.e. the
  user builds from the root outward, one step at a time — which is what the panel does.
- `POST /{color}/accept` stays for one button, **"Start from my games"**, shown while a colour's
  repertoire is empty: it accepts the current `suggest()` result, preferring the theory move where
  the user's usual move is off-book and a theory move exists. Users can ignore it and build by hand.
- `GET /{color}` (the old suggestions list) and `GET /{color}/options` go away once the new
  frontend ships; the `suggest()` service stays for "Start from my games".

### The panel (C)

Opens on the right of the graph (below it on phones, < 820 px) for the selected position. Shows
the breadcrumb line (e.g. "White ▸ 1.e4 e5 2.Nc3 · Vienna Game · 74 games"), the board (same colours
as the review), and:

**At the user's turn**
- Their repertoire move, if any, with its explanation (owner-editable, reportable, as today), and
  Change / Remove.
- A **candidates** table, one row per move from the union of: the engine's top 4, master moves above
  the theory threshold, and the user's own moves here. Columns: move, engine eval (if in the top 4),
  masters games and score, your games and score, theory mark. Sorted by engine rank, then master
  games. **Pick** on a row sets it as the repertoire move (PUT position).
- If the user's most-played move here is off-book or clearly worse (the existing R2.3 rule), a
  one-line note says so.
- **A position the repertoire doesn't reach yet** (the user clicked a node deeper than their
  repertoire goes) shows the tables but no Pick button: "Your repertoire doesn't reach this position
  yet. Start at 2…Nc6" with a link selecting the first position on the line that needs a move. The
  user keeps building one step at a time, from the root outward. *(Builder check, my call.)*

**At the opponent's turn**
- A **replies** table: every reply in the user's games here (games, share, their score), plus master
  replies not yet met (dimmed, "not in your games"). Columns: reply, your games and share, masters
  share, theory mark, your answer (the repertoire move in the resulting position, "+ add answer", or
  "—" for a reply below `ACT_MIN_GAMES`). Clicking a row selects that resulting position, which is
  how the user steps deeper. Master replies the user hasn't met can be answered too, which is how
  they prepare a line before meeting it. *(Builder check, my call.)*
- The **coverage** of this position: the share of the user's games here whose reply has an answer.

The breadcrumb, the graph highlight and the panel stay in sync: clicking in either moves both.

### Coverage

Two numbers, so they can't be confused:

- **Above the graph**, per colour: "Your games stay in your repertoire for N moves on average" (the
  task 13 measure, `repertoire_plies`, averaged over all the user's checked games of that colour,
  not per week), plus the to-do count ("3 replies to answer, 2 places to extend").
- **In the panel, at the opponent's turn:** the share of the user's games at this position whose
  reply has an answer in the repertoire.

*(Builder check, my call: the first draft mixed the two into one sentence that didn't match the
task 13 measure.)*

### Data already accepted

Repertoire positions accepted through the old view stay as they are and show in the graph as
repertoire moves. Production has none yet (checked 2026-11-24), so nothing needs migrating.
*(Builder check, my call.)*

### What doesn't change

Opening cards and their review, explanations (Claude in batches, owner notes win, reports), the
task 12 checks (deviations, punish cards, due-now), the task 13 chart, and the data model except
for the new `engine_lines` table and the `explorer_cache` rows.

## Constraints

- Frontend: React 18 + TypeScript + Vite, no router, `react-chessboard` + `chess.js`; one new
  dependency, `d3-hierarchy`. Styles in `index.css` with the existing tokens.
- Backend: FastAPI + async SQLAlchemy + SQLite; new tables via `create_all`, new columns via the
  ADD COLUMN pattern in `db.py`. Every new setting also goes in `docker-compose.yml`.
- Lichess: one request at a time, the Blundr User-Agent, back off on 429 (see the Lichess notes in
  `CLAUDE.md` and memory). The masters endpoint needs the token.
- The VPS runs the game analysis with Stockfish already: panel engine runs are rationed (above).
- Tests never call Lichess or real Stockfish where a stub will do (`_StubEngine`, fake clients), as
  in the existing suites.

## Decided with the owner

- Theory first from master moves; then answers where opponents in your games leave theory; then
  depth where your games run past your repertoire (2026-11-24).
- At the user's turn, ranked engine moves to pick from, Chessbook-style, then step deeper one move
  at a time (2026-11-24).
- Layout A + C (2026-11-24); hand-drawn SVG; masters database with the dataset as fallback
  (2026-11-24, owner went with the recommendations).

## Left to the builder

- Exact JSON shapes of the two new endpoints.
- Pan and zoom details (drag to pan, wheel or buttons to zoom), and the visual details within the
  mockup's colour scheme.
- How "+N more" and folding look, and the exact polling interval.
- Whether the "recent deviations" list stays under the graph or becomes a link on each marked node
  (it must remain reachable).
- How to split the work into tasks; a sensible order is: masters client and cache → engine lines →
  graph and position endpoints → graph component → panel → retire the old view.
