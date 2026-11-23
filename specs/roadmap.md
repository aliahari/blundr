# Blundr — Roadmap

This roadmap puts [mission.md](mission.md) into an order. Each phase has a goal, what's in and out of
scope, and a "done when" check tied to the mission's success measures. Detailed requirements,
design, and tasks for each item go in `specs/features/<NN-name>/` once that item is next up.

Phases are in order. A phase starts when the one before it is done, unless it says otherwise.

---

## Phase 0 — Blunder training *(shipped)*

What's live at blundr.ch today:

- Import Lichess games and analyze them with Stockfish, using the win-probability drop to flag blunders
- An "avoid" card and a "punish" card for each blunder
- Each blunder taught as one lesson, stepped through by hand
- SM-2 spaced-repetition scheduling, with a review log

---

## Phase 1 — Foundations for more than blunders *(shipped 2026-11-23)*

**Spec:** [features/01-foundations](features/01-foundations/requirements.md)

**Goal:** prepare the review loop and the analysis pipeline for training material that isn't a
blunder, and start measuring whether players improve.

The phases after this one all depend on it. Today a `ReviewCard` must belong to a `Blunder`
(`review_cards.blunder_id` is a required foreign key, unique together with `card_type`), so an
opening, tactic, or endgame card has nowhere to go.

**In scope**
- **A general review card:** a card that points to a training item of any kind (blunder, opening
  line, tactic, endgame position), with SM-2 scheduling unchanged. Existing blunder cards migrate
  without losing their review history.
- **One review queue:** a single daily session that mixes all card types and keeps the mission's
  10-minute limit.
