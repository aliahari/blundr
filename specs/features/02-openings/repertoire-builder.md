# Phase 2: the step-by-step repertoire builder (design)

Requirements: [requirements.md](requirements.md) · Design: [design.md](design.md) ·
Tasks: [tasks.md](tasks.md) · Replaces the tab from [repertoire-graph.md](repertoire-graph.md)

Drafted 2026-11-27 with the owner. Depends on [Sign in with Lichess](../lichess-sign-in/design.md)
for the explorer token, but works without it (see "When Lichess isn't there").

## Why

The owner found the graph-and-panel Repertoire tab (repertoire-graph.md, shipped in #32 to #34)
too complicated and too slow:

- **Too much on screen.** A graph, a board, two tables and a list of deviations, with nothing
  saying what to do next.
- **Each Pick waits on everything.** It saves the move, rechecks every game of that colour, then
  reloads the graph and the panel. The panel waits on a Stockfish run of about 1.5 s, and up to
  5 s when the engine is busy analysing games.

What the owner liked in Chessbook:

1. It shows you the positions you're likely to face.
2. It walks you through building the repertoire one position at a time.
3. It covers every likely case down to a set depth.
4. You add depth later.

The graph design took "likely" from the user's own games, which goes quiet exactly where it's
needed. When you switch to a theory move you haven't played, your games say nothing about what
opponents answer (owner, 2026-11-27). So likelihood comes from Lichess games played by people at
your rating.

## Goals

- The tab opens on **the most likely position you haven't prepared**, with your choices. You pick
  one and it moves straight to the next one.
- "Likely" means how often you'd reach the position playing your repertoire against opponents at
  your rating and time controls on Lichess.
- **Depth is a coverage goal:** cover every position you'd reach in at least 1 game in N. When
  you've covered everything at one level, you go deeper (1 in 20, then 50, 100, 200).
- **A step never waits on the network or the engine** when it can be prepared in advance. The next
  steps load in the background while you think.
- Everything you pick is trained exactly as today: opening cards, explanations, punish cards,
  deviation checks.

## Non-goals

- The graph while building. It stays, but only as a separate overview (see "The overview").
- Preparing against a specific opponent.
- Choosing a rating range other than your own. A "prepare one level up" setting can come later;
  the cache key already allows it.
- Building from your own games ("Start from my games"). It goes, because starting from the first
  gap covers the same ground.

## Anti-goals

- **A screen that needs explaining.** One position, your choices, one button per choice, and a
  progress line.
- **Asking you to prepare positions nobody reaches.** Below the coverage goal, positions aren't
  shown as steps.
- **A tab that stops when Lichess does.** The explorer had a three-day outage in February 2026, so
  master games stand in whenever it fails.
- **Spending the user's Lichess allowance carelessly.** Each position is fetched once per rating
  band and kept; requests go out one at a time.

## Approaches

- **A. The server works out the next gap (chosen, owner 2026-11-27).** One request returns the next
  unprepared position and its choices. The server walks your repertoire with the stored explorer
  counts, and while you choose it prepares the next few gaps: explorer data and engine lines.
- **B. The browser walks the tree.** Dropped: it would download the tree and every explorer result
  up front, making the first load heavy, which is the slowness being fixed.

## How it works

### Where the numbers come from

- **Opponents' replies:** the Lichess explorer's `/lichess` endpoint, called with the user's token
  ([sign-in design](../lichess-sign-in/design.md)) and these parameters:
  - `fen`;
  - `speeds` = the user's sync game types (rapid and blitz by default);
  - `ratings` = the explorer's rating bucket that holds the user's rating, plus the bucket above.
    The buckets are 0, 1000, 1200, 1400, 1600, 1800, 2000, 2200 and 2500.

  The rating is the user's Lichess rating for those speeds, the highest one if they differ. It's
  read from `/api/account` at sign-in and on each sync, and kept on the user as
  `lichess_ratings` (JSON).
- **Cache:** the existing `explorer_cache` table (position, rating band, body, fetched at). The
  band key holds the speeds and buckets, for example `blitz,rapid:1600,1800`. An entry is used for
  30 days, then fetched again the next time it's needed.
- **Your choices at your turn:** as in today's panel. Engine top 4 (`engine_lines`), master moves
  and theory (`is_theory`), plus a new column: what players at your level play here, as a share of
  games and a score.
- **Your games** still mark lines you've met ("you've had this 12 times") and where you left your
  repertoire. They don't decide likelihood.

### Likelihood and gaps

- **How likely a position is:** multiply the share of each opponent move along the line. Your own
  moves count as 1, since you always play your repertoire move. The start position is 1.
- **Walking the repertoire:** start from the start position and go best-first, most likely first,
  up to ply 30.
  - **At your turn with a repertoire move:** follow it.
  - **At your turn without one:** that's a **gap**, if the position's likelihood reaches the goal.
  - **At the opponent's turn:** follow every reply whose position still reaches the goal.
- **Thin data.** A position the explorer has fewer than 20 games for (`EXPLORER_MIN_GAMES`) ends
  the walk there: shares that thin are noise. It counts as covered and says "rarely reached at
  your level".
- **Transpositions:** positions are keyed by `board.epd()`, so a position two lines reach is one
  gap, prepared once. Its likelihood is that of the most likely line into it. *(Builder's call:
  adding the lines up would need the whole walk before anything is shown.)*
- **Checked by hand, not yet in code:** that a token with no scopes is accepted by the explorer.
  Check it with the owner's sign-in on local dev before building on it.
- **Order:** gaps come most likely first, ties broken by how many of your games reached them.
- **Progress:** `prepared` = positions at your turn, at or above the goal, with a repertoire move.
  `left` = gaps found. Both grow as you pick, because each pick opens new lines. The screen says
  "18 prepared · 7 to go at 1 in 20". When `left` is 0: "Everything you'll meet in 1 in 20 games
  is prepared. Go deeper: 1 in 50."
- **The goal** is stored per user and colour (`coverage_goal`, the N in 1 in N). It starts at 20;
  the steps are 20, 50, 100 and 200. You can also go back up a level.

### Requests

- **`GET /repertoire/{color}/next`** returns:
  - the first gap: line (UCI and SAN), FEN, likelihood, and the opponent's last move with its share;
  - its choices, as today's `candidates` plus `level_share` and `level_score`;
  - progress (`prepared`, `left`, `goal`);
  - `source`: `"lichess"`, or `"masters"` when it fell back.

  With no gap left, `gap` is null and the progress says done.
- **`PUT /repertoire/{color}/position`** (pick) saves the move as today, then answers with the next
  gap in the same shape, so a pick is a single round trip. The deviation recheck (`recheck_games`)
  moves to a background task; it doesn't need to finish before the next step.
- **`GET /repertoire/{color}/position?line=…`** stays, for opening any position from the outline
  (choices and Remove).
- **`PUT /repertoire/{color}/goal`** sets N.
- **`GET /repertoire/{color}/graph`** stays, for the overview.
- **Removed:** `/start` and `/accept`.

### Getting ahead of the user

After answering `/next` or a pick, the server starts a background job for that user. It is the
only one per user; a newer one replaces it.

- It walks on to find the next three gaps.
- It fetches any explorer data the walk needs.
- It warms the engine lines for those gaps.

Constraints:

- **Explorer requests go one at a time per user,** with the client's usual delay between them.
- **Engine runs happen only when the engine is free.** Game analysis keeps priority, through the
  existing `_engine_slot()`.
- **A gap with no engine lines yet** shows its choices without evaluations, as the panel does
  today when the engine is busy.

**First visit.** The walk needs explorer data for each opponent position it passes, so the first
`/next` on an empty or new repertoire may make a few requests (about one per opponent move on the
way). It waits at most 3 s in total for the explorer. Past that, the positions it couldn't fetch
use master counts for this answer, and the background job fetches them properly.

### When Lichess isn't there

- **No token** (never connected, expired or revoked), or the explorer fails or times out: use
  master-game shares from `master_moves` for those positions.
- The screen shows a quiet note: "Using master games: Lichess isn't reachable" or "Sign in with
  Lichess again to see what players at your level play".
- A failed fetch isn't cached, so a later walk retries it.
- Master shares make "likely" mean likely in master play. That's worse for club opponents, but
  still a sensible walk.

### The screen

```
 With White | With Black                      18 prepared · 7 to go at 1 in 20  [Deeper ▾]

 1.e4 e5 2.Nf3 Nc6 3.Bc4 Nf6                   [ board, your side at the bottom,
 Two Knights · 1 in 9 games at your level         the opponent's last move highlighted ]
 Your opponent played 3…Nf6 (31% here)

 Your move
 ┌────────┬──────────┬───────────────────┬──────────┬────────┐
 │ Move   │ Engine   │ At your level     │ Masters  │        │
 │ d3     │ #2 +0.3  │ 41% · scores 54%  │ 22%      │ [Pick] │
 │ Ng5    │ #1 +0.5  │ 30% · scores 51%  │ 35%      │ [Pick] │
 │ ...                                                        │
 └────────────────────────────────────────────────────────────┘
                                                   [See your repertoire]
```

- **Phone:** the board on top, then the choices. Nothing else above them.
- **After a pick:** the next gap slides in. A short line ("Added 4.d3. Next: after 3…Bc5") stays
  for a few seconds.
- **No explanations on the Repertoire tab** (owner, 2026-11-27), neither in the builder nor in
  the overview. Building is about choosing. You read why a move makes sense when you learn it in
  Learn, which already teaches each new opening card with its explanation before asking for the
  move, and shows it again after each answer.
- **The owner's own explanations** (written or edited by the owner, which win over Claude's) are
  edited from Learn instead. The owner gets an "Edit" link on the explanation box there, using the
  existing `ExplanationPanel` editing. *(Builder's call: the edit has to live somewhere once the
  Repertoire tab stops showing explanations.)*

### The overview

"See your repertoire" opens the graph from repertoire-graph.md as a view of its own, away from
building (owner, 2026-11-27). It shows the shape of your lines, how often each came up, and where
you or your opponents left theory. Clicking a position opens it with its choices and
Remove (today's position panel, trimmed). "Where your games left the repertoire" moves into this
view as a short list.

The graph's marks ("add your answer", "extend here") are dropped, since finding gaps is now the
builder's job. Positions that are gaps at the current goal are marked instead, and clicking one
opens it in the builder.

## Changes to earlier decisions

- **Roadmap D8** ("the explorer is an optional extra only"): the explorer is now the main source
  for opponents' replies, with master games as the fallback.
- **repertoire-graph.md's non-goal** "what club players at a rating range play" is now in scope.
- **Its anti-goal** "a tab that depends on someone else's server" becomes "a tab that stops when
  Lichess does": the cache and the master fallback keep it working.

## What's left to the builder

- Exact copy, and how the next gap animates in.
- The outline's collapse rules and how many moves it shows at first.
- Whether `/next` and the pick reply share one response model.
- Tests: the walk with a fake explorer (likelihood, goal, thin-data stop, fallback), the
  background job's one-per-user rule, and no real Lichess calls anywhere.
