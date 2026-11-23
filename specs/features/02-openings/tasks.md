# Phase 2 — Openings: Tasks

Requirements: [requirements.md](requirements.md) · Design: [design.md](design.md)

Work test-first, then run `make test`, `make lint` (no new findings in touched files), and for
frontend work `npx tsc --noEmit` plus `npm run build`. Every change goes through a PR against
`main`.

Order: tasks 1–2 (old games' moves) don't depend on the rest. Tasks 3–6 (tree and repertoire),
then 7–8 (explanations), 9–10 (review), 11–12 (deviations and measuring) and 13 (rollout) build on
each other in that order.

---

## Old games' moves

### 1. Bulk export from Lichess
- [ ] Check the bulk-export-by-ID endpoint, its batch limit and its rate limits in the Lichess API
      docs. Record what you find in design D1.
- [ ] Add `LichessClient.export_games_by_ids(ids) -> list[LichessGame]`, keeping the custom
      User-Agent and the existing rate-limit delay.
- [ ] Test it with a mocked HTTP transport: batching at the limit, NDJSON parsing, and IDs missing
      from the response.
- Covers: R1.1

### 2. Move backfill job
- [ ] Add `MoveBackfillJob`: select games with `positions IS NULL`, fetch them in batches, replay
      them, and write `positions` (with null evaluations), `user_moves` and `speed`. Track it in
      `app.state` like `AnalysisJob`.
- [ ] Start it after a sync when such games exist, and add a status endpoint (or include it in
      `/analysis/status`).
- [ ] Note in `AnalyzedGame.positions`' docstring and in CLAUDE.md that `eval_cp` and `best_uci`
      can be null.
- [ ] Test: games get their moves, a missing game stays NULL and is logged, a second run fetches
      nothing, and the blunder rate now counts these games.
- Covers: R1.1–R1.3

---

## Tree and repertoire

### 3. Data model and migrations
- [ ] Add the `repertoire_moves`, `opening_explanations`, `explanation_reports`,
      `repertoire_deviations` and `opening_names` tables (created by `create_all`).
- [ ] Add the ADD COLUMN migrations: `review_cards.repertoire_move_id` with its unique index,
      `analyzed_games.repertoire_exit_ply` and `repertoire_checked_at`, and `users.is_owner`.
- [ ] Register `"opening": "repertoire_move_id"` in `CARD_SOURCE_COLUMNS` and in
      `ReviewCard.source_id`.
- [ ] Migration test (`tests/test_migrations.py` style): an old-schema database gains every column
      and table, and a second run changes nothing.
- Covers: data model for R2–R7

### 4. Opening names
- [ ] Confirm the licence of the `lichess-org/chess-openings` files on GitHub. Stop and ask the
      owner if it isn't CC0 or similar.
- [ ] Add the dataset as one file under `app/data/`, and load it into `opening_names` at startup,
      keyed by `board.epd()` after replaying each line. Only reload when the file changes.
- [ ] Test: well-known positions get the right name and ECO code, and a transposed move order
      finds the same name.
- Covers: R2.4

### 5. Tree building and suggestions
- [ ] `repertoire_service.build_tree()` and `suggest()` as in design D3, as pure functions.
- [ ] Tests on hand-made games covering:
  - both colours
  - the 3-game threshold, and the ply-30 cap
  - a transposition appearing once
  - ties broken by results
  - the "clearly worse" flag with and without stored evaluations
- Covers: R2.1–R2.3

### 6. Repertoire endpoints
- [ ] Add `GET /repertoire/{color}`, `POST .../accept`, `PUT .../position`, `DELETE .../position`
      and `GET /repertoire/deviations`, all behind `get_current_user`.
- [ ] Accepting creates `repertoire_moves` rows (with `parent_key`) and their `opening` cards in
      tree order. Removing deletes unreachable descendants and their cards.
- [ ] Test through the API: accept, replace, add a move by hand, remove a subtree, and check that
      cards appear and disappear to match.
- Covers: R3.1–R3.3, R5.1, R5.3

---

## Explanations

### 7. Generating explanations with Claude
- [ ] Add the `anthropic` dependency, plus `ANTHROPIC_API_KEY` (optional) and `EXPLANATION_MODEL`
      (default `claude-opus-5-5`) to `Settings`. Keep `extra = "ignore"` working with the shared
      `.env`.
- [ ] Add `explanation_service`, with the call as in design D4:
  - `messages.parse` with the output schema, effort `medium`, and the server-side fallback
  - a refusal check before reading the content
  - a fixed system prompt, with `PROMPT_VERSION`
- [ ] Add `ExplanationJob` (two calls at a time), queued when positions are accepted. It skips
      positions that already have any explanation.
- [ ] Tests with the Anthropic client stubbed:
  - the request shape: model, schema, effort, fallback and the grounding fields
  - a refusal stores nothing
  - no key means no call and no error
  - one call per (position, move) across two users
- [ ] **No real API calls in tests or CI.** One manual run against the real API on 5 positions,
      with the cost noted in the PR, before rollout.
- Covers: R4.1, R4.2, R4.6

### 8. Owner notes and reports
- [ ] Add `PUT /openings/explanations` (owner only), `POST .../{id}/report` and
      `GET .../reported` (owner only). The explanation lookup prefers `owner` over `claude`.
- [ ] Test: owner text replaces Claude's, non-owners get 403 on owner endpoints, and the same user
      reporting twice counts once.
- Covers: R4.3, R4.4

---

## Review

### 9. Opening cards in the review queue
- [ ] Add the `opening` payload to `ReviewCardResponse`: the line from `parent_key`, the FEN, the
      answer, the name and the explanation, using the owner text first.
- [ ] Load the repertoire move for opening cards. Check the queue serves blunder and opening cards
      together, with new opening items in line order.
- [ ] Test: a mixed queue, line order of new items, and the payload contents for a transposed
      position.
- Covers: R5.1–R5.3

### 10. Frontend
- [ ] Types and `api.ts` for the repertoire endpoints and the `opening` payload.
- [ ] `ReviewPanel`: render `kind === 'opening'` with line replay, the explanation before the first
      attempt and after later ones, grading, and the report link.
- [ ] New `'repertoire'` view: White/Black tabs, the tree, a board, accept/replace/remove controls,
      the explanation (editable for the owner), and the deviations list.
- [ ] `tsc --noEmit` and `npm run build` pass. Check it in the browser on a scratch copy of the
      database, using a test account, as in Phase 1.
- Covers: R3, R4.5, R5.2, R6.4

---

## Deviations and measuring

### 11. Checking games against the repertoire
- [ ] Add `repertoire_service.first_deviation()` as in design D6, as a pure function.
- [ ] Run it after each analyzed game, after each backfill batch, and over all of a user's games on
      their first acceptance. Write `repertoire_deviations`, `repertoire_exit_ply` and
      `repertoire_checked_at`. A user deviation makes that card due now.
- [ ] Tests on hand-made games:
  - a user deviation, at the right ply
  - an opponent deviation, with and without a `punish_uci`
  - a game past the prepared part, with no deviation
  - a game with no stored evaluations
  - a user deviation makes the card due without changing ease or interval
- Covers: R6.1–R6.4

### 12. Repertoire depth on the dashboard
- [ ] Add `GET /stats/repertoire-depth` in `progress_service`, plus a dashboard chart with the same
      "not enough games yet" rule as the blunder rate.
- [ ] Test the weekly figures against hand-made games.
- Covers: R7

---

## Ship

### 13. Rollout
- [ ] Set `ANTHROPIC_API_KEY` in the VPS `.env`. The owner does this; never commit it. Set
      `is_owner` for the owner's account by hand.
- [ ] Back up the production database, then test the migrations on a copy, as in Phase 1.
- [ ] Merge through a PR. After the deploy:
  - check the backfill job filled in the old games' moves
  - check a first acceptance produces cards and explanations
  - check the cost of the first explanations against the estimate in design D4
- [ ] Update CLAUDE.md and AGENTS.md (the new services and tables, and nullable evaluations), and
      tick off Phase 2 in the roadmap.
