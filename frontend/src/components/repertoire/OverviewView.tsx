import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  getDeviations, getRepertoireGraph, getRepertoirePosition, removeRepertoirePosition,
  setRepertoireMove,
} from '../../services/api';
import type {
  Color, DeviationInfo, PositionPanel as Panel, RepertoireGraph as Graph,
} from '../../types';
import PositionPanel from './PositionPanel';
import RepertoireGraph from './RepertoireGraph';
import { lineTo } from './layoutGraph';

/**
 * "See your repertoire": your lines as a graph, the selected position beside
 * it, and where your games left the repertoire. Positions the builder would
 * ask about are marked; clicking one opens it in the builder.
 */

function plural(n: number, one: string, many: string) {
  return `${n} ${n === 1 ? one : many}`;
}

interface Props {
  color: Color;
  onBack: () => void;
  onOpenGap: (line: string[]) => void;
}

export default function OverviewView({ color, onBack, onOpenGap }: Props) {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [unfold, setUnfold] = useState<string[]>([]);
  const [line, setLine] = useState<string[]>([]);
  const [panel, setPanel] = useState<Panel | null>(null);
  const [deviations, setDeviations] = useState<DeviationInfo[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

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
  const gaps = useMemo(() => new Set(graph?.gaps ?? []), [graph]);
  const thin = useMemo(() => new Set(graph?.thin ?? []), [graph]);
  const colourDeviations = deviations.filter((d) => nodeKeys.has(d.position_key));

  const selectKey = useCallback((key: string) => {
    if (!graph) return;
    const to = lineTo(graph, key);
    if (gaps.has(key)) onOpenGap(to);
    else setLine(to);
  }, [graph, gaps, onOpenGap]);

  const change = async (action: () => Promise<{ removed: number }>, done: string, next?: string[]) => {
    setBusy(true);
    setError(null);
    try {
      const result = await action();
      setMessage(result.removed
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
    change(() => setRepertoireMove(color, panel.key, uci), `Added ${san} to your repertoire.`, [...line, uci]);
  };

  const onRemove = () => {
    if (!panel?.repertoire_move) return;
    change(() => removeRepertoirePosition(color, panel.key), `Removed ${panel.repertoire_move.san}.`);
  };

  return (
    <div className="overview">
      <div className="repertoire-summary">
        <button type="button" className="btn btn-secondary btn-sm" onClick={onBack}>Back to building</button>
        {graph?.avg_moves_in_repertoire != null && (
          <p>Your games stay in your repertoire for {graph.avg_moves_in_repertoire} moves on average.</p>
        )}
        {graph && graph.gaps.length > 0 && (
          <p>
            {plural(graph.gaps.length, 'position', 'positions')} to prepare at your depth
            {graph.gaps.some((k) => nodeKeys.has(k)) ? '; the ones your games reached are dashed.' : '.'}
          </p>
        )}
      </div>

      {error && <div className="error-message">{error}</div>}
      {message && <p className="detail-message" role="status">{message}</p>}
      {!graph && !error && <div className="status-message"><p>Loading your repertoire…</p></div>}

      {graph && (
        <div className="repertoire-layout">
          <div className="repertoire-graph-col">
            <RepertoireGraph graph={graph} selectedKey={panel?.key ?? null} gaps={gaps} thin={thin}
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
              <PositionPanel color={color} panel={panel} line={line} busy={busy}
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
