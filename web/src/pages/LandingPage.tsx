import { useState, useEffect } from 'react';
import { Shield, ArrowRight } from 'lucide-react';
import { useCursorGlow } from '../utils/useAnimations';
import { supabase } from '../supabaseClient';
import { motion } from 'framer-motion';

/* ── Typewriter ─────────────────────────────────────────────────── */
const TypewriterText = ({ texts }: { texts: string[] }) => {
  const [textIndex, setTextIndex] = useState(0);
  const [text, setText] = useState('');
  const [isDeleting, setIsDeleting] = useState(false);

  useEffect(() => {
    const tick = () => {
      const currentFullText = texts[textIndex];

      if (!isDeleting && text === currentFullText) {
        setTimeout(() => setIsDeleting(true), 2000);
      } else if (isDeleting && text === '') {
        setIsDeleting(false);
        setTextIndex((prev) => (prev + 1) % texts.length);
      } else {
        setText(currentFullText.substring(0, text.length + (isDeleting ? -1 : 1)));
      }
    };
    const timer = setTimeout(tick, isDeleting ? 30 : 80);
    return () => clearTimeout(timer);
  });

  return (
    <span className="accent-word">{text}<span className="typewriter-cursor">|</span></span>
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
      <circle cx="400" cy="100" r="4" fill="#c18a73" opacity="0.4" />
      <circle cx="200" cy="200" r="4" fill="#c18a73" opacity="0.4" />
      <circle cx="600" cy="200" r="4" fill="#c18a73" opacity="0.4" />
      <circle cx="800" cy="200" r="4" fill="#c18a73" opacity="0.4" />
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
      <style>{`
        .landing-premium-root {
          min-height: 100vh;
          background: #0B0B0B;
          position: relative;
          overflow: hidden;
          display: flex;
          align-items: center;
          justify-content: center;
          color: #fff;
          font-family: 'Inter', sans-serif;
        }
        .landing-content-wrapper {
          display: grid;
          grid-template-columns: 1.2fr 1fr;
          width: 100%;
          max-width: 1400px;
          position: relative;
          z-index: 10;
          padding: 4rem;
          gap: 6rem;
          align-items: center;
        }
        .hero-title {
          font-size: 4.5rem;
          font-weight: 800;
          line-height: 1.05;
          letter-spacing: -0.04em;
          margin-bottom: 1.8rem;
          background: linear-gradient(135deg, #fff 0%, #a0a0a0 100%);
          -webkit-background-clip: text;
          -webkit-text-fill-color: transparent;
        }
        .accent-word {
          background: linear-gradient(135deg, #c18a73 0%, #d4a18b 100%);
          -webkit-background-clip: text;
          -webkit-text-fill-color: transparent;
        }
        .auth-card-premium {
          width: 100%;
          max-width: 440px;
          background: rgba(255, 255, 255, 0.03);
          backdrop-filter: blur(40px);
          border: 1px solid rgba(255, 255, 255, 0.1);
          border-radius: 32px;
          padding: 3rem;
          box-shadow: 0 40px 100px rgba(0, 0, 0, 0.5);
        }
        .auth-input-group input {
          width: 100%;
          background: rgba(255, 255, 255, 0.05);
          border: 1px solid rgba(255, 255, 255, 0.1);
          border-radius: 14px;
          padding: 1.1rem 1.25rem;
          color: #fff;
          font-size: 1rem;
          outline: none;
          transition: all 0.2s ease;
        }
        .auth-input-group input:focus {
          border-color: #c18a73;
          background: rgba(255, 255, 255, 0.08);
          box-shadow: 0 0 0 4px rgba(193, 138, 115, 0.2);
        }
        .auth-submit-btn-premium {
          width: 100%;
          background: #c18a73;
          color: #fff;
          border: none;
          border-radius: 14px;
          padding: 1.2rem;
          font-size: 1.1rem;
          font-weight: 700;
          cursor: pointer;
          transition: all 0.2s ease;
          margin-top: 1rem;
        }
        .auth-submit-btn-premium:hover {
          background: #d4a18b;
          transform: translateY(-2px);
          box-shadow: 0 10px 30px rgba(193, 138, 115, 0.4);
        }
        .google-auth-btn-landing {
          width: 100%;
          background: #fff;
          color: #000;
          border: none;
          border-radius: 14px;
          padding: 1.1rem;
          font-size: 1rem;
          font-weight: 600;
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 0.75rem;
          cursor: pointer;
          transition: all 0.2s ease;
        }
        .google-auth-btn-landing:hover {
          background: #f0f0f0;
          transform: translateY(-2px);
        }
        .circuit-bg {
          position: absolute;
          inset: 0;
          pointer-events: none;
          opacity: 0.3;
        }
        .circuit-bg svg {
          width: 100%;
          height: 100%;
          stroke: rgba(255, 255, 255, 0.1);
        }
        .orb {
          position: absolute;
          border-radius: 50%;
          filter: blur(120px);
        }
        .orb-1 {
          top: -10%;
          right: -5%;
          width: 600px;
          height: 600px;
          background: radial-gradient(circle, #c18a7333 0%, transparent 70%);
        }
        .orb-2 {
          bottom: -20%;
          left: -10%;
          width: 800px;
          height: 800px;
          background: radial-gradient(circle, #3a78561a 0%, transparent 70%);
        }
        .typewriter-cursor {
          animation: blink 1s steps(1) infinite;
        }
        @keyframes blink {
          50% { opacity: 0; }
        }
        @media (max-width: 1080px) {
          .landing-content-wrapper {
            grid-template-columns: 1fr;
            padding: 2rem;
            text-align: center;
          }
          .hero-title {
            font-size: 3rem;
          }
        }
      `}</style>
      
      <CircuitBackground />
      
      <motion.div 
        className="landing-content-wrapper"
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.8 }}
      >
        {/* ── Left Side: Brand Story ── */}
        <div className="landing-left-story">
          <div className="landing-logo-container">
            <div className="top-nav-brand" style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
              <div className="top-nav-brand-logo" style={{ width: 40, height: 40, background: '#c18a73', borderRadius: 8, display: 'grid', placeItems: 'center', fontWeight: 'bold' }}>A</div>
              <div style={{ display: 'flex', flexDirection: 'column' }}>
                <span style={{ fontSize: '1.4rem', fontWeight: 800 }}>AgentIC</span>
                <span style={{ fontSize: '0.8rem', opacity: 0.6, letterSpacing: '0.1em' }}>Autonomous Silicon Studio</span>
              </div>
            </div>
          </div>

          <section className="hero-premium-landing">
            <div style={{ display: 'inline-flex', alignItems: 'center', gap: '0.6rem', padding: '0.4rem 1rem', background: '#ffffff1a', borderRadius: 100, fontSize: '0.8rem', fontWeight: 600, color: '#d4a18b', marginBottom: '2rem' }}>
              <div style={{ width: 6, height: 6, background: '#c18a73', borderRadius: '50%', boxShadow: '0 0 10px #c18a73' }} />
              Autonomous Silicon Design · v3.0
            </div>

            <h1 className="hero-title">
              <TypewriterText texts={[
                "Natural Language to GDSII",
                "Autonomous Silicon Studio",
                "AgentIC Pipeline",
              ]} />
            </h1>

            <p style={{ fontSize: '1.3rem', color: '#ffffff99', lineHeight: 1.6, marginBottom: '2.5rem', maxWidth: 600 }}>
              Describe any digital circuit in plain English. Our multi-agent AI writes RTL, 
              verifies logic, and generates fabrication-ready layouts (GDSII) automatically.
            </p>

            <div style={{ display: 'flex', gap: '3rem', marginBottom: '3rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', fontWeight: 500, color: '#ffffffcc' }}>
                <Shield size={20} color="#c18a73" />
                <span>Fail-Safe Autonomy</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', fontWeight: 500, color: '#ffffffcc' }}>
                <ArrowRight size={20} color="#c18a73" />
                <span>15-Stage Pipeline</span>
              </div>
            </div>

            <div style={{ display: 'inline-block', padding: '0.7rem 1.5rem', background: '#ffffff08', border: '1px solid #ffffff14', borderRadius: 12, fontSize: '0.9rem', color: '#ffffff80' }}>
              Optimized for <strong>Sky130 PDK</strong>
            </div>
          </section>
        </div>

        {/* ── Right Side: Combined Auth/Waitlist Form ── */}
        <div className="landing-right-auth">
          <motion.div 
            className="auth-card-premium"
            initial={{ scale: 0.95, opacity: 0 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: 0.2, duration: 0.5 }}
          >
            <h2 style={{ fontSize: '2.2rem', fontWeight: 700, marginBottom: '0.5rem' }}>
              {authMode === 'signup' ? 'Join the Waitlist' : 'Welcome Back'}
            </h2>
            <p style={{ color: '#ffffff80', fontSize: '1.05rem', marginBottom: '2.5rem' }}>
              {authMode === 'signup' 
                ? 'Register to secure your position in early access.'
                : 'Sign in to access your autonomous workspace.'}
            </p>

            <form className="auth-form-integrated" onSubmit={handleAuthSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
              <div className="auth-input-group" style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
                <label style={{ fontSize: '0.9rem', fontWeight: 600, color: '#ffffffb3' }}>Email Address</label>
                <input 
                  type="email" 
                  placeholder="you@company.com"
                  value={email}
                  onChange={e => setEmail(e.target.value)}
                  required 
                />
              </div>

              <div className="auth-input-group" style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
                <label style={{ fontSize: '0.9rem', fontWeight: 600, color: '#ffffffb3' }}>Password</label>
                <input 
                  type="password" 
                  placeholder="••••••••"
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  required 
                />
              </div>

              {error && <div style={{ background: '#b8303026', border: '1px solid #b830304d', color: '#ff9999', padding: '0.8rem', borderRadius: 10, fontSize: '0.9rem' }}>{error}</div>}
              {successMsg && <div style={{ background: '#3a785626', border: '1px solid #3a78564d', color: '#a8ffd0', padding: '0.8rem', borderRadius: 10, fontSize: '0.9rem' }}>{successMsg}</div>}

              <button type="submit" className="auth-submit-btn-premium">
                {loading ? <span className="spinner-auth" style={{ width: 22, height: 22, border: '3px solid #ffffff4d', borderTopColor: '#fff', borderRadius: '50%', display: 'inline-block', animation: 'blink 1s infinite' }} /> : (authMode === 'signup' ? 'Secure My Spot' : 'Login')}
              </button>
            </form>

            <div style={{ display: 'flex', alignItems: 'center', gap: '1.5rem', margin: '2rem 0' }}>
              <div style={{ flex: 1, height: 1, background: '#ffffff14' }} />
              <span style={{ fontSize: '0.8rem', fontWeight: 700, color: '#ffffff4d', letterSpacing: '0.15em' }}>OR</span>
              <div style={{ flex: 1, height: 1, background: '#ffffff14' }} />
            </div>

            <button className="google-auth-btn-landing" onClick={handleGoogleLogin}>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
                <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
                <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
                <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
                <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
              </svg>
              Continue with Google
            </button>

            <div style={{ marginTop: '2rem', textAlign: 'center', fontSize: '0.95rem', color: '#ffffff66' }}>
              {authMode === 'signup' ? (
                <span>Already on the list? <button style={{ background: 'none', border: 'none', color: '#d4a18b', fontWeight: 600, textDecoration: 'underline', cursor: 'pointer', marginLeft: 6 }} onClick={() => setAuthMode('login')}>Sign in</button></span>
              ) : (
                <span>New here? <button style={{ background: 'none', border: 'none', color: '#d4a18b', fontWeight: 600, textDecoration: 'underline', cursor: 'pointer', marginLeft: 6 }} onClick={() => setAuthMode('signup')}>Join the waitlist</button></span>
              )}
            </div>
          </motion.div>
        </div>
      </motion.div>

      <footer style={{ position: 'absolute', bottom: '2rem', width: '100%', textAlign: 'center', color: '#ffffff33', fontSize: '0.85rem' }}>
        <p>© 2026 AgentIC Autonomous Systems · Built for Silicon Pioneers</p>
      </footer>
    </div>
  );
};

