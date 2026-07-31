import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Chessboard } from 'react-chessboard';
import { Chess } from 'chess.js';
import { getDueCards, answerCard, getReviewStats, getBestReply } from '../services/api';
import { BestReply, BlunderInfo, ReviewCardInfo, ReviewGrade, ReviewStats } from '../types';
import { IconEye, IconCheck, IconStar, IconArrowLeft, IconArrowRight } from './icons';

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

/** One step of the walkthrough: what the board shows, where the win bar
 * sits, the move that got here (null on the opening frame), and the
 * commentary explaining it. */
type WalkFrame = {
  fen: string;
  winPct: number; // the blunderer's — i.e. the user's — win%, 0..100
  san: string | null;
  note: string;
  arrowUci: string | null;
};

const PIECE_NAMES: Record<string, string> = {
  p: 'pawn', n: 'knight', b: 'bishop', r: 'rook', q: 'queen', k: 'king',
};

/** Win% swing (in points) below which the eval isn't worth remarking on. */
const NOTABLE_SWING = 10;

/**
 * Commentary for one ply, built from what's already known: the move object
 * chess.js produced (captures, check, mate) and the stored win% either side
 * of it. Deliberately factual — it states what changed rather than trying to
 * sound like an annotator, because everything here is derived, not judged.
 */
function describePly(
  move: { san: string; captured?: string; color: string },
  byUser: boolean,
  winBefore: number,
  winAfter: number,
): string {
  const parts: string[] = [];

  if (move.san.includes('#')) {
    parts.push(byUser ? 'Checkmate — you win.' : "Checkmate. That's the game.");
  } else if (move.captured) {
    const piece = PIECE_NAMES[move.captured] ?? 'piece';
    parts.push(byUser ? `Takes the ${piece}.` : `Takes your ${piece}.`);
    if (move.san.includes('+')) parts.push('With check.');
  } else if (move.san.includes('+')) {
    parts.push('Check.');
  }

  const swing = winAfter - winBefore;
  if (Math.abs(swing) >= NOTABLE_SWING) {
    const verb = swing < 0 ? 'fall' : 'recover';
    parts.push(`Your winning chances ${verb} from ${Math.round(winBefore)}% to ${Math.round(winAfter)}%.`);
  }

  // A quiet move with no notable swing produces nothing above. Say where
  // things stand rather than leaving the commentary blank — on a line
  // that's already decided, "nothing changed" is the point.
  if (parts.length === 0) {
    parts.push(`A quiet move — your chances stay around ${Math.round(winAfter)}%.`);
  }

  return parts.join(' ');
}

/**
 * The walkthrough: the position you faced, the move you played, then each
 * stored ply of how it was punished. Every frame comes from data already on
 * the blunder — no engine calls — so stepping back and forth is free.
 *
 * Frame 0 is the position before the blunder, so indices run 0..n with no
 * special case for "nothing played yet".
 */
