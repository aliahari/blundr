import { useCallback, useEffect, useState } from 'react';
import { healthCheck, getMe, getToken, setToken, clearToken, LICHESS_SIGN_IN_URL } from './services/api';
import { UserInfo } from './types';
import LandingPage from './components/LandingPage';
import Dashboard from './components/Dashboard';
import LearnView from './components/LearnView';
import RepertoireTab from './components/repertoire/RepertoireTab';
import SettingsView from './components/SettingsView';
import { IconHome, IconLearn, IconLichess, IconLogout, IconRepertoire } from './components/icons';

type View = 'dashboard' | 'learn' | 'repertoire' | 'settings';

const SIGN_IN_ERRORS: Record<string, string> = {
  denied: 'You cancelled signing in on Lichess.',
  state: 'That sign-in link expired. Please try again.',
  lichess: "Lichess didn't answer. Please try again in a minute.",
};

function App() {
  const [user, setUser] = useState<UserInfo | null>(null);
  const [sessionChecked, setSessionChecked] = useState(false);
  const [view, setView] = useState<View>('dashboard');
  // Which Learn tab opens ("Continue learning" from the Repertoire tab opens Openings)
  const [learnTab, setLearnTab] = useState<'blunders' | 'openings'>('blunders');
  const [error, setError] = useState<string | null>(null);
  // Why the last sign-in didn't work, from a /#auth_error= redirect
  const [signInError, setSignInError] = useState<string | null>(null);

  const refreshUser = useCallback(async () => {
    const me = await getMe();
    setUser(me);
  }, []);

  // Restore session + check backend health on mount
  useEffect(() => {
    const init = async () => {
      // Back from signing in with Lichess: the server put our session token
      // (or what went wrong) in the URL fragment. Read it once, then drop it
      // from the address bar and history.
      const hash = new URLSearchParams(window.location.hash.slice(1));
      const auth = hash.get('auth');
      const authError = hash.get('auth_error');
      if (auth || authError) {
        if (auth) setToken(auth);
        if (authError) setSignInError(SIGN_IN_ERRORS[authError] ?? SIGN_IN_ERRORS.lichess);
        window.history.replaceState({}, '', window.location.pathname);
      }

      const isHealthy = await healthCheck();
      if (!isHealthy) {
        setError('Backend server is not running. Please start the backend first.');
      }
      if (getToken()) {
        try {
          await refreshUser();
        } catch {
          clearToken(); // stale/expired token
        }
      }
      setSessionChecked(true);
    };
    init();
  }, [refreshUser]);

  const handleLogout = () => {
    clearToken();
    setUser(null);
    setView('dashboard');
  };

  const displayName = user?.display_name || user?.username || '';

  return (
    <div className="min-h-screen">
      {/* Top nav */}
      <header className="topnav">
        <div className="topnav-brand">
          <h1>Blund<span>r</span></h1>
        </div>

        {user && (
          <>
            <nav className="topnav-tabs">
              <button
                type="button"
                className={`tab ${view === 'dashboard' ? 'tab-active' : ''}`}
                onClick={() => setView('dashboard')}
              >
                <IconHome /> Home
              </button>
              <button
                type="button"
                className={`tab ${view === 'learn' ? 'tab-active' : ''}`}
                onClick={() => { setLearnTab('blunders'); setView('learn'); }}
              >
                <IconLearn /> Learn
              </button>
              <button
                type="button"
                className={`tab ${view === 'repertoire' ? 'tab-active' : ''}`}
                onClick={() => setView('repertoire')}
              >
                <IconRepertoire /> Repertoire
              </button>
            </nav>

            <div className="topnav-user">
              <button
                type="button"
                className={`topnav-profile ${view === 'settings' ? 'topnav-profile-active' : ''}`}
                onClick={() => setView('settings')}
                title="Settings"
              >
                <span className="avatar">
                  {user.avatar_url
                    ? <img src={user.avatar_url} alt="" onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }} />
                    : <span>{displayName.slice(0, 1).toUpperCase()}</span>}
                </span>
                <span className="topnav-name">{displayName}</span>
              </button>
              <button type="button" className="logout-link" onClick={handleLogout}>
                <IconLogout size={20} /> Log out
              </button>
            </div>
          </>
        )}
      </header>

      {/* Main Container */}
      {user && !user.lichess_connected && (
        <div className="reconnect-banner" role="status">
          <span>Your Lichess sign-in has ended.</span>
          <a className="btn btn-primary btn-sm btn-lichess" href={LICHESS_SIGN_IN_URL}>
            <IconLichess size={16} /> Sign in with Lichess again
          </a>
        </div>
      )}

      <main className="container">
        {!sessionChecked ? (
          <div className="status-message"><p>Loading…</p></div>
        ) : !user ? (
          <>
            {error && <div className="error-message">{error}</div>}
            <LandingPage signInError={signInError} />
          </>
        ) : view === 'settings' ? (
          <SettingsView onSaved={refreshUser} />
        ) : view === 'learn' ? (
          <LearnView key={learnTab} isOwner={user.is_owner} initialTab={learnTab} />
        ) : view === 'repertoire' ? (
          <RepertoireTab onOpenLearn={() => { setLearnTab('openings'); setView('learn'); }} />
        ) : (
          <Dashboard
            hasLichessAccount={Boolean(user.lichess_username)}
            onOpenSettings={() => setView('settings')}
          />
        )}
      </main>
    </div>
  );
}

export default App;
