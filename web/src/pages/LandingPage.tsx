import { useState, useEffect } from 'react';
import { supabase } from '../supabaseClient';
import { motion } from 'framer-motion';

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
    <div className="landing-root">
      {/* Subtle grid background */}
      <div className="landing-grid-bg" />

      <motion.div
        className="landing-wrapper"
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: [0.25, 0.46, 0.45, 0.94] }}
      >
        {/* ─── Left: Brand ─── */}
        <div className="landing-brand">
          <div className="landing-logo">
            <div className="landing-logo-mark">A</div>
            <div className="landing-logo-text">
              <span className="landing-logo-name">AgentIC</span>
              <span className="landing-logo-tag">Autonomous Silicon Studio</span>
            </div>
          </div>

          <h1 className="landing-headline">
            <span className="landing-headline-accent">{display}</span>
            <span className="landing-cursor">|</span>
          </h1>

          <p className="landing-body">
            Describe any digital circuit in plain English. Our multi-agent AI writes
            synthesizable RTL, verifies logic with formal proofs, and generates
            fabrication-ready GDSII layouts — fully autonomously.
          </p>

          <div className="landing-features">
            <div className="landing-feature">
              <div className="landing-feature-icon">⚡</div>
              <div>
                <div className="landing-feature-title">26-Stage Pipeline</div>
                <div className="landing-feature-desc">Spec to silicon in one command</div>
              </div>
            </div>
            <div className="landing-feature">
              <div className="landing-feature-icon">🧠</div>
              <div>
                <div className="landing-feature-title">Self-Healing Agents</div>
                <div className="landing-feature-desc">Auto-debug across verification loops</div>
              </div>
            </div>
            <div className="landing-feature">
              <div className="landing-feature-icon">🔬</div>
              <div>
                <div className="landing-feature-title">Sky130 PDK</div>
                <div className="landing-feature-desc">Open-source fabrication ready</div>
              </div>
            </div>
          </div>

          <div className="landing-trust">
            <span className="landing-trust-text">Built for hardware engineers and chip designers</span>
          </div>
        </div>

        {/* ─── Right: Auth ─── */}
        <div className="landing-auth">
          <motion.div
            className="landing-auth-card"
            initial={{ opacity: 0, scale: 0.97 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: 0.15, duration: 0.4 }}
          >
            <h2 className="landing-auth-title">
              {authMode === 'signup' ? 'Join the Waitlist' : 'Welcome Back'}
            </h2>
            <p className="landing-auth-sub">
              {authMode === 'signup'
                ? 'Create an account to secure early access and save your workspace context.'
                : 'Sign in to continue to your workspace and BYOK setup.'}
            </p>

            <div className="landing-onboarding-note">
              <strong>What happens next</strong>
              <span>After sign-in, add your BYOK key locally in the browser, then launch your first build from Design Studio or HITL.</span>
            </div>

            <form className="landing-auth-form" onSubmit={handleSubmit}>
              <div className="landing-field">
                <label className="landing-label" htmlFor="landing-email">Email</label>
                <input
                  id="landing-email"
                  className="landing-input"
                  type="email"
                  placeholder="you@company.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  autoComplete="email"
                />
              </div>
              <div className="landing-field">
                <label className="landing-label" htmlFor="landing-pw">Password</label>
                <input
                  id="landing-pw"
                  className="landing-input"
                  type="password"
                  placeholder="••••••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  minLength={6}
                  autoComplete={authMode === 'login' ? 'current-password' : 'new-password'}
                />
              </div>

              {error && <div className="landing-alert landing-alert--error">{error}</div>}
              {successMsg && <div className="landing-alert landing-alert--success">{successMsg}</div>}

              <button
                type="submit"
                className="landing-submit"
                disabled={loading || !email || !password}
              >
                {loading
                  ? <span className="landing-spinner" />
                  : authMode === 'signup' ? 'Create Account' : 'Sign In'}
              </button>
            </form>

            <div className="landing-divider">
              <span className="landing-divider-line" />
              <span className="landing-divider-text">or</span>
              <span className="landing-divider-line" />
            </div>

            <button className="landing-google" onClick={handleGoogle}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
                <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
                <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
                <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
              </svg>
              Continue with Google
            </button>

            <div className="landing-switch">
              {authMode === 'signup' ? (
                <>Already have an account? <button className="landing-switch-btn" onClick={() => { setAuthMode('login'); setError(''); setSuccessMsg(''); }}>Sign in</button></>
              ) : (
                <>New here? <button className="landing-switch-btn" onClick={() => { setAuthMode('signup'); setError(''); setSuccessMsg(''); }}>Create account</button></>
              )}
            </div>
          </motion.div>
        </div>
      </motion.div>

      <footer className="landing-footer">
        <p>© 2026 AgentIC · Autonomous Silicon Design</p>
      </footer>
    </div>
  );
};
