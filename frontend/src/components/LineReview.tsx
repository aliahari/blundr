import { useCallback, useEffect, useRef, useState } from 'react';
import { Chessboard } from 'react-chessboard';
import { Chess } from 'chess.js';
import { getDueCards, answerCard, reportExplanation, REVIEW_MORE_STEP } from '../services/api';
import { ExplanationInfo, LineReviewCard, ReviewGrade, isLineCard } from '../types';
import ExplanationPanel from './ExplanationPanel';

/**
 * Review for opening cards and punish cards (Phase 2). Both are "replay
 * this line, then find the move": an opening card asks for your
 * repertoire move, a punish card for the reply that punishes an opponent's
 * slip from one of your games.
 *
 * Flow per card: the line replays onto the board; on an opening card seen
 * for the first time the move is taught first (shown with its
 * explanation), then practised; you play the move on the board. A right
 * move is graded Good or Easy; a wrong move (or Show answer) records
 * "again" and the card comes back later this session.
 */

type Phase = 'replay' | 'learn' | 'ask' | 'right' | 'wrong';

/** This tab's queue: opening cards and punish cards, apart from blunders. */
const LINE_KINDS = ['opening', 'deviation'];

const REPLAY_MS = 350;
const BOARD_LIGHT = '#e9dcc3';
const BOARD_DARK = '#8b6b4a';

interface View {
  color: 'white' | 'black';
  line: string[];
  fen: string;
  answerUci: string;
  answerSan: string;
  title: string;
  subtitle: string | null;
  explanation: ExplanationInfo | null;
}

function viewOf(card: LineReviewCard): View {
  if (card.kind === 'opening' && card.opening) {
    const o = card.opening;
    return {
      color: o.color, line: o.line, fen: o.fen, answerUci: o.answer_uci, answerSan: o.answer_san,
      title: o.opening ? o.opening.name : 'Your repertoire',
      subtitle: o.theory_line ? `Theory: ${o.theory_line}` : 'Your own move here (off-book)',
      explanation: o.explanation,
    };
  }
  const d = card.deviation!;
  return {
    color: d.color, line: d.line, fen: d.fen, answerUci: d.answer_uci, answerSan: d.answer_san,
    title: d.opening ? d.opening.name : 'Punish the mistake',
    subtitle: `From your game on ${new Date(d.played_at).toLocaleDateString()}: your opponent left your repertoire with ${d.opponent_move_san}.`,
    explanation: null,
  };
}

/** For the owner: write or edit the explanation of this move (it wins over Claude's). */
function OwnerEdit({ fen, moveUci, moveSan }: { fen: string; moveUci: string; moveSan: string }) {
  const [open, setOpen] = useState(false);
  // The position key is board.epd(): the FEN without its move counters
  const positionKey = fen.split(' ').slice(0, 4).join(' ');
  if (!open) {
    return (
      <button type="button" className="link-button" onClick={() => setOpen(true)}>
        Edit the explanation
      </button>
    );
  }
  return <ExplanationPanel positionKey={positionKey} moveUci={moveUci} moveSan={moveSan} isOwner />;
}

/** Numbered SAN, e.g. "1. e4 e5 2. Nf3". */
function numbered(sans: string[]): string {
  return sans.map((san, i) => (i % 2 === 0 ? `${i / 2 + 1}. ${san}` : san)).join(' ');
}

