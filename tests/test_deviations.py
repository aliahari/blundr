"""
Phase 2, task 12: checking games against the repertoire (design D6; R6.1-R6.5).

first_deviation() and punish_move() are pure and tested on hand-made games;
check_games() runs against a throwaway database, as in test_move_backfill.
"""
import json
from datetime import datetime, timedelta

import chess
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.db_models import (
    AnalyzedGame,
    Base,
    RepertoireDeviation,
    RepertoireMove,
    ReviewCard,
    TheoryMove,
    User,
    new_review_card,
)
from app.models.game import LichessGame, Player
from app.services import backfill_service
from app.services.analysis_service import AnalysisJob
from app.services.deviation_service import check_games
from app.services.repertoire_service import Deviation, first_deviation, punish_move

START = chess.Board().epd()


def _key(*sans):
    board = chess.Board()
    for san in sans:
        board.push_san(san)
    return board.epd()


def _uci(sans, san):
    """The uci of `san` played after `sans`."""
    board = chess.Board()
    for s in sans:
        board.push_san(s)
    return board.parse_san(san).uci()


def _positions(sans, evals=None, best=None):
    """A game's stored moves; evals (user's side) and best moves optional."""
    board = chess.Board()
    out = []
    for i, san in enumerate(sans):
        move = board.parse_san(san)
        out.append({
            "san": san, "uci": move.uci(),
            "eval_cp": evals[i] if evals else None,
            "best_uci": best[i] if best else None,
        })
        board.push(move)
    return out


# A White repertoire: 1.e4, and 2.Nf3 after 1...e5.
WHITE = {START: "e2e4", _key("e4", "e5"): "g1f3"}
# A Black repertoire: 1...c5 against 1.e4, and 2...d6 after 2.Nf3.
BLACK = {_key("e4"): "c7c5", _key("e4", "c5", "Nf3"): "d7d6"}


class TestFirstDeviation:

    def test_user_deviation_at_the_right_ply(self):
        dev = first_deviation(_positions(["e4", "e5", "Bc4", "Nc6"]), "white", WHITE)
        assert dev == Deviation(3, "user", _key("e4", "e5"), "g1f3", "f1c4")

    def test_opponent_deviation(self):
        dev = first_deviation(_positions(["e4", "c5", "Nf3"]), "white", WHITE)
        assert dev == Deviation(2, "opponent", _key("e4"), None, "c7c5")

    def test_past_the_prepared_part_is_no_deviation(self):
        # After 2.Nf3 the repertoire has nothing more: whatever follows is fine.
        assert first_deviation(_positions(["e4", "e5", "Nf3", "Nc6", "Bb5"]), "white", WHITE) is None
        # A repertoire that is only 1.e4 has no opponent replies to deviate from.
        assert first_deviation(_positions(["e4", "c5"]), "white", {START: "e2e4"}) is None

    def test_black_repertoire(self):
        assert first_deviation(_positions(["d4", "d5"]), "black", BLACK) == \
            Deviation(1, "opponent", START, None, "d2d4")
        assert first_deviation(_positions(["e4", "e5"]), "black", BLACK) == \
            Deviation(2, "user", _key("e4"), "c7c5", "e7e5")
        assert first_deviation(_positions(["e4", "c5", "Nf3", "d6", "d4"]), "black", BLACK) is None

    def test_empty_repertoire_and_short_games(self):
        assert first_deviation(_positions(["e4", "c5"]), "white", {}) is None
        assert first_deviation(_positions(["e4"]), "white", WHITE) is None
        assert first_deviation([], "white", WHITE) is None


