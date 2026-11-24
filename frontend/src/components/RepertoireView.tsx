import { useCallback, useEffect, useMemo, useState } from 'react';
import { Chessboard } from 'react-chessboard';
import { Chess } from 'chess.js';
import {
  acceptPositions, getDeviations, getExplanation, getMoveOptions, getRepertoire,
  removeRepertoirePosition, setRepertoireMove, writeExplanation,
} from '../services/api';
import {
  Color, DeviationInfo, ExplanationInfo, MoveOption, RepertoireEntry, RepertoireInfo,
  RepertoireSuggestion,
} from '../types';

/**
 * Your opening repertoire (Phase 2): what your games suggest, what you've
 * accepted, and the tools to shape it. Accepted positions become opening
 * cards in Learn.
 */

const BOARD_LIGHT = '#e9dcc3';
const BOARD_DARK = '#8b6b4a';

/** One row of the tree: a position where it's your move. */
interface Row {
  key: string;
  ply: number;
  depth: number;
  parentKey: string | null;
  suggestion: RepertoireSuggestion | null;
  accepted: RepertoireEntry | null;
  replySan?: string; // the opponent's move that leads here
}

const fenOf = (key: string) => `${key} 0 1`;

/** A position key as the server writes it (python-chess's board.epd()): the
 * first four FEN fields, with the en-passant square only when a capture is
 * actually possible there. chess.js writes it after every double pawn push. */
function keyOf(fen: string): string {
  const fields = fen.split(' ').slice(0, 4);
  if (fields[3] !== '-') {
    const game = new Chess(fen);
    if (!game.moves({ verbose: true }).some(m => m.flags.includes('e'))) fields[3] = '-';
  }
  return fields.join(' ');
}

/** "3. Bb5" for White's move, "3... a6" for Black's. */
function moveLabel(ply: number, san: string): string {
  const n = Math.floor(ply / 2) + 1;
  return ply % 2 === 0 ? `${n}. ${san}` : `${n}... ${san}`;
}

/** Suggestions and accepted positions merged into one tree, parents first. */
function buildRows(info: RepertoireInfo): Row[] {
  const accepted = new Map(info.accepted.map(a => [a.position_key, a]));
  const rows = new Map<string, Row>();
  for (const s of info.suggestions) {
    rows.set(s.position_key, {
      key: s.position_key, ply: s.ply, depth: 0, parentKey: s.parent_key,
      suggestion: s, accepted: accepted.get(s.position_key) ?? null,
    });
  }
  for (const a of info.accepted) {
    if (!rows.has(a.position_key)) {
      const board = new Chess(fenOf(a.position_key));
      const parent = a.parent_key ? rows.get(a.parent_key) : undefined;
      // Ply isn't stored for hand-added positions; count it from the parent.
      const ply = parent ? parent.ply + 2 : (board.turn() === 'w' ? 0 : 1);
      rows.set(a.position_key, {
        key: a.position_key, ply, depth: 0, parentKey: a.parent_key, suggestion: null, accepted: a,
      });
    }
  }
  // The opponent's reply that leads to each row, so sibling rows (3. f4
  // after 2...Nf6 vs after 2...Nc6) can be told apart.
  for (const r of rows.values()) {
    const parent = r.parentKey ? rows.get(r.parentKey) : undefined;
    let game: Chess;
    if (parent) {
      const uci = parent.accepted?.move_uci ?? parent.suggestion?.move_uci;
      if (!uci) continue;
      game = new Chess(fenOf(parent.key));
      try { game.move({ from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci[4] }); } catch { continue; }
    } else {
      game = new Chess();
      if (keyOf(game.fen()) === r.key) continue; // White's root: no reply before it
    }
    const reply = game.moves({ verbose: true }).find(m => {
      const g = new Chess(game.fen());
      g.move(m.san);
      return keyOf(g.fen()) === r.key;
    });
    if (reply) r.replySan = reply.san;
  }
  // Depth from parent links; order: depth-first from the roots, children by ply then move.
  const children = new Map<string | null, Row[]>();
  for (const r of rows.values()) {
    const p = r.parentKey && rows.has(r.parentKey) ? r.parentKey : null;
    if (!children.has(p)) children.set(p, []);
    children.get(p)!.push(r);
  }
  const ordered: Row[] = [];
  const walk = (parent: string | null, depth: number) => {
    const kids = (children.get(parent) ?? []).sort((a, b) =>
      (b.suggestion?.games ?? 0) - (a.suggestion?.games ?? 0) || a.key.localeCompare(b.key));
    for (const k of kids) {
      k.depth = depth;
      ordered.push(k);
      walk(k.key, depth + 1);
    }
  };
  walk(null, 0);
  return ordered;
}

