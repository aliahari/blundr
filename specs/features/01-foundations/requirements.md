# Phase 1 — Foundations: Requirements

Roadmap: [Phase 1](../../roadmap.md#phase-1--foundations-for-more-than-blunders) ·
Design: [design.md](design.md) · Tasks: [tasks.md](tasks.md)

## Why

The phases after this one add opening, tactic, endgame and strategy training. Today a review
card can only belong to a blunder, the dashboard can't show whether a player is improving, and
the analysis throws away everything except the blunders. This phase fixes those three things
before any new training is built on them. It adds almost nothing new for the player.

## Scope

### R1 — A review card can belong to any kind of training item

- **R1.1** A review card records what kind of item it trains (`blunder` for now) and points to
  that item. Adding a new kind in a later phase must not require rebuilding the `review_cards`
  table again.
- **R1.2** Every existing card keeps its ID, its scheduling state (ease, interval, repetitions,
  lapses, due date, last reviewed) and its full `review_logs` history after the migration.
- **R1.3** A card records when it was created, so stats don't need to look at the blunder to find
  out how old a card is.
- **R1.4** Deleting a training item deletes its cards, just as deleting a blunder does today.
- **R1.5** A training item can have **one card per move** (a "step"), each scheduled on its own.
  An opening line or a combination is several moves long, and a player usually struggles with
  only some of them. A miss on move 7 should bring back move 7 sooner, not the whole line.
  Today's blunder cards are all step 0, so nothing changes for them.

**Acceptance**
- On a copy of the production database, the migration finishes and the row counts are
  unchanged. A sample of cards has identical scheduling fields before and after.
- Running the migration a second time does nothing.
- All existing tests pass without changing what they assert.
- A test can create two cards for the same item and `card_type` with different steps, grade one
  of them, and see only that one rescheduled.

### R2 — One review queue for every kind of card

- **R2.1** `GET /reviews/due` returns cards of every kind in one list. Due cards the player has
  seen before come first, most overdue first, followed by new cards. This is today's behaviour.
- **R2.2** The daily new-card limit (`max_new_per_day`) counts **training items**, not cards. A
  blunder's avoid and punish cards together still use one slot, and a later kind with several
  cards behaves the same way.
- **R2.3** The cards of one item are served next to each other, in a fixed order for that kind:
  avoid, then punish, for blunders.
- **R2.4** Each card in the response says what kind it is and carries that kind's data. Blunder
  cards carry the same `blunder` object as today.
- **R2.5** `/reviews/stats` and `/stats/overview` count due and new cards the same way as
  `/reviews/due` (they already share the helpers, and that must stay true).

**Acceptance**
- With only blunder cards in the database, `/reviews/due` and `/reviews/stats` return the same
  responses as before the change, apart from the new `kind` field.
- A test with a fake second kind of item shows that both kinds are mixed in one queue and that
  each item uses one slot of the daily limit.

### R3 — Keep what the analysis already computes

- **R3.1** For every newly analyzed game, store each move with the engine's evaluation after it
  (from the player's side) and the engine's best move in the position before it. Stockfish
  already computes all of this in the analysis loop.
- **R3.2** Store how many moves the player made in the game and the game's speed (bullet, blitz,
  rapid, classical).
- **R3.3** Storing this must not add any Stockfish calls, and must not make analysis noticeably
  slower.
- **R3.4** Games analyzed before this change keep working. Their stored move data is empty, and
  anything that needs it skips them.

**Acceptance**
- After syncing one game, its stored moves replay legally from the starting position, and their
  count equals the number of moves in the game.
- The analysis test with the stubbed engine shows the same number of `engine.analyse` calls as
  before.

### R4 — Progress baseline: is the player blundering less?

- **R4.1** Measure the **blunder rate**: blunders per 100 of the player's own moves, grouped by
  the week the games were played.
- **R4.2** Break the rate down by game phase (opening / middlegame / endgame) and by speed.
- **R4.3** Only games that have the R3 data count toward the rate. Say plainly in the response
  how many games each week is based on, so a week with two games isn't read as a trend.
- **R4.4** Show the rate on the dashboard as a weekly line with a phase filter. Hide the line
  until there are at least 4 weeks with enough games (builder's call: about 5 or more).

**Acceptance**
- An endpoint returns the weekly rates, and tests check them against hand-made data.
- The dashboard shows the line and the phase filter, and `npm run build` passes.

## Out of scope

- Any new kind of training: opening, tactic or endgame cards.
- Detecting mistakes or inaccuracies, meaning drops smaller than a blunder. The stored data from R3 makes
  this possible later, but this phase doesn't do it.
- Re-analyzing old games to fill in their R3 data. See the open question in the design.
- Changes to the SM-2 scheduling itself.
- Switching to Alembic or Postgres.

## Done when

The roadmap's check: blunder training works exactly as it does today on the new card model, and
a player can see their own blunder rate over time.
