import { useCallback, useEffect, useRef, useState } from 'react';
import { Chessboard } from 'react-chessboard';
import { Chess } from 'chess.js';
import { getExplore, getNext, LICHESS_SIGN_IN_URL, setDepth, setRepertoireMove } from '../../services/api';
import { BUILDER_DEPTHS, type CandidateRow, type Color, type ExplorePosition } from '../../types';
import { evalText, pct } from './cells';
import { numberedSan } from './layoutGraph';

/**
 * The builder as a navigator (specs/features/02-openings/repertoire-builder.md,
 * "The screen"): walk your repertoire move by move. At your opponent's turn you
 * see their likely replies, named, with how often each comes up and how much
 * below it you've prepared; at your turn, your choices. Every move is played
 * out on the board.
 */

const BOARD_LIGHT = '#e9dcc3';
const BOARD_DARK = '#8b6b4a';
const STEP_MS = 320; // between moves when a line is played out
const FIRST_ROWS = 8;

interface Props {
  color: Color;
  /** Open this position first (a gap clicked in the overview). */
  initialLine: string[] | null;
  onOpenOverview: () => void;
}

function fenAfter(line: string[]): string {
  const game = new Chess();
  for (const uci of line) game.move({ from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci[4] });
  return game.fen();
}

function oneIn(likelihood: number | null): string {
  if (likelihood === null || likelihood <= 0) return '—';
  if (likelihood >= 0.995) return 'every game';
  return `1 in ${Math.max(1, Math.round(1 / likelihood))} games`;
}

/** Win / draw / loss for the side to move, as a bar with the win and loss shares. */
function ResultsBar({ c }: { c: CandidateRow }) {
  if (c.level_win == null || c.level_draw == null || c.level_loss == null) return <>—</>;
  const w = Math.round(c.level_win * 100);
  const d = Math.round(c.level_draw * 100);
  const l = Math.round(c.level_loss * 100);
  return (
    <span className="results-bar" title={`Wins ${w}%, draws ${d}%, losses ${l}% for the side to move`}>
      <span className="results-win" style={{ flexGrow: Math.max(w, 1) }}>{w}</span>
      <span className="results-draw" style={{ flexGrow: Math.max(d, 1) }} />
      <span className="results-loss" style={{ flexGrow: Math.max(l, 1) }}>{l}</span>
    </span>
  );
}

function CompletionBar({ value }: { value: number | null }) {
  if (value === null) return <span className="muted">—</span>;
  return (
    <span className="completion-bar" title={`${Math.round(value * 100)}% prepared`}>
      <span style={{ width: `${Math.round(value * 100)}%` }} />
    </span>
  );
}