function ExplanationBox({ explanation, moveSan }: { explanation: ExplanationInfo; moveSan: string }) {
  const [reporting, setReporting] = useState(false);
  const [note, setNote] = useState('');
  const [reported, setReported] = useState(false);
  const [failed, setFailed] = useState(false);

  const send = async () => {
    try {
      await reportExplanation(explanation.id, note.trim());
      setReported(true);
      setReporting(false);
    } catch {
      setFailed(true);
    }
  };

  return (
    <div className="explanation">
      <p><span className="explanation-label">Plan</span>{explanation.plan}</p>
      <p><span className="explanation-label">Why {moveSan}</span>{explanation.why_this_move}</p>
      {explanation.pawn_structure && (
        <p><span className="explanation-label">Pawns</span>{explanation.pawn_structure}</p>
      )}
      <div className="explanation-foot">
        <span>{explanation.source === 'owner' ? 'Written by the coach' : 'Explained by Claude'}</span>
        {reported ? (
          <span>Reported, thank you.</span>
        ) : reporting ? (
          <span className="report-form">
            <input
              id={`report-note-${explanation.id}`}
              type="text"
              placeholder="What's wrong? (optional)"
              value={note}
              maxLength={1000}
              onChange={e => setNote(e.target.value)}
            />
            <button type="button" className="btn btn-secondary btn-sm" onClick={send}>Send report</button>
            {failed && <span className="attempt-wrong">Couldn't send. Try again.</span>}
          </span>
        ) : (
          <button type="button" className="link-button" onClick={() => setReporting(true)}>
            Report as wrong
          </button>
        )}
      </div>
    </div>
  );
}

function LineReview({ isOwner = false }: { isOwner?: boolean }) {
  const [queue, setQueue] = useState<LineReviewCard[]>([]);
  const [phase, setPhase] = useState<Phase>('replay');
  const [boardFen, setBoardFen] = useState<string>(new Chess().fen());
  const [played, setPlayed] = useState<string | null>(null); // SAN of a wrong attempt
  const [missed, setMissed] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const replayTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const card = queue[0] ?? null;
  const view = card ? viewOf(card) : null;

  const stopReplay = () => {
    if (replayTimer.current) clearInterval(replayTimer.current);
    replayTimer.current = null;
  };

  /** After the replay: teach first (new opening card) or ask straight away. */
  const afterReplay = useCallback((c: LineReviewCard, missedNow: Set<number>) => {
    const v = viewOf(c);
    setBoardFen(v.fen);
    const firstTime = c.kind === 'opening' && c.repetitions === 0 && c.lapses === 0 && !missedNow.has(c.card_id);
    setPhase(firstTime ? 'learn' : 'ask');
  }, []);

  /** Replay the card's line from the start position, one move at a time. */
  const present = useCallback((cards: LineReviewCard[], missedNow: Set<number>) => {
    stopReplay();
    setQueue(cards);
    setPlayed(null);
    const next = cards[0];
    if (!next) return;
    const v = viewOf(next);
    const game = new Chess();
    setBoardFen(game.fen());
    setPhase('replay');
    if (v.line.length === 0) {
      afterReplay(next, missedNow);
      return;
    }
    let i = 0;
    replayTimer.current = setInterval(() => {
      try {
        game.move(v.line[i]);
      } catch {
        stopReplay();
        afterReplay(next, missedNow);
        return;
      }
      setBoardFen(game.fen());
      i += 1;
      if (i >= v.line.length) {
        stopReplay();
        afterReplay(next, missedNow);
      }
    }, REPLAY_MS);
  }, [afterReplay]);

  // "Review more": new items let in past today's limit, this session
  const [extraNew, setExtraNew] = useState(0);
  const [askedForMore, setAskedForMore] = useState(false);

  const refresh = useCallback(async (extra: number = 0) => {
    setLoading(true);
    setError(null);
    try {
      const cards = await getDueCards(20, { kinds: LINE_KINDS, extraNew: extra });
      present(cards.filter(isLineCard), new Set());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load your openings');
    } finally {
      setLoading(false);
    }
  }, [present]);

  useEffect(() => { refresh(); return stopReplay; }, [refresh]);

  const reviewMore = () => {
    const extra = extraNew + REVIEW_MORE_STEP;
    setExtraNew(extra);
    setAskedForMore(true);
    refresh(extra);
  };

  const skipReplay = () => {
    if (!card) return;
    stopReplay();
    afterReplay(card, missed);
  };

  const startPractice = () => {
    if (!view) return;
    setBoardFen(view.fen);
    setPhase('ask');
  };

  /** A wrong move or Show answer: record "again", retry later this session. */
  const miss = (attemptSan: string | null) => {
    if (!card || !view) return;
    setPlayed(attemptSan);
    setBoardFen(view.fen);
    setPhase('wrong');
    setMissed(prev => new Set(prev).add(card.card_id));
    answerCard(card.card_id, 'again').catch(() => setError('Couldn\'t save that result. Check your connection.'));
  };

  const onPieceDrop = (from: string, to: string, piece: string): boolean => {
    if (!card || !view || phase !== 'ask') return false;
    const game = new Chess(view.fen);
    let move;
    try {
      const promotion = piece[1]?.toLowerCase() === 'p' && (to[1] === '8' || to[1] === '1') ? 'q' : undefined;
      move = game.move({ from, to, promotion });
    } catch {
      return false; // illegal: the piece snaps back
    }
    const uci = move.from + move.to + (move.promotion ?? '');
    if (uci === view.answerUci) {
      setBoardFen(game.fen());
      setPhase('right');
      return true;
    }
    miss(move.san);
    return false;
  };

  const grade = async (g: ReviewGrade) => {
    if (!card) return;
    try {
      await answerCard(card.card_id, g);
      present(queue.slice(1), missed);
    } catch {
      setError('Couldn\'t save that grade. Check your connection and try again.');
    }
  };

  /** After a miss: the card goes to the back of the queue for another try. */
  const nextAfterMiss = () => {
    if (!card) return;
    present([...queue.slice(1), card], missed);
  };

  if (loading) return <div className="status-message"><p>Loading your openings…</p></div>;
  if (error && !card) return <div className="error-message">{error}</div>;
  if (!card || !view) {
    return (
      <div className="review-panel line-review-empty">
        <p>{askedForMore ? 'Nothing more to review.' : 'No opening or punish cards are due.'}</p>
        <p className="intro-note">
          Pick your moves in the Repertoire tab to train them here. Punish cards appear when an opponent
          leaves your repertoire with a mistake.
        </p>
        <button type="button" className="btn btn-secondary" onClick={reviewMore}>Review more</button>
      </div>
    );
  }

  const answerArrow: Array<[any, any, string]> =
    phase === 'learn' || phase === 'wrong' || phase === 'right'
      ? [[view.answerUci.slice(0, 2), view.answerUci.slice(2, 4), '#5cb87a']]
      : [];
  const ownColor = view.color === 'white' ? 'w' : 'b';
  const remaining = queue.length;

  return (
    <div className="review-panel">
      <div className="review-meta">
        <span>
          <span className={`card-type-badge ${card.kind === 'opening' ? 'card-type-avoid' : 'card-type-punish'}`}>
            {card.kind === 'opening' ? 'OPENING' : 'PUNISH'}
          </span>
          {remaining} to go{missed.has(card.card_id) ? ' · retry' : ''}
        </span>
        <span title={view.title}>{view.title}</span>
      </div>

      <div className="review-board">
        <Chessboard
          position={boardFen}
          onPieceDrop={onPieceDrop}
          boardOrientation={view.color}
          arePiecesDraggable={phase === 'ask'}
          isDraggablePiece={({ piece }) => piece[0] === ownColor}
          customBoardStyle={{ borderRadius: '6px' }}
          customLightSquareStyle={{ backgroundColor: BOARD_LIGHT }}
          customDarkSquareStyle={{ backgroundColor: BOARD_DARK }}
          customArrows={answerArrow}
        />
      </div>

      <p className="line-moves">{view.line.length ? numbered(view.line) : 'Starting position'}</p>

      <div className="review-prompt">
        {view.subtitle && <p className="intro-note">{view.subtitle}</p>}

        {phase === 'replay' && (
          <>
            <p>Replaying the line…</p>
            <button type="button" className="btn btn-secondary btn-sm" onClick={skipReplay}>Skip</button>
          </>
        )}

        {phase === 'learn' && (
          <>
            <p>New position. Here you play <span className="sym move-good">{view.answerSan}</span>.</p>
            {view.explanation
              ? <ExplanationBox explanation={view.explanation} moveSan={view.answerSan} />
              : <p className="intro-note">The explanation for this move is still being written.</p>}
            {isOwner && card.kind === 'opening' && (
              <OwnerEdit key={`${view.fen}|${view.answerUci}`} fen={view.fen} moveUci={view.answerUci} moveSan={view.answerSan} />
            )}
            <div className="review-grades">
              <button type="button" className="btn btn-primary" onClick={startPractice}>Practice it</button>
            </div>
          </>
        )}

        {phase === 'ask' && (
          <>
            <p>
              {card.kind === 'opening'
                ? 'Play your move in this position.'
                : 'Your move. How do you punish it?'}
            </p>
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => miss(null)}>Show answer</button>
          </>
        )}

        {phase === 'right' && (
          <>
            <p className="attempt-correct">Right: {view.answerSan}.</p>
            {view.explanation && <ExplanationBox explanation={view.explanation} moveSan={view.answerSan} />}
            {isOwner && card.kind === 'opening' && (
              <OwnerEdit key={`${view.fen}|${view.answerUci}`} fen={view.fen} moveUci={view.answerUci} moveSan={view.answerSan} />
            )}
            <div className="review-grades">
              <button type="button" className="btn btn-secondary" onClick={() => grade('good')}>Good</button>
              <button type="button" className="btn btn-primary" onClick={() => grade('easy')}>Easy</button>
            </div>
          </>
        )}

        {phase === 'wrong' && (
          <>
            <p className="attempt-wrong">
              {played ? `${played} isn't it. ` : ''}The move here is <span className="sym move-good">{view.answerSan}</span>.
            </p>
            {view.explanation && <ExplanationBox explanation={view.explanation} moveSan={view.answerSan} />}
            <div className="review-grades">
              <span className="auto-again-note">It comes back later in this session.</span>
              <button type="button" className="btn btn-primary" onClick={nextAfterMiss}>Next</button>
            </div>
          </>
        )}

        {error && <p className="attempt-wrong">{error}</p>}
      </div>
    </div>
  );
}

export default LineReview;
