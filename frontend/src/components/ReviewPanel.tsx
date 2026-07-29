import { useCallback, useEffect, useRef, useState } from 'react';
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
  | { state: 'wrong'; san: string; uci: string; reply?: BestReply | null }
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

/** One frame of the avoid card's first-exposure teaching animation: the
 * board position and the win% to show on the bar at that point. */
type IntroFrame = { fen: string; winPct: number };

/**
 * The blunder move, then each stored ply of the punishing line, as a
 * sequence of frames to step through. Falls back to just the blunder move
 * when there's no stored line (older rows, or blunders with no refutation).
 */
function buildIntroFrames(mode: Mode, blunder: BlunderInfo): IntroFrame[] {
  const chess = new Chess(mode.baseFen);
  chess.move(blunder.move_played_san);
  const frames: IntroFrame[] = [{ fen: chess.fen(), winPct: blunder.win_prob_after }];
  for (const ply of blunder.refutation_line) {
    chess.move({ from: ply.move_uci.slice(0, 2), to: ply.move_uci.slice(2, 4), promotion: ply.move_uci[4] as any });
    frames.push({ fen: chess.fen(), winPct: ply.win_prob });
  }
  return frames;
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
  // Win% shown on the bar while stepping through the intro animation;
  // null outside 'intro' (falls back to mode.solverWinPct).
  const [introWinPct, setIntroWinPct] = useState<number | null>(null);
  const [selectedSquare, setSelectedSquare] = useState<string | null>(null);
  // Cards missed this session — they come back at the end of the queue as
  // a retry round, and the status line should say so
  const [missedIds, setMissedIds] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const card = queue[0] ?? null;

  // Kept in sync with missedIds so presentCard() (called from event
  // handlers, not effects) can read the current value without needing it
  // in a dependency array.
  const missedIdsRef = useRef<Set<number>>(missedIds);
  useEffect(() => { missedIdsRef.current = missedIds; }, [missedIds]);

  const introTimersRef = useRef<number[]>([]);
  const clearIntroTimers = () => {
    introTimersRef.current.forEach(clearTimeout);
    introTimersRef.current = [];
  };
  useEffect(() => clearIntroTimers, []); // cancel pending timers on unmount

  /**
   * Show whichever card is now at the front of the queue. First-ever
   * exposure to an "avoid" card slowly steps through the blunder move and
   * its punishing continuation — the user watches it happen and the win-bar
   * track it — then the board resets and they attempt the correction.
   * Every other case (punish cards, retries, already-seen cards) goes
   * straight to 'thinking' with no animation and no spoilers.
   */
  const presentCard = (cards: ReviewCardInfo[]) => {
    clearIntroTimers();
    setQueue(cards);
    setSelectedSquare(null);
    setIntroWinPct(null);

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

    const STEP_MS = 700;
    const FINAL_HOLD_MS = 1200;
    const frames = buildIntroFrames(mode, next.blunder);

    setAttempt({ state: 'intro' });
    setDisplayFen(mode.baseFen);
    setIntroWinPct(mode.solverWinPct);

    const timers = frames.map((frame, i) => window.setTimeout(() => {
      setDisplayFen(frame.fen);
      setIntroWinPct(frame.winPct);
    }, STEP_MS * (i + 1)));

    timers.push(window.setTimeout(() => {
      setDisplayFen(mode.baseFen);
      setIntroWinPct(null);
      setAttempt({ state: 'thinking' });
    }, STEP_MS * frames.length + FINAL_HOLD_MS));

    introTimersRef.current = timers;
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
    } else {
      const uci = move.from + move.to + (move.promotion ?? '');
      setAttempt({ state: 'wrong', san: move.san, uci });
      recordMiss(card); // auto-"again", no button needed
      // Ask the engine how the opponent punishes this attempt
      getBestReply(mode.baseFen, uci)
        .then(reply => setAttempt(prev =>
          prev.state === 'wrong' && prev.uci === uci ? { ...prev, reply } : prev
        ))
        .catch(() => setAttempt(prev =>
          prev.state === 'wrong' && prev.uci === uci ? { ...prev, reply: null } : prev
        ));
    }
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

  const uciArrow = (uci: string, color: string) =>
    [uci.slice(0, 2), uci.slice(2, 4), color] as [any, any, string];

  // Board arrows per phase:
  // - thinking: none, for either card type — the point being solved for
  //   can't be spoiled before an attempt/reveal. (Avoid cards get their
  //   context from the one-time intro animation instead of a standing arrow.)
  // - wrong attempt: the correct move (green) and the opponent's punish of
  //   the attempt (red), drawn on the post-attempt position
  let arrows: Array<[any, any, string]> = [];
  if (attempt.state === 'wrong') {
    arrows = [uciArrow(mode.targetUci, '#5cb87a')];
    if (attempt.reply?.reply_uci) arrows.push(uciArrow(attempt.reply.reply_uci, '#d96b52'));
  }

  // "Why it was a blunder": the engine's punish of the move played in the
  // game — only relevant on the avoid card; on a punish card it just
  // restates what the user was solving for.
  const whyBlunder = mode.cardType === 'avoid' && b.refutation_san
    ? <>In the game, <strong className="move-bad">{b.move_played_san}</strong> was punished by{' '}
        <strong>{b.refutation_san}</strong>.</>
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
  // During 'intro', introWinPct drives the bar frame-by-frame as the
  // animation steps through the line; otherwise it's mode.solverWinPct.
  // The board is oriented so the solver's side sits at the bottom, so the
  // bar mirrors that.
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
        {attempt.state === 'intro' && (
          <p className="intro-note">Watch what happens…</p>
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
              <span className="sym">✗</span> {attempt.san} isn't it. Best was{' '}
              <strong className="move-good">{mode.targetSan}</strong> (green arrow).
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
      </div>

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
