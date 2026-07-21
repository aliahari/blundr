import { useCallback, useEffect, useRef, useState } from 'react';
import { getStatsOverview, getTimeline, startSync, getAnalysisStatus, ApiError } from '../services/api';
import { AnalysisStatus, StatsOverview, TimelinePoint } from '../types';
import ComboChart from './ComboChart';
import { IconRefresh } from './icons';

interface DashboardProps {
  hasLichessAccount: boolean;
  onOpenSettings: () => void;
}

const POLL_MS = 2500;

/** "2h ago" / "5m ago" style formatting for the last-sync timestamp. */
const relativeTime = (iso: string | null | undefined): string => {
  if (!iso) return 'recently';
  // The backend stores naive UTC timestamps — parse them as UTC
  const then = new Date(iso.endsWith('Z') ? iso : iso + 'Z').getTime();
  const mins = Math.max(0, Math.round((Date.now() - then) / 60000));
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  return `${hours}h ago`;
};

/**
 * Landing view: headline stats, activity charts, and the background sync
 * that keeps them fresh. Sync starts automatically on mount (using the
 * user's saved preferences) and stats refresh as it progresses.
 */
function Dashboard({ hasLichessAccount, onOpenSettings }: DashboardProps) {
  const [overview, setOverview] = useState<StatsOverview | null>(null);
  const [timeline, setTimeline] = useState<TimelinePoint[]>([]);
  const [sync, setSync] = useState<AnalysisStatus | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refreshStats = useCallback(async () => {
    try {
      const [o, t] = await Promise.all([getStatsOverview(), getTimeline(30)]);
      setOverview(o);
      setTimeline(t);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load stats');
    }
  }, []);

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const beginPolling = useCallback(() => {
    stopPolling();
    pollRef.current = setInterval(async () => {
      try {
        const s = await getAnalysisStatus();
        setSync(s);
        if (s.state !== 'running') {
          stopPolling();
          refreshStats();
        } else if (s.games_done > 0) {
          refreshStats(); // stats grow while the sync runs
        }
      } catch {
        stopPolling();
      }
    }, POLL_MS);
  }, [refreshStats]);

  const runSync = useCallback(async (force: boolean) => {
    setError(null);
    // Disable the button for the whole POST round trip — the sync endpoint
    // fetches games from Lichess before responding, which takes seconds,
    // and only then does state flip to "running"
    setStarting(true);
    try {
      const s = await startSync(force);
      setSync(s);
      if (s.state === 'running') beginPolling();
      else if (s.state !== 'skipped') refreshStats();
    } catch (err) {
      if (err instanceof ApiError && err.status === 400) {
        setError(err.message); // no lichess account configured
      } else {
        setError(err instanceof Error ? err.message : 'Sync failed');
      }
    } finally {
      setStarting(false);
    }
  }, [beginPolling, refreshStats]);

  // Load stats immediately. The on-load sync is NOT forced: the server
  // skips it unless the last successful sync is older than 24h — only the
  // Sync now button forces one.
  useEffect(() => {
    refreshStats();
    if (hasLichessAccount) runSync(false);
    return stopPolling;
  }, [refreshStats, runSync, hasLichessAccount]);

  const syncing = sync?.state === 'running';
  const pct = sync && sync.games_total > 0
    ? Math.round((sync.games_done / sync.games_total) * 100)
    : 0;

  return (
    <div className="dashboard">
      {/* Sync status line */}
      <div className="sync-bar">
        {syncing ? (
          <>
            <div className="progress-bar sync-progress">
              <div className="progress-fill" style={{ width: `${pct}%` }} />
            </div>
            <span className="progress-label">
              Syncing… {sync!.games_done}/{sync!.games_total} games · {sync!.blunders_found} new blunders
            </span>
          </>
        ) : (
          <>
            <span className="sync-status-text">
              {sync?.state === 'done' && sync.games_total > 0
                ? `✓ Synced ${sync.games_total} new game${sync.games_total === 1 ? '' : 's'}, found ${sync.blunders_found} blunder${sync.blunders_found === 1 ? '' : 's'}`
                : sync?.state === 'done'
                  ? '✓ Up to date'
                  : sync?.state === 'skipped'
                    ? `Last synced ${relativeTime(sync.last_synced_at)}`
                    : sync?.state === 'error'
                      ? `Sync failed: ${sync.error}`
                      : ''}
            </span>
            {hasLichessAccount && (
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => runSync(true)}
                disabled={starting}
              >
                <IconRefresh size={14} /> {starting ? 'Syncing…' : 'Sync now'}
              </button>
            )}
          </>
        )}
      </div>

      {!hasLichessAccount && (
        <div className="status-message">
          <div className="icon">♟️</div>
          <p>Connect your Lichess account to start syncing games.</p>
          <button type="button" className="btn btn-primary" onClick={onOpenSettings}>
            Open Settings
          </button>
        </div>
      )}

      {error && <div className="error-message">{error}</div>}

      {/* KPI row */}
      {overview && (
        <div className="stat-tiles">
          <div className="stat-tile">
            <span className="stat-value">{overview.games_analyzed}</span>
            <span className="stat-label">Games analyzed</span>
          </div>
          <div className="stat-tile">
            <span className="stat-value">{overview.total_blunders}</span>
            <span className="stat-label">Blunders found</span>
          </div>
          <div className="stat-tile">
            <span className="stat-value">{overview.blunders_mastered}</span>
            <span className="stat-label">Mastered</span>
          </div>
          <div className="stat-tile">
            <span className="stat-value">{overview.due_now}</span>
            <span className="stat-label">Due for review</span>
          </div>
          <div className="stat-tile">
            <span className="stat-value">{overview.reviews_done}</span>
            <span className="stat-label">Reviews done</span>
          </div>
        </div>
      )}

      {/* Activity charts: counts as bars (left axis), derived rates as a
          line (right axis) — the line is the improvement signal */}
      <div className="chart-grid">
        <ComboChart
          title="Games & blunder rate"
          points={timeline.map(p => ({
            date: p.date,
            bar: p.games,
            // Rate is undefined on days without games — gap, not zero
            line: p.games > 0 ? Math.round((p.blunders / p.games) * 100) / 100 : null,
          }))}
          barLabel="Games analyzed"
          lineLabel="Blunders per game"
          barColor="#d4a34a"
          lineColor="#d96b52"
        />
        <ComboChart
          title="Reviews & mastery"
          points={timeline.map(p => ({
            date: p.date,
            bar: p.reviews,
            line: p.mastered_pct,
          }))}
          barLabel="Blunders reviewed"
          lineLabel="Mastered"
          barColor="#4aa893"
          lineColor="#6f9bd6"
          lineMax={100}
          lineSuffix="%"
        />
      </div>
    </div>
  );
}

export default Dashboard;
