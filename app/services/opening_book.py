"""
Opening names and theory moves from Lichess's chess-openings dataset
(specs/features/02-openings, R2.4 and R2.5).

The dataset (CC0, shipped in app/data/chess-openings, see SOURCE.md there)
has about 3,900 named openings, each with an ECO code and a `pgn` line: the
well-known moves, or the most common master moves, into that named
position. From it Blundr builds two tables:

- opening_names: the position at the end of each line -> (ECO, name).
  Transposed move orders reach the same position key, so they find the same
  name.
- theory_moves: every move along every line, keyed by the position before
  it -> the shortest named line it's on. A move in that table is "theory";
  any other move is "off-book".

Position keys are board.epd(), the same key the rest of Phase 2 uses. The
tables are rebuilt at startup only when the files change (their hash is
kept in app_meta).
"""
import csv
import hashlib
import logging
from pathlib import Path

import chess
from sqlalchemy import delete, insert

from ..db import async_session
from ..models.db_models import AppMeta, OpeningName, TheoryMove

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "chess-openings"
TSV_FILES = ("a.tsv", "b.tsv", "c.tsv", "d.tsv", "e.tsv")
META_KEY = "opening_book_hash"

Row = tuple[str, str, str]  # (eco, name, pgn)


def read_rows(data_dir: Path = DATA_DIR) -> list[Row]:
    """All (eco, name, pgn) rows from the dataset's TSV files, in file order."""
    rows: list[Row] = []
    for filename in TSV_FILES:
        with open(data_dir / filename, newline="", encoding="utf-8") as fh:
            for record in csv.DictReader(fh, delimiter="\t"):
                rows.append((record["eco"], record["name"], record["pgn"]))
    return rows


def _sans(pgn: str) -> list[str]:
    """The moves of a dataset line: '1. e4 c5 2. Nf3' -> ['e4', 'c5', 'Nf3']."""
    return [token for token in pgn.split() if not token[0].isdigit()]


def build_book(rows: list[Row]) -> tuple[dict[str, tuple[str, str]], dict[tuple[str, str], str]]:
    """
    (names, theory) from dataset rows.

    names:  position key at the end of a line -> (eco, name)
    theory: (position key before a move, move uci) -> name of the shortest
            line containing that move

    Where several lines share a position or a move, the shortest line wins
    (fewest moves), and the first in file order breaks ties. A line that
    can't be replayed is logged and skipped.
    """
    names: dict[str, tuple[str, str, int]] = {}
    theory: dict[tuple[str, str], tuple[str, int]] = {}

    for eco, name, pgn in rows:
        sans = _sans(pgn)
        board = chess.Board()
        steps: list[tuple[str, str]] = []
        try:
            for san in sans:
                key = board.epd()
                move = board.push_san(san)
                steps.append((key, move.uci()))
        except ValueError:
            logger.warning(f"Opening book: can't replay {name!r} ({pgn!r}); skipped")
            continue

        length = len(steps)
        end_key = board.epd()
        if end_key not in names or length < names[end_key][2]:
            names[end_key] = (eco, name, length)
        for step in steps:
            if step not in theory or length < theory[step][1]:
                theory[step] = (name, length)

    return (
        {key: (eco, name) for key, (eco, name, _) in names.items()},
        {step: name for step, (name, _) in theory.items()},
    )


def _files_hash(data_dir: Path) -> str:
    digest = hashlib.sha256()
    for filename in TSV_FILES:
        digest.update(filename.encode())
        digest.update((data_dir / filename).read_bytes())
    return digest.hexdigest()


async def load_opening_book(data_dir: Path = DATA_DIR) -> bool:
    """
    Rebuild opening_names and theory_moves when the dataset files changed
    since the last load. Returns whether it rebuilt them.
    """
    files_hash = _files_hash(data_dir)
    async with async_session() as db:
        meta = await db.get(AppMeta, META_KEY)
        if meta is not None and meta.value == files_hash:
            return False

    names, theory = build_book(read_rows(data_dir))

    async with async_session() as db:
        await db.execute(delete(OpeningName))
        await db.execute(delete(TheoryMove))
        if names:
            await db.execute(insert(OpeningName), [
                {"position_key": key, "eco": eco, "name": name}
                for key, (eco, name) in names.items()
            ])
        if theory:
            await db.execute(insert(TheoryMove), [
                {"position_key": key, "move_uci": uci, "line_name": name}
                for (key, uci), name in theory.items()
            ])
        meta = await db.get(AppMeta, META_KEY)
        if meta is None:
            db.add(AppMeta(key=META_KEY, value=files_hash))
        else:
            meta.value = files_hash
        await db.commit()

    logger.info(f"Opening book loaded: {len(names)} named positions, {len(theory)} theory moves")
    return True
