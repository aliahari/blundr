import { useEffect, useMemo, useRef, useState } from 'react';
import type { GraphEdge, GraphNode, RepertoireGraph as Graph } from '../../types';
import { COLUMN_WIDTH, layoutGraph, numberedSan, PILL_HEIGHT, PILL_WIDTH } from './layoutGraph';

interface Props {
  graph: Graph;
  selectedKey: string | null;
  /** Positions the builder would ask about up to your depth: drawn dashed, labelled. */
  gaps?: Set<string>;
  /** Positions too rarely reached at your level to go on from. */
  thin?: Set<string>;
  onSelect: (key: string) => void;
  onUnfold: (key: string) => void;
}

type Kind = 'repertoire' | 'theory' | 'opponent_left' | 'you_left';

const STROKE: Record<Kind, string> = {
  repertoire: 'var(--success)',
  theory: 'var(--primary)',
  opponent_left: 'var(--error)',
  you_left: 'var(--warning)',
};

const MARK_LABEL: Record<string, { text: string; color: string }> = {
  you_left_theory: { text: 'you left theory', color: 'var(--warning)' },
  gap: { text: '+ prepare this', color: 'var(--primary)' },
};
const EMPTY = new Set<string>();

const ZOOMS = [0.5, 0.67, 0.8, 1, 1.25, 1.5, 2];

function kindOf(edge: GraphEdge, child: GraphNode): Kind {
  if (edge.repertoire) return 'repertoire';
  if (edge.theory) return 'theory';
  // The move into a position where it's the user's turn was the opponent's
  return child.user_to_move ? 'opponent_left' : 'you_left';
}

