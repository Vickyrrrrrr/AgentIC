import { useState, useEffect } from 'react';
import { Shield, ArrowRight } from 'lucide-react';
import { useCursorGlow } from '../utils/useAnimations';
import { supabase } from '../supabaseClient';

/* ── Typewriter ─────────────────────────────────────────────────── */
const TypewriterText = ({ texts }: { texts: string[] }) => {
  const [textIndex, setTextIndex] = useState(0);
  const [text, setText] = useState('');
  const [isDeleting, setIsDeleting] = useState(false);

  useEffect(() => {
    const currentFullText = texts[textIndex];
    const typingSpeed = isDeleting ? 30 : 80;

    if (!isDeleting && text === currentFullText) {
      const id = setTimeout(() => setIsDeleting(true), 2000);
      return () => clearTimeout(id);
    } else if (isDeleting && text === '') {
      setIsDeleting(false);
      setTextIndex((prev) => (prev + 1) % texts.length);
      return;
    }

    const id = setTimeout(() => {
      setText(currentFullText.substring(0, text.length + (isDeleting ? -1 : 1)));
    }, typingSpeed);

    return () => clearTimeout(id);
  }, [text, isDeleting, textIndex, texts]);

  return (
    <span className="accent-word">{text}<span style={{ animation: 'typewriter-blink 1s steps(1) infinite', fontWeight: 300 }}>|</span></span>
  );
};

/* ── Circuit SVG Background ─────────────────────────────────────── */
const CircuitBackground = () => (
  <div className="circuit-bg">
    <svg viewBox="0 0 1200 800" fill="none" xmlns="http://www.w3.org/2000/svg">
      <line x1="0" y1="100" x2="400" y2="100" />
      <line x1="400" y1="100" x2="450" y2="150" />
      <line x1="200" y1="200" x2="800" y2="200" />
      <line x1="600" y1="200" x2="650" y2="250" />
      <line x1="100" y1="400" x2="600" y2="400" />
      <line x1="300" y1="500" x2="900" y2="500" />
      <line x1="0" y1="650" x2="500" y2="650" />
      <line x1="600" y1="300" x2="1200" y2="300" />
      <circle cx="400" cy="100" r="4" fill="var(--accent)" opacity="0.4" />
      <circle cx="200" cy="200" r="4" fill="var(--accent)" opacity="0.4" />
      <circle cx="600" cy="200" r="4" fill="var(--accent)" opacity="0.4" />
      <circle cx="800" cy="200" r="4" fill="var(--accent)" opacity="0.4" />
    </svg>
    <div className="orb orb-1" />
    <div className="orb orb-2" />
    <div className="orb orb-3" />
  </div>
);

export const LandingPage = ({ onAuthSuccess }: { onAuthSuccess: () => void }) => {
  const [authMode, setAuthMode] = useState<'login' | 'signup'>('signup');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [successMsg, setSuccessMsg] = useState('');

  useCursorGlow();

  const handleAuthSubmit = async (e: React.FormEvent) => {
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
        setSuccessMsg('Check your email to confirm and join the waitlist!');
      }
    } catch (err: any) {
      setError(err.message || 'Authentication failed');
    }
    setLoading(false);
  };

  const handleGoogleLogin = async () => {
    setError('');
    const { error: err } = await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: { redirectTo: window.location.origin }
    });
    if (err) setError(err.message);
  };

  return (
    <div className="landing-premium-root">
      <CircuitBackground />
      
      <div className="landing-content-wrapper">
        {/* ── Left Side: Brand Story ── */}
        <div className="landing-left-story">
          <div className="landing-logo-container">
            <div className="top-nav-brand">
              <div className="top-nav-brand-logo">A</div>
              <div className="top-nav-brand-text">
                <span className="top-nav-brand-title">AgentIC</span>
                <span className="top-nav-brand-sub">Autonomous Silicon Studio</span>
              </div>
            </div>
          </div>

          <section className="hero-premium-landing">
            <div className="hero-badge-landing">
              <span className="hero-badge-dot" />
              Autonomous Silicon Design · v3.0
            </div>

            <h1 className="hero-title">
              <TypewriterText texts={[
                "Natural Language to GDSII",
                "Autonomous Silicon Studio",
                "AgentIC Pipeline",
              ]} />
            </h1>

            <p className="hero-subtitle-landing">
              Describe any digital circuit in plain English. Our multi-agent AI writes RTL, 
              verifies logic, and generates fabrication-ready layouts (GDSII) automatically.
            </p>

            <div className="landing-hero-features">
              <div className="landing-h-feature">
                <Shield size={18} className="landing-h-icon" />
                <span>Fail-Safe Autonomy</span>
              </div>
              <div className="landing-h-feature">
                <ArrowRight size={18} className="landing-h-icon" />
                <span>15-Stage Pipeline</span>
              </div>
            </div>

            <div className="landing-pdk-badge">
              <span>Optimized for <strong>Sky130 PDK</strong></span>
            </div>
          </section>
        </div>

        {/* ── Right Side: Combined Auth/Waitlist Form ── */}
        <div className="landing-right-auth">
          <div className="auth-card-premium">
            <h2 className="auth-card-title">
              {authMode === 'signup' ? 'Join the Waitlist' : 'Welcome Back'}
            </h2>
            <p className="auth-card-subtitle">
              {authMode === 'signup' 
                ? 'Register to secure your position in early access.'
                : 'Sign in to access your autonomous workspace.'}
            </p>

            <form className="auth-form-integrated" onSubmit={handleAuthSubmit}>
              <div className="auth-input-group">
                <label>Email Address</label>
                <input 
                  type="email" 
                  placeholder="you@company.com"
                  value={email}
                  onChange={e => setEmail(e.target.value)}
                  required 
                />
              </div>

              <div className="auth-input-group">
                <label>Password</label>
                <input 
                  type="password" 
                  placeholder="••••••••"
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  required 
                />
              </div>

              {error && <div className="auth-error-msg">{error}</div>}
              {successMsg && <div className="auth-success-msg">{successMsg}</div>}

              <button type="submit" className="auth-submit-btn-premium" disabled={loading}>
                {loading ? <span className="spinner-auth" /> : (authMode === 'signup' ? 'Secure My Spot' : 'Login')}
              </button>
            </form>

            <div className="auth-divider-landing">
              <span>OR</span>
            </div>

            <button className="google-auth-btn-landing" onClick={handleGoogleLogin}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
                <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
                <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
                <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
              </svg>
              Continue with Google
            </button>

            <div className="auth-mode-toggle">
              {authMode === 'signup' ? (
                <span>Already on the list? <button onClick={() => setAuthMode('login')}>Sign in</button></span>
              ) : (
                <span>New here? <button onClick={() => setAuthMode('signup')}>Join the waitlist</button></span>
              )}
            </div>
          </div>
        </div>
      </div>

      <footer className="landing-footer-minimal">
        <p>© 2026 AgentIC Autonomous Systems · Built for Silicon Pioneers</p>
      </footer>
    </div>
  );
};

