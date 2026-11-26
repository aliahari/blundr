"""The repertoire graph, built from games, repertoire and theory (repertoire graph, task 4)."""
import chess

from app.services.masters_service import MasterMove
from app.services.repertoire_graph import build_graph
from app.services.repertoire_service import START_KEY, TreeGame, build_tree
from tests.test_deviations import _positions


def K(*sans: str) -> str:
    b = chess.Board()
    for s in sans:
        b.push_san(s)
    return b.epd()


def U(sans_before: tuple[str, ...], san: str) -> tuple[str, str]:
    b = chess.Board()
    for s in sans_before:
        b.push_san(s)
    return b.epd(), b.parse_san(san).uci()


def games(color: str, *lines: tuple[str, str]) -> list[TreeGame]:
    """Each line is (moves as a space-separated string, result)."""
    return [TreeGame(color, result, _positions(moves.split())) for moves, result in lines]


def graph(color, game_list, *, repertoire=None, masters=None, dataset=None, names=None,
          deviations=None, available=False, unfold=frozenset()):
    dataset = dataset or {}
    tree = build_tree(game_list, color, theory=dataset)
    return build_graph(tree, color, repertoire or {}, masters or {}, dataset, names or {},
                       deviations or set(), available, unfold)


def edge(g, parent, uci):
    found = [e for e in g.edges if e.parent == parent and e.uci == uci]
    assert len(found) == 1, (parent, uci, found)
    return found[0]


# Dataset lines: 1.e4 e5 2.Nf3, 1.e4 c5 2.Nf3, 1.d4 d5
DATASET = {
    U((), "e4"): "King's Pawn", U(("e4",), "e5"): "Open Game", U(("e4", "e5"), "Nf3"): "King's Knight",
    U(("e4",), "c5"): "Sicilian", U(("e4", "c5"), "Nf3"): "Sicilian",
    U((), "d4"): "Queen's Pawn", U(("d4",), "d5"): "Closed Game",
}


def test_counts_scores_and_theory_per_edge():
    g = graph("white", games("white", ("e4 e5 Nf3", "1-0"), ("e4 e5 Nf3", "1/2-1/2"),
                             ("e4 e5 Qh5", "0-1")), dataset=DATASET,
              names={K("e4"): ("B00", "King's Pawn")})
    e4 = edge(g, START_KEY, "e2e4")
    assert (e4.games, e4.w, e4.d, e4.l, e4.san) == (3, 1, 1, 1, "e4")
    assert e4.theory is True and e4.master_games == 0 and e4.repertoire is False
    assert edge(g, K("e4", "e5"), "g1f3").theory is True
    assert edge(g, K("e4", "e5"), "g1f3").games == 2
    node = g.nodes[K("e4", "e5")]
    assert (node.games, node.ply, node.user_to_move) == (3, 2, True)
    assert node.line == ["e2e4", "e7e5"] and node.parent == K("e4")
    assert node.opening == ("B00", "King's Pawn")  # inherited along the line
    assert g.nodes[START_KEY].games == 3 and g.nodes[START_KEY].opening is None
    assert node.folded == 1  # 2.Qh5 was played once


def test_rare_moves_fold_away():
    board = chess.Board()
    board.push_san("e4")
    board.push_san("e5")
    seconds = [board.san(m) for m in board.legal_moves]
    lines = [(f"e4 e5 {s}", "1-0") for s in seconds]
    g = graph("white", games("white", *lines))
    parent = K("e4", "e5")
    assert g.nodes[parent].folded == len(seconds)
    assert not [e for e in g.edges if e.parent == parent]
    assert set(g.nodes) == {START_KEY, K("e4"), parent}

    g = graph("white", games("white", *lines), unfold={parent})
    assert len([e for e in g.edges if e.parent == parent]) == len(seconds)
    assert g.nodes[parent].folded == 0


def test_transposition_is_one_node_with_a_dashed_link():
    g = graph("white", games("white", ("d4 Nf6 c4 e6", "1-0"), ("d4 Nf6 c4 e6", "1-0"),
                             ("c4 e6 d4 Nf6", "1-0"), ("c4 e6 d4 Nf6", "1-0")))
    meet = K("d4", "Nf6", "c4", "e6")
    assert meet == K("c4", "e6", "d4", "Nf6")
    into = [e for e in g.edges if e.child == meet]
    assert len(into) == 2
    assert sorted(e.transposition for e in into) == [False, True]
    tree_edge = next(e for e in into if not e.transposition)
    assert g.nodes[meet].parent == tree_edge.parent
    assert g.nodes[meet].games == 4


def test_a_line_returning_to_the_start_doesnt_loop():
    g = graph("white", games("white", ("Nf3 Nf6 Ng1 Ng8 e4", "1-0"), ("Nf3 Nf6 Ng1 Ng8 e4", "1-0")))
    back = edge(g, K("Nf3", "Nf6", "Ng1"), "f6g8")
    assert back.child == START_KEY and back.transposition is True
    assert g.nodes[START_KEY].parent is None and g.nodes[START_KEY].ply == 0
    assert edge(g, START_KEY, "e2e4").games == 2


