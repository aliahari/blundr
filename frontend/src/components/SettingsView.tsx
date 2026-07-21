import { useEffect, useState } from 'react';
import { getSettings, updateSettings } from '../services/api';
import { SettingsInfo } from '../types';
import { IconSave, IconBullet, IconBlitz, IconRapid, IconClassical, IconCorrespondence, IconSpinner } from './icons';

interface SettingsViewProps {
  /** Called after a successful save so the app shell can refresh profile info */
  onSaved: () => void;
}

const GAME_TYPE_OPTIONS = [
  { id: 'bullet', label: 'Bullet', Icon: IconBullet },
  { id: 'blitz', label: 'Blitz', Icon: IconBlitz },
  { id: 'rapid', label: 'Rapid', Icon: IconRapid },
  { id: 'classical', label: 'Classical', Icon: IconClassical },
  { id: 'correspondence', label: 'Correspondence', Icon: IconCorrespondence },
];

const LOOKBACK_OPTIONS = [
  { days: 7, label: '1 week' },
  { days: 14, label: '2 weeks' },
  { days: 30, label: '1 month' },
  { days: 90, label: '3 months' },
];

/**
 * Profile (name, picture, Lichess account) + sync preferences
 * (which game types to fetch, how far back).
 */
function SettingsView({ onSaved }: SettingsViewProps) {
  const [settings, setSettings] = useState<SettingsInfo | null>(null);
  const [email, setEmail] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [avatarUrl, setAvatarUrl] = useState('');
  const [lichessUsername, setLichessUsername] = useState('');
  const [gameTypes, setGameTypes] = useState<string[]>(['rapid', 'blitz']);
  const [daysBack, setDaysBack] = useState(7);
  const [maxNewPerDay, setMaxNewPerDay] = useState(10);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getSettings()
      .then(s => {
        setSettings(s);
        setEmail(s.email ?? '');
        setDisplayName(s.display_name ?? '');
        setAvatarUrl(s.avatar_url ?? '');
        setLichessUsername(s.lichess_username ?? '');
        setGameTypes(s.sync_game_types);
        setDaysBack(s.sync_days_back);
        setMaxNewPerDay(s.max_new_per_day);
      })
      .catch(err => setError(err instanceof Error ? err.message : 'Failed to load settings'));
  }, []);

  const toggleGameType = (id: string) => {
    setGameTypes(prev =>
      prev.includes(id) ? prev.filter(t => t !== id) : [...prev, id]
    );
    setSaved(false);
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (gameTypes.length === 0) {
      setError('Select at least one game type');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await updateSettings({
        email,
        display_name: displayName,
        avatar_url: avatarUrl,
        lichess_username: lichessUsername,
        sync_game_types: gameTypes,
        sync_days_back: daysBack,
        max_new_per_day: maxNewPerDay,
      });
      setSaved(true);
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save settings');
    } finally {
      setBusy(false);
    }
  };

  if (!settings && !error) {
    return <div className="status-message"><p>Loading settings…</p></div>;
  }

  return (
    <div className="form-card settings-card">
      <form onSubmit={handleSave}>
        <h2 className="form-title">Profile</h2>

        <div className="settings-profile-row">
          <div className="avatar avatar-lg">
            {avatarUrl
              ? <img src={avatarUrl} alt="" onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }} />
              : <span>{(displayName || settings?.username || '?').slice(0, 1).toUpperCase()}</span>}
          </div>
          <div className="settings-profile-fields">
            <div className="form-group">
              <label htmlFor="set-name">Display name</label>
              <input
                type="text" id="set-name" className="input"
                placeholder={settings?.username}
                value={displayName}
                onChange={(e) => { setDisplayName(e.target.value); setSaved(false); }}
              />
            </div>
            <div className="form-group">
              <label htmlFor="set-avatar">Picture URL</label>
              <input
                type="url" id="set-avatar" className="input"
                placeholder="https://…/avatar.png"
                value={avatarUrl}
                onChange={(e) => { setAvatarUrl(e.target.value); setSaved(false); }}
              />
            </div>
          </div>
        </div>

        <div className="form-group">
          <label htmlFor="set-email">Email</label>
          <input
            type="email" id="set-email" className="input"
            placeholder="Used for password reset"
            value={email}
            onChange={(e) => { setEmail(e.target.value); setSaved(false); }}
          />
        </div>

        <div className="form-group">
          <label htmlFor="set-lichess">Lichess account</label>
          <input
            type="text" id="set-lichess" className="input"
            placeholder="Your Lichess username"
            value={lichessUsername}
            onChange={(e) => { setLichessUsername(e.target.value); setSaved(false); }}
          />
        </div>

        <h2 className="form-title settings-section">Sync</h2>

        <div className="form-group">
          <span className="button-group-label">Game types to fetch</span>
          <div className="button-group">
            {GAME_TYPE_OPTIONS.map(t => (
              <button
                key={t.id}
                type="button"
                className={`btn btn-secondary btn-icon ${gameTypes.includes(t.id) ? 'btn-active' : ''}`}
                onClick={() => toggleGameType(t.id)}
              >
                <t.Icon /> {t.label}
              </button>
            ))}
          </div>
        </div>

        <div className="form-group">
          <span className="button-group-label">Fetch games from the past</span>
          <div className="button-group">
            {LOOKBACK_OPTIONS.map(o => (
              <button
                key={o.days}
                type="button"
                className={`btn btn-secondary ${daysBack === o.days ? 'btn-active' : ''}`}
                onClick={() => { setDaysBack(o.days); setSaved(false); }}
              >
                {o.label}
              </button>
            ))}
          </div>
        </div>

        <h2 className="form-title settings-section">Learning</h2>

        <div className="form-group">
          <label htmlFor="set-max-new">New blunders per day</label>
          <input
            type="number" id="set-max-new" className="input input-narrow"
            min={0} max={100}
            value={maxNewPerDay}
            onChange={(e) => {
              setMaxNewPerDay(Math.max(0, Math.min(100, Number(e.target.value) || 0)));
              setSaved(false);
            }}
          />
          <p className="field-hint">
            How many never-seen blunders enter your Learn queue each day.
            Cards you've already started always come back regardless. 0 pauses new blunders.
          </p>
        </div>

        {error && <div className="error-message">{error}</div>}
        {saved && <div className="saved-message">✓ Settings saved</div>}

        <button type="submit" className="btn btn-primary submit-btn" disabled={busy}>
          {busy ? <><IconSpinner /> Saving…</> : <><IconSave /> Save Settings</>}
        </button>
      </form>
    </div>
  );
}

export default SettingsView;
