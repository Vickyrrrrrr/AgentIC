import { useState } from 'react';
import { supabase } from '../supabaseClient';

type AuthMode = 'login' | 'signup';

export const AuthPage = ({ onAuth }: { onAuth: () => void }) => {
  const [mode, setMode] = useState<AuthMode>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [successMsg, setSuccessMsg] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setSuccessMsg('');
    setLoading(true);

    try {
      if (mode === 'login') {
        const { error: err } = await supabase.auth.signInWithPassword({ email, password });
        if (err) throw err;
        onAuth();
      } else {
        const { error: err } = await supabase.auth.signUp({ 
          email, 
          password,
          options: {
            emailRedirectTo: window.location.origin
          }
        });
        if (err) throw err;
        setSuccessMsg('We sent you a confirmation link. Check your email to continue.');
      }
    } catch (err: any) {
      setError(err.message || 'Authentication failed');
    }
    setLoading(false);
  };

  return (
    <div className="auth-root">
      {/* ── Left Panel: Brand Story ── */}
      <div className="auth-left">
        <div className="auth-left-content">
          <div className="auth-logo">
            <div className="auth-logo-mark">
              <svg width="32" height="32" viewBox="0 0 32 32" fill="none">
                <rect width="32" height="32" rx="8" fill="currentColor" opacity="0.12"/>
                <path d="M8 24V8l8 4v8l8-4" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"/>
                <circle cx="24" cy="20" r="3" fill="currentColor" opacity="0.5"/>
              </svg>
            </div>
            <span className="auth-logo-text">AgentIC</span>
          </div>

          <h1 className="auth-headline">
            Text to Silicon.<br/>
            <span className="auth-headline-dim">Autonomously.</span>
          </h1>

          <p className="auth-value-prop">
            Describe any digital chip in plain English. Our multi-agent AI system writes RTL, 
            verifies logic, runs formal proofs, and generates fabrication-ready GDSII — 
            all without human intervention.
          </p>

          <div className="auth-features">
            <div className="auth-feature">
              <div className="auth-feature-icon">⚡</div>
              <div>
                <div className="auth-feature-title">15-Stage Pipeline</div>
                <div className="auth-feature-desc">From spec to silicon in one command</div>
              </div>
            </div>
            <div className="auth-feature">
              <div className="auth-feature-icon">🧠</div>
              <div>
                <div className="auth-feature-title">Self-Healing AI</div>
                <div className="auth-feature-desc">Auto-fixes bugs across verification loops</div>
              </div>
            </div>
            <div className="auth-feature">
              <div className="auth-feature-icon">🔬</div>
              <div>
                <div className="auth-feature-title">Sky130 PDK</div>
                <div className="auth-feature-desc">Open-source fabrication-ready output</div>
              </div>
            </div>
          </div>

          <div className="auth-left-footer">
            <span>Trusted by chip designers worldwide</span>
          </div>
        </div>
      </div>

      {/* ── Right Panel: Auth Form ── */}
      <div className="auth-right">
        <div className="auth-form-wrapper">
          <h2 className="auth-form-title">
            {mode === 'login' ? 'Welcome back' : 'Get started'}
          </h2>
          <p className="auth-form-sub">
            {mode === 'login'
              ? 'Sign in to your workspace'
              : 'Create your account to start building'}
          </p>

          <form className="auth-form" onSubmit={handleSubmit}>
            <div className="auth-field">
              <label className="auth-label" htmlFor="auth-email">Email address</label>
              <input
                id="auth-email"
                type="email"
                className="auth-input"
                placeholder="you@company.com"
                value={email}
                onChange={e => setEmail(e.target.value)}
                required
                autoFocus
                autoComplete="email"
              />
            </div>

            <div className="auth-field">
              <label className="auth-label" htmlFor="auth-password">Password</label>
              <input
                id="auth-password"
                type="password"
                className="auth-input"
                placeholder="••••••••"
                value={password}
                onChange={e => setPassword(e.target.value)}
                required
                minLength={6}
                autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              />
            </div>

            {error && (
              <div className="auth-alert auth-alert--error">
                <span className="auth-alert-icon">✕</span>
                {error}
              </div>
            )}
            {successMsg && (
              <div className="auth-alert auth-alert--success">
                <span className="auth-alert-icon">✓</span>
                {successMsg}
              </div>
            )}

            <button
              type="submit"
              className="auth-submit"
              disabled={loading || !email || !password}
            >
              {loading ? (
                <span className="auth-submit-loading">
                  <span className="auth-spinner" />
                  {mode === 'login' ? 'Signing in…' : 'Creating account…'}
                </span>
              ) : (
                mode === 'login' ? 'Continue' : 'Create account'
              )}
            </button>
          </form>

          <div className="auth-divider">
            <span>{mode === 'login' ? "Don't have an account?" : 'Already have an account?'}</span>
          </div>

          <button
            className="auth-switch"
            onClick={() => { setMode(mode === 'login' ? 'signup' : 'login'); setError(''); setSuccessMsg(''); }}
          >
            {mode === 'login' ? 'Create account' : 'Sign in instead'}
          </button>

          <p className="auth-legal">
            By continuing, you agree to AgentIC's Terms of Service and Privacy Policy.
          </p>
        </div>
      </div>
    </div>
  );
};
