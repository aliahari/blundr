import { IconRefresh, IconEye, IconLearn, IconLichess } from './icons';
import { LICHESS_SIGN_IN_URL } from '../services/api';

interface LandingPageProps {
  /** A line saying why the last sign-in didn't work, if it didn't. */
  signInError?: string | null;
}

const FEATURES = [
  {
    Icon: IconRefresh,
    title: 'Automatic sync',
    body: "Connect your Lichess account once. Blundr keeps fetching your new games in the background — no manual exporting.",
  },
  {
    Icon: IconEye,
    title: 'Engine-verified blunders',
    body: "Every position is checked by Stockfish. A move that drops your win chance by 25%+ gets flagged, with the punish shown right on the board.",
  },
  {
    Icon: IconLearn,
    title: 'Spaced repetition training',
    body: "Review flagged positions like an Anki deck. Miss one and it comes right back for another try in the same session.",
  },
];

/**
 * Public landing page shown to signed-out visitors, explaining what the
 * app does before asking them to sign in with Lichess.
 */
function LandingPage({ signInError }: LandingPageProps) {
  return (
    <div className="landing">
      <section className="landing-hero">
        <h1 className="landing-hero-brand">Blund<span>r</span></h1>
        <p className="landing-hero-tagline">
          Learn from every blunder you make on Lichess.
        </p>
        <p className="landing-hero-sub">
          Blundr syncs your games, uses Stockfish to find the moves that cost
          you the game, and drills them back into your training queue until
          you stop making them.
        </p>
        {signInError && <p className="error-message" role="alert">{signInError}</p>}
        <div className="landing-cta">
          <a className="btn btn-primary btn-lichess" href={LICHESS_SIGN_IN_URL}>
            <IconLichess size={20} /> Sign in with Lichess
          </a>
        </div>
        <p className="landing-note">Blundr reads your public games. It never posts or plays for you.</p>
      </section>

      <section className="landing-features">
        {FEATURES.map(f => (
          <div className="landing-feature" key={f.title}>
            <div className="landing-feature-icon"><f.Icon size={22} /></div>
            <h3>{f.title}</h3>
            <p>{f.body}</p>
          </div>
        ))}
      </section>
    </div>
  );
}

export default LandingPage;
