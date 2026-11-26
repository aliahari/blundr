"""Master move statistics built from PGN (repertoire graph, task 1)."""
import sqlite3
from pathlib import Path

import chess
import pytest

from app.services.masters_service import (
    MasterMove,
    MastersBook,
    build_masters_db,
    is_theory,
    masters_db_path,
)

FIXTURE = Path(__file__).parent / "fixtures" / "masters_sample.pgn"


def key_after(*sans: str) -> str:
    board = chess.Board()
    for san in sans:
        board.push_san(san)
    return board.epd()


def counts(path: Path, key: str) -> dict[str, tuple[int, int, int]]:
    con = sqlite3.connect(path)
    try:
        rows = con.execute(
            "SELECT move_uci, white, draws, black FROM master_moves WHERE position_key = ?", (key,)
        ).fetchall()
    finally:
        con.close()
    return {u: (w, d, b) for u, w, d, b in rows}


@pytest.fixture
def built(tmp_path) -> Path:
    out = tmp_path / "masters.sqlite"
    build_masters_db(FIXTURE, out, min_position_games=1, processes=1, source_sha256="abc123")
    return out


def test_build_counts_results_per_position_and_move(tmp_path):
    out = tmp_path / "masters.sqlite"
    games = build_masters_db(FIXTURE, out, min_position_games=1, processes=1, source_sha256="abc123")
    assert games == 11  # the "*" game isn't counted
    start = counts(out, key_after())
    # G1 1-0, G2 draw, G3 0-1, G4 1-0, G5 1-0, G12 0-1; G9 ("*") skipped
    assert start["e2e4"] == (3, 1, 2)
    # G6, G7, G8, and G10 (whose 2.Ke3 is illegal: only its first two plies count)
    assert start["d2d4"] == (2, 1, 1)
    assert start["c2c4"] == (1, 0, 0)
    assert counts(out, key_after("d4", "d5")) == {"c2c4": (1, 1, 0)}
    # comments, NAGs and move suffixes don't break parsing
    assert counts(out, key_after("e4", "c5", "Nf3")) == {"d7d6": (1, 0, 0)}
    con = sqlite3.connect(out)
    meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
    con.close()
    assert meta["source_sha256"] == "abc123"
    assert meta["built_at"]


def test_build_with_worker_processes_matches(tmp_path):
    out = tmp_path / "masters.sqlite"
    build_masters_db(FIXTURE, out, min_position_games=1, processes=2)
    assert counts(out, key_after())["e2e4"] == (3, 1, 2)


def test_build_prunes_rare_positions(tmp_path):
    out = tmp_path / "pruned.sqlite"
    build_masters_db(FIXTURE, out, min_position_games=3, processes=1)
    assert counts(out, key_after("e4", "c5")) == {}  # reached by G4 and G5 only
    assert counts(out, key_after("e4", "e5"))  # G1, G2, G3
    out1 = tmp_path / "all.sqlite"
    build_masters_db(FIXTURE, out1, min_position_games=1, processes=1)
    assert counts(out1, key_after("e4", "c5"))


def test_build_stops_at_max_ply(tmp_path):
    out = tmp_path / "short.sqlite"
    build_masters_db(FIXTURE, out, max_ply=2, min_position_games=1, processes=1)
    assert counts(out, key_after("e4"))  # the second ply is counted
    assert counts(out, key_after("e4", "e5")) == {}  # the third isn't


def test_build_replaces_an_existing_file(built):
    build_masters_db(FIXTURE, built, min_position_games=1, processes=1, source_sha256="def456")
    assert counts(built, key_after())["e2e4"] == (3, 1, 2)


def test_book_lookup_and_unavailable(built, tmp_path):
    book = MastersBook(built)
    assert book.available
    start, unknown = key_after(), key_after("a4")
    found = book.moves([start, unknown])
    assert unknown not in found
    by_uci = {m.uci: m for m in found[start]}
    assert by_uci["e2e4"] == MasterMove("e2e4", 6, 3, 1, 2)
    assert [m.uci for m in found[start]] == ["e2e4", "d2d4", "c2c4"]  # most played first

    missing = MastersBook(tmp_path / "nope.sqlite")
    assert missing.available is False
    assert missing.moves([start]) == {}
    assert not (tmp_path / "nope.sqlite").exists()  # looking doesn't create it


def test_book_lookup_in_chunks(built):
    keys = [key_after()] + [f"fake {i}" for i in range(1200)]
    assert list(MastersBook(built).moves(keys)) == [key_after()]


def mm(uci: str, games: int) -> MasterMove:
    return MasterMove(uci, games, games, 0, 0)


@pytest.mark.parametrize(
    "masters, dataset, available, expected",
    [
        ([mm("e2e4", 10), mm("d2d4", 30)], {}, True, True),  # 10 games is enough
        ([mm("e2e4", 9), mm("d2d4", 30)], {}, True, False),  # 9 in a busy position isn't
        ([mm("e2e4", 1), mm("d2d4", 6)], {}, True, True),  # a quiet position (7): any master move
        ([mm("d2d4", 6)], {}, True, False),  # quiet, but masters never played it
        (None, {}, True, False),  # a position masters never reached
        (None, {("K", "e2e4"): "King's Pawn"}, True, True),  # unless it's on a dataset line
        ([mm("e2e4", 50)], {}, False, False),  # no masters file: the dataset decides
        (None, {("K", "e2e4"): "King's Pawn"}, False, True),
    ],
)
def test_is_theory(masters, dataset, available, expected):
    assert is_theory("K", "e2e4", masters, dataset, available) is expected


def test_masters_db_path_defaults_beside_the_database():
    assert masters_db_path("", "sqlite+aiosqlite:////app/data/blundr.db") == Path("/app/data/masters.sqlite")
    assert masters_db_path("", "sqlite+aiosqlite:///./blundr.db") == Path("masters.sqlite")
    assert masters_db_path("/x/m.sqlite", "sqlite+aiosqlite:////app/data/blundr.db") == Path("/x/m.sqlite")
