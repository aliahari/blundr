"""
Master move statistics, built from the 40H database of master games
(specs/features/02-openings/repertoire-graph.md, "Masters data, from 40H").

`build_masters_db` turns a PGN file into a small SQLite file of
(position, move) → results; `MastersBook` reads it; `is_theory` is the one
rule for whether a move counts as theory. The file lives apart from the main
database so it can be rebuilt at will.
"""
from __future__ import annotations

import multiprocessing
import re
import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import chess

THEORY_MIN_MASTER_GAMES = 10
MASTERS_MIN_POSITION_GAMES = 3
DEFAULT_MAX_PLY = 30

_BATCH_GAMES = 2000
_LOOKUP_CHUNK = 500
_RESULTS = {"1-0": 0, "1/2-1/2": 1, "0-1": 2}
_COMMENT = re.compile(r"\{[^}]*\}|;[^\n]*")
_NAG = re.compile(r"\$\d+")
_MOVE_NUMBER = re.compile(r"^\d+\.+")
_SUFFIX = re.compile(r"[!?]+$")


@dataclass(frozen=True)
class MasterMove:
    uci: str
    games: int
    white: int
    draws: int
    black: int


def _read_games(path: Path) -> Iterator[tuple[str | None, str]]:
    """Yield (result, movetext) per game, reading only the Result header."""
    result: str | None = None
    lines: list[str] = []
    in_moves = False
    with open(path, encoding="latin-1") as f:
        for line in f:
            if line.startswith("["):
                if in_moves:
                    yield result, "\n".join(lines)
                    result, lines, in_moves = None, [], False
                if line.startswith("[Result "):
                    parts = line.split('"')
                    result = parts[1] if len(parts) > 1 else None
            elif line.strip():
                in_moves = True
                lines.append(line.rstrip("\n"))
    if lines:
        yield result, "\n".join(lines)


def _strip_variations(text: str) -> str:
    out, depth = [], 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def _count_batch(
    batch: list[tuple[str | None, str]], max_ply: int
) -> list[tuple[str, str, int, int, int]]:
    agg: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0, 0])
    for result, text in batch:
        r = _RESULTS.get(result or "")
        if r is None:
            continue
        text = _strip_variations(_NAG.sub(" ", _COMMENT.sub(" ", text)))
        board = chess.Board()
        ply = 0
        for tok in text.split():
            if ply >= max_ply:
                break
            tok = _MOVE_NUMBER.sub("", tok)
            if not tok or tok in _RESULTS or tok == "*":
                continue
            try:
                move = board.parse_san(_SUFFIX.sub("", tok))
            except ValueError:
                break  # an illegal or garbled move: nothing after it counts
            agg[(board.epd(), move.uci())][r] += 1
            board.push(move)
            ply += 1
    return [(k, u, w, d, b) for (k, u), (w, d, b) in agg.items()]


def _batches(games: Iterable, n: int = _BATCH_GAMES) -> Iterator[list]:
    buf = []
    for g in games:
        buf.append(g)
        if len(buf) == n:
            yield buf
            buf = []
    if buf:
        yield buf


