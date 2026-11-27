# Sign in with Lichess Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lichess OAuth becomes Blundr's only sign-in. The user's Lichess token is kept
encrypted on the server and used for their Lichess requests.

**Architecture:**
- The server runs OAuth 2 with PKCE against lichess.org (`/api/auth/lichess/start` and
  `/callback`), stores the token Fernet-encrypted on `users`, and hands the browser Blundr's JWT
  in a URL fragment.
- Password accounts, resets and SMTP are removed.
- A one-time migration deletes the old accounts and rebuilds `users`.

**Tech Stack:** FastAPI, async SQLAlchemy/aiosqlite, httpx, `cryptography` (Fernet), pyjwt,
React 18 + TS.

**Spec:** [design.md](design.md)

## Global Constraints

- The Lichess token never reaches the browser, a log line or a URL. Only Blundr's JWT goes in the
  `/#auth=` fragment.
- OAuth: `client_id` = `LICHESS_OAUTH_CLIENT_ID` (default `"blundr.ch"`), PKCE `S256`, no `scope`
  parameter, `redirect_uri` = `f"{PUBLIC_URL}/api/auth/lichess/callback"`.
- The state cookie is named `lichess_oauth`: HttpOnly, SameSite=Lax, `Secure` when `PUBLIC_URL`
  starts with `https://`, path `/api/auth/lichess`, max-age 600, value signed with `JWT_SECRET`.
- Error codes in `/#auth_error=<code>`: `denied` (the user clicked Deny), `state` (missing or
  mismatched state), `lichess` (exchange or account call failed).
- New settings, each also in `docker-compose.yml`'s `environment:` block:
  `PUBLIC_URL="http://localhost:3000"`, `LICHESS_OAUTH_CLIENT_ID="blundr.ch"`,
  `LICHESS_TOKEN_KEY=""`, `OWNER_LICHESS_USERNAME=""`.
- Tests make no real network calls. Lichess is faked with `httpx.MockTransport`.
- One shared `TestClient` (`tests/conftest.py`). Don't add another.
- Migrations follow `app/db.py`'s manual pattern. The users rebuild follows
  `_rebuild_review_cards()`'s order: create new, copy, check the row count, drop old, rename new.
- The production database is backed up, and the owner says go, before the PR with Task 4 merges.
- Every change goes on a branch and a PR with Auto-fix and auto-merge. Never push to `main`.

## Review Focus

1. **A callback replayed or opened without the cookie** (another browser, an expired cookie):
   redirect with `auth_error=state` and create no user. Test in Task 3.
2. **A Lichess username whose case changed since last time:** the same user, by Lichess ID, with
   the stored username updated, never a second account. Test in Task 3.
3. **`LICHESS_TOKEN_KEY` empty in production:** `/start` answers 503 "Sign-in isn't configured",
   rather than storing unencrypted tokens or failing in the callback. Test in Task 2.
4. **A stored token Lichess rejects (401) during sync:** the token is cleared, sync reports
   "Sign in with Lichess again", other users are unaffected, and `/auth/me` says
   `lichess_connected: false`. Test in Task 5.
5. **The migration on a database already migrated, or on a fresh one:** it does nothing and
   deletes nothing. Test in Task 4.

---

### Task 1: Test users without the register route

Tests create users through `/api/auth/register` and `/api/auth/login`, which Task 4 removes.
Switch them to a helper first, while the old routes still exist.

**Files:**
- Create: `tests/helpers.py`
- Modify: every test that calls `_register` or `/api/auth/login`: `tests/test_blundr_features.py`,
  `test_blunder_rate.py`, `test_move_backfill.py`, `test_move_data.py`, `test_owner_notes.py`,
  `test_repertoire_api.py`, `test_repertoire_depth.py`, `test_review_payloads.py`

**Interfaces:**
- Produces: `make_user(username: str, *, lichess_username: str | None = None, is_owner: bool = False) -> dict[str, str]`.
  It inserts a `users` row with the sync `sqlite3` connection to the test DB (the way
  `test_repertoire_api._user_with_games` already writes games) and returns
  `{"Authorization": f"Bearer {create_access_token(id)}"}`. `lichess_username` defaults to
  `f"{username}_li"`. Usernames must be unique per call site, as today.
