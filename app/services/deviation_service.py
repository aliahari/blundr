"""
Checking games against the user's repertoire (specs/features/02-openings,
design D6; R6.1-R6.5).

Each game is checked once, the first time it has moves and the user has a
repertoire for its colour: it gets `repertoire_checked_at`, and
`repertoire_exit_ply` when it left the repertoire, with a
`repertoire_deviations` row. A game whose colour has no repertoire yet stays
unchecked, so the first positions the user accepts are checked against all
their games.

- The user left the repertoire: that position's card is due now (its ease,
  interval and streak are left alone; the review grades it).
- The opponent left it with a blunder, and the game has stored evaluations:
  the engine's best reply is stored as `punish_uci` and becomes a punish
  card. A game is checked only once, so a punish card the user removes is
  never made again.

Called after each analyzed game, after each backfill batch, and after the
user accepts or sets repertoire positions. The caller commits.
"""
import json
import logging
from collections.abc import Iterable

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models.db_models import (
    AnalyzedGame,
    RepertoireDeviation,
    RepertoireMove,
    ReviewCard,
    TheoryMove,
    new_review_card,
    utcnow,
)
from .repertoire_service import first_deviation, punish_move

logger = logging.getLogger(__name__)


async def check_games(
    db: AsyncSession, user_id: int, game_ids: Iterable[int] | None = None
) -> int:
    """
    Check the user's unchecked games (only `game_ids`, if given) against
    their repertoire. Returns how many games were checked.
    """
    repertoires: dict[str, dict[str, str]] = {"white": {}, "black": {}}
    for color, key, uci in (await db.execute(
        select(RepertoireMove.color, RepertoireMove.position_key, RepertoireMove.move_uci)
        .where(RepertoireMove.user_id == user_id)
    )).all():
        repertoires[color][key] = uci
    colors = [color for color, moves in repertoires.items() if moves]
    if not colors:
        return 0

    query = select(AnalyzedGame).where(
        AnalyzedGame.user_id == user_id,
        AnalyzedGame.repertoire_checked_at.is_(None),
        AnalyzedGame.positions.is_not(None),
        AnalyzedGame.user_color.in_(colors),
    )
    if game_ids is not None:
        ids = list(game_ids)
        if not ids:
            return 0
        query = query.where(AnalyzedGame.id.in_(ids))
    games = (await db.scalars(query.order_by(AnalyzedGame.id))).all()

    now = utcnow()
    found = 0
    for game in games:
        positions = json.loads(game.positions)
        deviation = first_deviation(positions, game.user_color, repertoires[game.user_color])
        game.repertoire_checked_at = now
        game.repertoire_exit_ply = deviation.ply if deviation else None
        if deviation is None:
            continue
        if await db.scalar(
            select(RepertoireDeviation.id).where(RepertoireDeviation.game_id == game.id)
        ) is not None:
            continue  # recorded before; never make its cards again
        found += 1

        punish_uci = None
        if deviation.side == "opponent":
            punish_uci = punish_move(
                positions, deviation.ply, game.initial_eval_cp,
                settings.BLUNDER_WINPROB_THRESHOLD, settings.BLUNDER_MIN_WINPROB_BEFORE,
            )
        played_was_theory = await db.scalar(
            select(TheoryMove.move_uci).where(
                TheoryMove.position_key == deviation.position_key,
                TheoryMove.move_uci == deviation.played_uci,
            )
        ) is not None
        row = RepertoireDeviation(
            user_id=user_id, game_id=game.id, ply=deviation.ply, side=deviation.side,
            position_key=deviation.position_key, expected_uci=deviation.expected_uci,
            played_uci=deviation.played_uci, punish_uci=punish_uci,
            played_was_theory=played_was_theory,
        )
        db.add(row)
        await db.flush()

        if punish_uci is not None:
            db.add(new_review_card(
                user_id=user_id, kind="deviation", card_type="punish",
                repertoire_deviation_id=row.id,
            ))
        if deviation.side == "user":
            await db.execute(
                update(ReviewCard)
                .where(ReviewCard.repertoire_move_id.in_(
                    select(RepertoireMove.id).where(
                        RepertoireMove.user_id == user_id,
                        RepertoireMove.color == game.user_color,
                        RepertoireMove.position_key == deviation.position_key,
                    )
                ))
                .values(due_at=now)
                .execution_options(synchronize_session=False)
            )

    if games:
        logger.info(f"Repertoire check for user {user_id}: {len(games)} games, {found} deviations")
    return len(games)
