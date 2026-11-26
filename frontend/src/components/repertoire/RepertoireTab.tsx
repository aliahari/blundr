import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  getDeviations, getRepertoireGraph, getRepertoirePosition, removeRepertoirePosition,
  setRepertoireMove, startFromGames,
} from '../../services/api';
import type {
  Color, DeviationInfo, PositionPanel as Panel, RepertoireGraph as Graph,
} from '../../types';
import PositionPanel from './PositionPanel';
import RepertoireGraph from './RepertoireGraph';
import { lineTo } from './layoutGraph';

/**
 * The Repertoire tab (specs/features/02-openings/repertoire-graph.md): your
 * lines as a graph on the left, the selected position on the right. You build
 * the repertoire from the start outward by picking a move in each position;
 * picked positions become opening cards in Learn.
 */

function plural(n: number, one: string, many: string) {
  return `${n} ${n === 1 ? one : many}`;
}

export default function RepertoireTab({ isOwner }: { isOwner: boolean }) {
  const [color, setColor] = useState<Color>('white');
  const [graph, setGraph] = useState<Graph | null>(null);
  const [unfold, setUnfold] = useState<string[]>([]);
  const [line, setLine] = useState<string[]>([]);
  const [panel, setPanel] = useState<Panel | null>(null);
  const [deviations, setDeviations] = useState<DeviationInfo[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0); // bump to refetch the graph and the panel

  // A new colour starts over at the first position
  useEffect(() => {
    setGraph(null);
    setPanel(null);
    setUnfold([]);
    setLine([]);
    setMessage(null);
  }, [color]);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    Promise.all([getRepertoireGraph(color, unfold), getDeviations()])
      .then(([g, devs]) => {
        if (cancelled) return;
        setGraph(g);
        setDeviations(devs);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Couldn't load your repertoire.");
      });
    return () => { cancelled = true; };
  }, [color, unfold, reload]);

  useEffect(() => {
    let cancelled = false;
    getRepertoirePosition(color, line)
      .then((p) => { if (!cancelled) setPanel(p); })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Couldn't load that position.");
      });
    return () => { cancelled = true; };
  }, [color, line, reload]);

  const nodeKeys = useMemo(() => new Set(graph?.nodes.map((n) => n.key) ?? []), [graph]);
  const marks = useMemo(() => {
    const count = { add_answer: 0, extend: 0, you_left_theory: 0 };
    for (const n of graph?.nodes ?? []) if (n.mark) count[n.mark] += 1;
    return count;
  }, [graph]);
  const rootGames = graph?.nodes.find((n) => n.parent === null)?.games ?? 0;
  // The deviations list isn't per colour: keep those whose position is in this graph
  const colourDeviations = deviations.filter((d) => nodeKeys.has(d.position_key));

  const selectKey = useCallback((key: string) => {
    if (graph) setLine(lineTo(graph, key));
  }, [graph]);

  const change = async (
    action: () => Promise<{ removed: number }>, done: string, next?: string[], reportRemoved = true,
  ) => {
    setBusy(true);
    setError(null);
    try {
      const result = await action();
      setMessage(result.removed && reportRemoved
        ? `${done} ${plural(result.removed, 'position', 'positions')} below it no longer reached, so removed.`
        : done);
      if (next) setLine(next);
      setReload((r) => r + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "That didn't work.");
    } finally {
      setBusy(false);
    }
  };

  const onPick = (uci: string) => {
    if (!panel) return;
    const san = panel.candidates.find((c) => c.uci === uci)?.san ?? uci;
    // Step on to the opponent's replies, to keep building the line
    change(() => setRepertoireMove(color, panel.key, uci), `Added ${san} to your repertoire.`, [...line, uci]);
  };

  const onRemove = () => {
    if (!panel?.repertoire_move) return;
    change(() => removeRepertoirePosition(color, panel.key), `Removed ${panel.repertoire_move.san}.`);
  };

  // Where a usual move was off-book, the theory move replaces it and the line
  // below the old move goes: not worth a "removed" note here.
  const onStart = () => change(() => startFromGames(color), 'Started your repertoire from your games.', undefined, false);

  const side = color === 'white' ? 'White' : 'Black';
  const todo = [
    marks.add_answer && plural(marks.add_answer, 'reply to answer', 'replies to answer'),
    marks.extend && plural(marks.extend, 'place to extend', 'places to extend'),
    marks.you_left_theory && plural(marks.you_left_theory, 'place you left theory', 'places you left theory'),
  ].filter(Boolean).join(', ');

  return (
    <div className="repertoire-tab">
      <div className="repertoire-head">
        <div className="learn-tabs" role="tablist">
          {(['white', 'black'] as Color[]).map((c) => (
            <button key={c} type="button" role="tab" aria-selected={color === c}
              className={`tab ${color === c ? 'tab-active' : ''}`} onClick={() => setColor(c)}>
              With {c === 'white' ? 'White' : 'Black'}
            </button>
          ))}
        </div>
        {graph && (
          <div className="repertoire-summary">
            {graph.avg_moves_in_repertoire !== null && (
              <p>Your games stay in your repertoire for {graph.avg_moves_in_repertoire} moves on average.</p>
            )}
            {todo && <p>{todo}.</p>}
            {graph.theory_source === 'dataset' && (
              <p className="intro-note">Theory from named opening lines only while master games load.</p>
            )}
          </div>
        )}
      </div>

      {error && <div className="error-message">{error}</div>}
      {message && <p className="detail-message" role="status">{message}</p>}

      {graph?.empty_repertoire && (
        <div className="repertoire-start">
          <p>
            Pick your move in each position, starting from the first, or begin with the moves you
            usually play with {side}.
          </p>
          <button type="button" className="btn btn-primary" disabled={busy || rootGames === 0} onClick={onStart}>
            Start from my games
          </button>
          {rootGames === 0 && (
            <p className="intro-note">No analysed games with {side} yet. Sync your games from Home first.</p>
          )}
        </div>
      )}

      {!graph && !error && <div className="status-message"><p>Loading your repertoire…</p></div>}

      {graph && (
        <div className="repertoire-layout">
          <div className="repertoire-graph-col">
            <RepertoireGraph graph={graph} selectedKey={panel?.key ?? null}
              onSelect={selectKey} onUnfold={(key) => setUnfold((u) => (u.includes(key) ? u : [...u, key]))} />
            <p className="graph-legend">
              <span className="legend-repertoire">your repertoire</span>
              <span className="legend-theory">theory</span>
              <span className="legend-opponent">opponent left theory</span>
              <span className="legend-you">you left theory</span>
            </p>
          </div>
          <div className="repertoire-panel-col">
            {panel ? (
              <PositionPanel color={color} panel={panel} line={line} isOwner={isOwner} busy={busy}
                onPick={onPick} onRemove={onRemove} onSelectLine={setLine} />
            ) : (
              <div className="status-message"><p>Loading the position…</p></div>
            )}
          </div>
          <div className="repertoire-devs-col">
            <section className="deviations">
              <h3>Where your games left the repertoire</h3>
              {colourDeviations.length === 0 ? (
                <p className="intro-note">Nothing yet. New games are checked against your repertoire as they're analysed.</p>
              ) : (
                <ul>
                  {colourDeviations.slice(0, 15).map((d) => (
                    <li key={d.game_id}>
                      <span>{new Date(d.played_at).toLocaleDateString()}</span>
                      <button type="button" className="link-button" onClick={() => selectKey(d.position_key)}>
                        {d.side === 'user' ? 'You' : 'Your opponent'} left at move {Math.floor((d.ply - 1) / 2) + 1}
                        {d.played_was_theory ? ' (still theory)' : ' (off-book)'}
                        {d.punish_uci ? ': a mistake you can punish' : ''}
                      </button>
                      <a href={`https://lichess.org/${d.lichess_game_id}`} target="_blank" rel="noreferrer">Game</a>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        </div>
      )}
    </div>
  );
}
