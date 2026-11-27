import { useEffect, useRef, useState } from 'react';
import { Chessboard } from 'react-chessboard';
import { getNext, LICHESS_SIGN_IN_URL, setGoal, setRepertoireMove } from '../../services/api';
import { BUILDER_GOALS, type BuilderNext, type Color } from '../../types';
import { evalText, pct, ShareCell } from './cells';
import { numberedSan } from './layoutGraph';

/**
 * The step-by-step builder (specs/features/02-openings/repertoire-builder.md):
 * the likeliest position you haven't prepared, your choices, one Pick each.
 * A pick answers with the next position, so each step is one request.
 */

const BOARD_LIGHT = '#e9dcc3';
const BOARD_DARK = '#8b6b4a';
const FLASH_MS = 4000;

interface Props {
  color: Color;
  /** Open this position first (a gap clicked in the overview). */
  initialLine: string[] | null;
  onOpenOverview: () => void;
}

export default function BuilderView({ color, initialLine, onOpenOverview }: Props) {
  const [next, setNext] = useState<BuilderNext | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const flashTimer = useRef<number | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    setNext(null);
    setError(null);
    getNext(color, initialLine ?? undefined)
      .then((n) => { if (!cancelled) setNext(n); })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Couldn't load the next position.");
      });
    return () => { cancelled = true; };
  }, [color, initialLine]);

  useEffect(() => () => window.clearTimeout(flashTimer.current), []);

  const showFlash = (text: string) => {
    setFlash(text);
    window.clearTimeout(flashTimer.current);
    flashTimer.current = window.setTimeout(() => setFlash(null), FLASH_MS);
  };

  const gap = next?.gap ?? null;

  const onPick = async (uci: string, san: string) => {
    if (!gap) return;
    setBusy(true);
    setError(null);
    try {
      const result = await setRepertoireMove(color, gap.key, uci);
      const after = result.next;
      setNext(after);
      const added = `Added ${numberedSan(gap.line.length + 1, san)}.`;
      const nextGap = after?.gap;
      showFlash(nextGap?.last_reply_san
        ? `${added} Next: after ${numberedSan(nextGap.line.length, nextGap.last_reply_san)}`
        : added);
    } catch (err) {
      setError(err instanceof Error ? err.message : "That didn't work.");
    } finally {
      setBusy(false);
    }
  };

  const onGoal = async (goal: number) => {
    setBusy(true);
    setError(null);
    try {
      setNext(await setGoal(color, goal));
    } catch (err) {
      setError(err instanceof Error ? err.message : "That didn't work.");
    } finally {
      setBusy(false);
    }
  };

  const deeper = next ? BUILDER_GOALS.find((g) => g > next.goal) : undefined;
  const lastUci = gap && gap.last_reply_san ? gap.line[gap.line.length - 1] : null;
  const arrows: Array<[any, any, string]> = lastUci ? [[lastUci.slice(0, 2), lastUci.slice(2, 4), '#d4a34a']] : [];
  const atLevel = next?.source === 'lichess' ? 'at your level' : 'in master games';

  return (
    <div className="builder">
      <div className="builder-head">
        {next && (
          <p className="builder-progress">
            {next.prepared} prepared · {next.left} to go at 1 in {next.goal}
          </p>
        )}
        <label className="builder-goal">
          <span>Depth</span>
          <select className="input" value={next?.goal ?? 20} disabled={busy || !next}
            onChange={(e) => onGoal(Number(e.target.value))}>
            {BUILDER_GOALS.map((g) => <option key={g} value={g}>1 in {g} games</option>)}
          </select>
        </label>
        <button type="button" className="btn btn-secondary btn-sm" onClick={onOpenOverview}>
          See your repertoire
        </button>
      </div>

      {error && <div className="error-message">{error}</div>}
      {flash && <p className="detail-message" role="status">{flash}</p>}
      {next?.reconnect ? (
        <p className="intro-note">
          <a href={LICHESS_SIGN_IN_URL}>Sign in with Lichess again</a> to see what players at your level play.
        </p>
      ) : next?.source === 'masters' && (
        <p className="intro-note">Using master games: Lichess isn't reachable.</p>
      )}

      {!next && !error && <div className="status-message"><p>Finding your next position…</p></div>}

      {next && !gap && (
        <div className="builder-done">
          <p>Everything you'll meet in 1 in {next.goal} games is prepared.</p>
          {deeper && (
            <button type="button" className="btn btn-primary" disabled={busy} onClick={() => onGoal(deeper)}>
              Go deeper: 1 in {deeper}
            </button>
          )}
        </div>
      )}

      {gap && (
        <div className="builder-step">
          <div className="builder-info">
            <p className="builder-line">
              {gap.line_san.length === 0 ? 'The first move' : gap.line_san.map((san, i) => numberedSan(i + 1, san)).join(' ')}
            </p>
            <p className="builder-meta">
              {gap.opening && <>{gap.opening.name} · </>}
              {gap.likelihood !== null && gap.likelihood > 0
                ? <>1 in {Math.max(1, Math.round(1 / gap.likelihood))} games {atLevel}</>
                : <>your choice here</>}
              {gap.my_games > 0 && <> · you've had this {gap.my_games} time{gap.my_games === 1 ? '' : 's'}</>}
            </p>
            {gap.last_reply_san && gap.last_share !== null && (
              <p className="builder-meta">
                Your opponent played {numberedSan(gap.line.length, gap.last_reply_san)} ({pct(gap.last_share)} here)
              </p>
            )}
          </div>

          <div className="builder-board">
            <Chessboard
              position={gap.fen}
              boardOrientation={color}
              arePiecesDraggable={false}
              customBoardStyle={{ borderRadius: '6px' }}
              customLightSquareStyle={{ backgroundColor: BOARD_LIGHT }}
              customDarkSquareStyle={{ backgroundColor: BOARD_DARK }}
              customArrows={arrows}
            />
          </div>

          <div className="builder-choices">
            <h4>Your move</h4>
            {gap.engine_busy && (
              <p className="intro-note">The engine is busy analysing games, so there are no evaluations right now.</p>
            )}
            <div className="table-scroll">
              <table className="position-table">
                <thead>
                  <tr>
                    <th scope="col">Move</th>
                    <th scope="col">Engine</th>
                    <th scope="col">At your level</th>
                    <th scope="col">Masters</th>
                    <th scope="col"><span className="sr-only">Action</span></th>
                  </tr>
                </thead>
                <tbody>
                  {gap.candidates.map((c) => (
                    <tr key={c.uci}>
                      <th scope="row">
                        <span className="cell-main">{c.san}</span>
                        {c.theory && <span className="cell-sub"><span className="theory-tag">theory</span></span>}
                      </th>
                      <td>
                        {c.engine_rank ? (
                          <><span className="cell-main">#{c.engine_rank}</span><span className="cell-sub">{evalText(c.eval_cp)}</span></>
                        ) : '—'}
                      </td>
                      <td><ShareCell share={c.level_share} score={c.level_score} /></td>
                      <td><ShareCell share={c.master_share} score={c.master_score} /></td>
                      <td>
                        <button type="button" className="btn btn-primary btn-sm" disabled={busy}
                          onClick={() => onPick(c.uci, c.san)} aria-label={`Pick ${c.san}`}>
                          Pick
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {gap.candidates.length > 0 && (
              <p className="table-legend">
                Each share is how often that move is chosen here, by players at your level on Lichess and in
                master games. The score counts a win as 1 and a draw as ½, for the side to move.
              </p>
            )}
            {gap.candidates.length === 0 && <p className="intro-note">No moves to suggest here.</p>}
          </div>
        </div>
      )}
    </div>
  );
}
