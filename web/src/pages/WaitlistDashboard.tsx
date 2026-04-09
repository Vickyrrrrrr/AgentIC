import { LogOut, CheckCircle, Bell, ShieldCheck } from 'lucide-react';
import { supabase } from '../supabaseClient';
import { useCursorGlow } from '../utils/useAnimations';
import { motion } from 'framer-motion';

export const WaitlistDashboard = ({ email }: { email: string }) => {
  useCursorGlow();

  const handleLogout = async () => {
    await supabase.auth.signOut();
    window.location.reload();
  };

  return (
    <div className="waitlist-root">
      <style>{`
        .waitlist-root {
          min-height: 100vh;
          background: #0B0B0B;
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 2rem;
          position: relative;
          overflow: hidden;
          color: #fff;
          font-family: 'Inter', sans-serif;
        }
        .waitlist-glass {
          width: 100%;
          max-width: 640px;
          background: rgba(255, 255, 255, 0.03);
          backdrop-filter: blur(40px);
          border: 1px solid rgba(255, 255, 255, 0.1);
          border-radius: 40px;
          padding: 4rem;
          text-align: center;
          position: relative;
          z-index: 10;
          box-shadow: 0 40px 120px rgba(0, 0, 0, 0.6);
        }
        .waitlist-badge {
          display: inline-flex;
          align-items: center;
          gap: 0.6rem;
          padding: 0.5rem 1.25rem;
          background: rgba(58, 120, 86, 0.15);
          border: 1px solid rgba(58, 120, 86, 0.3);
          border-radius: 100px;
          font-size: 0.8rem;
          font-weight: 700;
          color: #61b88b;
          margin-bottom: 2.5rem;
        }
        .waitlist-badge-dot {
          width: 8px;
          height: 8px;
          background: #61b88b;
          border-radius: 50%;
          box-shadow: 0 0 12px #61b88b;
        }
        .waitlist-title {
          font-size: 3.2rem;
          font-weight: 800;
          margin-bottom: 1.5rem;
          letter-spacing: -0.04em;
          background: linear-gradient(135deg, #fff 0%, #a0a0a0 100%);
          -webkit-background-clip: text;
          -webkit-text-fill-color: transparent;
        }
        .waitlist-message {
          font-size: 1.2rem;
          color: rgba(255, 255, 255, 0.6);
          line-height: 1.6;
          margin-bottom: 3.5rem;
        }
        .waitlist-step {
          display: flex;
          gap: 1.5rem;
          padding: 1.5rem;
          background: rgba(255, 255, 255, 0.04);
          border: 1px solid rgba(255, 255, 255, 0.06);
          border-radius: 20px;
          text-align: left;
          margin-bottom: 1rem;
          transition: all 0.3s ease;
        }
        .waitlist-step:hover {
          background: rgba(255, 255, 255, 0.08);
          transform: translateX(8px);
        }
        .waitlist-step-num {
          width: 36px;
          height: 36px;
          border-radius: 50%;
          background: #c18a73;
          display: grid;
          place-items: center;
          font-weight: 800;
          flex-shrink: 0;
        }
        .waitlist-btn-primary {
          background: #c18a73;
          color: #fff;
          border: none;
          border-radius: 14px;
          padding: 1.1rem 2.5rem;
          font-weight: 700;
          cursor: pointer;
          display: flex;
          align-items: center;
          gap: 0.75rem;
          transition: all 0.2s ease;
        }
        .waitlist-btn-primary:hover {
          background: #d4a18b;
          transform: translateY(-2px);
          box-shadow: 0 10px 30px rgba(193, 138, 115, 0.4);
        }
        .orb {
          position: absolute;
          border-radius: 50%;
          filter: blur(120px);
          z-index: 1;
        }
        .orb-1 {
          top: -10%;
          right: -5%;
          width: 600px;
          height: 600px;
          background: radial-gradient(circle, #c18a7322 0%, transparent 70%);
        }
        .orb-2 {
          bottom: -20%;
          left: -10%;
          width: 800px;
          height: 800px;
          background: radial-gradient(circle, #3a785611 0%, transparent 70%);
        }
      `}</style>

      <motion.div 
        className="waitlist-glass"
        initial={{ opacity: 0, y: 50, scale: 0.95 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
      >
        <motion.div 
          className="waitlist-badge"
          initial={{ opacity: 0, y: -20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3, duration: 0.5 }}
        >
          <div className="waitlist-badge-dot" />
          Waitlist Status: Confirmed
        </motion.div>

        <motion.div 
          style={{ marginBottom: '2rem', color: '#c18a73', display: 'flex', justifyContent: 'center' }}
          initial={{ scale: 0, rotate: -180 }}
          animate={{ scale: 1, rotate: 0 }}
          transition={{ type: "spring", stiffness: 200, damping: 20, delay: 0.4 }}
        >
          <CheckCircle size={90} strokeWidth={1.5} />
        </motion.div>

        <motion.h1 
          className="waitlist-title"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.5, duration: 0.6 }}
        >
          You're on the list!
        </motion.h1>
        
        <motion.p 
          className="waitlist-message"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.7, duration: 0.8 }}
        >
          Welcome to the future of autonomous silicon design. We've registered your account (<strong style={{color: '#fff'}}>{email}</strong>) and added you to the AgentIC early access waitlist.
        </motion.p>

        <div style={{ marginBottom: '4rem' }}>
          {[
            { n: 1, t: 'Verify Email', d: 'Check your inbox for a confirmation link to stay active.' },
            { n: 2, t: 'Wait for Invitation', d: 'We are onboarding cohorts daily to ensure system stability.' },
            { n: 3, t: 'Enter the Studio', d: 'Once approved, your workspace will unlock automatically.' }
          ].map((step, idx) => (
            <motion.div 
              key={step.n} 
              className="waitlist-step"
              initial={{ opacity: 0, x: -30 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.9 + (idx * 0.15), type: "spring", stiffness: 100 }}
            >
              <div className="waitlist-step-num">{step.n}</div>
              <div>
                <div style={{ fontWeight: 700, fontSize: '1.1rem', marginBottom: '0.2rem' }}>{step.t}</div>
                <div style={{ fontSize: '0.9rem', color: '#ffffff66' }}>{step.d}</div>
              </div>
            </motion.div>
          ))}
        </div>

        <motion.div 
          style={{ display: 'flex', gap: '1.5rem', justifyContent: 'center' }}
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 1.5, duration: 0.5 }}
        >
          <button style={{ background: '#ffffff0d', border: '1px solid #ffffff1a', color: '#fff', borderRadius: 14, padding: '1.1rem 2rem', fontWeight: 600, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '0.75rem', transition: 'all 0.2s ease' }} onClick={() => window.open('https://www.buildstack.live')} onMouseOver={(e) => e.currentTarget.style.background = '#ffffff1a'} onMouseOut={(e) => e.currentTarget.style.background = '#ffffff0d'}>
            <Bell size={18} /> Documentation
          </button>
          <button className="waitlist-btn-primary" onClick={handleLogout}>
            <LogOut size={18} /> Logout
          </button>
        </motion.div>

        <motion.div 
          style={{ marginTop: '3.5rem', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.6rem', fontSize: '0.85rem', color: '#ffffff33' }}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 1.8, duration: 1 }}
        >
          <ShieldCheck size={16} /> <span>Secured by AgentIC Identity Protocol</span>
        </motion.div>
      </motion.div>
      
      <div className="orb orb-1" />
      <div className="orb orb-2" />
    </div>
  );
};

