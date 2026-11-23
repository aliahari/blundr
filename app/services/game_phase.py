"""
Which phase of the game a position is in: opening, middlegame or endgame.

Used to break the blunder rate down by phase (specs/features/01-foundations,
D4), and meant for later phases too (opening repertoire depth, endgame
detection), so the thresholds live here and nowhere else.

The rules are deliberately simple and close to Lichess's own split between
phases, which counts minor and major pieces ("pieces": knights, bishops,
rooks, queens — not kings or pawns):

- endgame: 6 or fewer pieces left on the board in total
- opening: still early (ply <= OPENING_MAX_PLY) and both sides still have
  at least OPENING_MIN_PIECES_PER_SIDE pieces, i.e. at most one piece
  traded each
- middlegame: everything else
"""
from typing import Literal

import chess

Phase = Literal["opening", "middlegame", "endgame"]

OPENING_MAX_PLY = 20  # move 10 for both sides
OPENING_MIN_PIECES_PER_SIDE = 6  # out of 7 at the start
ENDGAME_MAX_PIECES = 6  # both sides together

_PIECE_TYPES = (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN)


def _pieces(board: chess.Board, color: chess.Color) -> int:
    return sum(len(board.pieces(piece_type, color)) for piece_type in _PIECE_TYPES)


def game_phase(board: chess.Board, ply: int) -> Phase:
    """
    Phase of `board`, the position after `ply` half-moves (0 = the starting
    position). To label a move, pass the position it was played from.
    """
    white, black = _pieces(board, chess.WHITE), _pieces(board, chess.BLACK)
    if white + black <= ENDGAME_MAX_PIECES:
        return "endgame"
    if ply <= OPENING_MAX_PLY and min(white, black) >= OPENING_MIN_PIECES_PER_SIDE:
        return "opening"
    return "middlegame"
