/** Table cells shared by the builder and the position panel. */

const MATE_SCORE = 10000;

export const pct = (x: number | null) => (x === null ? '—' : `${Math.round(x * 100)}%`);

/** "94 games · 41%" over "scores 57%": how often a move is played here, and how it scores. */
export function GamesCell({ games, share, score }: { games: number; share: number | null; score: number | null }) {
  if (!games) return <>—</>;
  return (
    <>
      <span className="cell-main">
        {games}<span className="cell-unit"> {games === 1 ? 'game' : 'games'}</span>
        {share !== null && <> · {pct(share)}</>}
      </span>
      {score !== null && <span className="cell-sub">scores {pct(score)}</span>}
    </>
  );
}

/** "41%" over "scores 54%": a share of games with its score, no game count. */
export function ShareCell({ share, score }: { share: number | null; score: number | null }) {
  if (share === null || share === 0) return <>—</>;
  return (
    <>
      <span className="cell-main">{pct(share)}</span>
      {score !== null && <span className="cell-sub">scores {pct(score)}</span>}
    </>
  );
}

export function evalText(cp: number | null): string {
  if (cp === null) return '';
  if (Math.abs(cp) >= MATE_SCORE) return cp > 0 ? 'mate' : 'mated';
  const pawns = Math.round(cp / 10) / 10;
  if (pawns === 0) return '0.0';
  return `${pawns > 0 ? '+' : ''}${pawns.toFixed(1)}`;
}
