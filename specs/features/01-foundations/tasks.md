# Phase 1 — Foundations: Tasks

Requirements: [requirements.md](requirements.md) · Design: [design.md](design.md)

Work test-first: write the failing test, make it pass, then run `make test` and `make lint`.
Each task should end in a state that can be committed. Tasks 1–5 (cards and queue) and 6–7 (move
data) don't depend on each other. Task 8 needs 6 and 7. Task 9 needs 8.

Approaches D1 and D3 were approved on 2026-11-23.

---

## Cards and queue

### 1. Migration test harness
- [x] Add a test that creates a database with the **current** `review_cards` schema (copy the DDL
      into the test), inserts users, games, blunders, two cards per blunder (one with no punish
      card) and review logs, then runs `create_tables()`.
- [x] Check: row counts are unchanged, card IDs and scheduling fields are identical,
      `kind == 'blunder'`, `created_at` equals the blunder's `created_at`, `review_logs` still
      join to their cards, and running it again changes nothing.
- [x] Use a separate temporary database file, not the shared `tests/test_blundr.db`, so the shared
      `TestClient` setup in `conftest.py` is untouched.
- Covers: R1.2, R1.3

### 2. Rebuild `review_cards` (D1)
- [x] Update the `ReviewCard` model: add `kind`, `step` (default 0) and `created_at`, make
      `blunder_id` nullable, widen `card_type` to 20 characters, and widen the unique constraint
      to `(blunder_id, card_type, step)`.
- [x] Add a test that two steps of the same item are scheduled separately (R1.5).
- [x] Add the rebuild migration to `db.create_tables()`, triggered when `kind` is missing. Include
      the row-count check and the rollback if it fails.
- [x] Add `ReviewCard.item_key` and a `new_card(...)` helper that checks exactly one source is set.
      Use the helper in `run_analysis_job` where cards are created.
- [x] Task 1's test passes.
- Covers: R1.1–R1.4

### 3. Generalize the queue helpers (D2)
- [x] Rename and generalize `_blunders_started_today`, `_fresh_blunder_ids` and
      `_fresh_cards_stmt` to work on item keys. The key expression is built in one function.
- [x] Add `kind` to `ReviewCardResponse` and make `blunder` optional.
- [x] Add a test that inserts a fake second kind (a card with `kind='test'` and no `blunder_id`,
      using a test-only source column or a stubbed key function). Show that each item uses one
      slot of the daily limit and that its cards come out next to each other.
- [x] The existing review tests pass unchanged, apart from the added `kind` field.
- Covers: R2.1–R2.4

### 4. Stats on the new card model
- [x] `/stats/overview`: group mastery by item key, and keep `blunders_mastered` limited to
      blunders.
- [x] `/stats/timeline`: read `ReviewCard.created_at` and drop the join to `Blunder`.
- [x] Add a test checking that `/reviews/stats`, `/stats/overview` and `/reviews/due` agree on the
      due and new counts.
- Covers: R2.5

### 5. Frontend: handle the new `kind` field
- [x] `types`: add `kind` to `ReviewCardInfo`, and make `blunder` optional.
- [x] `ReviewPanel`: render by `kind`, and skip a card with an unknown kind rather than crash.
- [x] `cd frontend && npm run build` passes, and a manual review session works as before.
- Covers: R2.4

---

## Move data and blunder rate

### 6. Keep the move data from the analysis (D3)
- [x] Add the `AnalyzedGame` columns `positions`, `user_moves`, `speed` and `initial_eval_cp`, plus
      the ADD COLUMN migration.
- [x] Parse `speed` in `lichess_client._parse_game_data` if it isn't parsed already, and add it to
      `LichessGame`.
- [x] `detect_blunders` builds the list of moves during its existing loop and returns it together
      with the blunders. `run_analysis_job` saves it.
- [x] Test with the stubbed engine: the number of `engine.analyse` calls doesn't change, the
      stored moves replay legally from the starting position, `user_moves` is correct for both
      colors, and a game that stops early because of a move that can't be replayed stores the
      moves up to that point.
- Covers: R3.1–R3.4

### 7. Game-phase helper
- [x] Add `app/services/game_phase.py` with `game_phase(board, ply) -> Literal["opening",
      "middlegame", "endgame"]`, using the thresholds in the design.
- [x] Unit-test it on hand-picked positions: the starting position, a typical middlegame, a rook
      ending, a queen-less middlegame, and the boundary cases.

### 8. Blunder-rate endpoint (D4)
- [ ] `GET /stats/blunder-rate` with the `weeks`, `phase` and `speed` filters. Weeks with no games
      come back with zeros and `rate: null`, and games with no move data are skipped.
- [ ] Test it against hand-made games with known blunder plies, across two weeks, both colors,
      every phase and a mix of speeds.
- Covers: R4.1–R4.3

### 9. Dashboard chart
- [ ] Add `api.ts` and type support for the new endpoint.
- [ ] In `Dashboard.tsx`, add the rate chart (reusing `ComboChart`) with a phase filter and a
      "not enough games yet" state.
- [ ] `npm run build` passes, and the chart shows real data from a locally synced account.
- Covers: R4.4

---

## Ship

### 10. Production rollout
- [ ] Take a backup of the production `blundr.db` from the VPS.
- [ ] Run the backend locally on a **copy** of that backup, and check that the migration finishes
      and a review session works.
- [ ] Update the "What this is" and data-model sections of `CLAUDE.md` and `AGENTS.md` for the new
      card model, and point them to `specs/`.
- [ ] Push to `main`, which deploys. Check the backend logs for the migration, and do one review on
      blundr.ch.
- [ ] Tick off Phase 1 in `specs/roadmap.md`.