function ExplanationPanel({
  positionKey, moveUci, moveSan, isOwner,
}: { positionKey: string; moveUci: string; moveSan: string; isOwner: boolean }) {
  const [explanation, setExplanation] = useState<ExplanationInfo | null | undefined>(undefined);
  const [editing, setEditing] = useState(false);
  const [plan, setPlan] = useState('');
  const [why, setWhy] = useState('');
  const [pawns, setPawns] = useState('');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setExplanation(undefined);
    setEditing(false);
    getExplanation(positionKey, moveUci)
      .then(e => { if (!cancelled) setExplanation(e); })
      .catch(() => { if (!cancelled) setExplanation(null); });
    return () => { cancelled = true; };
  }, [positionKey, moveUci]);

  const startEdit = () => {
    setPlan(explanation?.plan ?? '');
    setWhy(explanation?.why_this_move ?? '');
    setPawns(explanation?.pawn_structure ?? '');
    setError(null);
    setEditing(true);
  };

  const save = async () => {
    try {
      const saved = await writeExplanation({
        position_key: positionKey, move_uci: moveUci, plan, why_this_move: why, pawn_structure: pawns,
      });
      setExplanation(saved);
      setEditing(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Couldn\'t save the explanation.');
    }
  };

  if (editing) {
    return (
      <div className="explanation explanation-edit">
        <label htmlFor="exp-plan">Plan</label>
        <textarea id="exp-plan" rows={3} value={plan} onChange={e => setPlan(e.target.value)} />
        <label htmlFor="exp-why">Why {moveSan}</label>
        <textarea id="exp-why" rows={3} value={why} onChange={e => setWhy(e.target.value)} />
        <label htmlFor="exp-pawns">Pawn structure (optional)</label>
        <textarea id="exp-pawns" rows={2} value={pawns} onChange={e => setPawns(e.target.value)} />
        {error && <p className="attempt-wrong">{error}</p>}
        <div className="row-actions">
          <button type="button" className="btn btn-primary btn-sm" disabled={!plan.trim() || !why.trim()} onClick={save}>
            Save for every player
          </button>
          <button type="button" className="btn btn-secondary btn-sm" onClick={() => setEditing(false)}>Cancel</button>
        </div>
      </div>
    );
  }

  return (
    <div className="explanation">
      {explanation === undefined && <p className="intro-note">Loading the explanation…</p>}
      {explanation === null && (
        <p className="intro-note">
          No explanation yet. It's written in the background shortly after you accept the position.
        </p>
      )}
      {explanation && (
        <>
          <p><span className="explanation-label">Plan</span>{explanation.plan}</p>
          <p><span className="explanation-label">Why {moveSan}</span>{explanation.why_this_move}</p>
          {explanation.pawn_structure && (
            <p><span className="explanation-label">Pawns</span>{explanation.pawn_structure}</p>
          )}
          <div className="explanation-foot">
            <span>{explanation.source === 'owner' ? 'Written by the coach' : 'Explained by Claude'}</span>
          </div>
        </>
      )}
      {isOwner && explanation !== undefined && (
        <button type="button" className="link-button" onClick={startEdit}>
          {explanation ? 'Edit this explanation' : 'Write an explanation'}
        </button>
      )}
    </div>
  );
}