class TestPunishMove:
    # 1.e4 e5 2.Nf3 Qh4?? 3.Nxh4: evals from White's (the user's) side.
    SANS = ["e4", "e5", "Nf3", "Qh4", "Nxh4"]
    BEST = [None, None, None, None, _uci(["e4", "e5", "Nf3", "Qh4"], "Nxh4")]

    def test_a_blunder_gives_the_engine_reply(self):
        positions = _positions(self.SANS, evals=[30, 25, 30, 900, 900], best=self.BEST)
        assert punish_move(positions, 4, 20, 25.0, 40.0) == "f3h4"

    def test_a_small_slip_is_not_flagged(self):
        positions = _positions(self.SANS, evals=[30, 25, 30, 90, 90], best=self.BEST)
        assert punish_move(positions, 4, 20, 25.0, 40.0) is None

    def test_an_opponent_already_lost_is_not_flagged(self):
        positions = _positions(self.SANS, evals=[30, 25, 700, 1500, 1500], best=self.BEST)
        assert punish_move(positions, 4, 20, 25.0, 40.0) is None

    def test_no_evals_or_no_next_move(self):
        assert punish_move(_positions(self.SANS, best=self.BEST), 4, None, 25.0, 40.0) is None
        short = _positions(self.SANS[:4], evals=[30, 25, 30, 900])
        assert punish_move(short, 4, 20, 25.0, 40.0) is None

    def test_first_move_uses_the_initial_eval(self):
        # Black's first move is the deviation: its "before" is the start position's eval.
        positions = _positions(["e4", "f6", "d4"], evals=[30, 600, 600],
                               best=[None, None, "d2d4"])
        assert punish_move(positions, 2, 20, 25.0, 40.0) == "d2d4"


# --- check_games against a database -----------------------------------------

