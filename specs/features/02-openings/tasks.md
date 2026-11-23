# Phase 2 — Openings: Tasks

Requirements: [requirements.md](requirements.md) · Design: [design.md](design.md)

Work test-first, then run `make test`, `make lint` (no new findings in touched files), and for
frontend work `npx tsc --noEmit` plus `npm run build`. Every change goes through a PR against
`main`.

Order: tasks 1–2 (old games' moves) don't depend on the rest. Tasks 3–6 (tree and repertoire),
then 7–9 (explanations), 10–11 (review), 12–13 (deviations and measuring) and 15 (rollout) build on
each other in that order. Task 14 (explorer) can go any time after task 6.

---

## Old games' moves

### 1. Bulk export from Lichess
- [x] Check the bulk-export-by-ID endpoint, its batch limit and its rate limits in the Lichess API
      docs. Record what you find in design D1.
- [x] Add `LichessClient.export_games_by_ids(ids) -> list[LichessGame]`, keeping the custom
      User-Agent and the existing rate-limit delay.
- [x] Test it with a mocked HTTP transport: batching at the limit, NDJSON parsing, and IDs missing
      from the response.
- Covers: R1.1

### 2. Move backfill job
- [x] Add `backfill_service.backfill_moves()`: select games with `positions IS NULL`, fetch them in
      batches, replay them, and write `positions` (with null evaluations), `user_moves` and
      `speed`. It runs at the tail of each sync's background task, not as a separate job (see
      design D1 for why).
- [x] Run it from every sync, including one with no new games, and report its progress in
      `/analysis/status` (`backfill_total`, `backfill_done`).
- [x] Note in `AnalyzedGame.positions`' docstring and in CLAUDE.md that `eval_cp` and `best_uci`
      can be null.
- [x] Test: games get their moves, a missing game stays NULL and is logged, a second run fetches
      only what's still missing, and the blunder rate now counts these games.
- Covers: R1.1–R1.3

---

## Tree and repertoire

### 3. Data model and migrations
- [ ] Add the new tables, created by `create_all`: `repertoire_moves`, `opening_explanations`,
      `explanation_reports`, `repertoire_deviations` (with `played_was_theory`), `opening_names`,
      `theory_moves`, `explanation_batches` and `explorer_cache`.
- [ ] Add the ADD COLUMN migrations:
  - `review_cards.repertoire_move_id` and `review_cards.repertoire_deviation_id`, each with its
    unique index
  - `analyzed_games.repertoire_exit_ply` and `repertoire_checked_at`
  - `users.is_owner`
- [ ] Register `"opening": "repertoire_move_id"` and `"deviation": "repertoire_deviation_id"` in
      `CARD_SOURCE_COLUMNS` and in `ReviewCard.source_id`.
- [ ] Migration test (`tests/test_migrations.py` style): an old-schema database gains every column
      and table, and a second run changes nothing.
- Covers: data model for R2–R7

### 4. Opening names and theory moves
- [x] Licence: `lichess-org/chess-openings` is CC0 (checked on the repo, 2026-11-23).
- [ ] Add the five TSV files (`a.tsv`…`e.tsv`) under `app/data/chess-openings/`, with a note of the
      source commit and the licence.
- [ ] At startup, replay each line's `pgn` with python-chess. Fill `opening_names` (the position at
      the end of each line) and `theory_moves` (every move along every line, keyed by the position
      before it, with the shortest named line). Only reload when the files change.
- [ ] Test:
  - well-known positions get the right name and ECO code
  - a transposed move order finds the same name
  - the moves of a known main line are all theory, and a sideline move isn't
- Covers: R2.4, R2.5 (data)

### 5. Tree building and suggestions
- [ ] `repertoire_service.build_tree()` and `suggest()` as in design D3, as pure functions, with
      theory marking and `theory_uci`.
- [ ] Tests on hand-made games covering:
  - both colours
  - the 3-game threshold, and the ply-30 cap
  - a transposition appearing once
  - ties broken by results
  - the "clearly worse" flag with and without stored evaluations
  - an off-book usual move getting a `theory_uci`
- Covers: R2.1–R2.3, R2.5

### 6. Repertoire endpoints
- [ ] Add `GET /repertoire/{color}`, `POST .../accept`, `PUT .../position`, `DELETE .../position`
      and `GET /repertoire/deviations`, all behind `get_current_user`.
- [ ] Accepting creates `repertoire_moves` rows (with `parent_key`) and their `opening` cards in
      tree order. Removing deletes unreachable descendants and their cards. Adding a move by hand
      lists the theory moves from that position first.
- [ ] Test through the API: accept, replace, add a move by hand, remove a subtree, and check that
      cards appear and disappear to match.
- Covers: R3.1–R3.3, R5.1, R5.3

---

## Explanations

### 7. Generating explanations with Claude, in batches
- [ ] Add the `anthropic` dependency, plus `ANTHROPIC_API_KEY` (optional) and `EXPLANATION_MODEL`
      (default `claude-opus-5-5` until task 8 picks one) to `Settings`. Keep `extra = "ignore"`
      working with the shared `.env`.
- [ ] Add `explanation_service`, which builds one request per (position, move) as in design D4:
  - the output schema in `output_config.format`, and effort `medium`
  - the grounding fields, including theory status and the named line
  - a fixed system prompt, with `PROMPT_VERSION`
- [ ] Confirm against the current docs that `output_config.format` is accepted in Batches
      requests. If it isn't, ask for JSON in the prompt instead; validation stays the same.
