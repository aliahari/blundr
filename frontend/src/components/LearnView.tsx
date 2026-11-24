import { useState } from 'react';
import ReviewPanel from './ReviewPanel';
import LineReview from './LineReview';

/**
 * Learn: one review queue on the server, shown in two places. Blunder
 * cards keep their walkthrough lesson (ReviewPanel); opening and punish
 * cards replay a line and ask for a move (LineReview).
 */
function LearnView() {
  const [tab, setTab] = useState<'blunders' | 'openings'>('blunders');
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
      {tab === 'blunders' ? <ReviewPanel /> : <LineReview />}
    </div>
  );
}

export default LearnView;