def test_marks():
    rep = {START_KEY: "e2e4"}
    g = graph("white", games(
        "white",
        ("e4 e5 Qh5", "1-0"), ("e4 e5 Qh5", "0-1"),      # you left theory at 2.Qh5
        ("e4 a6 d4", "1-0"), ("e4 a6 d4", "1-0"),        # 1...a6 is off-book: add your answer
        ("e4 c5 Nf3", "1-0"), ("e4 c5 Nf3", "1-0"),      # 1...c5 is theory: extend here
    ), repertoire=rep, dataset=DATASET)
    assert g.nodes[K("e4", "e5")].mark == "you_left_theory"
    assert g.nodes[K("e4", "a6")].mark == "add_answer"
    assert g.nodes[K("e4", "c5")].mark == "extend"
    assert g.nodes[START_KEY].mark is None  # has a repertoire move
    assert g.nodes[K("e4")].mark is None  # the opponent's turn

    # Playing against your repertoire move (a task 12 user deviation) marks it too
    rep2 = {START_KEY: "e2e4", K("e4", "e5"): "g1f3"}
    g = graph("white", games("white", ("e4 e5 Nf3", "1-0"), ("e4 e5 Nf3", "1-0")),
              repertoire=rep2, dataset=DATASET, deviations={K("e4", "e5")})
    assert g.nodes[K("e4", "e5")].mark == "you_left_theory"

    # Below ACT_MIN_GAMES: no mark, even when unfolded into view
    g = graph("white", games("white", ("e4 a6 d4", "1-0"), ("e4 e5 Nf3", "1-0"),
                             ("e4 e5 Nf3", "1-0")),
              repertoire=rep, dataset=DATASET, unfold={K("e4")})
    assert g.nodes[K("e4", "a6")].mark is None

    # An empty repertoire leads nowhere yet: nothing to answer or extend
    g = graph("white", games("white", ("e4 a6 d4", "1-0"), ("e4 a6 d4", "1-0"),
                             ("e4 c5 Nf3", "1-0"), ("e4 c5 Nf3", "1-0")), dataset=DATASET)
    assert g.nodes[K("e4", "a6")].mark is None
    assert g.nodes[K("e4", "c5")].mark is None


def test_black_extends_from_the_start():
    g = graph("black", games("black", ("d4 d5 c4", "0-1"), ("d4 d5 c4", "0-1")),
              repertoire={K("e4"): "c7c5"}, dataset=DATASET)
    assert g.nodes[K("d4")].mark == "extend"  # no answer to 1.d4 yet
    assert g.nodes[START_KEY].user_to_move is False


def test_repertoire_move_not_in_games_is_drawn():
    rep = {START_KEY: "e2e4", K("e4", "e5"): "f1c4", K("e4", "c5"): "g1f3"}
    g = graph("white", games("white", ("e4 e5 Nf3", "1-0"), ("e4 e5 Nf3", "1-0")),
              repertoire=rep, dataset=DATASET)
    bc4 = edge(g, K("e4", "e5"), "f1c4")
    assert (bc4.games, bc4.repertoire, bc4.san) == (0, True, "Bc4")
    assert g.nodes[K("e4", "e5", "Bc4")].games == 0
    assert edge(g, K("e4", "e5"), "g1f3").repertoire is False
    # the opponent reply leading to a repertoire position is drawn too
    c5 = edge(g, K("e4"), "c7c5")
    assert c5.games == 0 and g.nodes[K("e4", "c5")].games == 0
    assert edge(g, K("e4", "c5"), "g1f3").repertoire is True


def test_master_counts_decide_theory():
    masters = {START_KEY: [MasterMove("e2e4", 50, 20, 20, 10), MasterMove("a2a3", 2, 1, 1, 0)]}
    lines = [("e4 e5", "1-0")] * 2 + [("a3 e5", "1-0")] * 2
    g = graph("white", games("white", *lines), masters=masters, available=True)
    assert edge(g, START_KEY, "e2e4").theory is True
    assert edge(g, START_KEY, "e2e4").master_games == 50
    assert edge(g, START_KEY, "a2a3").theory is False
    assert edge(g, START_KEY, "a2a3").master_games == 2

    g = graph("white", games("white", *lines), masters=masters, available=False)
    assert edge(g, START_KEY, "e2e4").theory is False  # the (empty) dataset decides


def test_no_games_is_just_the_root():
    for color in ("white", "black"):
        g = graph(color, [])
        assert list(g.nodes) == [START_KEY]
        assert g.edges == [] and g.todo == []
        assert g.nodes[START_KEY].games == 0 and g.nodes[START_KEY].line == []


def test_todo_order():
    rep = {START_KEY: "e2e4"}
    g = graph("white", games(
        "white",
        ("e4 a6 d4", "1-0"), ("e4 a6 d4", "1-0"),
        ("e4 c5 Nf3", "1-0"), ("e4 c5 Nf3", "1-0"), ("e4 c5 Nf3", "1-0"),
    ), repertoire=rep, dataset=DATASET)
    assert g.todo == [K("e4", "c5"), K("e4", "a6")]