function RepertoireView({ isOwner }: { isOwner: boolean }) {
  const [color, setColor] = useState<Color>('white');
  const [info, setInfo] = useState<RepertoireInfo | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [options, setOptions] = useState<MoveOption[] | null>(null);
  const [extendFen, setExtendFen] = useState<string | null>(null);   // adding a reply: after your move
  const [extendKey, setExtendKey] = useState<string | null>(null);   // ...and after the opponent's reply
  const [extendOptions, setExtendOptions] = useState<MoveOption[] | null>(null);
  const [deviations, setDeviations] = useState<DeviationInfo[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (c: Color) => {
    setError(null);
    try {
      const [rep, devs] = await Promise.all([getRepertoire(c), getDeviations()]);
      setInfo(rep);
      setDeviations(devs);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Couldn\'t load your repertoire.');
    }
  }, []);

  useEffect(() => {
    setInfo(null);
    setSelected(null);
    load(color);
  }, [color, load]);

  const rows = useMemo(() => (info ? buildRows(info) : []), [info]);
  const row = rows.find(r => r.key === selected) ?? null;

  useEffect(() => {
    setOptions(null);
    setExtendFen(null);
    setExtendKey(null);
    setExtendOptions(null);
    setMessage(null);
  }, [selected]);

  const run = async (action: () => Promise<{ removed: number }>, done: string) => {
    setBusy(true);
    setError(null);
    try {
      const result = await action();
      await load(color);
      setMessage(result.removed ? `${done} ${result.removed} position${result.removed === 1 ? '' : 's'} below it no longer reached, so removed.` : done);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'That didn\'t work.');
    } finally {
      setBusy(false);
    }
  };

  const currentUci = row ? (row.accepted?.move_uci ?? row.suggestion?.move_uci ?? null) : null;
  const currentSan = row ? (row.accepted?.move_san ?? row.suggestion?.move_san ?? '') : '';
  const arrows: Array<[any, any, string]> = currentUci && !extendFen
    ? [[currentUci.slice(0, 2), currentUci.slice(2, 4), row?.accepted ? '#5cb87a' : '#d4a34a']]
    : [];

  /** "Add the next move": after your move, play the opponent's reply on the board. */
  const startExtend = () => {
    if (!row?.accepted) return;
    const game = new Chess(fenOf(row.key));
    try {
      game.move({ from: row.accepted.move_uci.slice(0, 2), to: row.accepted.move_uci.slice(2, 4), promotion: row.accepted.move_uci[4] });
    } catch {
      return;
    }
    setExtendFen(game.fen());
    setExtendKey(null);
    setExtendOptions(null);
  };

  const onExtendDrop = (from: string, to: string): boolean => {
    if (!extendFen) return false;
    const game = new Chess(extendFen);
    try {
      game.move({ from, to, promotion: 'q' });
    } catch {
      return false;
    }
    const epKey = keyOf(game.fen());
    setExtendFen(game.fen());
    setExtendKey(epKey);
    getMoveOptions(color, epKey).then(setExtendOptions).catch(() => setExtendOptions([]));
    return true;
  };

  const ownTurnColor = color === 'white' ? 'w' : 'b';

  return (
    <div className="repertoire">
      <div className="repertoire-head">
        <div className="learn-tabs" role="tablist">
          {(['white', 'black'] as Color[]).map(c => (
            <button
              key={c} type="button" role="tab" aria-selected={color === c}
              className={`tab ${color === c ? 'tab-active' : ''}`}
              onClick={() => setColor(c)}
            >
              With {c === 'white' ? 'White' : 'Black'}
            </button>
          ))}
        </div>
        {info && (
          <p className="intro-note">
            Built from {info.games_used} of your games with {color === 'white' ? 'White' : 'Black'}:
            positions you reached in 3 or more games, up to move 15.
            {' '}{info.accepted.length} in your repertoire.
          </p>
        )}
      </div>

      {error && <div className="error-message">{error}</div>}
      {!info && !error && <div className="status-message"><p>Loading your repertoire…</p></div>}

      {info && (
        <div className="repertoire-body">
          <div className="repertoire-tree" aria-label="Positions">
            {rows.length === 0 && (
              <p className="intro-note">
                No line repeats in 3 or more of your games yet. Play more, or sync older games from Home.
              </p>
            )}
            {rows.map(r => {
              const s = r.suggestion;
              const a = r.accepted;
              const san = a?.move_san ?? s?.move_san ?? '';
              const state = a ? (s && a.move_uci !== s.move_uci ? 'changed' : 'accepted') : 'suggested';
              return (
                <button
                  key={r.key} type="button"
                  className={`tree-row tree-${state} ${selected === r.key ? 'tree-selected' : ''}`}
                  style={{ paddingLeft: `${0.6 + r.depth * 0.9}rem` }}
                  onClick={() => setSelected(r.key)}
                >
                  <span className="tree-move">
                    {r.replySan && <span className="tree-reply">{moveLabel(r.ply - 1, r.replySan)} </span>}
                    {moveLabel(r.ply, san)}
                  </span>
                  <span className="tree-name">{(a?.opening ?? s?.opening)?.name ?? ''}</span>
                  <span className="tree-state">
                    {state === 'accepted' ? '✓' : state === 'changed' ? '✓ yours' : s ? `${s.games} games` : ''}
                  </span>
                </button>
              );
            })}
          </div>

          <div className="repertoire-detail">
            {!row && <p className="intro-note">Choose a position to see it on the board.</p>}
            {row && (
              <>
                <div className="review-board">
                  <Chessboard
                    position={extendFen ?? fenOf(row.key)}
                    boardOrientation={color}
                    arePiecesDraggable={Boolean(extendFen) && !extendKey}
                    isDraggablePiece={({ piece }) => piece[0] !== ownTurnColor}
                    onPieceDrop={onExtendDrop}
                    customBoardStyle={{ borderRadius: '6px' }}
                    customLightSquareStyle={{ backgroundColor: BOARD_LIGHT }}
                    customDarkSquareStyle={{ backgroundColor: BOARD_DARK }}
                    customArrows={arrows}
                  />
                </div>

                <h3 className="detail-title">
                  {moveLabel(row.ply, currentSan)}
                  {(row.accepted?.opening ?? row.suggestion?.opening) && (
                    <span className="detail-opening">{(row.accepted?.opening ?? row.suggestion?.opening)!.name}</span>
                  )}
                </h3>

                {row.suggestion && (
                  <ul className="detail-facts">
                    <li>
                      Your usual move here: <b>{row.suggestion.move_san}</b>, in {row.suggestion.games} games,
                      scoring {Math.round(row.suggestion.score * 100)}%.
                    </li>
                    <li>
                      {row.suggestion.theory_line
                        ? <>Theory: <b>{row.suggestion.theory_line}</b>.</>
                        : <>Off-book: not on a named opening line.</>}
                    </li>
                    {row.suggestion.better_san && (
                      <li className="attempt-wrong">
                        Stockfish prefers <b>{row.suggestion.better_san}</b>: your move gives away about
                        {' '}{row.suggestion.avg_drop}% winning chances on average.
                      </li>
                    )}
                  </ul>
                )}

                {message && <p className="detail-message">{message}</p>}

                <div className="row-actions">
                  {!row.accepted && row.suggestion && (
                    <>
                      <button type="button" className="btn btn-primary btn-sm" disabled={busy}
                        onClick={() => run(() => acceptPositions(color, [row.key]), 'Added this line.')}>
                        Add to repertoire
                      </button>
                      <button type="button" className="btn btn-secondary btn-sm" disabled={busy}
                        onClick={() => run(() => acceptPositions(color, [row.key], true), 'Added this line and everything below it.')}>
                        Add with everything below
                      </button>
                    </>
                  )}
                  {row.suggestion?.better_uci && row.accepted?.move_uci !== row.suggestion.better_uci && (
                    <button type="button" className="btn btn-secondary btn-sm" disabled={busy}
                      onClick={() => run(() => setRepertoireMove(color, row.key, row.suggestion!.better_uci!), `Now playing ${row.suggestion!.better_san}.`)}>
                      Play {row.suggestion.better_san} instead
                    </button>
                  )}
                  {row.suggestion && row.suggestion.theory_alternatives
                    .filter(t => t.uci !== currentUci)
                    .slice(0, 4)
                    .map(t => (
                      <button key={t.uci} type="button" className="btn btn-secondary btn-sm" disabled={busy}
                        title={t.line}
                        onClick={() => run(() => setRepertoireMove(color, row.key, t.uci), `Now playing ${t.san}.`)}>
                        Play {t.san} (theory)
                      </button>
                    ))}
                  {row.accepted && (
                    <>
                      <button type="button" className="btn btn-secondary btn-sm" disabled={busy}
                        onClick={() => getMoveOptions(color, row.key).then(setOptions).catch(() => setOptions([]))}>
                        Change move
                      </button>
                      <button type="button" className="btn btn-secondary btn-sm" disabled={busy} onClick={startExtend}>
                        Add the next move
                      </button>
                      <button type="button" className="btn btn-secondary btn-sm" disabled={busy}
                        onClick={() => run(() => removeRepertoirePosition(color, row.key), 'Removed.')}>
                        Remove
                      </button>
                    </>
                  )}
                </div>

                {options && (
                  <div className="move-options">
                    <label htmlFor="change-move">Your move in this position</label>
                    <select id="change-move" defaultValue=""
                      onChange={e => e.target.value && run(() => setRepertoireMove(color, row.key, e.target.value), 'Move changed.')}>
                      <option value="" disabled>Choose a move…</option>
                      {options.map(o => (
                        <option key={o.uci} value={o.uci}>
                          {o.san}{o.theory_line ? ` · theory (${o.theory_line})` : ''}{o.games ? ` · ${o.games} games` : ''}
                        </option>
                      ))}
                    </select>
                  </div>
                )}

                {extendFen && (
                  <div className="move-options">
                    {!extendKey && <p>Play your opponent's reply on the board.</p>}
                    {extendKey && extendOptions === null && <p className="intro-note">Loading your options…</p>}
                    {extendKey && extendOptions && (
                      <>
                        <label htmlFor="extend-move">Your move after that</label>
                        <select id="extend-move" defaultValue=""
                          onChange={e => e.target.value && run(() => setRepertoireMove(color, extendKey, e.target.value), 'Added to your repertoire.')}>
                          <option value="" disabled>Choose a move…</option>
                          {extendOptions.map(o => (
                            <option key={o.uci} value={o.uci}>
                              {o.san}{o.theory_line ? ` · theory (${o.theory_line})` : ''}{o.games ? ` · ${o.games} games` : ''}
                            </option>
                          ))}
                        </select>
                      </>
                    )}
                    <button type="button" className="link-button" onClick={() => { setExtendFen(null); setExtendKey(null); }}>
                      Cancel
                    </button>
                  </div>
                )}

                {currentUci && (
                  <ExplanationPanel
                    positionKey={row.key} moveUci={currentUci} moveSan={currentSan} isOwner={isOwner}
                  />
                )}
              </>
            )}
          </div>
        </div>
      )}

      <section className="deviations">
        <h3>Where your games left the repertoire</h3>
        {deviations.length === 0 ? (
          <p className="intro-note">Nothing yet. New games are checked against your repertoire as they're analyzed.</p>
        ) : (
          <ul>
            {deviations.map(d => (
              <li key={d.game_id}>
                <span>{new Date(d.played_at).toLocaleDateString()}</span>
                <span>
                  {d.side === 'user' ? 'You' : 'Your opponent'} left at ply {d.ply}
                  {d.played_was_theory ? ' (still theory)' : ' (off-book)'}
                  {d.punish_uci ? ': a mistake you can punish' : ''}
                </span>
                <a href={`https://lichess.org/${d.lichess_game_id}`} target="_blank" rel="noreferrer">Game</a>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

export default RepertoireView;
