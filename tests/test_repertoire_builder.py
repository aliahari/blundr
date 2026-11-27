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
    w = walk("white", {}, {}, 20)
    assert [g.key for g in w.gaps] == [START]
    assert w.gaps[0].likelihood == 1.0
    assert w.gaps[0].line == []
    assert w.prepared == 0


def test_black_gaps_are_whites_replies():
    replies = {START: [("e2e4", 60), ("d2d4", 30), ("c2c4", 6), ("g1f3", 4)]}
    w = walk("black", {}, replies, 20)
    assert [g.key for g in w.gaps] == [key_after("e4"), key_after("d4"), key_after("c4")]
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
    w20 = walk("white", repertoire, replies, 20)
    gap = next(g for g in w20.gaps if g.key == d6)
    assert gap.likelihood == pytest.approx(0.1)
    assert gap.line == [uci((), "e4"), uci(("e4",), "e5"), uci(("e4", "e5"), "Nf3"),
                        uci(("e4", "e5", "Nf3"), "d6")]
    w5 = walk("white", repertoire, replies, 5)
    assert d6 not in {g.key for g in w5.gaps}


def test_missing_keys_reported():
    w = walk("white", {START: "e2e4"}, {}, 20)
    assert w.missing == {key_after("e4")}
    assert w.gaps == []


def test_thin_position_stops_and_counts_covered():
    replies = {key_after("e4"): [("e7e5", 10), ("c7c5", 9)]}
    w = walk("white", {START: "e2e4"}, replies, 20)
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
    w = walk("white", repertoire, replies, 20)
    target = key_after("d4", "Nf6", "c4", "e6")
    assert target == key_after("d4", "e6", "c4", "Nf6")
    hits = [g for g in w.gaps if g.key == target]
    assert len(hits) == 1
    assert hits[0].likelihood == pytest.approx(0.35)  # the likelier line into it


def test_game_over_is_covered():
    # Fool's mate: after 1.f3 e5 2.g4 Qh4# White is mated; Black's line ends there
    rep = {key_after("f3"): uci(("f3",), "e5"), key_after("f3", "e5", "g4"): uci(("f3", "e5", "g4"), "Qh4#")}
    replies = {START: [("f2f3", 100)], key_after("f3", "e5"): [("g2g4", 100)]}
    w = walk("black", rep, replies, 20)
    assert w.gaps == []
    assert w.missing == set()


def test_prepared_count():
    repertoire, replies = _italian_setup()
    replies[key_after("e4", "e5", "Nf3")] = [("b8c6", 100)]
    repertoire[key_after("e4", "e5", "Nf3", "Nc6")] = uci(("e4", "e5", "Nf3", "Nc6"), "Bc4")
    replies[key_after("e4", "e5", "Nf3", "Nc6", "Bc4")] = [("f8c5", 100)]
    w = walk("white", repertoire, replies, 20)
    assert w.prepared == 3


def test_tie_broken_by_my_games():
    replies = {START: [("e2e4", 50), ("d2d4", 50)]}
    w = walk("black", {}, replies, 20, my_games={key_after("d4"): 7})
    assert [g.key for g in w.gaps] == [key_after("d4"), key_after("e4")]
    assert w.gaps[0].my_games == 7
