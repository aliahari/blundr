import { useEffect, useState } from 'react';
import { getExplanation, writeExplanation } from '../services/api';
import { ExplanationInfo } from '../types';

/** The explanation for a repertoire move; the owner can write or edit it for every player. */
export default function ExplanationPanel({
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
          No explanation yet. It's written in the background shortly after you pick the move.
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
