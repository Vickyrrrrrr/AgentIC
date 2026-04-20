import { useState, useEffect } from 'react';
import { supabase } from '../supabaseClient';
import { motion } from 'framer-motion';
import UnicornScene from 'unicornstudio-react';

export const LandingPage = ({ onAuthSuccess }: { onAuthSuccess: () => void }) => {
  const [authMode, setAuthMode] = useState<'login' | 'signup'>('signup');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [successMsg, setSuccessMsg] = useState('');

  // Typewriter
  const phrases = ['Natural Language to GDSII', 'Autonomous Silicon Design', 'Multi-Agent EDA Pipeline'];
  const [phraseIdx, setPhraseIdx] = useState(0);
  const [display, setDisplay] = useState('');
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    const full = phrases[phraseIdx];
    if (!deleting && display === full) {
      const t = setTimeout(() => setDeleting(true), 2200);
      return () => clearTimeout(t);
    }
    if (deleting && display === '') {
      setDeleting(false);
      setPhraseIdx((p) => (p + 1) % phrases.length);
      return;
    }
    const t = setTimeout(
      () => setDisplay(full.substring(0, display.length + (deleting ? -1 : 1))),
      deleting ? 25 : 70
    );
    return () => clearTimeout(t);
  }, [display, deleting, phraseIdx]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setSuccessMsg('');
    setLoading(true);
    try {
      if (authMode === 'login') {
        const { error: err } = await supabase.auth.signInWithPassword({ email, password });
        if (err) throw err;
        onAuthSuccess();
      } else {
        const { error: err } = await supabase.auth.signUp({
          email, password,
          options: { emailRedirectTo: window.location.origin }
        });
        if (err) throw err;
        setSuccessMsg('Check your email to confirm and join the waitlist.');
      }
    } catch (err: any) {
      setError(err.message || 'Authentication failed');
    }
    setLoading(false);
  };

  const handleGoogle = async () => {
    setError('');
    const { error: err } = await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: { redirectTo: window.location.origin }
    });
    if (err) setError(err.message);
  };

  return (
    <div className="uc-landing-root">
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');

        .uc-landing-root {
          position: relative;
          min-height: 100vh;
          width: 100%;
          overflow: hidden;
          font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
          background: #000;
        }

        /* ── Unicorn Scene: fullscreen background ── */
        .uc-scene-wrap {
          position: fixed;
          inset: 0;
          z-index: 0;
          pointer-events: none;
        }
        .uc-scene-wrap > div,
        .uc-scene-wrap canvas,
        .uc-scene-wrap iframe {
          width: 100% !important;
          height: 100% !important;
          position: absolute !important;
          inset: 0 !important;
          object-fit: cover;
        }

        /* ── Dark vignette overlay for readability ── */
        .uc-vignette {
          position: fixed;
          inset: 0;
          z-index: 1;
          background:
            radial-gradient(ellipse at center, transparent 30%, rgba(0,0,0,0.6) 100%),
            linear-gradient(180deg, rgba(0,0,0,0.3) 0%, transparent 40%, transparent 60%, rgba(0,0,0,0.5) 100%);
          pointer-events: none;
        }

        /* ── Content layer ── */
        .uc-content {
          position: relative;
          z-index: 10;
          min-height: 100vh;
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          padding: 2rem;
          gap: 2rem;
        }

        /* ── Top nav / brand bar ── */
        .uc-nav {
          position: fixed;
          top: 0;
          left: 0;
          right: 0;
          z-index: 20;
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 1.25rem 2.5rem;
          background: rgba(0, 0, 0, 0.2);
          backdrop-filter: blur(20px);
          -webkit-backdrop-filter: blur(20px);
          border-bottom: 1px solid rgba(255,255,255,0.06);
        }
        .uc-nav-brand {
          display: flex;
          align-items: center;
          gap: 0.75rem;
        }
        .uc-nav-logo {
          width: 36px;
          height: 36px;
          border-radius: 10px;
          background: linear-gradient(135deg, #c18a73 0%, #a06b56 100%);
          display: grid;
          place-items: center;
          font-weight: 900;
          font-size: 1.1rem;
          color: #fff;
          letter-spacing: -0.02em;
        }
        .uc-nav-name {
          font-size: 1.15rem;
          font-weight: 700;
          color: #fff;
          letter-spacing: -0.02em;
        }
        .uc-nav-tag {
          font-size: 0.7rem;
          color: rgba(255,255,255,0.4);
          font-weight: 500;
          letter-spacing: 0.06em;
          text-transform: uppercase;
        }
        .uc-nav-links {
          display: flex;
          align-items: center;
          gap: 1.5rem;
        }
        .uc-nav-link {
          font-size: 0.85rem;
          color: rgba(255,255,255,0.5);
          text-decoration: none;
          transition: color 0.2s;
          cursor: pointer;
          background: none;
          border: none;
          font-family: inherit;
        }
        .uc-nav-link:hover {
          color: #fff;
        }

        /* ── Hero text ── */
        .uc-hero {
          text-align: center;
          max-width: 700px;
          margin-bottom: 0.5rem;
        }
        .uc-hero-badge {
          display: inline-flex;
          align-items: center;
          gap: 0.5rem;
          padding: 0.4rem 1rem;
          background: rgba(193, 138, 115, 0.12);
          border: 1px solid rgba(193, 138, 115, 0.25);
          border-radius: 100px;
          font-size: 0.75rem;
          font-weight: 600;
          color: #d4a18b;
          margin-bottom: 1.75rem;
          letter-spacing: 0.03em;
        }
        .uc-hero-badge-dot {
          width: 6px;
          height: 6px;
          border-radius: 50%;
          background: #c18a73;
          box-shadow: 0 0 10px #c18a73;
          animation: uc-pulse 2s ease-in-out infinite;
        }
        @keyframes uc-pulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.5; transform: scale(1.3); }
        }
        .uc-hero h1 {
          font-size: clamp(2.4rem, 5vw, 3.8rem);
          font-weight: 800;
          color: #fff;
          letter-spacing: -0.04em;
          line-height: 1.1;
          margin: 0 0 0.5rem;
          min-height: 1.2em;
        }
        .uc-hero h1 .uc-accent {
          background: linear-gradient(135deg, #c18a73 0%, #e8c4b4 50%, #c18a73 100%);
          background-size: 200% 200%;
          -webkit-background-clip: text;
          -webkit-text-fill-color: transparent;
          animation: uc-shimmer 4s ease-in-out infinite;
        }
        @keyframes uc-shimmer {
          0%, 100% { background-position: 0% 50%; }
          50% { background-position: 100% 50%; }
        }
        .uc-cursor {
          color: #c18a73;
          animation: uc-blink 1s step-end infinite;
          font-weight: 300;
        }
        @keyframes uc-blink {
          0%, 100% { opacity: 1; }
          50% { opacity: 0; }
        }
        .uc-hero p {
          font-size: 1.05rem;
          color: rgba(255,255,255,0.5);
          line-height: 1.65;
          max-width: 560px;
          margin: 0 auto;
          font-weight: 400;
        }

        /* ── Feature pills ── */
        .uc-features {
          display: flex;
          gap: 0.75rem;
          justify-content: center;
          flex-wrap: wrap;
          margin-top: 1.75rem;
        }
        .uc-feature-pill {
          display: flex;
          align-items: center;
          gap: 0.5rem;
          padding: 0.55rem 1.1rem;
          background: rgba(255,255,255,0.04);
          border: 1px solid rgba(255,255,255,0.08);
          border-radius: 100px;
          font-size: 0.8rem;
          color: rgba(255,255,255,0.7);
          transition: all 0.25s ease;
          backdrop-filter: blur(10px);
        }
        .uc-feature-pill:hover {
          background: rgba(255,255,255,0.08);
          border-color: rgba(193, 138, 115, 0.3);
          color: #fff;
          transform: translateY(-2px);
        }

        /* ── Glass auth card ── */
        .uc-auth-card {
          width: 100%;
          max-width: 420px;
          background: rgba(12, 12, 12, 0.65);
          backdrop-filter: blur(40px) saturate(1.5);
          -webkit-backdrop-filter: blur(40px) saturate(1.5);
          border: 1px solid rgba(255,255,255,0.08);
          border-radius: 24px;
          padding: 2.5rem;
          box-shadow:
            0 25px 60px rgba(0,0,0,0.5),
            0 0 0 1px rgba(255,255,255,0.04) inset;
        }
        .uc-auth-title {
          font-size: 1.5rem;
          font-weight: 700;
          color: #fff;
          margin: 0 0 0.4rem;
          letter-spacing: -0.02em;
          text-align: center;
        }
        .uc-auth-sub {
          font-size: 0.85rem;
          color: rgba(255,255,255,0.4);
          text-align: center;
          margin: 0 0 1.75rem;
          line-height: 1.5;
        }
        .uc-field {
          margin-bottom: 1rem;
        }
        .uc-label {
          display: block;
          font-size: 0.75rem;
          font-weight: 600;
          color: rgba(255,255,255,0.5);
          margin-bottom: 0.4rem;
          text-transform: uppercase;
          letter-spacing: 0.06em;
        }
        .uc-input {
          width: 100%;
          padding: 0.75rem 1rem;
          background: rgba(255,255,255,0.05);
          border: 1px solid rgba(255,255,255,0.1);
          border-radius: 12px;
          color: #fff;
          font-size: 0.9rem;
          font-family: inherit;
          outline: none;
          transition: all 0.2s ease;
          box-sizing: border-box;
        }
        .uc-input::placeholder {
          color: rgba(255,255,255,0.2);
        }
        .uc-input:focus {
          border-color: rgba(193, 138, 115, 0.5);
          box-shadow: 0 0 0 3px rgba(193, 138, 115, 0.1);
          background: rgba(255,255,255,0.07);
        }

        .uc-alert {
          padding: 0.65rem 1rem;
          border-radius: 10px;
          font-size: 0.8rem;
          margin-bottom: 1rem;
          font-weight: 500;
        }
        .uc-alert--error {
          background: rgba(220, 38, 38, 0.12);
          border: 1px solid rgba(220, 38, 38, 0.25);
          color: #fca5a5;
        }
        .uc-alert--success {
          background: rgba(34, 197, 94, 0.12);
          border: 1px solid rgba(34, 197, 94, 0.25);
          color: #86efac;
        }

        .uc-submit {
          width: 100%;
          padding: 0.85rem;
          background: linear-gradient(135deg, #c18a73 0%, #a06b56 100%);
          border: none;
          border-radius: 12px;
          color: #fff;
          font-size: 0.9rem;
          font-weight: 700;
          font-family: inherit;
          cursor: pointer;
          transition: all 0.25s ease;
          letter-spacing: 0.01em;
          position: relative;
          overflow: hidden;
        }
        .uc-submit:hover:not(:disabled) {
          transform: translateY(-1px);
          box-shadow: 0 8px 25px rgba(193, 138, 115, 0.4);
        }
        .uc-submit:disabled {
          opacity: 0.5;
          cursor: not-allowed;
        }

        .uc-divider {
          display: flex;
          align-items: center;
          gap: 1rem;
          margin: 1.25rem 0;
        }
        .uc-divider-line {
          flex: 1;
          height: 1px;
          background: rgba(255,255,255,0.08);
        }
        .uc-divider-text {
          font-size: 0.75rem;
          color: rgba(255,255,255,0.25);
          text-transform: uppercase;
          letter-spacing: 0.08em;
        }

        .uc-google {
          width: 100%;
          padding: 0.75rem;
          background: rgba(255,255,255,0.04);
          border: 1px solid rgba(255,255,255,0.1);
          border-radius: 12px;
          color: rgba(255,255,255,0.8);
          font-size: 0.85rem;
          font-weight: 600;
          font-family: inherit;
          cursor: pointer;
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 0.6rem;
          transition: all 0.2s ease;
        }
        .uc-google:hover {
          background: rgba(255,255,255,0.08);
          border-color: rgba(255,255,255,0.18);
        }

        .uc-switch {
          text-align: center;
          margin-top: 1.25rem;
          font-size: 0.8rem;
          color: rgba(255,255,255,0.35);
        }
        .uc-switch-btn {
          background: none;
          border: none;
          color: #c18a73;
          cursor: pointer;
          font-weight: 600;
          font-family: inherit;
          font-size: 0.8rem;
          text-decoration: underline;
          text-underline-offset: 2px;
        }
        .uc-switch-btn:hover {
          color: #e8c4b4;
        }

        /* ── Spinner ── */
        .uc-spinner {
          display: inline-block;
          width: 18px;
          height: 18px;
          border: 2px solid rgba(255,255,255,0.3);
          border-top-color: #fff;
          border-radius: 50%;
          animation: uc-spin 0.6s linear infinite;
        }
        @keyframes uc-spin {
          to { transform: rotate(360deg); }
        }

        /* ── Footer ── */
        .uc-footer {
          position: fixed;
          bottom: 0;
          left: 0;
          right: 0;
          z-index: 20;
          text-align: center;
          padding: 1rem;
          font-size: 0.72rem;
          color: rgba(255,255,255,0.2);
          background: linear-gradient(0deg, rgba(0,0,0,0.4) 0%, transparent 100%);
        }

        /* ── Responsive ── */
        @media (max-width: 768px) {
          .uc-nav { padding: 1rem 1.25rem; }
          .uc-nav-links { display: none; }
          .uc-content { padding: 6rem 1.25rem 4rem; }
          .uc-hero h1 { font-size: 2rem; }
          .uc-hero p { font-size: 0.9rem; }
          .uc-auth-card { padding: 1.75rem; border-radius: 20px; }
          .uc-features { gap: 0.5rem; }
          .uc-feature-pill { font-size: 0.72rem; padding: 0.4rem 0.85rem; }
        }
      `}</style>

      {/* ── Fullscreen UnicornStudio Scene ── */}
      <div className="uc-scene-wrap">
        <UnicornScene
          projectId="T4unQTzjmZMUzACHD1es"
          width="100%"
          height="100%"
          scale={1}
          dpi={1.5}
          sdkUrl="https://cdn.jsdelivr.net/gh/hiunicornstudio/unicornstudio.js@2.1.9/dist/unicornStudio.umd.js"
        />
      </div>

      {/* ── Vignette overlay for text readability ── */}
      <div className="uc-vignette" />

      {/* ── Top Nav ── */}
      <nav className="uc-nav">
        <div className="uc-nav-brand">
          <div className="uc-nav-logo">A</div>
          <div>
            <div className="uc-nav-name">AgentIC</div>
            <div className="uc-nav-tag">Autonomous Silicon Studio</div>
          </div>
        </div>
        <div className="uc-nav-links">
          <button className="uc-nav-link" onClick={() => window.open('https://www.buildstack.live')}>Docs</button>
          <button className="uc-nav-link" onClick={() => window.open('https://github.com/Vickyrrrrrr/AgentIC')}>GitHub</button>
        </div>
      </nav>

      {/* ── Main content ── */}
      <div className="uc-content">
        {/* Hero text */}
        <motion.div
          className="uc-hero"
          initial={{ opacity: 0, y: 30 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
        >
          <div className="uc-hero-badge">
            <div className="uc-hero-badge-dot" />
            Early Access · Limited Spots
          </div>
          <h1>
            <span className="uc-accent">{display}</span>
            <span className="uc-cursor">|</span>
          </h1>
          <p>
            Describe any digital circuit in plain English. Our multi-agent AI writes
            synthesizable RTL, verifies logic, and generates fabrication-ready
            GDSII layouts — fully autonomously.
          </p>

          <div className="uc-features">
            <div className="uc-feature-pill">⚡ 26-Stage Pipeline</div>
            <div className="uc-feature-pill">🧠 Self-Healing Agents</div>
            <div className="uc-feature-pill">🔬 Sky130 PDK</div>
            <div className="uc-feature-pill">🛡️ Formal Verification</div>
          </div>
        </motion.div>

        {/* Auth card */}
        <motion.div
          className="uc-auth-card"
          initial={{ opacity: 0, y: 40, scale: 0.96 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          transition={{ delay: 0.2, duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
        >
          <h2 className="uc-auth-title">
            {authMode === 'signup' ? 'Join the Waitlist' : 'Welcome Back'}
          </h2>
          <p className="uc-auth-sub">
            {authMode === 'signup'
              ? 'Create an account to secure early access to autonomous chip design.'
              : 'Sign in to continue to your workspace.'}
          </p>

          <form onSubmit={handleSubmit}>
            <div className="uc-field">
              <label className="uc-label" htmlFor="uc-email">Email</label>
              <input
                id="uc-email"
                className="uc-input"
                type="email"
                placeholder="you@company.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoComplete="email"
              />
            </div>
            <div className="uc-field">
              <label className="uc-label" htmlFor="uc-pw">Password</label>
              <input
                id="uc-pw"
                className="uc-input"
                type="password"
                placeholder="••••••••"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={6}
                autoComplete={authMode === 'login' ? 'current-password' : 'new-password'}
              />
            </div>

            {error && <div className="uc-alert uc-alert--error">{error}</div>}
            {successMsg && <div className="uc-alert uc-alert--success">{successMsg}</div>}

            <button
              type="submit"
              className="uc-submit"
              disabled={loading || !email || !password}
            >
              {loading
                ? <span className="uc-spinner" />
                : authMode === 'signup' ? 'Create Account' : 'Sign In'}
            </button>
          </form>

          <div className="uc-divider">
            <span className="uc-divider-line" />
            <span className="uc-divider-text">or</span>
            <span className="uc-divider-line" />
          </div>

          <button className="uc-google" onClick={handleGoogle}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
              <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
              <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
              <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
              <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
            </svg>
            Continue with Google
          </button>

          <div className="uc-switch">
            {authMode === 'signup' ? (
              <>Already have an account? <button className="uc-switch-btn" onClick={() => { setAuthMode('login'); setError(''); setSuccessMsg(''); }}>Sign in</button></>
            ) : (
              <>New here? <button className="uc-switch-btn" onClick={() => { setAuthMode('signup'); setError(''); setSuccessMsg(''); }}>Create account</button></>
            )}
          </div>
        </motion.div>
      </div>

      {/* ── Footer ── */}
      <footer className="uc-footer">
        © 2026 AgentIC · Autonomous Silicon Design
      </footer>
    </div>
  );
};