export default function BuilderView({ color, initialLine, onOpenOverview }: Props) {
  const [line, setLine] = useState<string[]>(initialLine ?? []);
  const [loaded, setLoaded] = useState<ExplorePosition | null>(null);
  const [boardFen, setBoardFen] = useState(fenAfter(initialLine ?? []));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const shownLine = useRef<string[]>(initialLine ?? []);
  const timers = useRef<number[]>([]);

  // A new colour or a gap from the overview starts the walk there
  useEffect(() => {
    const start = initialLine ?? [];
    setLine(start);
    shownLine.current = start;
    setBoardFen(fenAfter(start));
  }, [color, initialLine]);

  /** Play the board from what it shows to the end of `next`, move by move when it's deeper. */
  const playTo = useCallback((next: string[]) => {
    timers.current.forEach(window.clearTimeout);
    timers.current = [];
    const from = shownLine.current;
    const extends_ = from.length < next.length && from.every((m, i) => next[i] === m);
    shownLine.current = next;
    if (!extends_) {
      setBoardFen(fenAfter(next));
      return;
    }
    next.slice(from.length).forEach((_, i) => {
      timers.current.push(window.setTimeout(
        () => setBoardFen(fenAfter(next.slice(0, from.length + i + 1))), i * STEP_MS));
    });
  }, []);

  useEffect(() => () => timers.current.forEach(window.clearTimeout), []);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    setShowAll(false);
    getExplore(color, line)
      .then((p) => { if (!cancelled) setLoaded(p); })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Couldn't load that position.");
      });
    return () => { cancelled = true; };
  }, [color, line]);

  const go = (next: string[]) => {
    playTo(next);
    setLine(next);
  };

  const onPick = async (c: CandidateRow) => {
    if (!pos) return;
    setBusy(true);
    setError(null);
    try {
      await setRepertoireMove(color, pos.key, c.uci, false);
      go([...line, c.uci]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "That didn't work.");
    } finally {
      setBusy(false);
    }
  };

  const onDepth = async (depth: number) => {
    setBusy(true);
    try {
      await setDepth(color, depth);
      setLine((l) => [...l]); // reload this position at the new depth
    } catch (err) {
      setError(err instanceof Error ? err.message : "That didn't work.");
    } finally {
      setBusy(false);
    }
  };

  const onNextGap = async () => {
    setBusy(true);
    setError(null);
    try {
      const next = await getNext(color);
      if (next.gap) go(next.gap.line);
      else setError(`Everything common up to move ${next.depth} is prepared.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "That didn't work.");
    } finally {
      setBusy(false);
    }
  };

  // Only the position the line points at: never the previous one while the next loads
  const pos = loaded && loaded.line.join(',') === line.join(',') ? loaded : null;
  const side = color === 'white' ? 'White' : 'Black';
  const lastUci = line[line.length - 1];
  const arrows: Array<[any, any, string]> = lastUci ? [[lastUci.slice(0, 2), lastUci.slice(2, 4), '#d4a34a']] : [];
  const sans = pos ? pos.line_san : loaded && line.join(',').startsWith(loaded.line.join(','))
    ? loaded.line_san : null;
  const lastSan = sans && sans.length ? numberedSan(sans.length, sans[sans.length - 1]) : null;
  const atLevel = pos?.source === 'masters' ? 'in master games' : 'at your level';

  const inScope = pos?.replies.filter((r) => r.in_scope) ?? [];
  const rare = pos?.replies.filter((r) => !r.in_scope) ?? [];
  const replies = showAll ? [...inScope, ...rare] : inScope;
  const choices = pos ? (showAll ? pos.candidates : pos.candidates.slice(0, FIRST_ROWS)) : [];
  const moreCount = pos ? (pos.user_to_move ? pos.candidates.length - FIRST_ROWS : rare.length) : 0;

  return (
    <div className="builder">
      <div className="builder-head">
        <nav className="builder-path" aria-label="Line">
          <button type="button" className="link-button" onClick={() => go([])}>{side}</button>
          {(sans ?? []).map((san, i) => (
            <span key={i}>
              {' ▸ '}
              <button type="button" className="link-button" onClick={() => go(line.slice(0, i + 1))}
                aria-current={i === line.length - 1 ? 'step' : undefined}>
                {numberedSan(i + 1, san)}
              </button>
            </span>
          ))}
        </nav>
        {pos && (
          <span className="builder-complete">
            {pos.completion === null ? 'Nothing to prepare at this depth' : `${Math.round(pos.completion * 100)}% complete`}
          </span>
        )}
        <label className="builder-goal">
          <span>Depth</span>
          <select className="input" value={pos?.depth ?? 6} disabled={busy || !pos}
            onChange={(e) => onDepth(Number(e.target.value))}>
            {BUILDER_DEPTHS.map((d) => <option key={d} value={d}>Up to move {d}</option>)}
          </select>
        </label>
        <button type="button" className="btn btn-secondary btn-sm" disabled={busy} onClick={onNextGap}>
          Next unprepared position
        </button>
        <button type="button" className="btn btn-secondary btn-sm" onClick={onOpenOverview}>
          See your repertoire
        </button>
      </div>

      {error && <div className="error-message">{error}</div>}
      {pos?.reconnect ? (
        <p className="intro-note">
          <a href={LICHESS_SIGN_IN_URL}>Sign in with Lichess again</a> to see what players at your level play.
        </p>
      ) : pos?.source === 'masters' && (
        <p className="intro-note">Using master games: Lichess isn't reachable.</p>
      )}

      <div className="builder-step">
        <div className="builder-board">
          <Chessboard
            position={boardFen}
            boardOrientation={color}
            arePiecesDraggable={false}
            animationDuration={STEP_MS - 40}
            customBoardStyle={{ borderRadius: '6px' }}
            customLightSquareStyle={{ backgroundColor: BOARD_LIGHT }}
            customDarkSquareStyle={{ backgroundColor: BOARD_DARK }}
            customArrows={arrows}
          />
          <p className="builder-position-name">
            {pos?.opening?.name ?? (line.length === 0 ? 'Starting position' : '')}
            {pos && pos.likelihood !== null && line.length > 0 && (
              <span className="muted"> · {oneIn(pos.likelihood)} {atLevel}</span>
            )}
          </p>
        </div>

        <div className="builder-choices">
          {!pos && !error && <div className="status-message"><p>Loading the position…</p></div>}

          {pos && !pos.user_to_move && (
            <>
              <h3 className="builder-title">
                {lastSan ? `Choose a reply to prepare for, after ${lastSan}` : 'Choose a first move to prepare for'}
              </h3>
              <div className="table-scroll">
                <table className="position-table">
                  <thead>
                    <tr>
                      <th scope="col">Opponent's move</th>
                      <th scope="col">Expected in</th>
                      <th scope="col">Prepared</th>
                    </tr>
                  </thead>
                  <tbody>
                    {replies.map((r) => (
                      <tr key={r.uci} className="clickable" onClick={() => go([...line, r.uci])}>
                        <th scope="row">
                          <button type="button" className="link-button"
                            onClick={(e) => { e.stopPropagation(); go([...line, r.uci]); }}>
                            {numberedSan(line.length + 1, r.san)}
                          </button>
                          {r.opening && <span className="cell-sub">{r.opening.name}</span>}
                        </th>
                        <td>{oneIn(r.likelihood)}</td>
                        <td><CompletionBar value={r.completion ?? (r.answered ? 1 : null)} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {replies.length === 0 && (
                <p className="intro-note">
                  {rare.length ? 'No common reply here within your depth.' : 'No replies known here.'}
                </p>
              )}
            </>
          )}

          {pos && pos.user_to_move && (
            <>
              <h3 className="builder-title">
                {lastSan ? `Choose your move after ${lastSan}` : 'Choose your first move'}
              </h3>
              {pos.pick_blocked_at && (
                <p className="position-blocked">
                  Your repertoire doesn't reach this position.{' '}
                  <button type="button" className="link-button" onClick={() => go(pos.pick_blocked_at!)}>
                    Go to where it stops
                  </button>
                </p>
              )}
              {pos.engine_busy && (
                <p className="intro-note">The engine is busy analysing games, so there are no evaluations right now.</p>
              )}
              <div className="table-scroll">
                <table className="position-table">
                  <thead>
                    <tr>
                      <th scope="col">Move</th>
                      <th scope="col">Masters</th>
                      <th scope="col">Eval</th>
                      <th scope="col">At your level</th>
                      <th scope="col"><span className="sr-only">Action</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    {choices.map((c) => {
                      const mine = pos.repertoire_move === c.uci;
                      return (
                        <tr key={c.uci} className={mine ? 'is-repertoire clickable' : undefined}
                          onClick={mine ? () => go([...line, c.uci]) : undefined}>
                          <th scope="row">
                            <span className="cell-main">{numberedSan(line.length + 1, c.san)}</span>
                            {c.opening
                              ? <span className="cell-sub">{c.opening.name}</span>
                              : c.theory && <span className="cell-sub"><span className="theory-tag">theory</span></span>}
                          </th>
                          <td>{c.master_share ? pct(c.master_share) : '—'}</td>
                          <td>{c.engine_rank ? evalText(c.eval_cp) : '—'}</td>
                          <td>
                            <span className="cell-main">{c.level_share ? pct(c.level_share) : '—'}</span>
                            <span className="cell-sub"><ResultsBar c={c} /></span>
                          </td>
                          <td>
                            {mine ? (
                              <span className="muted">yours ▸</span>
                            ) : !pos.pick_blocked_at && (
                              <button type="button" className="btn btn-primary btn-sm" disabled={busy}
                                onClick={(e) => { e.stopPropagation(); onPick(c); }} aria-label={`Pick ${c.san}`}>
                                Pick
                              </button>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {pos.candidates.length > 0 && (
                <p className="table-legend">
                  Masters: how often masters choose it. At your level: how often players at your rating
                  choose it, with wins, draws and losses for the side to move.
                </p>
              )}
              {pos.candidates.length === 0 && <p className="intro-note">No moves to suggest here.</p>}
            </>
          )}

          {pos && moreCount > 0 && (
            <button type="button" className="link-button builder-more" onClick={() => setShowAll((v) => !v)}>
              {showAll ? 'Show fewer' : `Show more (${moreCount})`}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
