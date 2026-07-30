import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Chessboard } from 'react-chessboard';
import { Chess } from 'chess.js';
import { getDueCards, answerCard, getReviewStats, getBestReply } from '../services/api';
import { BestReply, BlunderInfo, ReviewCardInfo, ReviewGrade, ReviewStats } from '../types';
import { IconEye, IconCheck, IconStar, IconArrowRight } from './icons';

type Attempt =
  | { state: 'intro' }
  | { state: 'thinking' }
  | { state: 'correct'; san: string }
  // reply: undefined = engine still thinking, null = no reply (move ended the game)
  // repeated: the attempt was the very move played in the game (avoid cards)
  | { state: 'wrong'; san: string; uci: string; repeated?: boolean; reply?: BestReply | null }
  | { state: 'revealed' };

/** Everything the board/prompt need, parameterized by which side of the
 * blunder this card trains — same shape for both, different values. */
type Mode = {
  cardType: 'avoid' | 'punish';
  solverColor: 'white' | 'black';
  baseFen: string;
  targetUci: string;
  targetSan: string;
  solverWinPct: number; // solver's win% at baseFen, 0..100
};

function deriveMode(card: ReviewCardInfo): Mode {
  const b = card.blunder;
  if (card.card_type === 'avoid') {
    return {
      cardType: 'avoid',
      solverColor: b.user_color,
      baseFen: b.fen_before,
      targetUci: b.best_move_uci,
      targetSan: b.best_move_san,
      solverWinPct: b.win_prob_before,
    };
  }
  // Punish: solve from the position right after the blunder, playing the
  // opponent's side, target is the engine's punish of it.
  const afterBlunder = new Chess(b.fen_before);
  afterBlunder.move(b.move_played_san);
  return {
    cardType: 'punish',
    solverColor: b.user_color === 'white' ? 'black' : 'white',
    baseFen: afterBlunder.fen(),
    targetUci: b.refutation_uci as string,  // guaranteed set: punish cards only exist when it is
    targetSan: b.refutation_san as string,
    solverWinPct: 100 - b.win_prob_after,
  };
}

/**
 * The board position and win% right after the blunder move plays out —
 * the frame the avoid card's intro animation settles on before offering
 * the Try it / Show why choice. Only avoid cards animate, so `beforeFen`
 * is always the pre-blunder position and the move still has to be applied.
 */
function blunderAfterFrame(blunder: BlunderInfo, beforeFen: string): { fen: string; winPct: number } {
  const chess = new Chess(beforeFen);
  chess.move(blunder.move_played_san);
  return { fen: chess.fen(), winPct: blunder.win_prob_after };
}

/**
 * Spaced-repetition review: each blunder trains two skills as separate
 * cards — "avoid" (play the blunderer, find the best move) and "punish"
 * (play the opponent, find the refutation).
 *
 * Correct attempts are graded by the user (Good/Easy). Misses (wrong move or
 * revealed answer) are automatically graded "again" and the card is
 * re-queued at the end of the current session for an immediate retry.
 * Wrong attempts are sent to the engine so the user sees how the opponent
 * would punish them.
 */
