# Phase 2 — Openings: Requirements

Roadmap: [Phase 2](../../roadmap.md#phase-2--openings) · Design: [design.md](design.md) ·
Tasks: [tasks.md](tasks.md)

## Why

Most club players don't know which openings they actually play, forget their lines a few moves in,
and learn openings as move lists they can't rebuild when memory runs out. Blundr already has every
game the player played. Phase 2 turns those games into a repertoire, teaches the ideas behind each
move before drilling it (mission belief #4, "understand first, then remember"), and checks each new
game against it.

## Decisions already made

- **Explanations come from Claude, and the owner's own notes win.** An explanation is generated once
  per position and move, stored, and shared by every player who reaches that position. Anything the
  owner writes for a position replaces Claude's text. (Decided 2026-11-23.)
- **One card per position, not per line.** A position reached through two move orders is learned
  once. (Owner's call 2026-11-23, proposed and not objected to.)
- **SM-2 stays.** Switching to FSRS is a separate decision for later. (Same.)

## Scope

### R1 — Every game has its moves

- **R1.1** Games analyzed before Phase 1 get their move lists fetched from Lichess and stored in
  `AnalyzedGame.positions`, without running Stockfish again. Their evaluations stay empty.
- **R1.2** This runs once per user as a background job, can be resumed, and respects Lichess's rate
  limits.
- **R1.3** As a side effect, these games start counting in the blunder-rate chart (their blunders
  are already known).

**Acceptance**
- After the job, every standard game of the user has `positions` and `user_moves` set, or is logged
  as not found on Lichess.
- Running it again fetches nothing.

### R2 — Find your repertoire

- **R2.1** Blundr builds an opening tree from the user's games, separately for White and Black.
  Each node is a position; the edges are the moves played from it, with how often and with what
  result.
- **R2.2** It suggests a repertoire: for each position where it's the user's move and that position
  came up in at least 3 of their games, the move they play most often. The tree goes as deep as the
  user's games keep repeating, and no further than move 15 (ply 30).
- **R2.3** Where the engine evaluation shows the user's usual move is clearly worse than the best
  move, the suggestion says so and offers the better move instead. The user decides.
- **R2.4** Each position shows its opening name (from Lichess's open opening-names dataset).

**Acceptance**
- For a hand-made set of games, the suggested repertoire has exactly the expected positions and
  moves, for both colours, and stops at the expected depth.
- A position reached through two move orders appears once.

### R3 — Choose your repertoire

- **R3.1** The user can accept a suggested move, replace it with any legal move, or remove a
  position (and so everything below it) from the repertoire.
- **R3.2** The user can extend the repertoire past what their games cover by adding a move by hand.
- **R3.3** Only accepted positions are trained. Suggestions alone never create cards.

**Acceptance**
- Accepting, replacing and removing work through the API and the UI, and removing a position also
  removes the positions below it that can't be reached any other way.

### R4 — Understand each move first

- **R4.1** Every accepted position has an explanation: the plan in this kind of position, why this
  move fits it, and the pawn structure if it matters. It's written for a club player in plain
  language, at most about 150 words.
- **R4.2** Explanations come from Claude (see design D4) and are stored per position and move,
  shared across users. They're generated in the background after a position is accepted; the user
  never waits on a page for one.
- **R4.3** The owner can write or edit the explanation for any position. An owner-written
  explanation always replaces Claude's.
- **R4.4** Every user can report an explanation as wrong. Reported explanations are listed for the
  owner.
- **R4.5** The first time a position comes up in review, the explanation is shown before the user is
  asked for the move. After that it's shown after they answer.
- **R4.6** Without an Anthropic API key configured, everything else in Phase 2 still works; positions
  just have no explanation until one is written by hand.

**Acceptance**
- With the Claude call stubbed, accepting 5 positions produces 5 stored explanations; accepting a
  position another user already has an explanation for makes no new call.
- An owner-written explanation is served instead of Claude's.

### R5 — Train it move by move

- **R5.1** Each accepted position where it's the user's move becomes a review card of kind
  `opening`, in the same queue as blunder cards (Phase 1).
- **R5.2** A review shows the line from the start position up to the card's position. Moves the
  user isn't being tested on are played for them; only the due position is graded.
- **R5.3** New opening positions enter the queue in line order (a parent position before its
  children), within the existing daily new-item limit.

**Acceptance**
- A user with accepted lines gets opening cards in `/reviews/due` next to blunder cards, and
  grading one reschedules only that position.

### R6 — Check every new game against the repertoire

- **R6.1** For each newly analyzed game (and each game whose moves R1 fetched), Blundr finds the
  first point where the game left the user's repertoire: the user played a different move ("you
  left your repertoire"), or the opponent played a move the repertoire doesn't cover ("your
  opponent left your preparation").
- **R6.2** When the user left their repertoire, that position's card becomes due now, so it comes
  up in the next review.
- **R6.3** When the opponent's new move loses at least as much as a blunder by Blundr's existing
  rule, it's flagged as a chance that was there to punish, with the engine's best reply. (Using the
  evaluations stored since Phase 1; games without evaluations don't get this flag.)
- **R6.4** A "recent deviations" list shows these, newest first, each linked to the position.

**Acceptance**
- For hand-made games against a fixed repertoire, the detected deviation is at the right ply and
  side, and a user deviation makes the right card due.

### R7 — Measure it

- **R7.1** Per game, record the ply where the game left the user's repertoire, or "never" if it
  didn't.
- **R7.2** Show the average of that by week on the dashboard: "how far you stay in your
  repertoire", the roadmap's success measure for this phase.

## Out of scope

- Generic opening courses and opening books, and preparing against a specific opponent.
- The Lichess opening explorer. It now requires a login token per request and has had outages
  (see design), so Phase 2 must not depend on it. It may come back later as an optional extra.
- Training opponent moves (positions where it's the opponent's turn don't get cards).
- FSRS, and a Woodpecker mode for openings (Phase 3 territory).
- Chess.com games.

## Done when

The roadmap's check: over a month of use, players' games stay in their repertoire for more moves,
and they leave it less often.
