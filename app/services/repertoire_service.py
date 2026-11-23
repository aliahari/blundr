"""
The opening tree built from a user's games, and the repertoire suggested
from it (specs/features/02-openings, design D3; R2.1-R2.3, R2.5).

Pure functions on plain data, so they're tested on hand-made games and the
routes (task 6) only load games and the opening book and call them.

Positions are keyed by board.epd() (placement, side to move, castling, en
passant only when a capture is legal), the key used across Phase 2, so a
position reached through two move orders is one node.
"""
from collections import Counter, deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

import chess

from .analysis_service import win_prob

START_KEY = chess.Board().epd()

# Defaults from the spec: a position needs this many of the user's games to
# be part of the suggested repertoire, which stops after move 15.
MIN_GAMES = 3
MAX_PLY = 30
# Average win-% the user's usual move gives away compared with the engine's
# best move before the suggestion offers the better move (R2.3).
WORSE_WINPROB = 10.0

Theory = Mapping[tuple[str, str], str]  # (position key, move uci) -> named line
Names = Mapping[str, tuple[str, str]]   # position key -> (eco, name)


@dataclass
class TreeGame:
    """What the tree needs from one analyzed game."""
    user_color: str             # "white" | "black"
    result: str                 # "1-0" | "0-1" | "1/2-1/2" | "*"
    positions: list[dict]       # AnalyzedGame.positions, decoded
    initial_eval_cp: int | None = None


@dataclass
class Edge:
    """A move played from a node, aggregated over the user's games."""
    uci: str
    san: str
    child_key: str
    count: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    theory_line: str | None = None   # named line it's on, None if off-book
    # For the user's own moves with stored evals: win-% given away compared
    # with the position before, and the engine's preferred moves there.
    drops: list[float] = field(default_factory=list)
    best_ucis: Counter = field(default_factory=Counter)

    @property
    def score(self) -> float:
        """Points per game from the user's side (win 1, draw 0.5)."""
        return (self.wins + 0.5 * self.draws) / self.count if self.count else 0.0

    @property
    def avg_drop(self) -> float | None:
        return sum(self.drops) / len(self.drops) if self.drops else None


@dataclass
class Node:
    key: str
    user_to_move: bool
    moves: dict[str, Edge] = field(default_factory=dict)


@dataclass
class Suggestion:
    """The suggested repertoire move in one of the user's positions."""
    position_key: str
    ply: int                     # plies played before this position
    move_uci: str
    move_san: str
    games: int                   # how often the user played it here
    score: float
    parent_key: str | None       # the user's previous repertoire position on the line
    opening: tuple[str, str] | None   # (eco, name), inherited from the last named position
    theory_line: str | None      # named line the move is on, None if off-book
    # When the move is off-book: every theory move from this position,
    # (uci, line), the ones the user has played first.
    theory_alternatives: list[tuple[str, str]]
    avg_drop: float | None       # average win-% given away, when evals exist
    better_uci: str | None       # engine's move, when the usual one is clearly worse


def _outcome(result: str, user_color: str) -> str | None:
    if result == "1/2-1/2":
        return "draw"
    if result in ("1-0", "0-1"):
        won = (result == "1-0") == (user_color == "white")
        return "win" if won else "loss"
    return None


