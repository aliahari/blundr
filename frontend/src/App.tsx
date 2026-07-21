import { useCallback, useEffect, useState } from 'react';
import { healthCheck, getMe, getToken, clearToken } from './services/api';
import { UserInfo } from './types';
import AuthForm from './components/AuthForm';
import LandingPage from './components/LandingPage';
import Dashboard from './components/Dashboard';
import ReviewPanel from './components/ReviewPanel';
import SettingsView from './components/SettingsView';
import { IconHome, IconLearn, IconLogout } from './components/icons';

type View = 'dashboard' | 'learn' | 'settings';

function App() {
  const [user, setUser] = useState<UserInfo | null>(null);
  const [sessionChecked, setSessionChecked] = useState(false);
  const [view, setView] = useState<View>('dashboard');
  const [error, setError] = useState<string | null>(null);
  // null = show the landing page; otherwise which auth form is open
  const [authMode, setAuthMode] = useState<'login' | 'register' | null>(null);
  // Set from a ?reset_token= link in a password-reset email. Takes over the
  // auth area regardless of session state — there's no client-side router,
  // so this is read once from the URL on load (see App.tsx history note below).
  const [resetToken, setResetToken] = useState<string | null>(null);

  const refreshUser = useCallback(async () => {
    const me = await getMe();
    setUser(me);
  }, []);

  // Passed to AuthForm as onAuthenticated. Reset itself doesn't log the
  // user in (they still have to submit the login form afterward, now
  // pre-switched to it) — this fires on THAT login, which is also the
  // moment resetToken must be cleared, or the resetToken-gated branch below
  // would keep showing the auth form forever instead of the dashboard.
  const handleAuthenticated = useCallback(async () => {
    setResetToken(null);
    await refreshUser();
  }, [refreshUser]);

  // Restore session + check backend health on mount
  useEffect(() => {
    const init = async () => {
      const token = new URLSearchParams(window.location.search).get('reset_token');
      if (token) {
        setResetToken(token);
        // Drop it from the visible URL/history — it's single-use and
        // shouldn't linger in a bookmark or the back button
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
    setAuthMode(null); // back to the landing page, not straight into a form
  };

  const displayName = user?.display_name || user?.username || '';

  return (
    <div className="min-h-screen">
      {/* Top nav */}
      <header className="topnav">
        <div className="topnav-brand">
          <h1>Blund<span>r</span></h1>
        </div>

        {user && !resetToken && (
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
                onClick={() => setView('learn')}
              >
                <IconLearn /> Learn
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
      <main className="container">
        {!sessionChecked ? (
          <div className="status-message"><p>Loading…</p></div>
        ) : resetToken ? (
          <AuthForm
            onAuthenticated={handleAuthenticated}
            initialMode="reset"
            resetToken={resetToken}
          />
        ) : !user ? (
          <>
            {error && <div className="error-message">{error}</div>}
            {authMode === null ? (
              <LandingPage onGetStarted={setAuthMode} />
            ) : (
              <AuthForm
                onAuthenticated={handleAuthenticated}
                initialMode={authMode}
                onBack={() => setAuthMode(null)}
              />
            )}
          </>
        ) : view === 'settings' ? (
          <SettingsView onSaved={refreshUser} />
        ) : view === 'learn' ? (
          <ReviewPanel />
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