def build_masters_db(
    pgn_path: Path,
    out_path: Path,
    *,
    max_ply: int = DEFAULT_MAX_PLY,
    min_position_games: int = MASTERS_MIN_POSITION_GAMES,
    processes: int = 2,
    source_sha256: str = "",
) -> int:
    """
    Build the masters file at `out_path` from `pgn_path`; return the number of
    finished games read. Writes only to `out_path` (replacing it); callers that
    need an atomic swap build to a temporary name and rename.
    """
    out_path = Path(out_path)
    out_path.unlink(missing_ok=True)
    played = 0

    def counted(games):
        nonlocal played
        for result, text in games:
            if result in _RESULTS:
                played += 1
            yield result, text

    con = sqlite3.connect(out_path)
    try:
        con.execute("PRAGMA journal_mode = OFF")
        con.execute("PRAGMA synchronous = OFF")
        con.execute("CREATE TABLE staging (k TEXT, u TEXT, w INT, d INT, b INT)")
        work = partial(_count_batch, max_ply=max_ply)
        batches = _batches(counted(_read_games(pgn_path)))
        insert = "INSERT INTO staging VALUES (?, ?, ?, ?, ?)"
        if processes <= 1:
            for batch in batches:
                con.executemany(insert, work(batch))
        else:
            with multiprocessing.get_context("spawn").Pool(processes) as pool:
                for rows in pool.imap_unordered(work, batches):
                    con.executemany(insert, rows)
        con.execute(
            "CREATE TABLE master_moves (position_key TEXT, move_uci TEXT, white INT, draws INT, "
            "black INT, PRIMARY KEY (position_key, move_uci)) WITHOUT ROWID"
        )
        con.execute(
            "INSERT INTO master_moves "
            "SELECT k, u, sum(w), sum(d), sum(b) FROM staging "
            "WHERE k IN (SELECT k FROM staging GROUP BY k HAVING sum(w + d + b) >= ?) "
            "GROUP BY k, u",
            (min_position_games,),
        )
        con.execute("DROP TABLE staging")
        con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        con.executemany(
            "INSERT INTO meta VALUES (?, ?)",
            [
                ("source_sha256", source_sha256),
                ("built_at", datetime.now(UTC).isoformat()),
                ("games", str(played)),
            ],
        )
        con.commit()
        con.execute("VACUUM")
    finally:
        con.close()
    return played


def masters_db_path(masters_db_path: str, database_url: str) -> Path:
    """MASTERS_DB_PATH, or masters.sqlite beside the main SQLite database file."""
    if masters_db_path:
        return Path(masters_db_path)
    db_file = database_url.split("///", 1)[-1] if "///" in database_url else "blundr.db"
    return Path(db_file).parent / "masters.sqlite"


def read_meta(path: Path) -> dict[str, str]:
    """The masters file's meta table, or {} when the file is missing or unreadable."""
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return dict(con.execute("SELECT key, value FROM meta").fetchall())
        finally:
            con.close()
    except sqlite3.Error:
        return {}


class MastersBook:
    """Read-only lookups in a masters file. Synchronous: call through asyncio.to_thread."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.available = self._check()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)

    def _check(self) -> bool:
        if not self.path.is_file():
            return False
        try:
            con = self._connect()
            try:
                con.execute("SELECT 1 FROM master_moves LIMIT 1").fetchall()
            finally:
                con.close()
            return True
        except sqlite3.Error:
            return False

    def moves(self, keys: Iterable[str]) -> dict[str, list[MasterMove]]:
        """Master moves per position key, most played first; unknown keys are left out."""
        if not self.available:
            return {}
        keys = list(dict.fromkeys(keys))
        found: dict[str, list[MasterMove]] = {}
        con = self._connect()
        try:
            for i in range(0, len(keys), _LOOKUP_CHUNK):
                chunk = keys[i : i + _LOOKUP_CHUNK]
                rows = con.execute(
                    "SELECT position_key, move_uci, white, draws, black FROM master_moves "
                    f"WHERE position_key IN ({','.join('?' * len(chunk))})",
                    chunk,
                ).fetchall()
                for key, uci, w, d, b in rows:
                    found.setdefault(key, []).append(MasterMove(uci, w + d + b, w, d, b))
        finally:
            con.close()
        for moves in found.values():
            moves.sort(key=lambda m: (-m.games, m.uci))
        return found


def is_theory(
    key: str,
    uci: str,
    masters: list[MasterMove] | None,
    dataset: Mapping[tuple[str, str], str],
    masters_available: bool,
) -> bool:
    """
    A move is theory when it's on a named dataset line, or — once the masters
    file exists — when masters played it at least THEORY_MIN_MASTER_GAMES
    times. In a quiet position (fewer master games than that in total) any
    master move counts.
    """
    if (key, uci) in dataset:
        return True
    if not masters_available or not masters:
        return False
    total = sum(m.games for m in masters)
    games = next((m.games for m in masters if m.uci == uci), 0)
    if total < THEORY_MIN_MASTER_GAMES:
        return games > 0
    return games >= THEORY_MIN_MASTER_GAMES
