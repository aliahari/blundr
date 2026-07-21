import { useCallback, useEffect, useState } from 'react';
import { Chessboard } from 'react-chessboard';
import { Chess } from 'chess.js';
import { getDueCards, answerCard, getReviewStats, getBestReply } from '../services/api';
import { BestReply, ReviewCardInfo, ReviewGrade, ReviewStats } from '../types';
import { IconEye, IconCheck, IconStar, IconArrowRight } from './icons';

type Attempt =
  | { state: 'thinking' }
  | { state: 'correct'; san: string }
  // reply: undefined = engine still thinking, null = no reply (move ended the game)
  | { state: 'wrong'; san: string; uci: string; reply?: BestReply | null }
  | { state: 'revealed' };

/**
 * Spaced-repetition review: shows the position the user faced right before
 * their blunder and asks them to find the engine's move.
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
  const [selectedSquare, setSelectedSquare] = useState<string | null>(null);
  // Cards missed this session — they come back at the end of the queue as
  // a retry round, and the status line should say so
  const [missedIds, setMissedIds] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const card = queue[0] ?? null;

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [cards, s] = await Promise.all([getDueCards(), getReviewStats()]);
      setQueue(cards);
      setStats(s);
      setAttempt({ state: 'thinking' });
      setSelectedSquare(null);
      setDisplayFen(cards.length > 0 ? cards[0].blunder.fen_before : null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load reviews');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const showCard = (cards: ReviewCardInfo[]) => {
    setQueue(cards);
    setAttempt({ state: 'thinking' });
    setSelectedSquare(null);
    setDisplayFen(cards.length > 0 ? cards[0].blunder.fen_before : null);
  };

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

    const chess = new Chess(card.blunder.fen_before);
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
    const best = card.blunder.best_move_uci;
    const isCorrect = move.from === best.slice(0, 2) && move.to === best.slice(2, 4);

    if (isCorrect) {
      setAttempt({ state: 'correct', san: move.san });
    } else {
      const uci = move.from + move.to + (move.promotion ?? '');
      setAttempt({ state: 'wrong', san: move.san, uci });
      recordMiss(card); // auto-"again", no button needed
      // Ask the engine how the opponent punishes this attempt
      getBestReply(card.blunder.fen_before, uci)
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

    if (selectedSquare === null) {
      const chess = new Chess(card.blunder.fen_before);
      const piece = chess.get(square as any);
      if (piece && (piece.color === 'w') === (card.blunder.user_color === 'white')) {
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
      const chess = new Chess(card.blunder.fen_before);
      const piece = chess.get(square as any);
      if (piece && (piece.color === 'w') === (card.blunder.user_color === 'white')) {
        setSelectedSquare(square);
      } else {
        setSelectedSquare(null);
      }
    }
  };

  const reveal = () => {
    if (!card) return;
    const chess = new Chess(card.blunder.fen_before);
    const best = card.blunder.best_move_uci;
    chess.move({ from: best.slice(0, 2), to: best.slice(2, 4), promotion: best[4] as any });
    setDisplayFen(chess.fen());
    setAttempt({ state: 'revealed' });
    recordMiss(card); // revealing counts as a miss — auto-"again"
  };

  /** After a miss: re-queue the card at the end of this session for retry. */
  const nextAfterMiss = () => {
    if (!card) return;
    showCard([...queue.slice(1), card]);
  };

  const grade = async (g: ReviewGrade) => {
    if (!card) return;
    try {
      await answerCard(card.card_id, g);
      const rest = queue.slice(1);
      showCard(rest);
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
  const resolved = attempt.state !== 'thinking';

  const uciArrow = (uci: string, color: string) =>
    [uci.slice(0, 2), uci.slice(2, 4), color] as [any, any, string];

  // Board arrows per phase:
  // - thinking: the game blunder (red) and, if known, the opponent's punish
  //   of it (orange) — the "don't do this again" context
  // - wrong attempt: the correct move (green) and the opponent's punish of
  //   the attempt (red), drawn on the post-attempt position
  let arrows: Array<[any, any, string]> = [];
  if (attempt.state === 'thinking') {
    arrows = [uciArrow(b.move_played_uci, '#d96b52')];
    if (b.refutation_uci) arrows.push(uciArrow(b.refutation_uci, '#e08a3c'));
  } else if (attempt.state === 'wrong') {
    arrows = [uciArrow(b.best_move_uci, '#5cb87a')];
    if (attempt.reply?.reply_uci) arrows.push(uciArrow(attempt.reply.reply_uci, '#d96b52'));
  }

  // "Why it was a blunder": the engine's punish of the move played in the game
  const whyBlunder = b.refutation_san
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
    const chess = new Chess(b.fen_before);
    for (const m of chess.moves({ square: selectedSquare as any, verbose: true })) {
      squareStyles[m.to] = m.captured
        ? { background: 'radial-gradient(circle, transparent 58%, rgba(212, 163, 74, 0.5) 60%)' }
        : { background: 'radial-gradient(circle, rgba(212, 163, 74, 0.5) 23%, transparent 25%)' };
    }
  }

  return (
    <div className="review-panel">
      <div className="review-meta">
        <span>
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

      <div className="review-board">
        <Chessboard
          position={displayFen ?? b.fen_before}
          onPieceDrop={onPieceDrop}
          onSquareClick={onSquareClick}
          onPieceDragBegin={(_piece, square) => {
            if (attempt.state === 'thinking') setSelectedSquare(square);
          }}
          onPieceDragEnd={() => setSelectedSquare(null)}
          boardOrientation={b.user_color}
          arePiecesDraggable={attempt.state === 'thinking'}
          isDraggablePiece={({ piece }) =>
            piece[0] === (b.user_color === 'white' ? 'w' : 'b')
          }
          customBoardStyle={{ borderRadius: '6px' }}
          customLightSquareStyle={{ backgroundColor: '#e9dcc3' }}
          customDarkSquareStyle={{ backgroundColor: '#8b6b4a' }}
          customArrows={arrows}
          customSquareStyles={squareStyles}
        />
      </div>

      <div className="review-prompt">
        {attempt.state === 'thinking' && (
          <>
            <p>
              In this game you played <strong className="move-bad">{b.move_played_san}</strong>{' '}
              (−{b.win_prob_drop.toFixed(0)}% win chance)
              {b.refutation_san && (
                <>, punished by <strong className="move-punish">{b.refutation_san}</strong></>
              )}
              . Find the better move.
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
              <strong className="move-good">{b.best_move_san}</strong> (green arrow).
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
          <p>The best move was <strong className="move-good">{b.best_move_san}</strong>.</p>
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