function buildWalkFrames(blunder: BlunderInfo): WalkFrame[] {
  const chess = new Chess(blunder.fen_before);
  const frames: WalkFrame[] = [{
    fen: blunder.fen_before,
    winPct: blunder.win_prob_before,
    san: null,
    note: 'The position you faced. Step forward to see what you played.',
    arrowUci: null,
  }];

  const userIsWhite = blunder.user_color === 'white';
  const pushFrame = (uci: string, sanHint: string, winPct: number, prefix?: string) => {
    const move = chess.move({
      from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: (uci[4] as any) || 'q',
    });
    if (!move) return; // corrupt stored line — stop rather than throw
    const byUser = move.color === (userIsWhite ? 'w' : 'b');
    const prev = frames[frames.length - 1].winPct;
    const note = describePly(move, byUser, prev, winPct);
    frames.push({
      fen: chess.fen(),
      winPct,
      san: sanHint || move.san,
      note: prefix ? `${prefix} ${note}`.trim() : note,
      arrowUci: uci,
    });
  };

  pushFrame(
    blunder.move_played_uci,
    blunder.move_played_san,
    blunder.win_prob_after,
    `You played ${blunder.move_played_san}.`,
  );
  for (const ply of blunder.refutation_line) {
    pushFrame(ply.move_uci, ply.move_san, ply.win_prob);
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
  // Which walkthrough frame is showing (0 = before the blunder). Only
  // meaningful while attempt.state === 'intro'.
  const [walkIndex, setWalkIndex] = useState(0);
  const [selectedSquare, setSelectedSquare] = useState<string | null>(null);
  // Cards missed this session — they come back at the end of the queue as
  // a retry round, and the status line should say so
  const [missedIds, setMissedIds] = useState<Set<number>>(new Set());
  // Punish cards being shown as the second half of a walkthrough lesson.
  // The walkthrough just showed the refutation, so answering one proves
  // nothing — these are played but never graded (see finishCard).
  const [taughtIds, setTaughtIds] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const card = queue[0] ?? null;

  // The walkthrough's frames, and the one currently shown. Cheap to rebuild
  // (pure chess.js replay over stored data), but memoized so stepping
  // doesn't re-derive the whole line on every render.
  const walkFrames = useMemo<WalkFrame[]>(
    () => (card && attempt.state === 'intro' ? buildWalkFrames(card.blunder) : []),
    [card, attempt.state],
  );
  const walkFrame = walkFrames[walkIndex] ?? null;

  // Board arrows per phase:
  // - intro: the move that produced the current frame (red), so each step
  //   shows at a glance what just happened. Absent on frame 0, where
  //   nothing has been played yet.
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
    if (attempt.state === 'intro') {
      return walkFrame?.arrowUci ? [uciArrow(walkFrame.arrowUci, '#d96b52')] : [];
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
  }, [card, attempt, walkFrame]);

  // Kept in sync with missedIds so presentCard() (called from event
  // handlers, not effects) can read the current value without needing it
  // in a dependency array.
  const missedIdsRef = useRef<Set<number>>(missedIds);
  useEffect(() => { missedIdsRef.current = missedIds; }, [missedIds]);

  // Same, for refresh() — see the filter there.
  const taughtIdsRef = useRef<Set<number>>(taughtIds);
  useEffect(() => { taughtIdsRef.current = taughtIds; }, [taughtIds]);

  /**
   * Does this card open with the teaching walkthrough?
   *
   * Only avoid cards: the walkthrough shows the refutation, which is the
   * punish card's answer, so a punish card must never lead with it.
   *
   * `repetitions === 0` covers both cases we teach — never seen, and
   * lapsed — because an "again" grade resets repetitions to 0 server-side.
   * Excluding cards missed this session is what stops the 10-minute retry
   * from replaying the lesson you just sat through.
   */
  const opensWithWalkthrough = (c: ReviewCardInfo) =>
    c.card_type === 'avoid' &&
    c.repetitions === 0 &&
    !missedIdsRef.current.has(c.card_id);

  /**
   * Show whichever card is now at the front of the queue, and decide
   * whether it opens on the walkthrough or straight on the question.
   *
   * When it does open on the walkthrough, that blunder's punish card is
   * pulled up to follow immediately: the two cards are the same lesson from
   * both sides, and teaching one then leaving the other to surface days
   * later wastes the context. That paired exposure is marked as taught, so
   * finishCard() knows not to grade it.
   */
  const presentCard = (cards: ReviewCardInfo[]) => {
    setSelectedSquare(null);
    setWalkIndex(0);

    const next = cards[0];
    if (!next) {
      setQueue(cards);
      setAttempt({ state: 'thinking' });
      setDisplayFen(null);
      return;
    }

    const mode = deriveMode(next);

    if (!opensWithWalkthrough(next)) {
      setQueue(cards);
      setAttempt({ state: 'thinking' });
      setDisplayFen(mode.baseFen);
      return;
    }

    // Pull this blunder's punish card up to second place, if it's due too.
    let ordered = cards;
    const siblingIdx = cards.findIndex(
      (c, i) => i > 0 && c.card_type === 'punish' && c.blunder.id === next.blunder.id,
    );
    if (siblingIdx > 0) {
      const sibling = cards[siblingIdx];
      ordered = [next, sibling, ...cards.filter((_, i) => i !== 0 && i !== siblingIdx)];
      setTaughtIds(prev => new Set(prev).add(sibling.card_id));
    }
    setQueue(ordered);

    setAttempt({ state: 'intro' });
    setDisplayFen(next.blunder.fen_before); // frame 0 — the position faced
  };

  /** Step the walkthrough, clamped to its ends. */
  const walkStep = (delta: number) => {
    if (!card) return;
    const frames = buildWalkFrames(card.blunder);
    const i = Math.max(0, Math.min(frames.length - 1, walkIndex + delta));
    setWalkIndex(i);
    setDisplayFen(frames[i].fen);
  };

  /** Leave the walkthrough and attempt the correction. */
  const startAttempt = () => {
    if (!card) return;
    setDisplayFen(deriveMode(card).baseFen);
    setAttempt({ state: 'thinking' });
  };

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [cards, s] = await Promise.all([getDueCards(), getReviewStats()]);
      setStats(s);
      // Cards taught this session are deliberately left ungraded, which
      // means the server still reports them as due — without this filter
      // they'd be re-served the moment the queue empties, forever, since
      // grading them is what would normally clear them.  They keep their
      // due date and come back in a later session, when the line they were
      // shown isn't fresh any more.
      presentCard(cards.filter(c => !taughtIdsRef.current.has(c.card_id)));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load reviews');
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  /**
   * Cards shown as the taught half of a lesson are played, not tested: the
   * walkthrough handed over the answer moments earlier, so grading one
   * would start its SM-2 schedule from a memory instead of a recall. It
   * stays due and gets its first real grade whenever it next comes up on
   * its own.
   */
  const isTaught = (c: ReviewCardInfo) => taughtIds.has(c.card_id);

  /** Record a miss: auto-grade "again" — no button press needed. */
  const recordMiss = (c: ReviewCardInfo) => {
    setMissedIds(prev => new Set(prev).add(c.card_id));
    if (isTaught(c)) return;
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

  /**
   * After a miss: re-queue for a retry later this session. A taught card is
   * dropped instead — it was never graded, so it's still due and will come
   * back on its own terms rather than as a retry of a question it was just
   * handed the answer to.
   */
  const nextAfterMiss = () => {
    if (!card) return;
    presentCard(isTaught(card) ? queue.slice(1) : [...queue.slice(1), card]);
  };

  const grade = async (g: ReviewGrade) => {
    if (!card) return;
    try {
      if (!isTaught(card)) await answerCard(card.card_id, g);
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

  const atWalkEnd = attempt.state === 'intro' && walkIndex === walkFrames.length - 1;

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
  // During the walkthrough the current frame drives the bar, so it tracks
  // the real eval at each step; otherwise it's mode.solverWinPct. Frames
  // store the blunderer's win%, which is the solver's here — walkthroughs
  // only ever run on avoid cards. The board is oriented so the solver's
  // side sits at the bottom, and the bar mirrors that.
  const barWinPct = walkFrame ? walkFrame.winPct : mode.solverWinPct;
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
          {/* Taught cards aren't graded, so say so rather than leaving the
              user to wonder why their answer didn't seem to count */}
          {isTaught(card) ? (
            <span className="new-card-badge" title="Part of the lesson — not scored"> LESSON</span>
          ) : missedIds.has(card.card_id) ? (
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
        {/* Walkthrough — avoid cards only, see presentCard */}
        {walkFrame && (
          <>
            <p className="intro-note">
              {walkFrame.san ? (
                <>
                  Step {walkIndex} of {walkFrames.length - 1}:{' '}
                  <strong className={walkIndex === 1 ? 'move-bad' : undefined}>
                    {walkFrame.san}
                  </strong>
                </>
              ) : 'Before the blunder'}
            </p>
            {walkFrame.note && <p className="why-blunder">{walkFrame.note}</p>}
          </>
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

      {/* Step through at your own pace, either direction. "Try it" is
          always available so the walkthrough can be cut short, and turns
          primary at the end of the line where it's the obvious next move. */}
      {walkFrame && (
        <div className="review-grades">
          <button
            type="button"
            className="btn btn-secondary btn-icon"
            onClick={() => walkStep(-1)}
            disabled={walkIndex === 0}
            aria-label="Previous move"
            title="Previous move"
          >
            <IconArrowLeft size={18} />
          </button>
          <button
            type="button"
            className="btn btn-secondary btn-icon"
            onClick={() => walkStep(1)}
            disabled={atWalkEnd}
            aria-label="Next move"
            title="Next move"
          >
            <IconArrowRight size={18} />
          </button>
          <button
            type="button"
            className={atWalkEnd ? 'btn btn-primary' : 'btn btn-secondary'}
            onClick={startAttempt}
          >
            Try it
          </button>
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
          <span className="auto-again-note">
            {isTaught(card)
              ? "Part of the lesson — this one isn't scored."
              : "Scheduled to repeat — you'll retry it shortly."}
          </span>
          <button type="button" className="btn btn-primary" onClick={nextAfterMiss}>
            <IconArrowRight size={16} /> Next
          </button>
        </div>
      )}
    </div>
  );
}

export default ReviewPanel;
