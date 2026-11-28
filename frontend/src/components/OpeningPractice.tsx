import { useCallback, useEffect, useRef, useState } from 'react';
import { Chessboard } from 'react-chessboard';
import { Chess } from 'chess.js';
import { answerCard, getLine, REVIEW_MORE_STEP } from '../services/api';
import type { LinePractice } from '../types';
import LineReview, { ExplanationBox, OwnerEdit } from './LineReview';

/**
 * Practising whole lines (specs/features/02-openings/repertoire-builder.md,
 * "Revisions"): from the start to the end of a branch, you play every one of
 * your moves and the opponent's replies play themselves. A new move is shown
 * with its explanation first; after that the explanation only appears when
 * you play a wrong move. Only due moves are graded. Punish cards follow, in
 * their own one-position flow.
 */

const BOARD_LIGHT = '#e9dcc3';
const BOARD_DARK = '#8b6b4a';
const OPPONENT_MS = 450;

type Phase = 'opponent' | 'learn' | 'ask' | 'wrong' | 'done';

function numbered(sans: string[]): string {
  return sans.map((san, i) => (i % 2 === 0 ? `${i / 2 + 1}. ${san}` : san)).join(' ');
}

export default function OpeningPractice({ isOwner }: { isOwner: boolean }) {
  const [line, setLine] = useState<LinePractice | null>(null);
  const [index, setIndex] = useState(0);
  const [phase, setPhase] = useState<Phase>('opponent');
  const [fen, setFen] = useState(new Chess().fen());
  const [played, setPlayed] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [noLines, setNoLines] = useState(false);
  const [extraNew, setExtraNew] = useState(0);
  const [linesDone, setLinesDone] = useState(0);
  const graded = useRef<Set<number>>(new Set()); // once per session
  const timer = useRef<number | undefined>(undefined);

  const load = useCallback(async (extra: number) => {
    setLoading(true);
    setError(null);
    try {
      const next = await getLine(extra);
      setLine(next);
      setNoLines(next === null);
      setIndex(0);
      setPlayed(null);
      setFen(next ? next.moves[0].fen_before : new Chess().fen());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't load a line.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(0); return () => window.clearTimeout(timer.current); }, [load]);

  // Each move: the opponent's plays itself; yours is taught (if new) or asked
  useEffect(() => {
    if (!line) return;
    window.clearTimeout(timer.current);
    if (index >= line.moves.length) {
      setPhase('done');
      return;
    }
    const move = line.moves[index];
    setFen(move.fen_before);
    if (move.by === 'opponent') {
      setPhase('opponent');
      timer.current = window.setTimeout(() => setIndex((i) => i + 1), OPPONENT_MS);
      return;
    }
    const firstTime = move.new && move.card_id !== null && !graded.current.has(move.card_id);
    setPhase(firstTime ? 'learn' : 'ask');
  }, [line, index]);

  const grade = (cardId: number | null, g: 'good' | 'again') => {
    if (cardId === null || graded.current.has(cardId)) return;
    graded.current.add(cardId);
    answerCard(cardId, g).catch(() => setError("Couldn't save that result. Check your connection."));
  };

  const advance = () => {
    setPlayed(null);
    setIndex((i) => i + 1);
  };

  const onPieceDrop = (from: string, to: string, piece: string): boolean => {
    if (!line || (phase !== 'ask' && phase !== 'learn' && phase !== 'wrong')) return false;
    const move = line.moves[index];
    const game = new Chess(move.fen_before);
    let tried;
    try {
      const promotion = piece[1]?.toLowerCase() === 'p' && (to[1] === '8' || to[1] === '1') ? 'q' : undefined;
      tried = game.move({ from, to, promotion });
    } catch {
      return false;
    }
    const uci = tried.from + tried.to + (tried.promotion ?? '');
    if (uci === move.uci) {
      // Right: graded Good if due (a move missed earlier this line was already graded Again)
      if (move.graded && phase !== 'wrong') grade(move.card_id, 'good');
      setFen(game.fen());
      window.setTimeout(advance, 250);
      return true;
    }
    if (phase === 'learn') return false; // learning: just play the move shown
    if (move.graded) grade(move.card_id, 'again');
    setPlayed(tried.san);
    setPhase('wrong');
    return false;
  };

  const reviewMore = () => {
    const extra = extraNew + REVIEW_MORE_STEP;
    setExtraNew(extra);
    load(extra);
  };

  if (loading) return <div className="status-message"><p>Loading a line…</p></div>;
  if (error && !line) return <div className="error-message">{error}</div>;

  if (noLines || !line) {
    // No opening line due: punish cards from your games, then Review more
    return (
      <div>
        {linesDone > 0 && <p className="detail-message" role="status">{linesDone} line{linesDone === 1 ? '' : 's'} done.</p>}
        <LineReview isOwner={isOwner} kinds={['deviation']} />
        <div className="review-grades">
          <button type="button" className="btn btn-secondary" onClick={reviewMore}>Review more lines</button>
        </div>
      </div>
    );
  }

  const move = line.moves[Math.min(index, line.moves.length - 1)];
  const sansSoFar = line.moves.slice(0, index).map((m) => m.san);
  const ownColor = line.color === 'white' ? 'w' : 'b';
  const showAnswer = phase === 'learn' || phase === 'wrong';
  const arrows: Array<[any, any, string]> =
    showAnswer ? [[move.uci.slice(0, 2), move.uci.slice(2, 4), '#5cb87a']] : [];
  const yours = line.moves.filter((m) => m.by === 'user');
  const yoursDone = line.moves.slice(0, index).filter((m) => m.by === 'user').length;

  return (
    <div className="review-panel">
      <div className="review-meta">
        <span>
          <span className="card-type-badge card-type-avoid">LINE</span>
          {yoursDone} of {yours.length} moves
        </span>
        <span title={line.title ?? undefined}>{line.title ?? 'Your repertoire'}</span>
      </div>

      <div className="review-board">
        <Chessboard
          position={fen}
          onPieceDrop={onPieceDrop}
          boardOrientation={line.color}
          arePiecesDraggable={phase === 'ask' || phase === 'learn' || phase === 'wrong'}
          isDraggablePiece={({ piece }) => piece[0] === ownColor}
          animationDuration={250}
          customBoardStyle={{ borderRadius: '6px' }}
          customLightSquareStyle={{ backgroundColor: BOARD_LIGHT }}
          customDarkSquareStyle={{ backgroundColor: BOARD_DARK }}
          customArrows={arrows}
        />
      </div>

      <p className="line-moves">{sansSoFar.length ? numbered(sansSoFar) : 'Starting position'}</p>

      <div className="review-prompt">
        {error && <div className="error-message">{error}</div>}

        {phase === 'opponent' && <p className="muted">Your opponent plays {move.san}…</p>}

        {phase === 'learn' && (
          <>
            <p>New move. Here you play <span className="sym move-good">{move.san}</span>: play it on the board.</p>
            {move.explanation
              ? <ExplanationBox explanation={move.explanation} moveSan={move.san} />
              : <p className="intro-note">The explanation for this move is still being written.</p>}
          </>
        )}

        {phase === 'ask' && (
          <p>{move.graded ? 'Your move.' : 'Your move (already learned, not graded).'}</p>
        )}

        {phase === 'wrong' && (
          <>
            <p className="attempt-wrong">
              {played ? `${played} isn't it. ` : ''}The move here is <span className="sym move-good">{move.san}</span>.
              Play it to go on.
            </p>
            {move.explanation && <ExplanationBox explanation={move.explanation} moveSan={move.san} />}
          </>
        )}

        {(phase === 'learn' || phase === 'wrong') && isOwner && (
          <OwnerEdit key={`${move.fen_before}|${move.uci}`} fen={move.fen_before} moveUci={move.uci} moveSan={move.san} />
        )}

        {phase === 'done' && (
          <>
            <p className="attempt-correct">Line done: {numbered(line.moves.map((m) => m.san))}</p>
            <div className="review-grades">
              <button type="button" className="btn btn-primary"
                onClick={() => { setLinesDone((n) => n + 1); load(extraNew); }}>
                Next line
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