- Also `user_id(headers: dict) -> int`, which reads `/api/auth/me`.

- [ ] **Step 1:** Write `tests/helpers.py`. Until Task 4 the row also needs
  `password_hash="x"`; leave a `# removed in Task 4` comment on that line.
- [ ] **Step 2:** Replace every `_register(...)` + login pair outside `TestAuth` and
  `TestPasswordReset` with `make_user(...)`. Those two classes stay until Task 4.
- [ ] **Step 3:** Run `uv run pytest -q`. Expected: same pass count as on `main`, 0 failures.
- [ ] **Step 4:** Commit `"Tests: create users directly, not through /auth/register"`.

### Task 2: Settings, token encryption and PKCE helpers

**Files:**
- Modify: `pyproject.toml` / `uv.lock` (`uv add cryptography`), `app/config.py`,
  `docker-compose.yml`, `tests/conftest.py`
- Create: `app/services/lichess_auth.py`
- Test: `tests/test_lichess_auth.py`

**Interfaces:**
- Produces, in `app/services/lichess_auth.py`:
  - `sign_in_configured() -> bool`: true when `LICHESS_TOKEN_KEY` is a valid Fernet key.
  - `encrypt_token(token: str) -> str` and `decrypt_token(enc: str) -> str | None` (None on a bad
    or foreign ciphertext, never raises).
  - `new_pkce() -> tuple[str, str]`: `(verifier, challenge)`, with a 64-character URL-safe
    verifier and `challenge = base64url(sha256(verifier))` without padding.
  - `authorize_url(state: str, challenge: str) -> str`.
  - `REDIRECT_PATH = "/api/auth/lichess/callback"`.
- `conftest.py` sets `LICHESS_TOKEN_KEY` to a fixed test Fernet key and `PUBLIC_URL` to
  `http://testserver`.

- [ ] **Step 1: Failing tests.** In `tests/test_lichess_auth.py`:
  - `test_token_round_trip`: `decrypt_token(encrypt_token("lip_abc")) == "lip_abc"`, and the
    ciphertext doesn't contain `"lip_abc"`;
  - `test_decrypt_garbage_is_none`: `decrypt_token("nope") is None`;
  - `test_pkce_challenge_matches_rfc7636`: for the RFC 7636 example verifier
    `"dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"`, the challenge is
    `"E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"`;
  - `test_authorize_url`: parsed query has `response_type=code`, `client_id=blundr.ch`,
    `redirect_uri=http://testserver/api/auth/lichess/callback`, `code_challenge_method=S256`,
    `state` and `code_challenge` as given, and no `scope` key.
