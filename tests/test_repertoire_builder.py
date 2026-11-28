"""The builder's walk: likely positions you haven't prepared (repertoire builder, task 2)."""
import chess
import pytest

from app.services.repertoire_builder import walk


def key_after(*sans: str) -> str:
    board = chess.Board()
    for san in sans:
        board.push_san(san)
    return board.epd()


def uci(sans_before: tuple[str, ...], san: str) -> str:
    board = chess.Board()
    for s in sans_before:
        board.push_san(s)
    return board.parse_san(san).uci()


START = key_after()


def test_white_empty_repertoire_gap_is_start():
    w = walk("white", {}, {}, 10)
    assert [g.key for g in w.gaps] == [START]
    assert w.gaps[0].likelihood == 1.0
    assert w.gaps[0].line == []
    assert w.prepared == 0


def test_black_gaps_are_whites_replies():
    # 1 in 100 games at your level is the floor: 1.b3 (0.5%) is out
    replies = {START: [("e2e4", 600), ("d2d4", 300), ("c2c4", 60), ("g1f3", 35), ("b2b3", 5)]}
    w = walk("black", {}, replies, 10)
    assert [g.key for g in w.gaps] == [key_after("e4"), key_after("d4"), key_after("c4"),
                                       key_after("Nf3")]
    assert w.gaps[0].likelihood == pytest.approx(0.6)
    assert w.gaps[2].likelihood == pytest.approx(0.06)
    assert w.gaps[0].last_reply == "e2e4"
    assert w.gaps[0].last_share == pytest.approx(0.6)
    assert w.gaps[0].line == ["e2e4"]


def _italian_setup():
    repertoire = {START: "e2e4", key_after("e4", "e5"): uci(("e4", "e5"), "Nf3")}
    replies = {
        key_after("e4"): [("e7e5", 50), ("c7c5", 50)],
        key_after("e4", "e5", "Nf3"): [("b8c6", 80), ("d7d6", 20)],
    }
    return repertoire, replies


def test_likelihood_multiplies():
    repertoire, replies = _italian_setup()
    d6 = key_after("e4", "e5", "Nf3", "d6")
    w20 = walk("white", repertoire, replies, 10)
    gap = next(g for g in w20.gaps if g.key == d6)
    assert gap.likelihood == pytest.approx(0.1)
    assert gap.line == [uci((), "e4"), uci(("e4",), "e5"), uci(("e4", "e5"), "Nf3"),
                        uci(("e4", "e5", "Nf3"), "d6")]
    w5 = walk("white", repertoire, replies, 10, floor=0.2)
    assert d6 not in {g.key for g in w5.gaps}


def test_missing_keys_reported():
    w = walk("white", {START: "e2e4"}, {}, 10)
    assert w.missing == {key_after("e4")}
    assert w.gaps == []


def test_thin_position_stops_and_counts_covered():
    replies = {key_after("e4"): [("e7e5", 10), ("c7c5", 9)]}
    w = walk("white", {START: "e2e4"}, replies, 10)
    assert w.thin == {key_after("e4")}
    assert w.gaps == []
    assert w.prepared == 1


def test_transposition_counted_once():
    # 1.d4 Nf6 2.c4 e6 and 1.d4 e6 2.c4 Nf6 reach one position
    repertoire = {
        START: "d2d4",
        key_after("d4", "Nf6"): uci(("d4", "Nf6"), "c4"),
        key_after("d4", "e6"): uci(("d4", "e6"), "c4"),
    }
    replies = {
        key_after("d4"): [("g8f6", 50), ("e7e6", 50)],
        key_after("d4", "Nf6", "c4"): [("e7e6", 60), ("g7g6", 40)],
        key_after("d4", "e6", "c4"): [("g8f6", 70), ("d7d5", 30)],
    }
    w = walk("white", repertoire, replies, 10)
    target = key_after("d4", "Nf6", "c4", "e6")
    assert target == key_after("d4", "e6", "c4", "Nf6")
    hits = [g for g in w.gaps if g.key == target]
    assert len(hits) == 1
    assert hits[0].likelihood == pytest.approx(0.35)  # the likelier line into it


