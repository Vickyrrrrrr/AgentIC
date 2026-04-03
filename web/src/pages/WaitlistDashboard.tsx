import { LogOut, CheckCircle, Bell, ShieldCheck } from 'lucide-react';
import { supabase } from '../supabaseClient';
import { useCursorGlow } from '../utils/useAnimations';

export const WaitlistDashboard = ({ email }: { email: string }) => {
  useCursorGlow();

  const handleLogout = async () => {
    await supabase.auth.signOut();
    window.location.reload();
  };

  return (
    <div className="waitlist-root">
      <div className="waitlist-glass">
        <div className="waitlist-badge">
          <span className="waitlist-badge-dot" />
          Waitlist Status: Confirmed
        </div>

        <div className="waitlist-icon">
          <CheckCircle size={64} className="waitlist-check" />
        </div>

        <h1 className="waitlist-title">You're on the list!</h1>
        
        <p className="waitlist-message">
          Welcome to the future of autonomous silicon design. We've registered your account (<strong>{email}</strong>) and added you to the AgentIC early access waitlist.
        </p>

        <div className="waitlist-steps">
          <div className="waitlist-step">
            <div className="waitlist-step-num">1</div>
            <div className="waitlist-step-content">
              <div className="waitlist-step-title">Verify Email</div>
              <div className="waitlist-step-desc">If you used email signup, confirm your address to stay active.</div>
            </div>
          </div>
          <div className="waitlist-step">
            <div className="waitlist-step-num">2</div>
            <div className="waitlist-step-content">
              <div className="waitlist-step-title">Wait for Invitation</div>
              <div className="waitlist-step-desc">We are onboarding users in cohorts to ensure system stability.</div>
            </div>
          </div>
          <div className="waitlist-step">
            <div className="waitlist-step-num">3</div>
            <div className="waitlist-step-content">
              <div className="waitlist-step-title">Enter Studio</div>
              <div className="waitlist-step-desc">Once approved, you'll have full access to our multi-agent pipeline.</div>
            </div>
          </div>
        </div>

        <div className="waitlist-actions">
          <button className="waitlist-btn-secondary" onClick={() => window.open('https://docs.agentic.ai')}>
            <Bell size={16} /> Read the Docs
          </button>
          <button className="waitlist-btn-primary" onClick={handleLogout}>
            <LogOut size={16} /> Logout
          </button>
        </div>

        <div className="waitlist-footer">
          <ShieldCheck size={14} /> <span>Your account is secured. No further sign-up required.</span>
        </div>
      </div>
      
      {/* Decorative background elements */}
      <div className="waitlist-orb orb-1" />
      <div className="waitlist-orb orb-2" />
    </div>
  );
};
