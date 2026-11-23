"""
Phase 2, task 5: the opening tree from the user's games, and the repertoire
suggested from it (design D3; R2.1-R2.3, R2.5).

Pure functions, tested on hand-made games.
"""
import chess

from app.services.repertoire_service import START_KEY, TreeGame, build_tree, suggest


def _positions(sans, evals=None, bests=None):
    board = chess.Board()
    out = []
    for i, san in enumerate(sans):
        move = board.parse_san(san)
        best = None
        if bests and bests[i]:
            best = board.parse_san(bests[i]).uci()
        out.append({
            "san": san, "uci": move.uci(),
            "eval_cp": evals[i] if evals else None,
            "best_uci": best,
        })
        board.push(move)
    return out


def _game(sans, color="white", result="1-0", evals=None, bests=None, initial=None):
    return TreeGame(
        user_color=color, result=result, positions=_positions(sans, evals, bests),
        initial_eval_cp=initial,
    )


def _key(*sans):
    board = chess.Board()
    for san in sans:
        board.push_san(san)
    return board.epd()


def _by_key(suggestions):
    return {s.position_key: s for s in suggestions}


class TestBuildTree:

    def test_counts_moves_and_results_from_the_users_side(self):
        games = [
            _game(["e4", "e5", "Nf3"], result="1-0"),
            _game(["e4", "c5", "Nf3"], result="0-1"),
            _game(["e4", "e5", "Bc4"], result="1/2-1/2"),
            _game(["d4", "d5"], color="black", result="0-1"),  # other colour: ignored
        ]
        tree = build_tree(games, "white")

        root = tree[START_KEY]
        assert root.user_to_move
        e4 = root.moves["e2e4"]
        assert (e4.count, e4.wins, e4.losses, e4.draws) == (3, 1, 1, 1)
        assert "d2d4" not in root.moves
        after_e4 = tree[e4.child_key]
        assert not after_e4.user_to_move
        assert after_e4.moves["e7e5"].count == 2

    def test_black_tree_counts_results_for_black(self):
        tree = build_tree([_game(["e4", "c5"], color="black", result="0-1")], "black")
        c5 = tree[_key("e4")].moves["c7c5"]
        assert (c5.wins, c5.losses) == (1, 0)
        assert tree[_key("e4")].user_to_move

    def test_transposition_is_one_node(self):
        games = [
            _game(["d4", "Nf6", "c4", "e6"]),
            _game(["c4", "e6", "d4", "Nf6"]),
        ]
        tree = build_tree(games, "white")
        node = tree[_key("d4", "Nf6", "c4", "e6")]
        assert node is tree[_key("c4", "e6", "d4", "Nf6")]

    def test_stops_at_max_ply(self):
        tree = build_tree([_game(["e4", "e5", "Nf3", "Nc6", "Bb5"])], "white", max_ply=3)
        assert _key("e4", "e5", "Nf3") in tree
        # The 4th ply (Nc6) is past the cap: no edge, no node for it.
        assert tree[_key("e4", "e5", "Nf3")].moves == {}

    def test_theory_marked_on_edges(self):
        theory = {(START_KEY, "e2e4"): "King's Pawn Game"}
        tree = build_tree([_game(["e4"]), _game(["a3"])], "white", theory=theory)
        assert tree[START_KEY].moves["e2e4"].theory_line == "King's Pawn Game"
        assert tree[START_KEY].moves["a2a3"].theory_line is None