- **Progress baseline:** record how often each player makes mistakes, per game and per kind of
  mistake, over time. This gives later phases something to show ("you blunder 40% less in the
  middlegame than in March").
- **Storing more per position:** keep enough of the analysis for each position that the later
  phases can work from games that were already analyzed, without running Stockfish on them again.

**Out of scope:** any new kind of training. Nothing new for the player yet, apart from an early
progress view.

**Done when:** blunder training works exactly as it does today on the new card model, and a player
can see their own blunder rate over time.

---

## Phase 2 — Openings

**Spec:** [features/02-openings](features/02-openings/requirements.md)

**Goal:** a player has a repertoire built from their own games, understands why its moves make
sense, and Blundr tells them when they leave it.

**In scope**
- **Find your repertoire:** build an opening tree for White and for Black from your imported games,
  showing what you actually play and how often.
- **Choose your repertoire:** accept, edit, or replace the lines Blundr suggests. This is the
  repertoire you train.
- **Deviation detection:** in each new game, flag the first move where you left your repertoire
  (the line you forgot) or your opponent did (a chance to punish), and say what happened next.
- **Teach the ideas:** for each important position in your repertoire, explain the plan, the
  pawn structure, and why the move makes sense. This comes before any memorization (mission
  belief #4).
- **Opening cards, one per move:** every move you must find in your repertoire gets its own
  card. A review plays the line from the start and grades only the moves that are due, so
  training focuses on the moves you actually miss, not the whole line. The lines you get wrong in
  real games enter the queue first.

**Out of scope:** generic opening courses, preparing against a specific opponent, very deep
theory past the point where your games leave the book.

**Decisions (2026-11-23, see the [feature spec](features/02-openings/design.md))**
- Explanations: Claude writes them from engine and game data, and the owner's own notes win.
  They're generated through the Batches API, and cheaper models are benchmarked before choosing.
- Theory: moves are marked as theory or off-book using Lichess's open opening dataset.
- An opponent's blunder out of the repertoire becomes a punish card.
- The Lichess opening explorer is an optional extra only: it now needs a login token and has had
  outages.
- Depth: up to where the player's games stop repeating (3+ games), at most move 15.
- One card per **position**, so transpositions are learned once.
- SM-2 stays for now; **FSRS** is still an open question for a later phase.

**Done when:** players' games stay in their repertoire for more moves, and they leave it less often,
over a month of use.

---

## Phase 3 — Tactics themes

**Goal:** turn individual tactical blunders into named patterns ("you keep missing back-rank
mates") and train the player's weakest themes.

**In scope**
- **Theme tagging:** label each blunder and refutation with its tactical theme (fork, pin, skewer,
  discovered attack, back-rank, deflection...).
- **Your weak themes:** a view that ranks themes by how often you miss or allow them.
- **Multi-move combinations:** when a tactic takes several moves, each of your moves in it is its
  own step, as in openings. You may find the first move of a combination and miss the third, and
  the review should then focus on the third.
- **Practice by theme:** review sessions focused on one theme, using positions from your own games
  first. Generic puzzles fill in only when your games don't have enough examples of a theme yet.
- **Patterns, not single mistakes:** surface a recurring weakness only after it shows up several
  times (mission belief #2).
- **Woodpecker drill mode:** a second way to train, next to spaced repetition.
  - Pick a fixed set of positions, for example "my 50 missed forks" or "all my blunders from
    October".
  - Solve them all in one cycle, then solve the same set again, aiming to finish each cycle faster.
  - The goal is speed and intuition, not just remembering the answer.
  - Blundr tracks time and accuracy for each position and each cycle, and shows the set getting
    faster.
  - It works on any group of positions: tactics themes here, blunders from Phase 0, and opening
    positions from Phase 2.

  Because everything needed for it already exists after Phase 1, this could also ship on its own
  before the rest of Phase 3.

**Out of scope:** an endless puzzle feed, or a puzzle rating competing with the Lichess one.

**Open questions**
- How to classify themes: rule-based detection with python-chess, borrowing the Lichess puzzle
  theme definitions, or both.
- How to source fill-in puzzles: the Lichess puzzle database (open licence) is the obvious
  candidate.
- Woodpecker cycle rules: a set size (the method's authors use hundreds of puzzles; your own
  games may give fewer), whether each cycle has a target time (such as half the previous one),
  and when a set counts as done.
- Should a Woodpecker attempt also update the position's spaced-repetition card? Suggestion:
  log every attempt (with time taken), but don't reschedule the card. The two modes then stay
  independent, and the review history still shows the practice.

**Done when:** a player's miss rate goes down for the themes they trained.

---

## Phase 4 — Endgames

**Goal:** convert winning endgames and hold drawn ones that you used to lose.

**In scope**
- **Detect missed endgames:** find endgame positions (by material) where your result was worse
  than the evaluation: a win you drew or lost, or a draw you lost.
- **Play it out:** replay the critical position against the engine until you win or hold it.
  This is the first case of training play inside Blundr (see mission, "Not a place to play, for
  now").
- **Endgame types:** group the positions you missed by type (rook endings, king and pawn, ...) so
  that patterns show, as in Phase 3.
- Use exact tablebase results where few enough pieces remain (the Lichess tablebase API).

**Out of scope:** a full endgame course, and composed studies.

**Done when:** a player converts more of their winning endgames.

---

## Phase 5 — Strategy

**Goal:** explain the slow mistakes that aren't blunders (bad trades, pawn-structure damage,
misplaced pieces, a missing plan) in plain language, based on your own games.

The hardest phase, so it comes last. It gains the most from what the earlier phases built
(per-position data, explanations, patterns).

**In scope**
- **Find slow mistakes:** moves that lose a moderate amount repeatedly, or a series of
  small-loss moves that together throw away a position.
- **Explain the plan:** describe in words what you did, what the better plan was, and why.
- **Strategic patterns:** recurring themes such as "you trade your good bishop" or "you push
  pawns in front of your king".

**Open questions:** whether this needs an LLM to write the explanations, and how to keep it honest
(mission principle: "Honest analysis"). Prototype before committing.

**Done when:** players rate strategy explanations as useful, and the patterns they were shown
show up less often in their games.

---

## Later / maybe

Not committed to. Revisit after Phase 2.

- **Chess.com import:** reaches far more players, but needs a second import path and a check of
  how analysis performs on Chess.com's time controls.
- **Training play through Lichess:** use the Lichess Board API to play repertoire training games
  or replay positions as real games. Only as training, per the mission.
- **Coach or teacher view:** share a student's weaknesses with a coach.
- **Mobile-friendly or offline review:** the review loop is the daily habit, so make it work well
  on a phone.

---

## How this roadmap changes

- Rework a phase when its "done when" check shows the feature isn't working, rather than moving on.
- When a phase starts, create `specs/features/<NN-name>/` with `requirements.md`, `design.md`, and
  `tasks.md`, and link it from that phase above.
- If an open question changes the scope of a phase, update this file.
