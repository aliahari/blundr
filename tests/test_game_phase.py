"""Phase 1, task 7: labelling a position as opening, middlegame or endgame."""
import chess
import pytest

from app.services.game_phase import game_phase

START = chess.STARTING_FEN
# Kings + pieces only (pawns don't count); positions are legal but not from
# real games — they only need the right material.
MIDDLEGAME_FULL = "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4"
ROOK_ENDING = "8/5pk1/6p1/8/3R4/6P1/r4PK1/8 w - - 0 40"
QUEENLESS_MIDDLEGAME = "r1b1kb1r/pp3ppp/2n2n2/8/8/2N2N2/PP3PPP/R1B1KB1R w KQkq - 0 10"
# 6 pieces left in total (R+B+N each): the endgame boundary.
SIX_PIECES = "2r1k3/8/3bn3/8/8/3BN3/8/2R1K3 w - - 0 30"
# 7 pieces: one more than the boundary.
SEVEN_PIECES = "2r1k3/8/3bn3/8/8/3BN3/8/2RQK3 w - - 0 30"
# 5 pieces on one side, 6 on the other: no longer an opening.
TRADED_EARLY = "r1bqk2r/pppp1ppp/5n2/4p3/4P3/5N2/PPPP1PPP/R1BQK2R w KQkq - 0 6"


@pytest.mark.parametrize("fen, ply, expected", [
    (START, 0, "opening"),
    (MIDDLEGAME_FULL, 7, "opening"),           # early, full material
    (MIDDLEGAME_FULL, 20, "opening"),          # last opening ply
    (MIDDLEGAME_FULL, 21, "middlegame"),       # past the opening by move count
    (TRADED_EARLY, 11, "middlegame"),          # early, but 5 pieces a side: traded off
    (QUEENLESS_MIDDLEGAME, 19, "opening"),     # queens off, but still 6 a side and early
    (QUEENLESS_MIDDLEGAME, 40, "middlegame"),  # no queens alone isn't an endgame
    (SEVEN_PIECES, 59, "middlegame"),
    (SIX_PIECES, 59, "endgame"),
    (ROOK_ENDING, 79, "endgame"),
])
def test_game_phase(fen, ply, expected):
    assert game_phase(chess.Board(fen), ply) == expected