class TestSuggest:

    def test_needs_three_games_and_follows_common_replies(self):
        games = (
            [_game(["e4", "e5", "Nf3", "Nc6"])] * 3
            + [_game(["e4", "c5", "Nf3"])] * 3
            + [_game(["e4", "e6", "d4"])] * 2       # French: only 2 games, not followed
        )
        by_key = _by_key(suggest(build_tree(games, "white"), "white"))

        assert by_key[START_KEY].move_san == "e4"
        assert by_key[_key("e4", "e5")].move_san == "Nf3"
        assert by_key[_key("e4", "c5")].move_san == "Nf3"
        assert _key("e4", "e6") not in by_key
        # After 2.Nf3 Nc6 there's no user move at all: the line ends there.
        assert len(by_key) == 3

    def test_most_played_move_wins_and_results_break_ties(self):
        games = (
            [_game(["e4", "e5", "Nf3"], result="0-1")] * 3
            + [_game(["e4", "e5", "Bc4"], result="1-0")] * 3
            + [_game(["e4", "e5", "Nc3"])] * 1
        )
        s = _by_key(suggest(build_tree(games, "white"), "white"))[_key("e4", "e5")]
        assert s.move_san == "Bc4"  # tied 3-3 with Nf3, better results
        assert s.games == 3

    def test_parent_key_and_line_order(self):
        games = [_game(["e4", "e5", "Nf3", "Nc6", "Bb5"])] * 3
        suggestions = suggest(build_tree(games, "white"), "white")

        assert [s.move_san for s in suggestions] == ["e4", "Nf3", "Bb5"]  # parents first
        assert suggestions[0].parent_key is None
        assert suggestions[1].parent_key == START_KEY
        assert suggestions[2].parent_key == _key("e4", "e5")

    def test_max_ply_caps_the_repertoire(self):
        games = [_game(["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4"])] * 3
        assert [s.move_san for s in suggest(build_tree(games, "white"), "white", max_ply=4)] == [
            "e4", "Nf3",
        ]

    def test_black_repertoire(self):
        games = [_game(["e4", "c5", "Nf3", "d6"], color="black", result="0-1")] * 3
        assert [s.move_san for s in suggest(build_tree(games, "black"), "black")] == ["c5", "d6"]

    def test_transposition_suggested_once(self):
        games = (
            [_game(["d4", "Nf6", "c4", "e6", "Nc3"])] * 3
            + [_game(["c4", "e6", "d4", "Nf6", "Nc3"])] * 3
        )
        suggestions = suggest(build_tree(games, "white"), "white")
        keys = [s.position_key for s in suggestions]
        assert len(keys) == len(set(keys))
        assert keys.count(_key("d4", "Nf6", "c4", "e6")) == 1


class TestClearlyWorse:

    # The user (white) keeps playing 3.Bc4?? into a big drop; the engine
    # preferred 3.Nf3. Evals are from white's side, after each move.
    WORSE = {
        "sans": ["e4", "e5", "Bc4"],
        "evals": [30, 25, -350],
        "bests": ["e4", "e5", "Nf3"],
        "initial": 20,
    }

    def test_flags_a_clearly_worse_usual_move(self):
        games = [_game(**self.WORSE)] * 3
        s = _by_key(suggest(build_tree(games, "white"), "white"))[_key("e4", "e5")]
        assert s.avg_drop > 10
        assert s.better_uci == "g1f3"

    def test_no_flag_without_evals(self):
        games = [_game(["e4", "e5", "Bc4"])] * 3
        s = _by_key(suggest(build_tree(games, "white"), "white"))[_key("e4", "e5")]
        assert s.avg_drop is None
        assert s.better_uci is None

    def test_no_flag_for_a_fine_move(self):
        fine = {**self.WORSE, "evals": [30, 25, 20]}
        games = [_game(**fine)] * 3
        s = _by_key(suggest(build_tree(games, "white"), "white"))[_key("e4", "e5")]
        assert s.better_uci is None


class TestTheory:

    THEORY = {
        (START_KEY, "e2e4"): "King's Pawn Game",
        (_key("e4"), "e7e5"): "King's Pawn Game",
        (_key("e4", "e5"), "g1f3"): "King's Knight Opening",
        (_key("e4", "e5"), "f1c4"): "Bishop's Opening",
    }

    def test_theory_move_carries_its_line(self):
        games = [_game(["e4", "e5", "Nf3"])] * 3
        s = _by_key(suggest(build_tree(games, "white", theory=self.THEORY), "white",
                            theory=self.THEORY))
        assert s[START_KEY].theory_line == "King's Pawn Game"
        assert s[_key("e4", "e5")].theory_line == "King's Knight Opening"
        assert s[_key("e4", "e5")].theory_alternatives == []

    def test_off_book_move_lists_the_theory_moves(self):
        games = [_game(["e4", "e5", "Qh5"])] * 3 + [_game(["e4", "e5", "Bc4"])]
        s = _by_key(suggest(build_tree(games, "white", theory=self.THEORY), "white",
                            theory=self.THEORY))[_key("e4", "e5")]
        assert s.move_san == "Qh5"
        assert s.theory_line is None
        # Every theory move from here, the ones the user has played first.
        assert [uci for uci, _ in s.theory_alternatives] == ["f1c4", "g1f3"]

    def test_opening_name_is_inherited_down_the_line(self):
        names = {_key("e4", "e5"): ("C20", "King's Pawn Game")}
        games = [_game(["e4", "e5", "Nf3", "Nc6", "Bb5"])] * 3
        s = _by_key(suggest(build_tree(games, "white"), "white", names=names))
        assert s[START_KEY].opening is None
        assert s[_key("e4", "e5")].opening == ("C20", "King's Pawn Game")
        assert s[_key("e4", "e5", "Nf3", "Nc6")].opening == ("C20", "King's Pawn Game")
