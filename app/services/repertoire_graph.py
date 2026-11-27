"""
The repertoire graph (specs/features/02-openings/repertoire-graph.md, "The
graph's data"): the user's game tree for one colour, merged with their
repertoire, master moves and the dataset's theory, folded and marked.

Pure: no database, no engine. Positions are board.epd() keys.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field

import chess

from .masters_service import MasterMove, is_theory
from .repertoire_service import MAX_PLY, START_KEY, Node, reachable

ACT_MIN_GAMES = 2   # games that must reach a position before it gets a mark
SHOW_MIN_GAMES = 2  # games a move needs to be shown without unfolding

YOU_LEFT_THEORY = "you_left_theory"  # the one mark: finding gaps is the builder's job


@dataclass
class GraphEdge:
    parent: str
    child: str
    uci: str
    san: str
    games: int
    w: int
    d: int
    l: int  # noqa: E741 — win/draw/loss, from the user's side
    master_games: int
    theory: bool
    repertoire: bool
    transposition: bool = False


@dataclass
class GraphNode:
    key: str
    user_to_move: bool
    games: int
    ply: int
    line: list[str]  # uci from the start, along the spanning parents
    parent: str | None
    opening: tuple[str, str] | None
    mark: str | None = None
    folded: int = 0


@dataclass
class Graph:
    nodes: dict[str, GraphNode]
    edges: list[GraphEdge]
    todo: list[str] = field(default_factory=list)  # marked keys, most games first


def _user_to_move(board: chess.Board, color: str) -> bool:
    return (board.turn == chess.WHITE) == (color == "white")


def _led_to(color: str, repertoire: Mapping[str, str]) -> set[str]:
    """Positions the repertoire leads to: after each reachable repertoire move,
    plus the start for a Black repertoire (it answers White's first moves)."""
    out: set[str] = set()
    for key in reachable(color, repertoire):
        board = chess.Board(key + " 0 1")
        board.push(chess.Move.from_uci(repertoire[key]))
        out.add(board.epd())
    if color == "black" and repertoire:
        out.add(START_KEY)
    return out


def build_graph(
    tree: dict[str, Node],
    color: str,
    repertoire: Mapping[str, str],
    masters: Mapping[str, list[MasterMove]],
    dataset: Mapping[tuple[str, str], str],
    names: Mapping[str, tuple[str, str]],
    user_deviation_keys: set[str],
    masters_available: bool,
    unfold: set[str] | frozenset[str] = frozenset(),
) -> Graph:
    rep_keys = set(repertoire)
    # Only positions the repertoire leads to can have a reply into it
    led_to = _led_to(color, repertoire)

    # Games reaching each position: the root counts games started, any other
    # position the games arriving there (moves back to the start excluded).
    reached: dict[str, int] = {}
    for node in tree.values():
        for e in node.moves.values():
            if e.child_key != START_KEY:
                reached[e.child_key] = reached.get(e.child_key, 0) + e.count
    root_node = tree.get(START_KEY)
    reached[START_KEY] = sum(e.count for e in root_node.moves.values()) if root_node else 0

    def candidate_edges(key: str, board: chess.Board, user_turn: bool) -> list[GraphEdge]:
        """Every move from `key`: the games', the repertoire's, and opponent
        replies leading to a repertoire position — whether shown or not."""
        masters_here = masters.get(key)
        master_counts = {m.uci: m.games for m in masters_here or ()}
        chosen = repertoire.get(key) if user_turn else None
        edges: dict[str, GraphEdge] = {}

        def make(uci: str, san: str, child: str, games=0, w=0, d=0, lo=0) -> GraphEdge:
            return GraphEdge(
                parent=key, child=child, uci=uci, san=san, games=games, w=w, d=d, l=lo,
                master_games=master_counts.get(uci, 0),
                theory=is_theory(key, uci, masters_here, dataset, masters_available),
                repertoire=uci == chosen,
            )

        node = tree.get(key)
        for e in (node.moves.values() if node else ()):
            edges[e.uci] = make(e.uci, e.san, e.child_key, e.count, e.wins, e.draws, e.losses)
        if chosen and chosen not in edges:
            move = chess.Move.from_uci(chosen)
            if move in board.legal_moves:
                san = board.san(move)
                board.push(move)
                edges[chosen] = make(chosen, san, board.epd())
                board.pop()
        if not user_turn and key in led_to:
            for move in board.legal_moves:
                uci = move.uci()
                if uci in edges:
                    continue
                board.push(move)
                child = board.epd()
                board.pop()
                if child in rep_keys:
                    edges[uci] = make(uci, board.san(move), child)
        return sorted(edges.values(), key=lambda e: (-e.games, e.san))

    def shown(e: GraphEdge) -> bool:
        return (
            e.repertoire
            or e.games >= SHOW_MIN_GAMES
            or e.parent in unfold
            or (e.games == 0 and e.child in rep_keys)  # leads to a repertoire position
        )

    root_board = chess.Board()
    nodes: dict[str, GraphNode] = {
        START_KEY: GraphNode(
            key=START_KEY, user_to_move=_user_to_move(root_board, color),
            games=reached[START_KEY], ply=0, line=[], parent=None,
            opening=names.get(START_KEY),
        )
    }
    edges: list[GraphEdge] = []
    out_edges: dict[str, list[GraphEdge]] = {}
    queue: deque[str] = deque([START_KEY])
    while queue:
        key = queue.popleft()
        here = nodes[key]
        board = chess.Board(key + " 0 1")
        candidates = candidate_edges(key, board, here.user_to_move)
        visible = [e for e in candidates if shown(e)]
        here.folded = len(candidates) - len(visible)
        out_edges[key] = visible
        for e in visible:
            if e.child in nodes:
                e.transposition = True
            elif here.ply >= MAX_PLY:
                here.folded += 1
                continue
            else:
                board.push(chess.Move.from_uci(e.uci))
                nodes[e.child] = GraphNode(
                    key=e.child, user_to_move=_user_to_move(board, color),
                    games=reached.get(e.child, 0), ply=here.ply + 1,
                    line=here.line + [e.uci], parent=key,
                    opening=names.get(e.child) or here.opening,
                )
                board.pop()
                queue.append(e.child)
            edges.append(e)

    # Marks, at the user's turn only
    into: dict[str, list[GraphEdge]] = {}
    for e in edges:
        into.setdefault(e.child, []).append(e)
    for key, node in nodes.items():
        if not node.user_to_move:
            continue
        if key in user_deviation_keys:
            node.mark = YOU_LEFT_THEORY
            continue
        if key in repertoire or node.games < ACT_MIN_GAMES:
            continue
        incoming = into.get(key, [])
        tree_edge = next((e for e in incoming if not e.transposition), None)
        arrived_in_theory = key == START_KEY or (tree_edge is not None and tree_edge.theory)
        played = [e for e in out_edges.get(key, []) if e.games > 0]
        if arrived_in_theory and played and not played[0].theory:
            node.mark = YOU_LEFT_THEORY

    todo = sorted(
        (k for k, n in nodes.items() if n.mark),
        key=lambda k: (-nodes[k].games, nodes[k].ply, k),
    )
    return Graph(nodes=nodes, edges=edges, todo=todo)