def test_game_over_is_covered():
    # Fool's mate: after 1.f3 e5 2.g4 Qh4# White is mated; Black's line ends there
    rep = {key_after("f3"): uci(("f3",), "e5"), key_after("f3", "e5", "g4"): uci(("f3", "e5", "g4"), "Qh4#")}
    replies = {START: [("f2f3", 100)], key_after("f3", "e5"): [("g2g4", 100)]}
    w = walk("black", rep, replies, 10)
    assert w.gaps == []
    assert w.missing == set()


def test_prepared_count():
    repertoire, replies = _italian_setup()
    replies[key_after("e4", "e5", "Nf3")] = [("b8c6", 100)]
    repertoire[key_after("e4", "e5", "Nf3", "Nc6")] = uci(("e4", "e5", "Nf3", "Nc6"), "Bc4")
    replies[key_after("e4", "e5", "Nf3", "Nc6", "Bc4")] = [("f8c5", 100)]
    w = walk("white", repertoire, replies, 10)
    assert w.prepared == 3


def test_tie_broken_by_my_games():
    replies = {START: [("e2e4", 50), ("d2d4", 50)]}
    w = walk("black", {}, replies, 10, my_games={key_after("d4"): 7})
    assert [g.key for g in w.gaps] == [key_after("d4"), key_after("e4")]
    assert w.gaps[0].my_games == 7


def test_completion_by_branch_and_overall():
    from app.services.repertoire_builder import completion
    repertoire, replies = _italian_setup()  # 1.e4 e5 2.Nf3 prepared; ...c5 and 2...Nc6/d6 open
    w = walk("white", repertoire, replies, 10)
    e4, e5, c5 = uci((), "e4"), uci(("e4",), "e5"), uci(("e4",), "c5")
    # Weighted by how often each position comes up: prepared are the start (1.0)
    # and 1...e5 (0.5); open are 1...c5 (0.5), 2...Nc6 (0.4) and 2...d6 (0.1)
    assert completion(w, []) == pytest.approx(1.5 / 2.5)
    assert completion(w, [e4, e5]) == pytest.approx(0.5 / 1.0)
    assert completion(w, [e4, c5]) == 0.0
    assert completion(w, [e4, uci(("e4",), "a6")]) is None  # nothing in scope below it


def test_walk_records_likelihoods():
    repertoire, replies = _italian_setup()
    w = walk("white", repertoire, replies, 10)
    assert w.likelihood[START] == 1.0
    assert w.likelihood[key_after("e4", "e5", "Nf3", "Nc6")] == pytest.approx(0.4)


def test_depth_limits_the_scope():
    repertoire, replies = _italian_setup()
    # Up to move 1: only White's first move is in scope, and it's prepared
    w1 = walk("white", repertoire, replies, 1)
    assert w1.gaps == [] and w1.prepared == 1
    # Up to move 2: the replies to 1.e4 are in scope, deeper ones aren't
    w2 = walk("white", repertoire, replies, 2)
    assert {g.key for g in w2.gaps} == {key_after("e4", "c5")}
    assert w2.prepared == 2


def test_shallow_gaps_come_first():
    repertoire = {START: "e2e4", key_after("e4", "e5"): uci(("e4", "e5"), "Nf3")}
    replies = {
        key_after("e4"): [("e7e5", 90), ("c7c5", 10)],
        key_after("e4", "e5", "Nf3"): [("b8c6", 80), ("d7d6", 20)],
    }
    w = walk("white", repertoire, replies, 10)
    # 1...c5 (1 in 10, move 2) before 2...Nc6 (72%, move 3): breadth first
    assert [g.key for g in w.gaps] == [key_after("e4", "c5"), key_after("e4", "e5", "Nf3", "Nc6"),
                                       key_after("e4", "e5", "Nf3", "d6")]


def test_no_reply_data_needed_past_the_depth():
    # Up to move 1 with 1.e4 prepared: Black's replies lead past the depth
    w = walk("white", {START: "e2e4"}, {}, 1)
    assert w.missing == set() and w.gaps == []
    # For Black, up to move 1: White's first moves are needed, nothing after Black's reply
    wb = walk("black", {key_after("e4"): uci(("e4",), "c5")}, {START: [("e2e4", 100)]}, 1)
    assert wb.missing == set()