@pytest_asyncio.fixture
async def db_factory(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'deviations.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(backfill_service, "async_session", factory)
    yield factory
    await engine.dispose()


async def _user(factory, repertoire: dict[str, dict[str, str]]) -> int:
    """A user with a repertoire ({colour: {key: uci}}), each position with its card."""
    async with factory() as s:
        user = User(username="dev", password_hash="x")
        s.add(user)
        await s.flush()
        for color, moves in repertoire.items():
            for key, uci in moves.items():
                row = RepertoireMove(user_id=user.id, color=color, position_key=key,
                                     move_uci=uci, move_san="?", source="games")
                s.add(row)
                await s.flush()
                s.add(new_review_card(user_id=user.id, kind="opening", card_type="play",
                                      repertoire_move_id=row.id))
        await s.commit()
        return user.id


_game_ids = iter(range(1, 10_000))


async def _game(factory, user_id, positions, color="white", initial_eval_cp=None) -> int:
    async with factory() as s:
        game = AnalyzedGame(
            user_id=user_id, lichess_game_id=f"dev{next(_game_ids):05d}",
            lichess_username="dev", user_color=color, white_name="W", black_name="B",
            result="1-0", played_at=datetime(2026, 11, 1),
            positions=json.dumps(positions) if positions is not None else None,
            user_moves=0, initial_eval_cp=initial_eval_cp,
        )
        s.add(game)
        await s.commit()
        return game.id


async def _check(factory, user_id, game_ids=None) -> int:
    async with factory() as s:
        checked = await check_games(s, user_id, game_ids)
        await s.commit()
        return checked


async def _get(factory, model, **where):
    async with factory() as s:
        query = select(model)
        for name, value in where.items():
            query = query.where(getattr(model, name) == value)
        return (await s.scalars(query)).all()


QH4 = ["e4", "e5", "Nf3", "Qh4", "Nxh4"]
QH4_EVALS = [30, 25, 30, 900, 900]
QH4_BEST = [None, None, None, None, "f3h4"]


class TestCheckGames:

    @pytest.mark.asyncio
    async def test_opponent_blunder_makes_one_punish_card(self, db_factory):
        uid = await _user(db_factory, {"white": WHITE | {_key("e4", "e5", "Nf3", "Nc6"): "f1b5"}})
        gid = await _game(db_factory, uid, _positions(QH4, QH4_EVALS, QH4_BEST), initial_eval_cp=20)

        assert await _check(db_factory, uid) == 1
        assert await _check(db_factory, uid) == 0  # checked once

        [dev] = await _get(db_factory, RepertoireDeviation, game_id=gid)
        assert (dev.ply, dev.side, dev.played_uci, dev.punish_uci) == (4, "opponent", "d8h4", "f3h4")
        assert dev.position_key == _key("e4", "e5", "Nf3")
        cards = await _get(db_factory, ReviewCard, repertoire_deviation_id=dev.id)
        assert [(c.kind, c.card_type) for c in cards] == [("deviation", "punish")]
        [game] = await _get(db_factory, AnalyzedGame, id=gid)
        assert game.repertoire_exit_ply == 4 and game.repertoire_checked_at is not None

    @pytest.mark.asyncio
    async def test_opponent_deviation_without_a_blunder_or_evals(self, db_factory):
        uid = await _user(db_factory, {"white": WHITE | {_key("e4", "e5", "Nf3", "Nc6"): "f1b5"}})
        quiet = await _game(db_factory, uid, _positions(["e4", "c5", "Nf3"], [30, 40, 35]),
                            initial_eval_cp=20)
        no_evals = await _game(db_factory, uid, _positions(QH4))   # a backfilled game

        await _check(db_factory, uid)

        for gid in (quiet, no_evals):
            [dev] = await _get(db_factory, RepertoireDeviation, game_id=gid)
            assert dev.side == "opponent" and dev.punish_uci is None
            assert await _get(db_factory, ReviewCard, repertoire_deviation_id=dev.id) == []

    @pytest.mark.asyncio
    async def test_user_deviation_makes_the_card_due_and_keeps_its_schedule(self, db_factory):
        uid = await _user(db_factory, {"white": WHITE})
        later = datetime(2030, 1, 1)
        async with db_factory() as s:
            row = await s.scalar(select(RepertoireMove).where(
                RepertoireMove.position_key == _key("e4", "e5")))
            card = await s.scalar(select(ReviewCard).where(ReviewCard.repertoire_move_id == row.id))
            card.ease, card.interval_days, card.repetitions, card.due_at = 2.7, 15.0, 4, later
            await s.commit()
            card_id = card.id
        gid = await _game(db_factory, uid, _positions(["e4", "e5", "Bc4"]))

        await _check(db_factory, uid)

        [dev] = await _get(db_factory, RepertoireDeviation, game_id=gid)
        assert (dev.side, dev.ply, dev.expected_uci, dev.played_uci) == ("user", 3, "g1f3", "f1c4")
        [card] = await _get(db_factory, ReviewCard, id=card_id)
        assert card.due_at < later and card.due_at > datetime.utcnow() - timedelta(minutes=1)
        assert (card.ease, card.interval_days, card.repetitions) == (2.7, 15.0, 4)

    @pytest.mark.asyncio
    async def test_a_game_past_the_prepared_part(self, db_factory):
        uid = await _user(db_factory, {"white": WHITE})
        gid = await _game(db_factory, uid, _positions(["e4", "e5", "Nf3", "Nc6", "Bc4"]))

        await _check(db_factory, uid)

        [game] = await _get(db_factory, AnalyzedGame, id=gid)
        assert game.repertoire_checked_at is not None and game.repertoire_exit_ply is None
        assert await _get(db_factory, RepertoireDeviation, game_id=gid) == []

    @pytest.mark.asyncio
    async def test_theory_flag(self, db_factory):
        uid = await _user(db_factory, {"white": WHITE})
        async with db_factory() as s:
            s.add(TheoryMove(position_key=_key("e4"), move_uci="c7c5", line_name="Sicilian Defense"))
            await s.commit()
        sicilian = await _game(db_factory, uid, _positions(["e4", "c5"]))
        odd = await _game(db_factory, uid, _positions(["e4", "a5"]))

        await _check(db_factory, uid)

        assert (await _get(db_factory, RepertoireDeviation, game_id=sicilian))[0].played_was_theory
        assert not (await _get(db_factory, RepertoireDeviation, game_id=odd))[0].played_was_theory

    @pytest.mark.asyncio
    async def test_games_wait_until_their_colour_has_a_repertoire(self, db_factory):
        uid = await _user(db_factory, {"white": WHITE})
        as_black = await _game(db_factory, uid, _positions(["d4", "d5"]), color="black")
        no_moves = await _game(db_factory, uid, None)

        assert await _check(db_factory, uid) == 0

        for gid in (as_black, no_moves):
            [game] = await _get(db_factory, AnalyzedGame, id=gid)
            assert game.repertoire_checked_at is None

    @pytest.mark.asyncio
    async def test_only_the_given_games(self, db_factory):
        uid = await _user(db_factory, {"white": WHITE})
        one = await _game(db_factory, uid, _positions(["e4", "c5"]))
        two = await _game(db_factory, uid, _positions(["e4", "e6"]))

        assert await _check(db_factory, uid, [one]) == 1
        assert await _check(db_factory, uid, []) == 0

        [game] = await _get(db_factory, AnalyzedGame, id=two)
        assert game.repertoire_checked_at is None


class _FakeLichess:
    EXPORT_BATCH_SIZE = 300

    def __init__(self, games):
        self.games = games

    async def export_games_by_ids(self, ids):
        return [self.games[i] for i in ids if i in self.games]


class TestBackfillChecksGames:

    @pytest.mark.asyncio
    async def test_games_that_get_their_moves_are_checked(self, db_factory):
        uid = await _user(db_factory, {"white": WHITE})
        gid = await _game(db_factory, uid, None)
        async with db_factory() as s:
            lichess_id = (await s.get(AnalyzedGame, gid)).lichess_game_id
        game = LichessGame(
            id=lichess_id, created_at=datetime(2026, 11, 1),
            white=Player(id="dev", name="dev", color="white"),
            black=Player(id="opp", name="opp", color="black"),
            moves=["e4", "c5", "Nf3"], speed="rapid",
        )

        await backfill_service.backfill_moves(AnalysisJob(), uid, _FakeLichess({lichess_id: game}))

        [dev] = await _get(db_factory, RepertoireDeviation, game_id=gid)
        assert (dev.side, dev.ply, dev.played_uci) == ("opponent", 2, "c7c5")


class TestRecheckWhenTheRepertoireGrows:
    """The repertoire graph builds a repertoire one move at a time: games that
    followed it to its end are checked again when it grows."""

    @pytest.mark.asyncio
    async def test_a_game_that_followed_to_the_end_is_checked_again(self, db_factory):
        from app.services.deviation_service import recheck_games

        uid = await _user(db_factory, {"white": {START: "e2e4"}})
        followed = await _game(db_factory, uid, _positions(["e4", "e5", "Bc4", "Nc6"]))
        left = await _game(db_factory, uid, _positions(["d4", "d5"]))  # left at move 1
        assert await _check(db_factory, uid) == 2  # both checked; only the d4 game deviates
        [game] = await _get(db_factory, AnalyzedGame, id=followed)
        assert game.repertoire_plies == 1 and game.repertoire_exit_ply is None

        async with db_factory() as s:  # the user adds 2.Nf3 after 1...e5
            row = RepertoireMove(user_id=uid, color="white", position_key=_key("e4", "e5"),
                                 move_uci=_uci(["e4", "e5"], "Nf3"), move_san="Nf3", source="manual")
            s.add(row)
            await s.flush()
            assert await recheck_games(s, uid, "white") == 1  # the d4 game isn't checked again
            await s.commit()

        [game] = await _get(db_factory, AnalyzedGame, id=followed)
        assert game.repertoire_exit_ply == 3 and game.repertoire_plies == 2
        devs = await _get(db_factory, RepertoireDeviation, user_id=uid)
        assert sorted((d.game_id, d.side) for d in devs) == [(followed, "user"), (left, "user")]

    @pytest.mark.asyncio
    async def test_other_colour_is_left_alone(self, db_factory):
        from app.services.deviation_service import recheck_games

        uid = await _user(db_factory, {"white": {START: "e2e4"}, "black": {_key("e4"): "c7c5"}})
        black = await _game(db_factory, uid, _positions(["e4", "c5", "Nf3"]), color="black")
        await _check(db_factory, uid)
        [before] = await _get(db_factory, AnalyzedGame, id=black)
        async with db_factory() as s:
            await recheck_games(s, uid, "white")
            await s.commit()
        [after] = await _get(db_factory, AnalyzedGame, id=black)
        assert after.repertoire_checked_at == before.repertoire_checked_at

    @pytest.mark.asyncio
    async def test_emptying_the_repertoire_clears_the_depth(self, db_factory):
        from sqlalchemy import delete as sa_delete

        from app.services.deviation_service import recheck_games

        uid = await _user(db_factory, {"white": {START: "e2e4"}})
        game = await _game(db_factory, uid, _positions(["e4", "e5", "Nf3"]))
        await _check(db_factory, uid)
        async with db_factory() as s:
            await s.execute(sa_delete(ReviewCard).where(ReviewCard.user_id == uid))
            await s.execute(sa_delete(RepertoireMove).where(RepertoireMove.user_id == uid))
            await recheck_games(s, uid, "white")
            await s.commit()
        [row] = await _get(db_factory, AnalyzedGame, id=game)
        assert row.repertoire_checked_at is None and row.repertoire_plies is None
