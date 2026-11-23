# Blundr — Mission

## Mission

**Blundr turns the games you've already played into the training you actually need.**

Other platforms teach you chess in general: courses, opening books, puzzle sets written for
everyone. Blundr works the other way around. **You play, you make mistakes, and we help you fix
them.**

Every lesson, drill, and review in Blundr comes from your own games. We find the moments where you
lost the thread (the blunder, the opening you didn't know, the endgame you couldn't convert, the
plan you never found) and train you on them until they stop happening.

## Who it's for

Club players, roughly 800–2000, who play regularly online and want to get better. They don't have a
coach, have no time for 300-page courses, and can't tell which of their thousands of games matter.
Blundr is the coach that has watched every one of them.

## What we believe

1. **Your games are the best curriculum.** A tactic you missed in your own game sticks better than a
   random puzzle, and a weakness you have outranks the ones the average player has.
2. **Mistakes come in patterns.** One blunder is noise. The same kind of blunder five times is a
   lesson. Blundr's job is to find those patterns and name them.
3. **Every mistake has two sides.** If you can learn to avoid a mistake, you can learn to punish
   it when your opponent makes it. We train both.
4. **Understand first, then remember.** A move you understand is one you can find again in a new
   position. We explain why a move works before asking you to recall it, then use spaced
   repetition to bring the position back until it sticks.
5. **Show progress in your games.** Success isn't streaks or cards reviewed. It's the mistake
   showing up less often in the games you play next.

## What Blundr trains

Each area starts from your games and feeds the same review loop.

| Area | What Blundr finds in your games | How it trains you |
|---|---|---|
| **Blunders** *(live today)* | Moves that threw away a winning or balanced position | "Avoid" and "punish" cards, stepped through as a lesson, on a spaced-repetition schedule |
| **Openings** | The openings you actually play, and the moves where you or your opponent left the main line | Build your repertoire from your own games and flag the moves where you strayed from it. Teach the ideas behind each line: the plans, the pawn structures, and why each move makes sense. That way you can find the right move when your memory runs out, instead of only memorizing a sequence of moves |
| **Tactics** | The tactics you missed or allowed (forks, pins, back-rank mates...) | Group misses by theme and drill your weakest themes using positions from your own games first |
| **Endgames** | Won or drawn endgames that you then lost or failed to win | Replay the critical position against the engine until you can win or hold it |
| **Strategy** | Slow mistakes the engine sees but that aren't single blunders: bad trades, pawn-structure errors, misplaced pieces | Explain the plan you missed in plain language, tied to the position in your game |

## How we'll know it's working

- The same kind of mistake shows up less often in a player's games over time.
- Players stay in their repertoire deeper into the game, and leave it less often.
- Players who keep up with their reviews gain rating faster than those who don't.
- Players come back daily because the reviews are short and feel worth doing.

## What Blundr is not

- **Not a course platform.** We don't sell generic courses or opening books. Teaching material
  only appears when it explains something from your own games.
- **Not a puzzle feed.** No endless stream of random puzzles. Generic positions only fill in where
  your games don't have enough examples of a pattern yet.
- **Not a place to play, for now.** You play on Lichess (and later other sites). Blundr is where
  you study what happened. Lichess's API would let players play rated games inside Blundr, but
  Lichess already does that well, so on its own it adds little. Play only belongs in Blundr when it
  is training: playing out a position from your own games against the engine, or playing a game
  that sticks to your repertoire. If we add it later, it serves that purpose.
- **Not an engine-number dump.** Engine evaluations are the raw material, not what we show you.
  Every lesson says, in words, what went wrong and what to do instead.

## Principles for building it

- **Start from the player's games.** A new feature must start from the player's own games. If it
  doesn't, it needs a strong reason to exist.
- **One review loop.** Every training area feeds the same spaced-repetition queue, not a separate
  app for each. A Woodpecker drill mode sits next to it: drill a fixed set of positions again
  and again, faster each cycle, until the right move comes without thinking.
- **Train the moves you get wrong.** A line is scheduled move by move. The moves you keep missing
  come back often, and the ones you already know don't waste your time.
- **Short sessions.** One review session fits in a coffee break, about 10 minutes.
- **Honest analysis.** Only flag what matters. Don't flag a loss of advantage in a position that
  was already lost, and don't drill trivia.