function ReviewPanel() {
  const [queue, setQueue] = useState<ReviewCardInfo[]>([]);
  const [stats, setStats] = useState<ReviewStats | null>(null);
  const [attempt, setAttempt] = useState<Attempt>({ state: 'thinking' });
  const [displayFen, setDisplayFen] = useState<string | null>(null);
  // Win% shown on the bar during the intro animation; null outside
  // 'intro' (falls back to mode.solverWinPct).
  const [introWinPct, setIntroWinPct] = useState<number | null>(null);
  // Has the blunder-move animation finished playing? False while still
  // showing the pre-blunder position.
  const [introRevealed, setIntroRevealed] = useState(false);
  // Avoid cards only: user asked to see the punishing sequence instead of
  // (or before) attempting the correction.
  const [showWhy, setShowWhy] = useState(false);
  const [selectedSquare, setSelectedSquare] = useState<string | null>(null);
  // Cards missed this session — they come back at the end of the queue as
  // a retry round, and the status line should say so
  const [missedIds, setMissedIds] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const card = queue[0] ?? null;

  // Board arrows per phase:
  // - intro (avoid cards only), once the blunder move has played: the
  //   blunder move itself (red), so it's clear at a glance what just
  //   happened — not a spoiler, it already happened in the game.
  // - thinking, punish cards: the blunder move (red). Punish cards open
  //   straight on the question, so this arrow is what shows *which* move
  //   is being punished — the prompt names it in SAN, the board points at
  //   it. Not a spoiler for the same reason.
  // - thinking, avoid cards: none — the point being solved for can't be
  //   spoiled before an attempt/reveal.
  // - wrong attempt: the correct move (green) and the opponent's punish of
  //   the attempt (red), drawn on the post-attempt position
  // Memoized (not recomputed as a fresh array every render): react-chessboard
  // re-syncs its internal arrow state off this array's *reference* and also
  // clears arrows whenever `position` changes — passing a new array literal
  // every render made the two effects race, clearing the arrow again just
  // after it appeared. Keeping the reference stable except when the arrow
  // should actually change fixes that.
  const arrows = useMemo<Array<[any, any, string]>>(() => {
    if (!card) return [];
    const mode = deriveMode(card);
    const uciArrow = (uci: string, color: string): [any, any, string] =>
      [uci.slice(0, 2), uci.slice(2, 4), color];
    if (attempt.state === 'intro' && introRevealed) {
      return [uciArrow(card.blunder.move_played_uci, '#d96b52')];
    }
    if (attempt.state === 'thinking' && mode.cardType === 'punish') {
      return [uciArrow(card.blunder.move_played_uci, '#d96b52')];
    }
    if (attempt.state === 'wrong') {
      const arr: Array<[any, any, string]> = [uciArrow(mode.targetUci, '#5cb87a')];
      if (attempt.reply?.reply_uci) arr.push(uciArrow(attempt.reply.reply_uci, '#d96b52'));
      return arr;
    }
    return [];
  }, [card, attempt, introRevealed]);

  // Kept in sync with missedIds so presentCard() (called from event
  // handlers, not effects) can read the current value without needing it
  // in a dependency array.
  const missedIdsRef = useRef<Set<number>>(missedIds);
  useEffect(() => { missedIdsRef.current = missedIds; }, [missedIds]);

  const introTimerRef = useRef<number | null>(null);
  const clearIntroTimer = () => {
    if (introTimerRef.current !== null) {
      clearTimeout(introTimerRef.current);
      introTimerRef.current = null;
    }
  };
  useEffect(() => clearIntroTimer, []); // cancel a pending timer on unmount

  const BLUNDER_ANIMATE_MS = 1400; // "a bit slowly" — long enough to actually watch it happen
  const ARROW_REVEAL_DELAY_MS = 350; // let the position change settle before showing the arrow

  /**
   * Show whichever card is now at the front of the queue.
   * - First-ever exposure to an "avoid" card: watch the blunder move play
   *   out first (not a spoiler — it already happened in the game), then
   *   pause on a choice: reset and attempt the correction ("Try it"), or
   *   see the full punishing sequence first ("Show why").
   * - Punish cards: straight to the question. The position already shows
   *   the blunder with an arrow on it and the prompt names the move, so
   *   replaying it first only delays the puzzle.
   * - Every other case (retries, already-seen avoid cards): straight to
   *   the question too.
   */
  const presentCard = (cards: ReviewCardInfo[]) => {
    clearIntroTimer();
    setQueue(cards);
    setSelectedSquare(null);
    setIntroWinPct(null);
    setIntroRevealed(false);
    setShowWhy(false);

    const next = cards[0];
    if (!next) {
      setAttempt({ state: 'thinking' });
      setDisplayFen(null);
      return;
    }

    const mode = deriveMode(next);
    const isFirstExposure =
      mode.cardType === 'avoid' &&
      next.repetitions === 0 &&
      next.lapses === 0 &&
      !missedIdsRef.current.has(next.card_id);

    if (!isFirstExposure) {
      setAttempt({ state: 'thinking' });
      setDisplayFen(mode.baseFen);
      return;
    }

    setAttempt({ state: 'intro' });
    setDisplayFen(mode.baseFen); // avoid: baseFen IS the pre-blunder position
    setIntroWinPct(mode.solverWinPct);

    const after = blunderAfterFrame(next.blunder, mode.baseFen);
    introTimerRef.current = window.setTimeout(() => {
      setDisplayFen(after.fen);
      setIntroWinPct(after.winPct);

      // Reveal the arrow on its own, later tick — react-chessboard clears
      // its internal arrow state as a side effect of the `position` prop
      // changing, so setting introRevealed (which is what makes the arrow
      // appear) in this same update races that clear and the arrow can
      // vanish moments after showing up. Waiting until the position change
      // has settled avoids the collision entirely.
      introTimerRef.current = window.setTimeout(() => {
        setIntroRevealed(true);
        // Stays in 'intro', showing the Try it / Show why choice
      }, ARROW_REVEAL_DELAY_MS);
    }, BLUNDER_ANIMATE_MS);
  };

  /** Reset to the pre-blunder position and attempt the correction. */
  const startAttempt = () => {
    if (!card) return;
    clearIntroTimer();
    const mode = deriveMode(card);
    setIntroRevealed(false);
    setShowWhy(false);
    setIntroWinPct(null);
    setDisplayFen(mode.baseFen);
    setAttempt({ state: 'thinking' });
  };

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [cards, s] = await Promise.all([getDueCards(), getReviewStats()]);
      setStats(s);
      presentCard(cards);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load reviews');
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  /** Record a miss: auto-grade "again" — no button press needed. */
  const recordMiss = (c: ReviewCardInfo) => {
    setMissedIds(prev => new Set(prev).add(c.card_id));
    answerCard(c.card_id, 'again')
      .then(() => getReviewStats().then(setStats).catch(() => {}))
      .catch(err => setError(err instanceof Error ? err.message : 'Failed to save review'));
  };

  /** Attempt a move; returns false if illegal (piece snaps back). */
  const tryMove = (source: string, target: string): boolean => {
    if (!card || attempt.state !== 'thinking') return false;
    const mode = deriveMode(card);

    const chess = new Chess(mode.baseFen);
    let move;
    try {
      // Queen is the sensible default for promotion attempts; correctness
      // is judged on from/to squares anyway.
      move = chess.move({ from: source, to: target, promotion: 'q' });
    } catch {
      return false; // illegal move
    }

    setSelectedSquare(null);
    setDisplayFen(chess.fen());
    // Compare from-square/to-square so promotion-piece defaults don't matter
    const targetUci = mode.targetUci;
    const isCorrect = move.from === targetUci.slice(0, 2) && move.to === targetUci.slice(2, 4);

    if (isCorrect) {
      setAttempt({ state: 'correct', san: move.san });
      return true;
    }

    const uci = move.from + move.to + (move.promotion ?? '');
    const blunder = card.blunder;
    // Walking into the exact move played in the game — the one mistake this
    // card exists to train out, so it gets named rather than reported as a
    // generic miss. Compared on from/to like isCorrect above, so a
    // promotion-piece mismatch can't mask it.
    const repeated =
      mode.cardType === 'avoid' &&
      move.from === blunder.move_played_uci.slice(0, 2) &&
      move.to === blunder.move_played_uci.slice(2, 4);

    recordMiss(card); // auto-"again": resets the card's streak, retry in 10min

    if (repeated) {
      // No engine round-trip needed: how this move gets punished is the
      // refutation already stored on the blunder.
      setAttempt({
        state: 'wrong',
        san: move.san,
        uci,
        repeated: true,
        reply: {
          move_san: blunder.move_played_san,
          reply_uci: blunder.refutation_uci,
          reply_san: blunder.refutation_san,
          eval_after_cp: blunder.eval_after_cp,
          game_over: blunder.refutation_uci === null, // null only when the blunder ended the game
        },
      });
      return true;
    }

    setAttempt({ state: 'wrong', san: move.san, uci });
    // Ask the engine how the opponent punishes this attempt
    getBestReply(mode.baseFen, uci)
      .then(reply => setAttempt(prev =>
        prev.state === 'wrong' && prev.uci === uci ? { ...prev, reply } : prev
      ))
      .catch(() => setAttempt(prev =>
        prev.state === 'wrong' && prev.uci === uci ? { ...prev, reply: null } : prev
      ));
    return true;
  };

  const onPieceDrop = (source: string, target: string): boolean => tryMove(source, target);

  /** Click-to-move: first click selects a piece, second click moves. */
  const onSquareClick = (square: string) => {
    if (!card || attempt.state !== 'thinking') return;
    const mode = deriveMode(card);

    if (selectedSquare === null) {
      const chess = new Chess(mode.baseFen);
      const piece = chess.get(square as any);
      if (piece && (piece.color === 'w') === (mode.solverColor === 'white')) {
        setSelectedSquare(square);
      }
      return;
    }

    if (selectedSquare === square) {
      setSelectedSquare(null); // deselect
      return;
    }

    if (!tryMove(selectedSquare, square)) {
      // Illegal target: maybe the user clicked another of their pieces
      const chess = new Chess(mode.baseFen);
      const piece = chess.get(square as any);
      if (piece && (piece.color === 'w') === (mode.solverColor === 'white')) {
        setSelectedSquare(square);
      } else {
        setSelectedSquare(null);
      }
    }
  };

  const reveal = () => {
    if (!card) return;
    const mode = deriveMode(card);
    const chess = new Chess(mode.baseFen);
    const targetUci = mode.targetUci;
    chess.move({ from: targetUci.slice(0, 2), to: targetUci.slice(2, 4), promotion: targetUci[4] as any });
    setDisplayFen(chess.fen());
    setAttempt({ state: 'revealed' });
    recordMiss(card); // revealing counts as a miss — auto-"again"
  };

  /** After a miss: re-queue the card at the end of this session for retry. */
  const nextAfterMiss = () => {
    if (!card) return;
    presentCard([...queue.slice(1), card]);
  };

  const grade = async (g: ReviewGrade) => {
    if (!card) return;
    try {
      await answerCard(card.card_id, g);
      const rest = queue.slice(1);
      presentCard(rest);
      getReviewStats().then(setStats).catch(() => {});
      if (rest.length === 0) refresh(); // "again" cards may already be re-due
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save review');
    }
  };

  if (loading) return <div className="status-message"><p>Loading reviews…</p></div>;
  if (error) return <div className="error-message">{error}</div>;

  if (!card) {
    return (
      <div className="status-message">
        <div className="icon">🎉</div>
        <p>No cards due for review right now.</p>
        {stats && stats.total_cards > 0 && stats.new_remaining_today === 0 && (
          <p>
            You've reached today's new-blunder limit — well done!
            Cards you've started will come back when they're due.
            You can raise the limit in Settings.
          </p>
        )}
        {stats && stats.total_cards > 0 && stats.new_remaining_today > 0 && (
          <p>{stats.total_cards} card{stats.total_cards === 1 ? '' : 's'} scheduled — come back later.</p>
        )}
        {stats && stats.total_cards === 0 && (
          <p>Sync some games first to build your blunder deck.</p>
        )}
      </div>
    );
  }

  const b = card.blunder;
  const mode = deriveMode(card);
  const resolved = attempt.state !== 'thinking' && attempt.state !== 'intro';

  // "Why it was a blunder": the engine's punish of the move played in the
  // game — only relevant on the avoid card; on a punish card it just
  // restates what the user was solving for. Dropped too when the user just
  // replayed that same move: the attempt feedback above already says how it
  // gets punished, and points at the arrow while doing it.
  const repeatedBlunder = attempt.state === 'wrong' && attempt.repeated === true;
  const whyBlunder = mode.cardType === 'avoid' && b.refutation_san && !repeatedBlunder
    ? <>In the game, <strong className="move-bad">{b.move_played_san}</strong> was punished by{' '}
        <strong>{b.refutation_san}</strong>.</>
    : null;

  // How play continues once the card's answer is on the board — the part
  // that explains *why* that move was right. Different source per card
  // type: the punish card's answer IS refutation_line[0], so its
  // continuation is the rest of that line; the avoid card's answer is
  // best_move, whose line is stored separately and already starts with the
  // opponent's reply. Only shown for 'correct'/'revealed': during
  // 'thinking' it would give the answer away, and after a wrong attempt
  // the board sits on the user's own move and the engine's punish of it —
  // an unrelated line, so this would read as continuing from that instead
  // (nonsense when their move got mated).
  const continuationPlies =
    attempt.state === 'correct' || attempt.state === 'revealed'
      ? (mode.cardType === 'punish' ? b.refutation_line.slice(1) : b.best_line)
      : [];
  const continuation = continuationPlies.length > 0
    ? continuationPlies.map(p => p.move_san).join(', ')
    : null;

  // The full punishing sequence, for the avoid card's opt-in "Show why" —
  // unlike punishContinuation this includes the first move too, since
  // none of it is the avoid card's answer (that's best_move, a totally
  // separate move refutation_line has no bearing on).
  const fullRefutationSequence = b.refutation_line.length > 0
    ? b.refutation_line.map(p => p.move_san).join(', ')
    : null;

  // Selected piece + its legal destinations: dot on empty squares, ring on
  // captures (the conventional treatment). Plain computation, no hook —
  // this sits below early returns where hooks aren't allowed, and it only
  // does work while a piece is actually selected.
  const squareStyles: Record<string, React.CSSProperties> = {};
  if (selectedSquare && attempt.state === 'thinking') {
    squareStyles[selectedSquare] = { background: 'rgba(212, 163, 74, 0.55)' };
    const chess = new Chess(mode.baseFen);
    for (const m of chess.moves({ square: selectedSquare as any, verbose: true })) {
      squareStyles[m.to] = m.captured
        ? { background: 'radial-gradient(circle, transparent 58%, rgba(212, 163, 74, 0.5) 60%)' }
        : { background: 'radial-gradient(circle, rgba(212, 163, 74, 0.5) 23%, transparent 25%)' };
    }
  }

  // Win chance at this card's reference position, split by chess side.
  // During 'intro', introWinPct drives the bar as the walkthrough steps
  // through the frames; otherwise it's mode.solverWinPct. The board is
  // oriented so the solver's side sits at the bottom, so the bar mirrors
  // that.
  const barWinPct = attempt.state === 'intro' && introWinPct !== null ? introWinPct : mode.solverWinPct;
  const bottomPct = Math.round(barWinPct);
  const topPct = 100 - bottomPct;
  const bottomColor = mode.solverColor;
  const topColor: 'white' | 'black' = bottomColor === 'white' ? 'black' : 'white';
  const whiteWin = bottomColor === 'white' ? bottomPct : topPct;
  const blackWin = 100 - whiteWin;
  const winBarLabel = mode.cardType === 'avoid'
    ? `Win chance right before the blunder — white ${whiteWin}%, black ${blackWin}%`
    : `Win chance right after the blunder — white ${whiteWin}%, black ${blackWin}%`;

  return (
    <div className="review-panel">
      <div className="review-meta">
        <span>
          <span className={`card-type-badge card-type-${mode.cardType}`}>
            {mode.cardType === 'avoid' ? 'AVOID' : 'PUNISH'}
          </span>
          {/* The session queue is the truth the user is working through —
              the server's "due" count drops to 0 during the retry round
              of freshly-missed cards, which reads as a lie */}
          {queue.length} to go
          {missedIds.has(card.card_id) ? (
            <span className="retry-card-badge"> RETRY</span>
          ) : card.repetitions === 0 && card.lapses === 0 ? (
            <span className="new-card-badge"> NEW</span>
          ) : null}
        </span>
        <span>
          vs {b.opponent} · {new Date(b.played_at).toLocaleDateString()} · move {Math.ceil(b.ply / 2)}
        </span>
      </div>

      <div className="review-board-row">
        {/* Bottom segment/label always match boardOrientation (mode.solverColor),
            so the bar reads top-to-bottom the same way the board does. */}
        <div className="win-prob-bar" title={winBarLabel}>
          <span className="win-prob-label">{topPct}%</span>
          <div className="win-prob-track">
            <div className={`win-prob-seg win-prob-seg-${topColor}`} style={{ flexBasis: `${topPct}%` }} />
            <div className={`win-prob-seg win-prob-seg-${bottomColor}`} style={{ flexBasis: `${bottomPct}%` }} />
          </div>
          <span className="win-prob-label">{bottomPct}%</span>
        </div>

        <div className="review-board">
          <Chessboard
            position={displayFen ?? mode.baseFen}
            onPieceDrop={onPieceDrop}
            onSquareClick={onSquareClick}
            onPieceDragBegin={(_piece, square) => {
              if (attempt.state === 'thinking') setSelectedSquare(square);
            }}
            onPieceDragEnd={() => setSelectedSquare(null)}
            boardOrientation={mode.solverColor}
            arePiecesDraggable={attempt.state === 'thinking'}
            isDraggablePiece={({ piece }) =>
              piece[0] === (mode.solverColor === 'white' ? 'w' : 'b')
            }
            customBoardStyle={{ borderRadius: '6px' }}
            customLightSquareStyle={{ backgroundColor: '#e9dcc3' }}
            customDarkSquareStyle={{ backgroundColor: '#8b6b4a' }}
            customArrows={arrows}
            customSquareStyles={squareStyles}
          />
        </div>
      </div>

      <div className="review-prompt">
        {/* Intro is avoid-cards-only — see presentCard. Nothing is said
            while the move plays; the question lands with it. */}
        {attempt.state === 'intro' && introRevealed && (
          <p className="intro-note">
            In the game you played <strong className="move-bad">{b.move_played_san}</strong>,
            which was a blunder. Can you notice why?
          </p>
        )}
        {attempt.state === 'intro' && introRevealed && showWhy && fullRefutationSequence && (
          <p className="why-blunder">Punished by: {fullRefutationSequence}.</p>
        )}
        {attempt.state === 'thinking' && mode.cardType === 'avoid' && (
          <>
            <p>Find the best move.</p>
            <button type="button" className="btn btn-secondary btn-sm" onClick={reveal}>
              <IconEye size={14} /> Show answer
            </button>
          </>
        )}
        {attempt.state === 'thinking' && mode.cardType === 'punish' && (
          <>
            <p>
              Playing as <strong>{mode.solverColor}</strong>, find the move that punishes{' '}
              <strong className="move-bad">{b.move_played_san}</strong>.
            </p>
            <button type="button" className="btn btn-secondary btn-sm" onClick={reveal}>
              <IconEye size={14} /> Show answer
            </button>
          </>
        )}
        {attempt.state === 'correct' && (
          <p className="attempt-correct"><span className="sym">✓</span> {attempt.san} — exactly. That was the engine's choice.</p>
        )}
        {attempt.state === 'wrong' && (
          <>
            <p className="attempt-wrong">
              <span className="sym">✗</span>{' '}
              {attempt.repeated ? (
                <>
                  <strong className="move-bad">{attempt.san}</strong> is the same blunder you
                  played in the game.
                </>
              ) : (
                <>{attempt.san} isn't it.</>
              )}{' '}
              Best was <strong className="move-good">{mode.targetSan}</strong> (green arrow).
            </p>
            {attempt.reply === undefined && (
              <p className="reply-pending">Checking your move with the engine…</p>
            )}
            {attempt.reply?.reply_san && (
              <p>
                Your {attempt.san} gets punished by{' '}
                <strong className="move-bad">{attempt.reply.reply_san}</strong> (red arrow).
              </p>
            )}
            {attempt.reply?.game_over && <p>Your move ends the game.</p>}
          </>
        )}
        {attempt.state === 'revealed' && (
          <p>The best move was <strong className="move-good">{mode.targetSan}</strong>.</p>
        )}
        {resolved && whyBlunder && <p className="why-blunder">{whyBlunder}</p>}
        {continuation && <p className="why-blunder">Then: {continuation}.</p>}
      </div>

      {/* "Can you notice why?" — No spells out the refutation first, Yes
          goes straight to attempting the correction. Once the refutation
          has been shown there's nothing left to answer, so both collapse
          into a single Continue. */}
      {attempt.state === 'intro' && introRevealed && (
        <div className="review-grades">
          {showWhy ? (
            <button type="button" className="btn btn-primary" onClick={startAttempt}>
              Continue <IconArrowRight size={16} />
            </button>
          ) : (
            <>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setShowWhy(true)}
              >
                No
              </button>
              <button type="button" className="btn btn-primary" onClick={startAttempt}>
                Yes
              </button>
            </>
          )}
        </div>
      )}

      {attempt.state === 'correct' && (
        <div className="review-grades">
          <button type="button" className="btn btn-primary" onClick={() => grade('good')}>
            <IconCheck size={16} /> Good
          </button>
          <button type="button" className="btn btn-secondary" onClick={() => grade('easy')}>
            <IconStar size={16} /> Easy
          </button>
        </div>
      )}

      {(attempt.state === 'wrong' || attempt.state === 'revealed') && (
        <div className="review-grades">
          <span className="auto-again-note">Scheduled to repeat — you'll retry it shortly.</span>
          <button type="button" className="btn btn-primary" onClick={nextAfterMiss}>
            <IconArrowRight size={16} /> Next
          </button>
        </div>
      )}
    </div>
  );
}

export default ReviewPanel;