- [ ] Add `ExplanationJob`:
  - submit a batch at most every 10 minutes and record it in `explanation_batches`
  - poll open batches every 5 minutes, and store succeeded results keyed by `custom_id`
  - put errored and expired items back in the queue
  - retry refused items once as a direct call with the server-side fallback
  - pick up open batches after a restart
- [ ] Tests with the Anthropic client stubbed:
  - the batch request shape: model, schema, effort and grounding fields
  - results in a shuffled order are stored against the right positions
  - a refusal is retried once, then left without an explanation
  - an invalid result is treated as failed
  - no key means no call and no error
  - one explanation per (position, move) across two users
  - a restart resumes an open batch
- [ ] **No real API calls in tests or CI.**
- Covers: R4.1, R4.2, R4.6

### 8. Benchmark the explanation model
- [ ] Add `scripts/benchmark_explanations.py` as in design D4. It runs 50 of the owner's accepted
      positions through `claude-opus-5-5`, `claude-sonnet-5-5` and `claude-haiku-5-5` in one
      batch, and writes a blind comparison sheet with the cost per model.
- [ ] **Run it only with the owner's go-ahead** (it costs about $1). The owner rates the sheet, and
      the chosen model goes into `EXPLANATION_MODEL` and the design.
- Covers: owner decision 1 (2026-11-23)

### 9. Owner notes and reports
- [ ] Add `PUT /openings/explanations` (owner only), `POST .../{id}/report` and
      `GET .../reported` (owner only). The explanation lookup prefers `owner` over `claude`.
- [ ] Test: owner text replaces Claude's, non-owners get 403 on owner endpoints, and the same user
      reporting twice counts once.
- Covers: R4.3, R4.4

---

## Review

### 10. Opening and punish cards in the review queue
- [ ] Add the `opening` payload to `ReviewCardResponse`: the line from `parent_key`, the FEN, the
      answer, the name and the explanation (owner text first). Add the payload for `deviation`
      punish cards as in design D6.
- [ ] Load the repertoire move or deviation for these cards. Check the queue serves blunder,
      opening and punish cards together, with new opening items in line order.
- [ ] Test: a mixed queue, line order of new items, and the payload contents for a transposed
      position.
- Covers: R5.1–R5.3, R6.5

### 11. Frontend
- [ ] Types and `api.ts` for the repertoire endpoints and the new payloads.
- [ ] `ReviewPanel`: render `kind === 'opening'` with line replay, the explanation before the first
      attempt and after later ones, grading, and the report link. Render `kind === 'deviation'`
      with the blunder-punish presentation.
- [ ] New `'repertoire'` view:
  - White/Black tabs, the tree, and a board
  - theory / off-book marks, with the theory move next to an off-book one
  - accept / replace / remove controls
  - the explanation, editable for the owner
  - the explorer panel when it has data
  - the deviations list
- [ ] `tsc --noEmit` and `npm run build` pass. Check it in the browser on a scratch copy of the
      database, using a test account, as in Phase 1.
- Covers: R2.5, R2.6 (UI), R3, R4.5, R5.2, R6.4

---

## Deviations and measuring

### 12. Checking games against the repertoire
- [ ] Add `repertoire_service.first_deviation()` as in design D6, as a pure function.
- [ ] Run it after each analyzed game, after each backfill batch, and over all of a user's games on
      their first acceptance. Write `repertoire_deviations` (with `played_was_theory`),
      `repertoire_exit_ply` and `repertoire_checked_at`. A user deviation makes that card due now.
      An opponent deviation with a `punish_uci` creates its punish card, once.
- [ ] Tests on hand-made games:
  - a user deviation, at the right ply
  - an opponent deviation, with and without a `punish_uci`, and its punish card
  - a game past the prepared part, with no deviation
  - a game with no stored evaluations
  - the theory flag on the deviating move
  - a user deviation makes the card due without changing ease or interval
- Covers: R6.1–R6.5

### 13. Repertoire depth on the dashboard
- [ ] Add `GET /stats/repertoire-depth` in `progress_service`, plus a dashboard chart with the same
      "not enough games yet" rule as the blunder rate.
- [ ] Test the weekly figures against hand-made games.
- Covers: R7

---

## Optional extra

### 14. Lichess explorer panel
- [ ] Add `LICHESS_API_TOKEN` (optional) to `Settings`.
- [ ] Add `LichessClient.explorer()` and the rating band lookup, with the cache and the
      failure-means-hidden behaviour from design D8.
- [ ] Add an endpoint for the repertoire view, e.g. `GET /repertoire/explorer?fen=`.
- [ ] Test with a mocked transport: a cache hit makes no call, no token means no call, and a
      timeout, 401 or 429 each give "no data".
- Covers: R2.6

---

## Ship

### 15. Rollout
- [ ] The owner sets `ANTHROPIC_API_KEY`, and optionally `LICHESS_API_TOKEN`, in the VPS `.env`.
      Never commit them. Set `is_owner` for the owner's account by hand.
- [ ] Back up the production database, then test the migrations on a copy, as in Phase 1.
- [ ] Merge through a PR. After the deploy:
  - check the backfill job filled in the old games' moves
  - check a first acceptance produces cards, and its explanation batch completes
  - compare the cost of the first batch with the estimate in design D4
- [ ] Update CLAUDE.md and AGENTS.md (the new services and tables, the new card kinds, and
      nullable evaluations), and tick off Phase 2 in the roadmap.
