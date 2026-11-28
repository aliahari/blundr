import { useEffect, useState } from 'react';
import { getRepertoireSummary } from '../../services/api';
import type { Color, RepertoireSummary } from '../../types';

/**
 * A colour's page: how much of what you'll meet you've prepared (built in
 * the navigator) and how much of it you've learned (opening cards in Learn).
 */

interface Props {
  color: Color;
  onBuild: () => void;
  onOverview: () => void;
  onLearn: () => void;
}

function Progress({ value, tone }: { value: number | null; tone: 'built' | 'learned' }) {
  return (
    <div className={`progress-track progress-${tone}`} role="progressbar"
      aria-valuenow={value === null ? undefined : Math.round(value * 100)} aria-valuemin={0} aria-valuemax={100}>
      <span style={{ width: `${Math.round((value ?? 0) * 100)}%` }} />
    </div>
  );
}

export default function ColourHome({ color, onBuild, onOverview, onLearn }: Props) {
  const [summary, setSummary] = useState<RepertoireSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setSummary(null);
    getRepertoireSummary(color)
      .then((s) => { if (!cancelled) setSummary(s); })
      .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : "Couldn't load your repertoire."); });
    return () => { cancelled = true; };
  }, [color]);

  if (error) return <div className="error-message">{error}</div>;
  if (!summary) return <div className="status-message"><p>Loading your repertoire…</p></div>;

  const complete = summary.completion;
  return (
    <div className="colour-home">
      <section className="colour-home-block">
        <h3>
          {complete === null ? 'Nothing to prepare yet' : `${Math.round(complete * 100)}% complete`}
          <span className="colour-home-note"> at 1 in {summary.goal} games</span>
        </h3>
        <Progress value={complete} tone="built" />
        <button type="button" className="colour-home-action" onClick={onBuild}>
          <span>{summary.saved === 0 ? 'Start building' : 'Continue building'}</span>
          <span className="colour-home-count">{summary.saved} saved</span>
        </button>
        <button type="button" className="link-button" onClick={onOverview}>See your repertoire</button>
      </section>

      <section className="colour-home-block">
        <h3>
          {summary.mastered_pct === null ? 'Nothing to learn yet' : `${Math.round(summary.mastered_pct * 100)}% mastered`}
          {summary.cards > 0 && <span className="colour-home-note"> · {summary.mastered} of {summary.cards} moves</span>}
        </h3>
        <Progress value={summary.mastered_pct} tone="learned" />
        <button type="button" className="colour-home-action" onClick={onLearn} disabled={summary.cards === 0}>
          <span>Continue learning</span>
          <span className="colour-home-count">
            {summary.due} due{summary.new > 0 ? ` · ${summary.new} new` : ''}
          </span>
        </button>
        <p className="intro-note">
          A move counts as mastered once you've answered it right on two separate days (a review gap of
          six days or more).
        </p>
      </section>
    </div>
  );
}
