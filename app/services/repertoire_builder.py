"""
The step-by-step builder's walk (specs/features/02-openings/repertoire-builder.md).

Pure: no database, no network. From the start position it follows the
user's repertoire move at their turn and every opponent reply likely enough
at the opponent's turn, most likely first. A position's likelihood is the
product of the opponent replies' shares along the line (the user's own moves
count as 1). A position at the user's turn, in scope, without a repertoire
move is a gap.

Positions are keyed by board.epd(), so a transposition is one position,
visited once, at the likelihood of the likeliest line into it.
"""
import heapq
import itertools
from collections.abc import Mapping
from dataclasses import dataclass, field

import chess

MAX_PLY = 30
EXPLORER_MIN_GAMES = 20
_EPS = 1e-12


@dataclass
class Gap:
    key: str
    line: list[str]  # uci moves from the start
    likelihood: float
    last_reply: str | None  # the opponent's move into it
    last_share: float | None  # that move's share of the games before it
    my_games: int


@dataclass
class Walk:
    gaps: list[Gap] = field(default_factory=list)
    prepared: int = 0  # in-scope positions at the user's turn with a repertoire move
    missing: set[str] = field(default_factory=set)  # opponent positions with no reply data
    thin: set[str] = field(default_factory=set)  # opponent positions with too few games
    prepared_lines: list[tuple[list[str], float]] = field(default_factory=list)  # (line, likelihood)
    likelihood: dict[str, float] = field(default_factory=dict)  # every visited position
    replies: dict[str, list[tuple[str, int]]] = field(default_factory=dict)  # the data it used


def walk(
    color: str,
    repertoire: Mapping[str, str],
    replies: Mapping[str, list[tuple[str, int]]],
    goal: int,
    *,
    my_games: Mapping[str, int] | None = None,
    max_ply: int = MAX_PLY,
    min_games: int = EXPLORER_MIN_GAMES,
) -> Walk:
    """Walk the repertoire at a coverage goal of 1 game in `goal`."""
    my_games = my_games or {}
    user_turn = chess.WHITE if color == "white" else chess.BLACK
    floor = 1.0 / goal
    result = Walk()
    order = itertools.count()
    start = chess.Board()
    # (-likelihood, tiebreak, board, line, last_reply, last_share)
    heap = [(-1.0, next(order), start, [], None, None)]
    seen: set[str] = set()

    while heap:
        neg, _, board, line, last_reply, last_share = heapq.heappop(heap)
        key = board.epd()
        if key in seen:
            continue
        seen.add(key)
        likelihood = -neg
        result.likelihood[key] = likelihood
        if board.is_game_over() or len(line) >= max_ply:
            continue

        if board.turn == user_turn:
            chosen = repertoire.get(key)
            if chosen is None:
                result.gaps.append(Gap(key, line, likelihood, last_reply, last_share,
                                       my_games.get(key, 0)))
                continue
            try:
                move = chess.Move.from_uci(chosen)
            except ValueError:
                continue
            if move not in board.legal_moves:
                continue
            result.prepared += 1
            result.prepared_lines.append((line, likelihood))
            child = board.copy(stack=False)
            child.push(move)
            heapq.heappush(heap, (neg, next(order), child, line + [chosen], None, None))
            continue

        here = replies.get(key)
        if here is None:
            result.missing.add(key)
            continue
        total = sum(games for _, games in here)
        if total < min_games:
            result.thin.add(key)
            continue
        for reply, games in here:
            share = games / total
            child_likelihood = likelihood * share
            if child_likelihood + _EPS < floor:
                continue
            try:
                move = chess.Move.from_uci(reply)
            except ValueError:
                continue
            if move not in board.legal_moves:
                continue
            child = board.copy(stack=False)
            child.push(move)
            heapq.heappush(heap, (-child_likelihood, next(order), child, line + [reply],
                                  reply, share))

    result.gaps.sort(key=lambda g: (-g.likelihood, -g.my_games, len(g.line)))
    return result


def completion(result: Walk, prefix: list[str]) -> float | None:
    """How much of what the user will meet on or below `prefix`, at their goal,
    they have prepared: in-scope positions at their turn, each weighted by how
    often it comes up (so a main line counts for more than a sideline). None
    when nothing is in scope there."""
    n = len(prefix)
    prepared = sum(lk for line, lk in result.prepared_lines if line[:n] == prefix)
    open_ = sum(g.likelihood for g in result.gaps if g.line[:n] == prefix)
    total = prepared + open_
    return prepared / total if total else None
