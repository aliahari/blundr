import { useState } from 'react';
import { login, register, forgotPassword, resetPassword } from '../services/api';
import { IconSpinner, IconArrowLeft } from './icons';

type Mode = 'login' | 'register' | 'forgot' | 'reset';

interface AuthFormProps {
  onAuthenticated: () => void;
  /** Which form to open on — 'reset' is set by App.tsx from a ?reset_token= link */
  initialMode?: Mode;
  /** Shows a "back" link to return to the landing page, when provided */
  onBack?: () => void;
  /** Required when initialMode is 'reset' — the token from the emailed link */
  resetToken?: string;
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/**
 * Login / register / forgot-password / reset-password — one card, one mode
 * at a time. Reset mode is entered externally (via a link in the reset
 * email); the other three are reachable from each other within the form.
 */
function AuthForm({ onAuthenticated, initialMode = 'login', onBack, resetToken }: AuthFormProps) {
  const [mode, setMode] = useState<Mode>(initialMode);
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [passwordRepeat, setPasswordRepeat] = useState('');
  const [lichessUsername, setLichessUsername] = useState('');
  const [error, setError] = useState<string | null>(null);
  // Forgot-mode's terminal "check your email" view (replaces the form)
  const [info, setInfo] = useState<string | null>(null);
  // Banner shown above whatever form is active — used for "password
  // updated" after a reset, which needs to be visible once we've already
  // switched back to the login form, not just in forgot-mode's own view
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const switchTo = (next: Mode) => {
    setMode(next);
    setError(null);
    setInfo(null);
    setSuccessMessage(null);
    setPasswordRepeat('');
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setInfo(null);

    // Mirror the backend's field rules so typical mistakes get instant,
    // specific feedback instead of a validation round trip
    if (mode === 'register') {
      if (username.trim().length < 3) {
        setError('Username must be at least 3 characters');
        return;
      }
      if (!EMAIL_RE.test(email.trim())) {
        setError('Enter a valid email address');
        return;
      }
      if (password.length < 8) {
        setError('Password must be at least 8 characters');
        return;
      }
      if (password !== passwordRepeat) {
        setError("Passwords don't match");
        return;
      }
    }
    if (mode === 'forgot' && !EMAIL_RE.test(email.trim())) {
      setError('Enter a valid email address');
      return;
    }
    if (mode === 'reset') {
      if (password.length < 8) {
        setError('Password must be at least 8 characters');
        return;
      }
      if (password !== passwordRepeat) {
        setError("Passwords don't match");
        return;
      }
    }

    setBusy(true);
    try {
      if (mode === 'login') {
        await login(username, password);
        onAuthenticated();
      } else if (mode === 'register') {
        await register(username, email.trim(), password, lichessUsername.trim());
        onAuthenticated();
      } else if (mode === 'forgot') {
        const res = await forgotPassword(email.trim());
        setInfo(res.message);
      } else {
        await resetPassword(resetToken!, password);
        switchTo('login');
        setSuccessMessage('Password updated. Log in with your new password.');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong');
    } finally {
      setBusy(false);
    }
  };

  const titles: Record<Mode, string> = {
    login: 'Log In',
    register: 'Create Account',
    forgot: 'Reset Password',
    reset: 'Choose a New Password',
  };

  const submitLabels: Record<Mode, string> = {
    login: 'Log In',
    register: 'Register',
    forgot: 'Send Reset Link',
    reset: 'Set New Password',
  };

  const canSubmit = (() => {
    if (busy) return false;
    if (mode === 'login') return Boolean(username.trim() && password);
    if (mode === 'register') return Boolean(username.trim() && email.trim() && password && passwordRepeat && lichessUsername.trim());
    if (mode === 'forgot') return Boolean(email.trim());
    return Boolean(password && passwordRepeat);
  })();

  return (
    <div className="form-card auth-card">
      {onBack && mode !== 'reset' && (
        <button type="button" className="link-button auth-back" onClick={onBack}>
          <IconArrowLeft size={14} /> Back
        </button>
      )}
      <h2 className="form-title">{titles[mode]}</h2>

      {successMessage && <p className="saved-message">✓ {successMessage}</p>}

      {/* Forgot-password success: nothing left to do but go back to login */}
      {mode === 'forgot' && info ? (
        <>
          <p className="auth-info">{info}</p>
          <button type="button" className="btn btn-secondary submit-btn" onClick={() => switchTo('login')}>
            Back to Log In
          </button>
        </>
      ) : (
        <form onSubmit={handleSubmit}>
          {(mode === 'login' || mode === 'register') && (
            <div className="form-group">
              <label htmlFor="auth-username">Username</label>
              <input
                type="text"
                id="auth-username"
                className="input"
                autoComplete="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
            </div>
          )}

          {mode === 'register' && (
            <div className="form-group">
              <label htmlFor="auth-email">Email</label>
              <input
                type="email"
                id="auth-email"
                className="input"
                autoComplete="email"
                placeholder="Used for password reset"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
          )}

          {mode === 'forgot' && (
            <div className="form-group">
              <label htmlFor="auth-forgot-email">Email</label>
              <input
                type="email"
                id="auth-forgot-email"
                className="input"
                autoComplete="email"
                placeholder="The email on your account"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
          )}

          {(mode === 'login' || mode === 'register' || mode === 'reset') && (
            <div className="form-group">
              <label htmlFor="auth-password">
                {mode === 'reset' ? 'New password (min 8 characters)' : `Password${mode === 'register' ? ' (min 8 characters)' : ''}`}
              </label>
              <input
                type="password"
                id="auth-password"
                className="input"
                autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
          )}

          {(mode === 'register' || mode === 'reset') && (
            <div className="form-group">
              <label htmlFor="auth-password-repeat">Repeat password</label>
              <input
                type="password"
                id="auth-password-repeat"
                className="input"
                autoComplete="new-password"
                value={passwordRepeat}
                onChange={(e) => setPasswordRepeat(e.target.value)}
              />
              {passwordRepeat.length > 0 && password !== passwordRepeat && (
                <p className="field-hint field-hint-error">Passwords don't match</p>
              )}
            </div>
          )}

          {mode === 'register' && (
            <div className="form-group">
              <label htmlFor="auth-lichess">Lichess username</label>
              <input
                type="text"
                id="auth-lichess"
                className="input"
                placeholder="The Lichess account whose games get analyzed"
                value={lichessUsername}
                onChange={(e) => setLichessUsername(e.target.value)}
              />
            </div>
          )}

          {mode === 'login' && (
            <button type="button" className="link-button auth-forgot-link" onClick={() => switchTo('forgot')}>
              Forgot password?
            </button>
          )}

          {error && <div className="error-message">{error}</div>}

          <button type="submit" className="btn btn-primary submit-btn" disabled={!canSubmit}>
            {busy ? <><IconSpinner /> Please wait…</> : submitLabels[mode]}
          </button>
        </form>
      )}

      {(mode === 'login' || mode === 'register') && (
        <p className="auth-switch">
          {mode === 'login' ? "Don't have an account? " : 'Already have an account? '}
          <button type="button" className="link-button" onClick={() => switchTo(mode === 'login' ? 'register' : 'login')}>
            {mode === 'login' ? 'Register' : 'Log in'}
          </button>
        </p>
      )}
    </div>
  );
}

export default AuthForm;