- [ ] **Step 2:** Run `uv run pytest tests/test_lichess_auth.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3:** Implement it, and add the four settings to `config.py` and `docker-compose.yml`
  (`${PUBLIC_URL:-https://blundr.ch}` in compose, other defaults empty, client ID `blundr.ch`).
  Expose the PKCE verifier helper with an optional argument so the RFC test can pass a fixed
  verifier, e.g. `challenge_for(verifier: str) -> str`.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `"Lichess sign-in: settings, token encryption, PKCE"`.

### Task 3: The sign-in routes

**Files:**
- Modify: `app/models/db_models.py` (User columns), `app/db.py` (`_add_missing_columns` for
  them), `app/routes/auth.py`, `app/main.py` (`app.state.oauth_http`)
- Test: `tests/test_lichess_signin.py`

**Interfaces:**
- New nullable `users` columns:
  - `auth_provider VARCHAR(20)`;
  - `provider_user_id VARCHAR(64)`, with a unique index `ix_users_provider` on
    `(auth_provider, provider_user_id)`;
  - `lichess_token_enc TEXT`;
  - `lichess_token_expires_at DATETIME`;
  - `lichess_ratings TEXT` (JSON `{"blitz": 1650, "rapid": 1720, ...}` from the account's `perfs`,
    each perf's `rating`; the builder plan reads it).
- `app.state.oauth_http`: an `httpx.AsyncClient` with the Blundr User-Agent and **no** default
  Authorization header, created in the lifespan and closed on shutdown. A route dependency
  `get_oauth_http(request) -> httpx.AsyncClient` returns it, and tests override it with
  `app.dependency_overrides`.
- Routes:
  - `GET /api/auth/lichess/start`: 503 when `not sign_in_configured()`; otherwise a 302 to
    `authorize_url(...)` and sets the cookie.
  - `GET /api/auth/lichess/callback?code=&state=&error=`: 302 to `/#auth=<jwt>` or
    `/#auth_error=<code>`; always deletes the cookie.
- Lichess calls in the callback:
  - `POST https://lichess.org/api/token`, form-encoded: `grant_type=authorization_code`, `code`,
    `code_verifier`, `redirect_uri`, `client_id`. It returns JSON `access_token`, `expires_in`.
  - `GET https://lichess.org/api/account` with `Authorization: Bearer <access_token>`. It returns
    `id`, `username`, `perfs`.
- User upsert, by `(auth_provider="lichess", provider_user_id=id.lower())`:
  - set `username` and `lichess_username` to the account's `username`;
  - set the token columns (`expires_at = now + expires_in`, or NULL when it's absent) and
    `lichess_ratings`;
  - set `is_owner = (OWNER_LICHESS_USERNAME != "" and username.lower() == OWNER_LICHESS_USERNAME.lower())`;
  - until Task 4, a new row also gets `password_hash=""`.

- [ ] **Step 1: Failing tests,** in `tests/test_lichess_signin.py`, with a `MockTransport` that
  answers `/api/token` and `/api/account`:
  - `test_start_redirects_to_lichess_and_sets_cookie`: 302, Location host `lichess.org`, cookie
    `lichess_oauth` set and HttpOnly.
  - `test_start_503_when_not_configured`: with `LICHESS_TOKEN_KEY` patched to `""`, 503.
  - `test_callback_creates_user`: start, then the callback with the cookie's state. Location
    starts with `/#auth=`, `/auth/me` with that JWT returns `lichess_username == "Alice"`, and
    the DB row's `lichess_token_enc` decrypts to the mock's token and doesn't contain it in
    plain text.
  - `test_callback_same_lichess_id_same_user`: a second sign-in where the account says
    `"ALICE"` gives the same user ID, with the username updated (Review Focus 2).
  - `test_callback_bad_state`: a wrong state, and no cookie at all. Both give `#auth_error=state`
    and the user count is unchanged (Review Focus 1).
  - `test_callback_denied`: `?error=access_denied&state=…` gives `#auth_error=denied`.
  - `test_callback_lichess_failure`: the token endpoint returns 400, giving `#auth_error=lichess`.
  - `test_owner_flag`: with `OWNER_LICHESS_USERNAME="alice"`, `/auth/me` has `is_owner: true`;
    another user's is false.
  - `test_token_never_in_response`: no response body or Location header in the flow contains the
    Lichess token.
- [ ] **Step 2:** Run `uv run pytest tests/test_lichess_signin.py -q`. Expected: FAIL (404 on the
  routes).
- [ ] **Step 3:** Implement the columns, migration, client and routes. Sign the cookie with
  `jwt.encode({"state", "verifier", "exp"}, JWT_SECRET)` so it expires on its own after 10 min.
- [ ] **Step 4:** Run the file, then `uv run pytest -q`. Expected: all pass.
- [ ] **Step 5:** Commit `"Sign in with Lichess: OAuth routes"`.

### Task 4: Remove passwords and the old accounts

**Files:**
- Modify: `app/models/db_models.py` (drop `password_hash`, `email`, `PasswordResetToken`),
  `app/db.py`, `app/routes/auth.py` (drop register/login/forgot/reset), `app/routes/settings.py`
  (drop email and `lichess_username` edits), `app/models/schemas.py` (drop
  `RegisterRequest`/`LoginRequest`/`ForgotPasswordRequest`/`ResetPasswordRequest` and the email
  fields), `app/services/auth_service.py` (drop bcrypt and reset helpers), `app/config.py` (drop
  SMTP_* and `VALIDATE_LICHESS_ACCOUNT`), `docker-compose.yml`, `pyproject.toml` (drop `bcrypt`
  if unused), `tests/conftest.py`, `tests/helpers.py`
- Delete: `app/services/email_service.py`, `TestAuth`'s register/login tests and
  `TestPasswordReset` in `tests/test_blundr_features.py`. Keep the `/auth/me` tests on
  `make_user`.
- Test: `tests/test_migrations.py`

**Interfaces:**
- Produces: `_migrate_to_lichess_accounts(conn) -> None` in `app/db.py`, called from
  `create_tables()` after `create_all`. It runs only when `PRAGMA table_info(users)` lists
  `password_hash`:
  1. With foreign keys off, delete every row of every table that has a foreign key to `users`,
     and of their child tables (`blunders` via `analyzed_games`; `review_logs` via
     `review_cards`), children first. Find them from `PRAGMA foreign_key_list` rather than a
     hard-coded list, so a forgotten table can't survive.
  2. Drop `password_reset_tokens`.
  3. Rebuild `users` in the new shape (new table, copy, check count, drop old, rename). There's
     nothing to copy, but keep the order.
  4. Write `app_meta` key `lichess_accounts_migrated`.
- Shared tables (`opening_names`, `theory_moves`, `engine_lines`, `opening_explanations`,
  `explorer_cache`, `explanation_batches`, `app_meta`) are untouched, except that
  `opening_explanations` has no user FK. Check it with the test.

- [ ] **Step 1: Failing tests,** in `tests/test_migrations.py`, following its old-schema pattern:
  - `test_lichess_migration_wipes_accounts_keeps_shared`: build the pre-migration schema with
    `password_hash`, two users with games, blunders, cards, review logs, repertoire moves,
    deviations, an explanation report and a reset token, plus rows in `engine_lines`,
    `opening_explanations` and `explorer_cache`. After `create_tables()`: zero rows in every
    user-owned table, `password_reset_tokens` gone, `users` without `password_hash`/`email` and
    with `provider_user_id`, and the shared rows still there.
  - `test_lichess_migration_is_a_no_op_when_done`: run `create_tables()` twice on a migrated DB
    that has a new-style user with a game. The user and game are still there (Review Focus 5).
- [ ] **Step 2:** Run `uv run pytest tests/test_migrations.py -q`. Expected: FAIL.
- [ ] **Step 3:** Implement the migration and remove the password code listed above.
  `grep -rn "password\|email\|SMTP\|VALIDATE_LICHESS" app tests` must find only intended
  survivors.
- [ ] **Step 4:** Run `uv run pytest -q` and `make lint`. Expected: all pass, lint clean.
- [ ] **Step 5:** Commit `"Remove password accounts; migrate users to Lichess sign-in"`.

### Task 5: Use the user's token for their Lichess requests

**Files:**
- Modify: `app/services/lichess_client.py`, `app/services/lichess_auth.py`,
  `app/routes/analysis.py` (sync), `app/services/backfill_service.py`, `app/routes/auth.py`
  (`/me`), `app/models/schemas.py` (`UserResponse.lichess_connected: bool`)
- Test: `tests/test_lichess_user_token.py`

**Interfaces:**
- `user_lichess_token(user: User) -> str | None`, in `lichess_auth.py`: None when there's no
  token, it's past `lichess_token_expires_at`, or decryption fails.
- `LichessClient.get_user_games(..., token: str | None = None)` and
  `export_games_by_ids(..., token: str | None = None)`: when given, send
  `Authorization: Bearer <token>`, overriding the client default.
- A new exception, `LichessAuthError(LichessAPIError)`, raised by `_make_request` on a 401.
- `async def forget_lichess_token(db, user) -> None`: clears `lichess_token_enc` and
  `lichess_token_expires_at`, then commits.
- Sync: on `LichessAuthError` with the user's token, call `forget_lichess_token` and set the
  job's error to `"Your Lichess sign-in has ended. Sign in with Lichess again."`.
- `/auth/me` returns `lichess_connected = user_lichess_token(user) is not None`.

- [ ] **Step 1: Failing tests:**
  - `test_sync_sends_user_token`: a fake transport records the Authorization header of a sync
    for a user with a stored token, and it equals `"Bearer <their token>"`.
  - `test_sync_401_forgets_token`: the fake returns 401. The user's token columns are cleared,
    `/auth/me` gives `lichess_connected: false`, the job error is the message above, and a second
    user's token is untouched (Review Focus 4).
  - `test_expired_token_not_used`: with `lichess_token_expires_at` in the past,
    `user_lichess_token` is None and the request goes without the user header.
- [ ] **Step 2:** Run the file. Expected: FAIL.
- [ ] **Step 3:** Implement it. Inject the fake by swapping `app.state.lichess_client.client`
  for an `httpx.AsyncClient(transport=MockTransport(...))` inside the test, and restore it in
  `finally`.
- [ ] **Step 4:** Run `uv run pytest -q`. Expected: all pass.
- [ ] **Step 5:** Commit `"Send each user's Lichess token on their own requests"`.

### Task 6: Frontend sign-in

**Files:**
- Modify: `frontend/src/App.tsx`, `frontend/src/components/LandingPage.tsx`,
  `frontend/src/services/api.ts`, `frontend/src/types/index.ts`,
  `frontend/src/components/SettingsView.tsx`, `frontend/src/styles/index.css`
- Delete: `frontend/src/components/AuthForm.tsx`

**Interfaces:**
- Consumes: `/api/auth/lichess/start`, the `#auth` and `#auth_error` fragments, and
  `UserResponse.lichess_connected`.
- Landing page: one button, "Sign in with Lichess", a plain link to `/api/auth/lichess/start`.
  Under it: "Blundr reads your public games. It never posts or plays for you."
- On load, `App.tsx` reads `location.hash` once:
  - `#auth=<jwt>`: `setToken`, then strip the hash with `history.replaceState`;
  - `#auth_error=<code>`: show one line, then strip the hash. The lines:
    - `denied`: "You cancelled signing in on Lichess."
    - `state`: "That sign-in link expired. Please try again."
    - `lichess`: "Lichess didn't answer. Please try again in a minute."
- Remove the `?reset_token=` handling, the register/login/forgot/reset API functions, and
  Settings' email, Lichess-username and password fields. The Lichess username is shown read-only.
- When `user.lichess_connected` is false, a banner above the views: "Your Lichess sign-in has
  ended." plus the same button, "Sign in with Lichess again".

- [ ] **Step 1:** Implement it.
- [ ] **Step 2:** Run `cd frontend && npm run build`. Expected: no type errors.
- [ ] **Step 3: Browser check on local dev,** using `.claude/launch.json`'s backend with a local
  `LICHESS_TOKEN_KEY` and `PUBLIC_URL=http://localhost:3000`. Click the button; the owner
  approves on lichess.org in the browser pane. Expect to land signed in, with no hash left in the
  URL. Then check Deny, and a reload of an old callback URL, which shows the "expired" line.
- [ ] **Step 4: The explorer token check** that the builder spec needs. With the signed-in user's
  stored token, call `https://explorer.lichess.ovh/lichess?fen=<start>&speeds=blitz&ratings=1600`
  once from a short local script that reads the token from the local DB without printing it.
  Expect 200 and record it in `specs/features/02-openings/repertoire-builder.md` (the "Checked by
  hand" line). If it's 401, stop and tell the owner: the builder's source has to change.
- [ ] **Step 5:** Commit `"Frontend: sign in with Lichess"`.

### Task 7: Docs and rollout

**Files:**
- Modify: `CLAUDE.md` (the `auth_service`/`email_service` lines become Lichess sign-in), `README.md`
  (env vars, the SMTP caveat removed), `specs/features/lichess-sign-in/design.md` (builder notes,
  if any)

- [ ] **Step 1:** Update the docs. Run `uv run pytest -q`; expected: all pass.
- [ ] **Step 2:** Commit `"Docs: Lichess sign-in"`, open the PR, and turn on Auto-fix. Ask the
  owner before turning on auto-merge, because merging runs the account-deleting migration.
- [ ] **Step 3: Rollout, owner steps, before merge:**
  - Generate a Fernet key and set `LICHESS_TOKEN_KEY` in `/root/blundr/.env` on the VPS. The owner
    pastes it there, never in chat. A command they can run:
    `uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
  - Set `PUBLIC_URL=https://blundr.ch` and `OWNER_LICHESS_USERNAME`.
  - Back up the production DB per CLAUDE.md, then the owner merges.
- [ ] **Step 4: After deploy, read-only checks:** `users` has 0 rows and no `password_hash`,
  `engine_lines` and `opening_explanations` counts match the backup, and signing in on
  blundr.ch works.