def build_tree(
    games: Iterable[TreeGame],
    color: str,
    theory: Theory | None = None,
    max_ply: int = MAX_PLY,
) -> dict[str, Node]:
    """
    The opening tree of the user's games with `color`, up to `max_ply` plies.
    Each game counts once per edge, even if it passes the same position twice.
    """
    theory = theory or {}
    user_is_white = color == "white"
    tree: dict[str, Node] = {}

    def node_for(board: chess.Board) -> Node:
        key = board.epd()
        node = tree.get(key)
        if node is None:
            node = Node(key=key, user_to_move=(board.turn == chess.WHITE) == user_is_white)
            tree[key] = node
        return node

    for game in games:
        if game.user_color != color:
            continue
        outcome = _outcome(game.result, game.user_color)
        board = chess.Board()
        node = node_for(board)
        seen: set[tuple[str, str]] = set()
        prev_eval = game.initial_eval_cp

        for entry in game.positions[:max_ply]:
            try:
                move = chess.Move.from_uci(entry["uci"])
                san = board.san(move)
            except (ValueError, KeyError):
                break  # stored data shouldn't be corrupt; stop rather than guess
            users_move = node.user_to_move
            board.push(move)
            child = node_for(board)

            edge = node.moves.get(move.uci())
            if edge is None:
                edge = Edge(uci=move.uci(), san=san, child_key=child.key,
                            theory_line=theory.get((node.key, move.uci())))
                node.moves[move.uci()] = edge
            if (node.key, move.uci()) not in seen:
                seen.add((node.key, move.uci()))
                edge.count += 1
                if outcome == "win":
                    edge.wins += 1
                elif outcome == "draw":
                    edge.draws += 1
                elif outcome == "loss":
                    edge.losses += 1
                eval_after = entry.get("eval_cp")
                if users_move and prev_eval is not None and eval_after is not None:
                    edge.drops.append(max(0.0, win_prob(prev_eval) - win_prob(eval_after)))
                    if entry.get("best_uci"):
                        edge.best_ucis[entry["best_uci"]] += 1

            prev_eval = entry.get("eval_cp")
            node = child

    if START_KEY not in tree:
        tree[START_KEY] = Node(key=START_KEY, user_to_move=user_is_white)
    return tree


def _theory_by_position(theory: Theory) -> dict[str, list[tuple[str, str]]]:
    index: dict[str, list[tuple[str, str]]] = {}
    for (key, uci), line in theory.items():
        index.setdefault(key, []).append((uci, line))
    return index


def suggest(
    tree: dict[str, Node],
    color: str,
    min_games: int = MIN_GAMES,
    max_ply: int = MAX_PLY,
    worse_winprob: float = WORSE_WINPROB,
    theory: Theory | None = None,
    names: Names | None = None,
) -> list[Suggestion]:
    """
    The suggested repertoire, parents before children (line order).

    From the start position, follow only edges played in at least
    `min_games` games: at the user's turn take their most-played move (ties
    broken by results, then by move for a stable order); at the opponent's
    turn follow every such reply. A position reached again through another
    move order is suggested once, under the line that reached it first.
    """
    names = names or {}
    theory_index = _theory_by_position(theory or {})
    suggestions: list[Suggestion] = []
    visited: set[str] = set()
    # (position key, plies played, parent repertoire key, inherited opening name)
    queue: deque[tuple[str, int, str | None, tuple[str, str] | None]] = deque(
        [(START_KEY, 0, None, None)]
    )

    while queue:
        key, ply, parent_key, opening = queue.popleft()
        if key in visited or ply >= max_ply:
            continue
        visited.add(key)
        node = tree.get(key)
        if node is None:
            continue
        opening = names.get(key, opening)
        common = [e for e in node.moves.values() if e.count >= min_games]
        if not common:
            continue

        if node.user_to_move:
            edge = max(common, key=lambda e: (e.count, e.score, e.uci))
            avg_drop = edge.avg_drop
            better_uci = None
            if avg_drop is not None and avg_drop >= worse_winprob:
                others = [(n, uci) for uci, n in edge.best_ucis.items() if uci != edge.uci]
                better_uci = max(others)[1] if others else None
            alternatives: list[tuple[str, str]] = []
            if edge.theory_line is None:
                played = {uci: e.count for uci, e in node.moves.items()}
                alternatives = sorted(
                    theory_index.get(key, []),
                    key=lambda item: (-played.get(item[0], 0), item[0]),
                )
            suggestions.append(Suggestion(
                position_key=key, ply=ply, move_uci=edge.uci, move_san=edge.san,
                games=edge.count, score=edge.score, parent_key=parent_key,
                opening=opening, theory_line=edge.theory_line,
                theory_alternatives=alternatives, avg_drop=avg_drop, better_uci=better_uci,
            ))
            queue.append((edge.child_key, ply + 1, key, opening))
        else:
            for edge in sorted(common, key=lambda e: (-e.count, e.uci)):
                queue.append((edge.child_key, ply + 1, parent_key, opening))

    return suggestions
