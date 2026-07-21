"""
Spaced repetition scheduling (SM-2 variant with a 3-grade scale).

Grades map to SM-2 quality: again=2 (fail), good=4, easy=5. A failed card
resets its repetition streak and comes back in 10 minutes; passed cards
follow the classic 1 day → 6 days → interval*ease progression.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from ..models.db_models import ReviewCard

GRADE_QUALITY = {"again": 2, "good": 4, "easy": 5}

MIN_EASE = 1.3
AGAIN_DELAY = timedelta(minutes=10)
MASTERED_REPETITIONS = 3  # consecutive successful reviews to count as mastered


def apply_grade(card: ReviewCard, grade: str, now: datetime | None = None) -> ReviewCard:
    """
    Mutate a ReviewCard's scheduling state for the given grade.

    Args:
        card: The card to update (mutated in place and returned)
        grade: "again" | "good" | "easy"
        now: Injectable clock for tests

    Raises:
        ValueError: On an unknown grade
    """
    if grade not in GRADE_QUALITY:
        raise ValueError(f"Unknown grade: {grade!r}")
    quality = GRADE_QUALITY[grade]
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)

    if quality < 3:
        # Failed: reset the streak, retry soon
        card.repetitions = 0
        card.lapses += 1
        card.interval_days = 0.0
        card.due_at = now + AGAIN_DELAY
    else:
        if card.repetitions == 0:
            card.interval_days = 1.0
        elif card.repetitions == 1:
            card.interval_days = 6.0
        else:
            card.interval_days = round(card.interval_days * card.ease, 1)
        card.repetitions += 1
        card.due_at = now + timedelta(days=card.interval_days)

        # SM-2 ease update only on successful recall
        card.ease = max(
            MIN_EASE,
            card.ease + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)),
        )

    card.last_reviewed_at = now
    return card


def mastered_percentage_by_day(
    card_created: Dict[int, date],
    logs: List[Tuple[int, str, datetime]],
    days: List[date],
) -> List[Optional[float]]:
    """
    Reconstruct the day-end "mastered %" for each day by replaying the
    review history: a card's repetition streak resets on "again" and grows
    on good/easy (mirroring apply_grade), and a card counts as mastered
    while its streak is >= MASTERED_REPETITIONS.

    Args:
        card_created: card_id -> date the card came into existence
        logs: (card_id, grade, reviewed_at) review history, any order
        days: ordered days to snapshot

    Returns:
        One value per day: mastered/existing*100, or None for days with no
        cards yet (a gap, not a zero — there was nothing to master).
    """
    reps: Dict[int, int] = {cid: 0 for cid in card_created}
    ordered = sorted(logs, key=lambda entry: entry[2])
    li = 0
    out: List[Optional[float]] = []

    for d in days:
        while li < len(ordered) and ordered[li][2].date() <= d:
            cid, grade, _ = ordered[li]
            if cid in reps:
                reps[cid] = 0 if grade == "again" else reps[cid] + 1
            li += 1

        existing = [cid for cid, created in card_created.items() if created <= d]
        if not existing:
            out.append(None)
        else:
            mastered = sum(1 for cid in existing if reps[cid] >= MASTERED_REPETITIONS)
            out.append(round(100 * mastered / len(existing), 1))
    return out
