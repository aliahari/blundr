"""
Phase 2, task 4: opening names and theory moves from lichess-org/chess-openings.

build_book() is tested on small hand-made rows and on the real files shipped
in app/data/chess-openings; the loader runs against a temp database swapped
in for opening_book.async_session.
"""
import shutil

import chess
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models.db_models import OpeningName, TheoryMove
from app.services import opening_book
from app.services.opening_book import DATA_DIR, build_book, load_opening_book, read_rows


def _key(*sans: str) -> str:
    board = chess.Board()
    for san in sans:
        board.push_san(san)
    return board.epd()


def _uci(sans_before: list[str], san: str) -> str:
    board = chess.Board()
    for s in sans_before:
        board.push_san(s)
    return board.parse_san(san).uci()


class TestBuildBook:

    def test_names_and_theory_from_rows(self):
        names, theory = build_book([
            ("C20", "King's Pawn Game", "1. e4 e5"),
            ("C60", "Ruy Lopez", "1. e4 e5 2. Nf3 Nc6 3. Bb5"),
        ])

        assert names[_key("e4", "e5")] == ("C20", "King's Pawn Game")
        assert names[_key("e4", "e5", "Nf3", "Nc6", "Bb5")] == ("C60", "Ruy Lopez")
        # Every move along every line is theory...
        assert (_key("e4", "e5", "Nf3", "Nc6"), _uci(["e4", "e5", "Nf3", "Nc6"], "Bb5")) in theory
        # ...and keeps the shortest named line it's on.
        assert theory[(chess.Board().epd(), "e2e4")] == "King's Pawn Game"

    def test_shortest_line_wins_a_shared_position(self):
        names, _ = build_book([
            ("X01", "Longer Name", "1. Nf3 d5 2. d4"),
            ("X02", "Shorter Name", "1. d4 d5"),
            ("X03", "Same Position Longer", "1. d4 Nf6 2. Nf3 d5 3. Ng1 Ng8"),
        ])
        # 1.d4 d5 and the roundabout line reach the same position: shorter wins.
        assert names[_key("d4", "d5")] == ("X02", "Shorter Name")

    def test_ignores_move_numbers_and_black_move_dots(self):
        names, _ = build_book([("B20", "Sicilian Defense", "1. e4 c5")])
        assert names == {_key("e4", "c5"): ("B20", "Sicilian Defense")}


@pytest.fixture(scope="module")
def real_book():
    return build_book(read_rows(DATA_DIR))


class TestRealDataset:

    def test_well_known_positions(self, real_book):
        names, _ = real_book
        assert names[_key("e4", "c5")] == ("B20", "Sicilian Defense")
        assert names[_key("e4", "e5", "Nf3", "Nc6", "Bb5")] == ("C60", "Ruy Lopez")
        assert names[_key("d4", "d5", "c4")] == ("D06", "Queen's Gambit")

    def test_transposed_move_order_finds_the_same_name(self, real_book):
        names, _ = real_book
        nimzo = names[_key("d4", "Nf6", "c4", "e6", "Nc3", "Bb4")]
        assert nimzo[1] == "Nimzo-Indian Defense"
        assert names[_key("c4", "e6", "d4", "Nf6", "Nc3", "Bb4")] == nimzo

    def test_main_line_is_theory_and_a_sideline_is_not(self, real_book):
        _, theory = real_book
        line = ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4", "Nf6"]
        for i, san in enumerate(line):
            assert (_key(*line[:i]), _uci(line[:i], san)) in theory, san
        # 4.Kf1?! in the Ruy Lopez isn't on any named line.
        before = ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6"]
        assert (_key(*before), _uci(before, "Kf1")) not in theory

    def test_size_is_plausible(self, real_book):
        names, theory = real_book
        assert 3000 < len(names) < 6000
        assert len(theory) > len(names)


@pytest_asyncio.fixture
async def temp_db(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'book.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(opening_book, "async_session", session_factory)
    yield session_factory
    await engine.dispose()


async def _counts(session_factory):
    async with session_factory() as s:
        return (
            await s.scalar(select(func.count()).select_from(OpeningName)),
            await s.scalar(select(func.count()).select_from(TheoryMove)),
        )


class TestLoader:

    @pytest.mark.asyncio
    async def test_loads_once_and_reloads_when_files_change(self, temp_db, tmp_path):
        data = tmp_path / "chess-openings"
        shutil.copytree(DATA_DIR, data)

        assert await load_opening_book(data) is True
        names, theory = await _counts(temp_db)
        assert names > 3000 and theory > names

        # Same files: nothing to do.
        assert await load_opening_book(data) is False
        assert await _counts(temp_db) == (names, theory)

        # A changed file is picked up, and old rows don't linger.
        (data / "a.tsv").write_text("eco\tname\tpgn\nA00\tAmar Opening\t1. Nh3\n")
        for f in ("b", "c", "d", "e"):
            (data / f"{f}.tsv").write_text("eco\tname\tpgn\n")
        assert await load_opening_book(data) is True
        assert await _counts(temp_db) == (1, 1)
