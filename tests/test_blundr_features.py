"""
Tests for the blunder-analysis core loop: auth, SM-2 scheduling, win-prob
math, and end-to-end blunder detection with a real engine.
"""
import pathlib
import shutil
from datetime import datetime, timedelta

import pytest

from tests.conftest import client

_TEST_DB_PATH = pathlib.Path(__file__).parent / "test_blundr.db"
from app.models.db_models import ReviewCard
from app.services.srs_service import apply_grade, MIN_EASE
from app.services.analysis_service import win_prob, detect_blunders
from app.config import settings


def _register(username: str, password: str = "password123", **extra) -> dict:
    # lichess_username and email are required at registration; default both for tests
    extra.setdefault("lichess_username", f"{username}_li")
    extra.setdefault("email", f"{username}@example.com")
    resp = client.post(
        "/api/auth/register",
        json={"username": username, "password": password, **extra},
    )
    return resp


class TestAuth:
    """Register / login / me flow."""

    def test_register_returns_token(self):
        resp = _register("alice", lichess_username="alice_lichess")
        assert resp.status_code == 201
        assert "access_token" in resp.json()

    def test_register_duplicate_username_conflict(self):
        _register("bob")
        resp = _register("bob")
        assert resp.status_code == 409

    def test_login_and_me(self):
        _register("carol", lichess_username="carol_li")
        resp = client.post(
            "/api/auth/login", json={"username": "carol", "password": "password123"}
        )
        assert resp.status_code == 200
        token = resp.json()["access_token"]

        me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        body = me.json()
        assert body["username"] == "carol"
        assert body["lichess_username"] == "carol_li"

    def test_login_wrong_password(self):
        _register("dave")
        resp = client.post(
            "/api/auth/login", json={"username": "dave", "password": "wrong-password"}
        )
        assert resp.status_code == 401

    def test_login_unknown_user_same_error(self):
        resp = client.post(
            "/api/auth/login", json={"username": "nobody", "password": "password123"}
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Incorrect username or password"

    def test_me_requires_token(self):
        assert client.get("/api/auth/me").status_code == 401

    def test_me_rejects_garbage_token(self):
        resp = client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
        assert resp.status_code == 401

    def test_protected_routes_require_auth(self):
        assert client.get("/api/reviews/due").status_code == 401
        assert client.get("/api/analysis/status").status_code == 401
        assert client.get("/api/analysis/blunders").status_code == 401

    def test_short_password_rejected(self):
        resp = client.post(
            "/api/auth/register",
            json={"username": "eve", "password": "short", "lichess_username": "eve_li"},
        )
        assert resp.status_code == 422

    def test_lichess_username_required(self):
        resp = client.post(
            "/api/auth/register",
            json={"username": "frank", "password": "password123", "email": "frank@example.com"},
        )
        assert resp.status_code == 422

    def test_email_required(self):
        resp = client.post(
            "/api/auth/register",
            json={"username": "grace", "password": "password123", "lichess_username": "grace_li"},
        )
        assert resp.status_code == 422

    def test_malformed_email_rejected(self):
        resp = client.post(
            "/api/auth/register",
            json={"username": "heidi", "password": "password123",
                  "lichess_username": "heidi_li", "email": "not-an-email"},
        )
        assert resp.status_code == 422

    def test_duplicate_email_conflict(self):
        _register("ivan1", email="shared@example.com")
        resp = _register("ivan2", email="shared@example.com")
        assert resp.status_code == 409

    def test_me_includes_email(self):
        _register("judy", email="judy@example.com")
        token = client.post(
            "/api/auth/login", json={"username": "judy", "password": "password123"}
        ).json()["access_token"]
        me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.json()["email"] == "judy@example.com"


class TestPasswordReset:
    """Forgot-password / reset-password flow."""

    def _latest_token_for(self, username: str) -> str:
        """Pull the reset token straight from the DB — the console-fallback
        email service only logs it, it's never returned by the API."""
        import sqlite3
        con = sqlite3.connect(str(_TEST_DB_PATH))
        row = con.execute(
            "SELECT prt.token FROM password_reset_tokens prt "
            "JOIN users u ON u.id = prt.user_id WHERE u.username = ? "
            "ORDER BY prt.id DESC LIMIT 1",
            (username,),
        ).fetchone()
        con.close()
        assert row is not None, f"no reset token found for {username}"
        return row[0]

    def test_forgot_password_same_response_regardless_of_existence(self):
        known = client.post("/api/auth/forgot-password", json={"email": "nobody-here@example.com"})
        _register("kevin", email="kevin@example.com")
        exists = client.post("/api/auth/forgot-password", json={"email": "kevin@example.com"})
        assert known.status_code == exists.status_code == 200
        assert known.json() == exists.json()

    def test_forgot_password_creates_usable_token(self):
        _register("laura", email="laura@example.com")
        client.post("/api/auth/forgot-password", json={"email": "laura@example.com"})
        token = self._latest_token_for("laura")

        resp = client.post(
            "/api/auth/reset-password", json={"token": token, "new_password": "newpassword456"}
        )
        assert resp.status_code == 200

        # Old password no longer works, new one does
        assert client.post(
            "/api/auth/login", json={"username": "laura", "password": "password123"}
        ).status_code == 401
        assert client.post(
            "/api/auth/login", json={"username": "laura", "password": "newpassword456"}
        ).status_code == 200

    def test_reset_token_is_single_use(self):
        _register("mallory", email="mallory@example.com")
        client.post("/api/auth/forgot-password", json={"email": "mallory@example.com"})
        token = self._latest_token_for("mallory")

        first = client.post(
            "/api/auth/reset-password", json={"token": token, "new_password": "firstpass123"}
        )
        assert first.status_code == 200

        second = client.post(
            "/api/auth/reset-password", json={"token": token, "new_password": "secondpass123"}
        )
        assert second.status_code == 400

    def test_reset_rejects_unknown_token(self):
        resp = client.post(
            "/api/auth/reset-password",
            json={"token": "this-token-does-not-exist", "new_password": "whatever123"},
        )
        assert resp.status_code == 400

    def test_reset_rejects_expired_token(self):
        import sqlite3
        from datetime import datetime, timedelta

        _register("nathan", email="nathan@example.com")
        client.post("/api/auth/forgot-password", json={"email": "nathan@example.com"})
        token = self._latest_token_for("nathan")

        expired = (datetime.utcnow() - timedelta(minutes=1)).isoformat(sep=" ")
        con = sqlite3.connect(str(_TEST_DB_PATH))
        con.execute(
            "UPDATE password_reset_tokens SET expires_at = ? WHERE token = ?",
            (expired, token),
        )
        con.commit()
        con.close()

        resp = client.post(
            "/api/auth/reset-password", json={"token": token, "new_password": "whatever123"}
        )
        assert resp.status_code == 400

    def test_reset_rejects_short_new_password(self):
        _register("olivia", email="olivia@example.com")
        client.post("/api/auth/forgot-password", json={"email": "olivia@example.com"})
        token = self._latest_token_for("olivia")

        resp = client.post(
            "/api/auth/reset-password", json={"token": token, "new_password": "short"}
        )
        assert resp.status_code == 422


class TestSM2:
    """Spaced-repetition scheduling math."""

    def _card(self) -> ReviewCard:
        return ReviewCard(user_id=1, blunder_id=1, ease=2.5, interval_days=0.0,
                          repetitions=0, lapses=0, due_at=datetime(2026, 1, 1))

    def test_good_progression(self):
        card = self._card()
        now = datetime(2026, 1, 1, 12, 0)

        apply_grade(card, "good", now=now)
        assert card.repetitions == 1
        assert card.interval_days == 1.0
        assert card.due_at == now + timedelta(days=1)

        apply_grade(card, "good", now=now)
        assert card.repetitions == 2
        assert card.interval_days == 6.0

        ease_before = card.ease
        apply_grade(card, "good", now=now)
        assert card.repetitions == 3
        assert card.interval_days == round(6.0 * ease_before, 1)

    def test_again_resets_and_lapses(self):
        card = self._card()
        now = datetime(2026, 1, 1, 12, 0)
        apply_grade(card, "good", now=now)
        apply_grade(card, "good", now=now)

        apply_grade(card, "again", now=now)
        assert card.repetitions == 0
        assert card.lapses == 1
        assert card.due_at == now + timedelta(minutes=10)

    def test_easy_raises_ease_good_keeps_it(self):
        card = self._card()
        now = datetime(2026, 1, 1)
        apply_grade(card, "easy", now=now)
        assert card.ease > 2.5

        card2 = self._card()
        apply_grade(card2, "good", now=now)
        assert card2.ease == pytest.approx(2.5)

    def test_ease_never_below_minimum(self):
        card = self._card()
        card.ease = MIN_EASE
        now = datetime(2026, 1, 1)
        # "good" applies a delta of 0 in classic SM-2; repeated grading
        # must never push ease below the floor
        for _ in range(5):
            apply_grade(card, "good", now=now)
        assert card.ease >= MIN_EASE

    def test_unknown_grade_raises(self):
        with pytest.raises(ValueError):
            apply_grade(self._card(), "meh")


class TestWinProb:
    """Centipawn → win-probability conversion."""

    def test_even_position_is_fifty(self):
        assert win_prob(0) == pytest.approx(50.0)

    def test_symmetry(self):
        assert win_prob(200) + win_prob(-200) == pytest.approx(100.0)

    def test_monotonic_and_saturating(self):
        assert win_prob(100) > win_prob(0) > win_prob(-100)
        assert win_prob(10000) == pytest.approx(100.0, abs=0.1)
        # Saturation: a 400cp swing in a won position matters far less than
        # the same swing through equality, and stays below blunder threshold
        won_game_drop = win_prob(900) - win_prob(500)
        equal_game_drop = win_prob(200) - win_prob(-200)
        assert won_game_drop < equal_game_drop
        assert won_game_drop < settings.BLUNDER_WINPROB_THRESHOLD


class TestSettings:
    """Profile + sync preference endpoints."""

    def _token(self, name: str) -> dict:
        _register(name, lichess_username="some_lichess")
        resp = client.post("/api/auth/login", json={"username": name, "password": "password123"})
        return {"Authorization": f"Bearer {resp.json()['access_token']}"}

    def test_defaults(self):
        h = self._token("settings_user1")
        body = client.get("/api/settings", headers=h).json()
        assert body["sync_game_types"] == ["rapid", "blitz"]
        assert body["sync_days_back"] == 7
        assert body["lichess_username"] == "some_lichess"

    def test_partial_update_roundtrip(self):
        h = self._token("settings_user2")
        resp = client.put("/api/settings", headers=h, json={
            "display_name": "Test Person",
            "sync_game_types": ["bullet"],
            "sync_days_back": 30,
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["display_name"] == "Test Person"
        assert body["sync_game_types"] == ["bullet"]
        assert body["sync_days_back"] == 30
        # Untouched field survives a partial update
        assert body["lichess_username"] == "some_lichess"

        # /auth/me reflects the profile change
        me = client.get("/api/auth/me", headers=h).json()
        assert me["display_name"] == "Test Person"

    def test_invalid_game_type_rejected(self):
        h = self._token("settings_user3")
        resp = client.put("/api/settings", headers=h, json={"sync_game_types": ["chess960"]})
        assert resp.status_code == 422

    def test_empty_game_types_rejected(self):
        h = self._token("settings_user4")
        resp = client.put("/api/settings", headers=h, json={"sync_game_types": []})
        assert resp.status_code == 422

    def test_days_back_bounds(self):
        h = self._token("settings_user5")
        assert client.put("/api/settings", headers=h, json={"sync_days_back": 0}).status_code == 422
        assert client.put("/api/settings", headers=h, json={"sync_days_back": 366}).status_code == 422

    def test_email_shown_and_editable(self):
        h = self._token("settings_user6")
        body = client.get("/api/settings", headers=h).json()
        assert body["email"] == "settings_user6@example.com"

        resp = client.put("/api/settings", headers=h, json={"email": "new_address@example.com"})
        assert resp.status_code == 200
        assert resp.json()["email"] == "new_address@example.com"

    def test_email_change_rejects_duplicate(self):
        _register("settings_other", email="taken@example.com")
        h = self._token("settings_user7")
        resp = client.put("/api/settings", headers=h, json={"email": "taken@example.com"})
        assert resp.status_code == 409


class TestStats:
    """Dashboard stats endpoints."""

    def _token(self, name: str, **extra) -> dict:
        _register(name, **extra)
        resp = client.post("/api/auth/login", json={"username": name, "password": "password123"})
        return {"Authorization": f"Bearer {resp.json()['access_token']}"}

    def test_overview_empty_user(self):
        h = self._token("stats_user1")
        body = client.get("/api/stats/overview", headers=h).json()
        assert body == {
            "games_analyzed": 0, "total_blunders": 0, "blunders_mastered": 0,
            "due_now": 0, "reviews_done": 0,
        }

    def test_timeline_zero_filled(self):
        h = self._token("stats_user2")
        body = client.get("/api/stats/timeline?days=7", headers=h).json()
        assert len(body) == 7
        assert all(p["games"] == 0 and p["blunders"] == 0 and p["reviews"] == 0 for p in body)
        # No cards exist → mastered % is a gap (None), not a fake zero
        assert all(p["mastered_pct"] is None for p in body)
        # Continuous, ordered dates ending today
        from datetime import date as _d
        assert body[-1]["date"] == _d.today().isoformat()

    def test_sync_requires_lichess_username(self):
        # Registration requires a Lichess account, but it can be cleared in
        # settings afterwards — sync must then refuse with a clear error
        h = self._token("stats_user3")
        client.put("/api/settings", headers=h, json={"lichess_username": ""})
        resp = client.post("/api/analysis/sync", headers=h)
        assert resp.status_code == 400
        assert "Lichess username" in resp.json()["detail"]

    def test_requires_auth(self):
        assert client.get("/api/stats/overview").status_code == 401
        assert client.get("/api/settings").status_code == 401
        assert client.post("/api/analysis/sync").status_code == 401

    def test_sync_skipped_within_24h_unless_forced(self):
        import sqlite3
        from datetime import datetime, timedelta, timezone

        h = self._token("stats_user4", lichess_username="whatever_li")
        uid = client.get("/api/auth/me", headers=h).json()["id"]

        # Pretend a sync completed 2 hours ago
        recent = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2))
        con = sqlite3.connect(str(_TEST_DB_PATH))
        con.execute("UPDATE users SET last_synced_at = ? WHERE id = ?",
                    (recent.isoformat(sep=" "), uid))
        con.commit()
        con.close()

        # Non-forced sync (the app's on-load call) is skipped
        resp = client.post("/api/analysis/sync", headers=h)
        assert resp.status_code == 202
        body = resp.json()
        assert body["state"] == "skipped"
        assert body["last_synced_at"] is not None
        # force=true bypasses the gate (it proceeds to the Lichess fetch and
        # fails on the fake account — anything but "skipped" proves the gate opened)
        resp = client.post("/api/analysis/sync?force=true", headers=h)
        assert resp.json().get("state") != "skipped"


class TestNewCardDailyLimit:
    """max_new_per_day gates how many never-reviewed blunders enter the queue."""

    def _setup_user_with_cards(self, name: str, n_cards: int, max_new: int) -> dict:
        """Register a user and seed n never-reviewed due cards directly in the DB."""
        import sqlite3
        _register(name)
        resp = client.post("/api/auth/login", json={"username": name, "password": "password123"})
        headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
        client.put("/api/settings", headers=headers, json={"max_new_per_day": max_new})

        me = client.get("/api/auth/me", headers=headers).json()
        uid = me["id"]

        con = sqlite3.connect(str(_TEST_DB_PATH))
        cur = con.cursor()
        cur.execute(
            "INSERT INTO analyzed_games (user_id, lichess_game_id, lichess_username,"
            " user_color, white_name, black_name, result, played_at, analyzed_at)"
            " VALUES (?, ?, ?, 'white', 'W', 'B', '1-0', '2026-01-01', '2026-01-01')",
            (uid, f"game_{name}", "someone"),
        )
        game_id = cur.lastrowid
        for i in range(n_cards):
            cur.execute(
                "INSERT INTO blunders (user_id, game_id, ply, fen_before,"
                " move_played_uci, move_played_san, best_move_uci, best_move_san,"
                " eval_before_cp, eval_after_cp, win_prob_drop, created_at)"
                " VALUES (?, ?, ?, 'fen', 'e2e4', 'e4', 'd2d4', 'd4', 0, -300, 30.0, '2026-01-01')",
                (uid, game_id, i + 1),
            )
            cur.execute(
                "INSERT INTO review_cards (user_id, blunder_id, ease, interval_days,"
                " repetitions, lapses, due_at)"
                " VALUES (?, ?, 2.5, 0.0, 0, 0, '2026-01-01')",
                (uid, cur.lastrowid),
            )
        con.commit()
        con.close()
        return headers

    def test_new_cards_capped_and_quota_consumed(self):
        h = self._setup_user_with_cards("limituser", n_cards=5, max_new=2)

        due = client.get("/api/reviews/due", headers=h).json()
        assert len(due) == 2  # 5 new cards exist, only 2 may start today

        stats = client.get("/api/reviews/stats", headers=h).json()
        assert stats["due_now"] == 2
        assert stats["new_remaining_today"] == 2
        assert stats["total_cards"] == 5

        # Starting one card consumes a slot...
        client.post(f"/api/reviews/{due[0]['card_id']}", headers=h, json={"grade": "good"})
        stats = client.get("/api/reviews/stats", headers=h).json()
        assert stats["new_remaining_today"] == 1
        due = client.get("/api/reviews/due", headers=h).json()
        assert len(due) == 1  # one new slot left

        # ...and so does the second, even when graded "again" (it's now a
        # seen card, due in 10 min, no longer new)
        client.post(f"/api/reviews/{due[0]['card_id']}", headers=h, json={"grade": "again"})
        stats = client.get("/api/reviews/stats", headers=h).json()
        assert stats["new_remaining_today"] == 0
        assert client.get("/api/reviews/due", headers=h).json() == []

    def test_zero_pauses_new_intake(self):
        h = self._setup_user_with_cards("pauseuser", n_cards=3, max_new=0)
        assert client.get("/api/reviews/due", headers=h).json() == []
        stats = client.get("/api/reviews/stats", headers=h).json()
        assert stats["due_now"] == 0
        assert stats["total_cards"] == 3


class TestMasteredTimeline:
    """Day-by-day mastered-% replay from review history."""

    def test_replay(self):
        from datetime import date as d, datetime as dt
        from app.services.srs_service import mastered_percentage_by_day

        # Two cards: card 1 exists from Jan 1, card 2 from Jan 3
        cards = {1: d(2026, 1, 1), 2: d(2026, 1, 3)}
        logs = [
            # Card 1 passes three times → mastered by Jan 4
            (1, "good", dt(2026, 1, 1, 10)),
            (1, "good", dt(2026, 1, 2, 10)),
            (1, "easy", dt(2026, 1, 4, 10)),
            # …then lapses on Jan 6 → un-mastered
            (1, "again", dt(2026, 1, 6, 10)),
            # Card 2 never reviewed
        ]
        days = [d(2026, 1, i) for i in range(1, 8)]
        out = mastered_percentage_by_day(cards, logs, days)

        assert out[0] == 0.0          # Jan 1: 1 card, streak 1 → not mastered
        assert out[1] == 0.0          # Jan 2: streak 2
        assert out[2] == 0.0          # Jan 3: card 2 appears (0/2)
        assert out[3] == 50.0         # Jan 4: card 1 mastered (1/2)
        assert out[4] == 50.0         # Jan 5: unchanged
        assert out[5] == 0.0          # Jan 6: lapse resets card 1 (0/2)
        assert out[6] == 0.0

    def test_no_cards_is_gap(self):
        from datetime import date as d
        from app.services.srs_service import mastered_percentage_by_day

        out = mastered_percentage_by_day({}, [], [d(2026, 1, 1)])
        assert out == [None]

        # Cards created later than the day → still a gap that day
        out = mastered_percentage_by_day({1: d(2026, 1, 5)}, [], [d(2026, 1, 1), d(2026, 1, 5)])
        assert out == [None, 0.0]


@pytest.mark.skipif(shutil.which(settings.STOCKFISH_PATH) is None,
                    reason="stockfish not installed")
class TestBlunderDetection:
    """End-to-end detection against a real engine."""

    @pytest.mark.asyncio
    async def test_fools_mate_flags_g4(self):
        import chess
        import chess.engine
        from app.models.game import LichessGame, Player

        game = LichessGame(
            id="testgame",
            created_at=datetime(2026, 1, 1),
            white=Player(id="tester", name="Tester", color="white"),
            black=Player(id="opp", name="Opp", color="black"),
            moves=["f3", "e5", "g4", "Qh4#"],
        )

        transport, engine = await chess.engine.popen_uci(settings.STOCKFISH_PATH)
        try:
            blunders = await detect_blunders(engine, game, chess.WHITE)
        finally:
            await engine.quit()

        # 3... g4?? allows mate in one: must be flagged
        assert any(b.move_played_san == "g4" and b.ply == 3 for b in blunders)
        g4 = next(b for b in blunders if b.ply == 3)
        assert g4.eval_after_cp <= -9000  # got mated
        assert g4.win_prob_drop >= settings.BLUNDER_WINPROB_THRESHOLD
        # FEN of the position where the blunder was played
        assert g4.fen_before.startswith("rnbqkbnr/pppp1ppp/8/4p3/8/5P2/PPPPP1PP/RNBQKBNR w")
        # The stored refutation is the engine's punish of g4: mate in one
        assert g4.refutation_san == "Qh4#"

    @pytest.mark.asyncio
    async def test_best_reply_to_move(self):
        from app.services.analysis_service import best_reply_to_move

        # After 1.f3 e5 2.g4, black's punish must be Qh4#
        fen = "rnbqkbnr/pppp1ppp/8/4p3/8/5P2/PPPPP1PP/RNBQKBNR w KQkq - 0 3"
        result = await best_reply_to_move(fen, "g2g4")
        assert result["move_san"] == "g4"
        assert result["reply_san"] == "Qh4#"
        assert result["game_over"] is False

    @pytest.mark.asyncio
    async def test_best_reply_game_over_and_validation(self):
        from app.services.analysis_service import best_reply_to_move

        # Fool's mate delivered: no reply exists
        fen = "rnbqkbnr/pppp1ppp/8/4p3/6P1/5P2/PPPPP2P/RNBQKBNR b KQkq - 0 2"
        result = await best_reply_to_move(fen, "d8h4")
        assert result["game_over"] is True
        assert result["reply_san"] is None

        with pytest.raises(ValueError):
            await best_reply_to_move(fen, "a1a8")  # illegal
        with pytest.raises(ValueError):
            await best_reply_to_move("not a fen", "e2e4")
