# Sign in with Lichess (design)

Drafted 2026-11-27 with the owner. Lichess becomes the only way to sign in to Blundr. Email and
password accounts, sign-up and password resets go away.

## Why

The step-by-step repertoire builder ([repertoire-builder.md](../02-openings/repertoire-builder.md))
needs the Lichess opening explorer, and since 2025 the explorer answers only requests that carry a
Lichess token ([Lichess blog](https://lichess.org/@/thibault/blog/the-opening-explorer-now-requires-authentication/FSWh9Zg3)).
One shared token on the server would put every user's requests on the owner's account. If users
sign in with Lichess, each user's requests go out under their own account.

It also helps elsewhere:
- We know the Lichess account belongs to the user. Today they type a username and we only check
  that it exists.
- Lichess sends a user their own games twice as fast when the request carries their token (60
  games/s against 30/s).
- No passwords, reset emails or SMTP to look after.

## Goals

- A visitor clicks "Sign in with Lichess", approves on lichess.org, and lands in Blundr signed in.
  Their first visit creates the account.
- The user's Lichess token stays on the server, encrypted in the database, and never reaches the
  browser.
- Explorer requests and game imports use the user's own token.
- When a token stops working (it expired or the user revoked it), the app says so plainly and
  offers "Sign in with Lichess again". Nothing breaks silently.

## Non-goals

- Email/password accounts, or keeping today's accounts. The owner said to delete the two existing
  accounts (2026-11-27).
- chess.com sign-in. It's on the roadmap ("Later"). This design shouldn't block it: the account
  links to a provider and an ID, not to Lichess everywhere.
- Asking Lichess for any permission (scope). A token with no scopes is enough for the explorer and
  for reading public games.
- Account deletion from inside the app (not there today either).

## Anti-goals

- **A Lichess token in the browser, in logs or in a URL.** It's a credential for the user's
  Lichess account.
- **A sign-in that can be replayed or forged.** The flow uses PKCE and a `state` check.
- **Deleting production data by surprise.** The migration that removes the old accounts runs only
  after a backup and the owner's go-ahead at deploy time.

## Approaches

- **A. The server runs the sign-in (chosen, owner 2026-11-27).** The browser goes to
  `/api/auth/lichess/start`, which redirects to lichess.org. Lichess redirects back to
  `/api/auth/lichess/callback`. The server exchanges the code for the token, stores it encrypted
  and gives the browser Blundr's usual session token (JWT).
- **B. The browser runs the sign-in** and posts the Lichess token to the server. Dropped: the token
  passes through the browser, and the server would still have to store it.

## How it works

### The flow

Lichess supports OAuth 2 with PKCE for apps it hasn't registered: the client ID is any unique
string and there's no client secret.

1. **`GET /api/auth/lichess/start`**:
   - creates a random `state` and a PKCE `code_verifier`;
   - stores both in a short-lived cookie (HttpOnly, SameSite=Lax, Secure outside local dev, path
     `/api/auth/lichess`, 10 minutes), signed with `JWT_SECRET`;
   - redirects to `https://lichess.org/oauth` with `response_type=code`, `client_id=blundr.ch`,
     `redirect_uri=<PUBLIC_URL>/api/auth/lichess/callback`, `code_challenge_method=S256`,
     `code_challenge` and `state`. No `scope`.
2. **`GET /api/auth/lichess/callback?code=…&state=…`**:
   - checks `state` against the cookie, then deletes the cookie;
   - posts to `https://lichess.org/api/token` (grant type `authorization_code`, the code, the
     verifier, the redirect URI, the client ID) and receives `access_token` and `expires_in`;
   - calls `GET https://lichess.org/api/account` with the token to get the account's `id` and
     `username`;
   - finds or creates the user by Lichess ID, then saves the encrypted token, its expiry, and the
     username (users can change the case of their username on Lichess).
3. **Handing over the session.** The callback redirects to `/#auth=<jwt>`. The frontend reads the
   fragment once on load, stores the JWT as it does today, and strips the fragment from the URL,
   the same way `?reset_token=` was handled. A URL fragment never reaches a server or a `Referer`
   header. *(Builder's call; an HttpOnly session cookie would mean reworking `api.ts`.)*
4. **Errors.** A user who clicks "Deny" on Lichess (`error=access_denied`), a bad `state`, or a
   failed exchange all redirect to `/#auth_error=<code>`. The landing page then explains what went
   wrong in one line and shows the button again.

### Storing the token

- New columns on `users`:
  - `auth_provider` (`"lichess"`);
  - `provider_user_id` (the Lichess ID, lowercase; unique with `auth_provider`);
  - `lichess_token_enc`, the token encrypted with Fernet from `cryptography`;
  - `lichess_token_expires_at`.
- The key is a new setting, `LICHESS_TOKEN_KEY`. It lives only in the server's `.env` and is passed
  through `docker-compose.yml`. Startup fails loudly if sign-in is used without it.
- `username` and `lichess_username` both become the Lichess username. `lichess_username` stays the
  name the rest of the app reads.
- Removed: `password_hash`, `email`, the `password_reset_tokens` table, `email_service.py`, the
  SMTP settings, `VALIDATE_LICHESS_ACCOUNT`, the register/login/forgot/reset routes and their
  schemas, and the sign-up form in `AuthForm.tsx`.

### Using the token

- **One helper** returns a user's decrypted token, or `None` when there is none or it has expired.
  Every Lichess call made for a user goes through it, which keeps the decrypting in one place.
- **Game imports** send the user's token on their own requests, overriding the shared client's
  default header. The shared `LICHESS_TOKEN` stays as an optional fallback for background work
  that has no user.
- **When Lichess answers 401 to a user's token**, the token is cleared and `/auth/me` returns
  `lichess_connected: false`. The app shows a banner, "Your Lichess sign-in has ended. Sign in
  with Lichess again", with the button. Signing in again stores the new token.
- **Expiry.** Tokens are expected to last about a year (`expires_in`; unconfirmed in the official
  docs, so the stored expiry is whatever Lichess sends). Blundr's own JWT keeps its 7-day life.
  Signing in again always renews the Lichess token.
- **Signing out** drops the JWT in the browser, as today. It doesn't revoke the Lichess token,
  because background analysis and syncing keep working while the user is away.

### The owner

`is_owner` (which unlocks writing your own explanations) is set at sign-in when the Lichess
username matches a new setting, `OWNER_LICHESS_USERNAME`. Nobody is the owner while it's empty. Set
in the server's `.env`. *(Builder's call: the accounts are being deleted, so the flag has to come
from somewhere.)*

### Removing the old accounts

- **What goes:** the two existing accounts and everything they own (games, blunders, cards, review
  logs, repertoire, deviations). That data hangs off `users`, and the users table loses its NOT
  NULL `password_hash`.
- **Shared data stays:** opening names, theory moves, masters data, engine lines, explanations by
  position, the explorer cache.
- **How it runs:** a one-time migration in `db.py`. It runs when the `users` table still has a
  `password_hash` column:
  1. delete the user-owned rows, children first;
  2. rebuild `users` in the new shape, following `_rebuild_review_cards()`'s safe order;
  3. record that it ran in `app_meta`.

  It's tested against the old schema in `tests/test_migrations.py`.
- **Deploy:** back up the production database first (CLAUDE.md), and get the owner's go-ahead before
  the PR merges, since a merge deploys it.

### Settings

| setting | default | note |
|---|---|---|
| `PUBLIC_URL` | `http://localhost:3000` | builds the redirect URI; `https://blundr.ch` on the server |
| `LICHESS_OAUTH_CLIENT_ID` | `blundr.ch` | |
| `LICHESS_TOKEN_KEY` | `""` | Fernet key; required to sign in |
| `OWNER_LICHESS_USERNAME` | `""` | |

All four go into `docker-compose.yml`'s `environment:` block.

### Local development and tests

- **Local dev** signs in through real lichess.org. Vite proxies `/api`, so the redirect URI is
  `http://localhost:3000/api/auth/lichess/callback`, which Lichess accepts.
- **Tests** never call Lichess:
  - the OAuth endpoints are faked with `httpx.MockTransport`;
  - the other tests create users directly and mint JWTs with a helper that replaces today's
    `register` calls in tests.

## What's left to the builder

- The exact layout of the landing-page button and the reconnect banner.
- Whether `/auth/me` reads `lichess_connected` from the stored expiry alone, or also after a
  recent 401.
- Splitting the work into tasks and PRs, with the migration in a PR of its own.
