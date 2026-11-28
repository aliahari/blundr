import { useState } from 'react';
import ReviewPanel from './ReviewPanel';
import OpeningPractice from './OpeningPractice';

/**
 * Learn: one review queue on the server, shown in two places. Blunder
 * cards keep their walkthrough lesson (ReviewPanel); opening and punish
 * cards replay a line and ask for a move (LineReview).
 */
function LearnView({ isOwner = false, initialTab = 'blunders' }: {
  isOwner?: boolean; initialTab?: 'blunders' | 'openings';
}) {
  const [tab, setTab] = useState<'blunders' | 'openings'>(initialTab);
  return (
    <div>
      <div className="learn-tabs" role="tablist" aria-label="What to review">
        <button type="button" role="tab" aria-selected={tab === 'blunders'}
          className={`tab ${tab === 'blunders' ? 'tab-active' : ''}`} onClick={() => setTab('blunders')}>
          Blunders
        </button>
        <button type="button" role="tab" aria-selected={tab === 'openings'}
          className={`tab ${tab === 'openings' ? 'tab-active' : ''}`} onClick={() => setTab('openings')}>
          Openings
        </button>
      </div>
      {tab === 'blunders' ? <ReviewPanel /> : <OpeningPractice isOwner={isOwner} />}
    </div>
  );
}

export default LearnView;
