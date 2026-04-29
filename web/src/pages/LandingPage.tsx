import { useState } from 'react';
import { supabase } from '../supabaseClient';
import { motion } from 'framer-motion';

export const LandingPage = ({ onAuthSuccess: _onAuthSuccess }: { onAuthSuccess: () => void }) => {
  const [email, setEmail] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [successMsg, setSuccessMsg] = useState('');

  const handleEmailSignup = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setSuccessMsg('');
    setLoading(true);
    try {
      const { error: err } = await supabase.auth.signUp({
        email,
        password: crypto.randomUUID(),
        options: { emailRedirectTo: window.location.origin }
      });
      if (err) throw err;
      setSuccessMsg('Check your email to confirm and join the waitlist.');
    } catch (err: any) {
      setError(err.message || 'Something went wrong. Try again.');
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
      <div className="landing-grid-bg" />

      <motion.div
        className="landing-card"
        initial={{ opacity: 0, y: 24 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: [0.25, 0.46, 0.45, 0.94] }}
      >
        <div className="landing-card-inner">
          {/* ─── Brand mark ─── */}
          <div className="landing-logo">
            <div className="landing-logo-mark">A</div>
            <div className="landing-logo-name">AgentIC</div>
          </div>

          {/* ─── Headline ─── */}
          <h1 className="landing-headline">
            Autonomous chip design.<br />From English to GDSII.
          </h1>
          <p className="landing-description">
            Describe any digital circuit in plain language. Our AI agents generate
            synthesizable RTL, run verification, and produce fabrication-ready
            layouts — fully autonomous.
          </p>

          {/* ─── Features row ─── */}
          <div className="landing-feature-row">
            <div className="landing-f-item">
              <span className="landing-f-label">Pipeline</span>
              <span className="landing-f-value">29-stage autonomous flow</span>
            </div>
            <div className="landing-f-item">
              <span className="landing-f-label">PDK</span>
              <span className="landing-f-value">Sky130 · GF180 · ASAP7</span>
            </div>
            <div className="landing-f-item">
              <span className="landing-f-label">Self-healing</span>
              <span className="landing-f-value">Auto-fix timing, DRC, LVS</span>
            </div>
          </div>

          {/* ─── Waitlist form ─── */}
          <div className="landing-waitlist">
            <h2 className="landing-waitlist-title">Join the waitlist</h2>
            <p className="landing-waitlist-sub">
              Early access is rolling out in cohorts. Sign up to reserve your spot.
            </p>

            {/* Google sign-in */}
            <button className="landing-google" onClick={handleGoogle} disabled={loading}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
                <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
                <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
                <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
              </svg>
              Continue with Google
            </button>

            <div className="landing-divider">
              <span className="landing-divider-line" />
              <span className="landing-divider-text">or use email</span>
              <span className="landing-divider-line" />
            </div>

            {/* Email form */}
            <form className="landing-form" onSubmit={handleEmailSignup}>
              <div className="landing-input-group">
                <input
                  className="landing-input"
                  type="email"
                  placeholder="you@company.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  autoComplete="email"
                />
                <button
                  type="submit"
                  className="landing-submit"
                  disabled={loading || !email}
                >
                  {loading ? 'Joining...' : 'Join waitlist'}
                </button>
              </div>
              {error && <div className="landing-alert landing-alert--error">{error}</div>}
              {successMsg && <div className="landing-alert landing-alert--success">{successMsg}</div>}
            </form>

            <p className="landing-disclaimer">
              No credit card. No spam. Just early access to autonomous silicon design.
            </p>
          </div>
        </div>
      </motion.div>

      <footer className="landing-footer">
        <p>© 2026 AgentIC</p>
      </footer>
    </div>
  );
};
