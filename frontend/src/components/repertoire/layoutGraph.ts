import { stratify, tree } from 'd3-hierarchy';
import type { GraphEdge, GraphNode, RepertoireGraph } from '../../types';

export const COLUMN_WIDTH = 118; // px per ply
export const ROW_HEIGHT = 30; // px per row
export const PILL_WIDTH = 92;
export const PILL_HEIGHT = 22;
export const LABEL_SPACE = 190; // room right of the last column for mark labels
const MARGIN = 16;

export interface GraphLayout {
  nodes: Map<string, { x: number; y: number }>;
  width: number;
  height: number;
}

/**
 * Left-to-right tidy tree over the spanning parents: x = ply column, y = row.
 * Coordinates are the left edge / vertical middle of each node's pill.
 */
export function layoutGraph(graph: RepertoireGraph): GraphLayout {
  const nodes = new Map<string, { x: number; y: number }>();
  if (graph.nodes.length === 0) return { nodes, width: 0, height: 0 };

  const root = stratify<GraphNode>()
    .id((n) => n.key)
    .parentId((n) => n.parent)(graph.nodes);
  // Children in the order the server's breadth-first walk used: most games first
  root.sort((a, b) => b.data.games - a.data.games);
  tree<GraphNode>().nodeSize([ROW_HEIGHT, COLUMN_WIDTH])(root);

  let minRow = Infinity;
  let maxRow = -Infinity;
  let maxDepth = 0;
  root.each((n) => {
    minRow = Math.min(minRow, n.x ?? 0);
    maxRow = Math.max(maxRow, n.x ?? 0);
    maxDepth = Math.max(maxDepth, n.depth);
  });
  root.each((n) => {
    nodes.set(n.data.key, {
      x: MARGIN + n.depth * COLUMN_WIDTH,
      y: MARGIN + PILL_HEIGHT / 2 + ((n.x ?? 0) - minRow),
    });
  });
  return {
    nodes,
    width: MARGIN * 2 + maxDepth * COLUMN_WIDTH + PILL_WIDTH + LABEL_SPACE,
    height: MARGIN * 2 + PILL_HEIGHT + (maxRow - minRow),
  };
}

/** The uci line from the start to `key`, along the spanning parents. */
export function lineTo(graph: RepertoireGraph, key: string): string[] {
  const parentOf = new Map(graph.nodes.map((n) => [n.key, n.parent]));
  const treeEdge = new Map<string, GraphEdge>();
  for (const e of graph.edges) if (!e.transposition) treeEdge.set(e.child, e);
  const line: string[] = [];
  let at: string | null | undefined = key;
  while (at && parentOf.get(at)) {
    const e = treeEdge.get(at);
    if (!e) break;
    line.push(e.uci);
    at = parentOf.get(at);
  }
  return line.reverse();
}

/** "1.e4" / "1…e5" for the move that reached a node at `ply` (1-based). */
export function numberedSan(ply: number, san: string): string {
  const moveNo = Math.floor((ply - 1) / 2) + 1;
  return ply % 2 === 1 ? `${moveNo}.${san}` : `${moveNo}…${san}`;
}