export default function RepertoireGraph({
  graph, selectedKey, gaps = EMPTY, thin = EMPTY, onSelect, onUnfold,
}: Props) {
  const layout = useMemo(() => layoutGraph(graph), [graph]);
  const nodesByKey = useMemo(() => new Map(graph.nodes.map((n) => [n.key, n])), [graph]);
  const treeEdges = useMemo(() => {
    const m = new Map<string, GraphEdge>();
    for (const e of graph.edges) if (!e.transposition) m.set(e.child, e);
    return m;
  }, [graph]);
  const root = graph.nodes.find((n) => n.parent === null);
  const rootGames = Math.max(1, root?.games ?? 1);
  // Positions with something drawn to their right have no room for text labels
  const roomRight = useMemo(() => {
    const taken = new Set([...layout.nodes.values()].map((p) => `${p.x},${p.y}`));
    const room = new Set<string>();
    for (const [key, p] of layout.nodes) {
      if (!taken.has(`${p.x + COLUMN_WIDTH},${p.y}`)) room.add(key);
    }
    return room;
  }, [layout]);

  const [zoomIndex, setZoomIndex] = useState(ZOOMS.indexOf(1));
  const zoom = ZOOMS[zoomIndex];
  const scroller = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; y: number; left: number; top: number; moved: boolean } | null>(null);

  // Open on the start position
  const rootKey = root?.key;
  useEffect(() => {
    const el = scroller.current;
    const pos = rootKey ? layout.nodes.get(rootKey) : undefined;
    if (el && pos) el.scrollTo({ left: 0, top: Math.max(0, pos.y - el.clientHeight / 2) });
    // Only when a different graph is laid out, not on every selection
  }, [layout, rootKey]);

  // Keep the selected position in view
  useEffect(() => {
    const el = scroller.current;
    const pos = selectedKey ? layout.nodes.get(selectedKey) : undefined;
    if (!el || !pos) return;
    const x = pos.x * zoom;
    const y = pos.y * zoom;
    const inView =
      x >= el.scrollLeft && x + PILL_WIDTH * zoom <= el.scrollLeft + el.clientWidth &&
      y >= el.scrollTop && y <= el.scrollTop + el.clientHeight;
    if (!inView) {
      el.scrollTo({
        left: Math.max(0, x - el.clientWidth / 3),
        top: Math.max(0, y - el.clientHeight / 2),
        behavior: 'smooth',
      });
    }
  }, [selectedKey, layout, zoom]);

  const onPointerDown = (e: React.PointerEvent) => {
    const el = scroller.current;
    if (!el || e.button !== 0) return;
    drag.current = { x: e.clientX, y: e.clientY, left: el.scrollLeft, top: el.scrollTop, moved: false };
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const el = scroller.current;
    const d = drag.current;
    if (!el || !d) return;
    const dx = e.clientX - d.x;
    const dy = e.clientY - d.y;
    if (!d.moved && Math.hypot(dx, dy) > 4) {
      d.moved = true;
      el.setPointerCapture(e.pointerId);
    }
    if (d.moved) {
      el.scrollLeft = d.left - dx;
      el.scrollTop = d.top - dy;
    }
  };
  const endDrag = (e: React.PointerEvent) => {
    const el = scroller.current;
    if (el?.hasPointerCapture(e.pointerId)) el.releasePointerCapture(e.pointerId);
    // Let the click that ends a drag see it was a drag, then forget it
    setTimeout(() => { drag.current = null; }, 0);
  };
  const clicked = (fn: () => void) => () => {
    if (drag.current?.moved) return;
    fn();
  };
  const onKey = (fn: () => void) => (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      fn();
    }
  };

  const edgePath = (from: { x: number; y: number }, to: { x: number; y: number }) => {
    const x1 = from.x + PILL_WIDTH;
    const mx = (x1 + to.x) / 2;
    return `M${x1},${from.y} C${mx},${from.y} ${mx},${to.y} ${to.x},${to.y}`;
  };

  return (
    <div className="repgraph">
      <div className="repgraph-zoom" role="group" aria-label="Zoom">
        <button type="button" className="btn btn-secondary repgraph-zoom-btn" aria-label="Zoom out"
          disabled={zoomIndex === 0} onClick={() => setZoomIndex((i) => Math.max(0, i - 1))}>−</button>
        <span className="repgraph-zoom-level">{Math.round(zoom * 100)}%</span>
        <button type="button" className="btn btn-secondary repgraph-zoom-btn" aria-label="Zoom in"
          disabled={zoomIndex === ZOOMS.length - 1}
          onClick={() => setZoomIndex((i) => Math.min(ZOOMS.length - 1, i + 1))}>+</button>
      </div>
      <div
        ref={scroller}
        className="repgraph-scroll"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
      >
        <svg
          width={layout.width * zoom}
          height={layout.height * zoom}
          viewBox={`0 0 ${layout.width} ${layout.height}`}
          role="tree"
          aria-label="Your opening lines"
        >
          {/* Links first, so pills sit on top */}
          {graph.edges.map((e) => {
            const from = layout.nodes.get(e.parent);
            const to = layout.nodes.get(e.child);
            const child = nodesByKey.get(e.child);
            if (!from || !to || !child) return null;
            if (e.transposition) {
              return (
                <path key={`${e.parent}|${e.uci}`} d={edgePath(from, to)} className="repgraph-transposition"
                  onClick={clicked(() => onSelect(e.child))}>
                  <title>{`${e.san} transposes here (${e.games} games)`}</title>
                </path>
              );
            }
            const width = Math.max(1.2, (e.games / rootGames) * 16);
            return (
              <path key={`${e.parent}|${e.uci}`} d={edgePath(from, to)} fill="none"
                stroke={STROKE[kindOf(e, child)]} strokeOpacity={0.35} strokeWidth={width} />
            );
          })}

          {graph.nodes.map((n) => {
            const pos = layout.nodes.get(n.key);
            if (!pos) return null;
            const edge = treeEdges.get(n.key);
            const kind: Kind = edge ? kindOf(edge, n) : 'repertoire';
            const selected = n.key === selectedKey;
            const label = edge ? numberedSan(n.ply, edge.san) : 'Start';
            const w = edge ? edge.w : 0;
            const d = edge ? edge.d : 0;
            const l = edge ? edge.l : 0;
            const total = w + d + l;
            const isGap = gaps.has(n.key);
            const mark = isGap ? MARK_LABEL.gap : n.mark ? MARK_LABEL[n.mark] : null;
            const leaf = roomRight.has(n.key);
            const markX = pos.x + PILL_WIDTH + 6;
            const foldX = markX + (mark ? mark.text.length * 5.6 + 8 : 0);
            const name = n.opening ? ` · ${n.opening.name}` : '';
            return (
              <g key={n.key} className={`repgraph-node${selected ? ' selected' : ''}`}>
                <g role="treeitem" aria-selected={selected} tabIndex={0}
                  aria-label={`${label}, ${n.games} games${name}${mark ? `, ${mark.text}` : ''}`}
                  onClick={clicked(() => onSelect(n.key))} onKeyDown={onKey(() => onSelect(n.key))}>
                  <title>{`${label}${name} — ${n.games} games${thin.has(n.key) ? ' · rarely reached at your level' : ''}`}</title>
                  <rect x={pos.x} y={pos.y - PILL_HEIGHT / 2} width={PILL_WIDTH} height={PILL_HEIGHT}
                    rx={PILL_HEIGHT / 2}
                    className={edge?.repertoire || !edge ? 'repgraph-pill repertoire' : 'repgraph-pill'}
                    stroke={selected ? 'var(--text)' : STROKE[kind]}
                    strokeWidth={selected ? 2.4 : 1.4}
                    strokeDasharray={isGap ? '4 3' : undefined} />
                  <text x={pos.x + 9} y={pos.y + 4} className="repgraph-san">{label}</text>
                  <text x={pos.x + PILL_WIDTH - 8} y={pos.y + 4} className="repgraph-count"
                    textAnchor="end">{n.games}</text>
                  {total > 0 && (
                    <g>
                      <rect x={pos.x + 10} y={pos.y + 7} width={(72 * w) / total} height={2.5}
                        className="repgraph-win" />
                      <rect x={pos.x + 10 + (72 * w) / total} y={pos.y + 7} width={(72 * d) / total}
                        height={2.5} className="repgraph-draw" />
                      <rect x={pos.x + 10 + (72 * (w + d)) / total} y={pos.y + 7}
                        width={(72 * l) / total} height={2.5} className="repgraph-loss" />
                    </g>
                  )}
                </g>
                {mark && leaf && (
                  <text x={markX} y={pos.y + 4} className="repgraph-mark" fill={mark.color}
                    onClick={clicked(() => onSelect(n.key))}>{mark.text}</text>
                )}
                {mark && !leaf && (
                  <circle cx={pos.x + PILL_WIDTH - 4} cy={pos.y - PILL_HEIGHT / 2 + 1} r={4.5}
                    fill={mark.color} className="repgraph-mark-dot"
                    onClick={clicked(() => onSelect(n.key))}>
                    <title>{mark.text}</title>
                  </circle>
                )}
                {n.folded > 0 && (
                  <text x={leaf ? foldX : pos.x + PILL_WIDTH + 3} y={leaf ? pos.y + 4 : pos.y - 5}
                    className="repgraph-more" role="button" tabIndex={0}
                    aria-label={`Show ${n.folded} more moves`}
                    onClick={clicked(() => onUnfold(n.key))} onKeyDown={onKey(() => onUnfold(n.key))}>
                    {leaf ? `+${n.folded} more` : `+${n.folded}`}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}
