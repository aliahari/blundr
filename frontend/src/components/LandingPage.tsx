import { IconRefresh, IconEye, IconLearn } from './icons';

interface LandingPageProps {
  onGetStarted: (mode: 'login' | 'register') => void;
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
 * app does before asking them to register or log in.
 */
function LandingPage({ onGetStarted }: LandingPageProps) {
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
        <div className="landing-cta">
          <button type="button" className="btn btn-primary" onClick={() => onGetStarted('register')}>
            Create Account
          </button>
          <button type="button" className="btn btn-secondary" onClick={() => onGetStarted('login')}>
            Log In
          </button>
        </div>
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
