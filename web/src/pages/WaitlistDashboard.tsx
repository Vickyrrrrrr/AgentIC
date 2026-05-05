import { supabase } from '../supabaseClient';
import { motion } from 'framer-motion';

export const WaitlistDashboard = ({ email }: { email: string }) => {
  const handleLogout = async () => {
    await supabase.auth.signOut();
    window.location.reload();
  };

  return (
    <div className="waitlist-root">
      <div className="landing-grid-bg" />

      <motion.div
        className="waitlist-card"
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6, ease: [0.25, 0.46, 0.45, 0.94] }}
      >
        <div className="waitlist-card-inner">
          {/* Status */}
          <div className="waitlist-status">
            <span className="waitlist-dot" />
            <span className="waitlist-status-text">Waitlist confirmed</span>
          </div>

          {/* Heading */}
          <h1 className="waitlist-heading">You&rsquo;re on the&nbsp;list.</h1>

          {/* Email */}
          <p className="waitlist-body">
            We&rsquo;ve registered{' '}
            <span className="waitlist-email">{email}</span>
            . Access to the Design Studio is rolling out in&nbsp;cohorts.
            We&rsquo;ll notify you when your workspace is&nbsp;ready.
          </p>

          {/* Steps */}
          <div className="waitlist-steps">
            <div className="waitlist-step">
              <div className="waitlist-step-marker waitlist-step-marker--done">
                <svg width="14" height="10" viewBox="0 0 14 10" fill="none">
                  <path d="M1 4.5L5 8.5L13 1" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </div>
              <div className="waitlist-step-content">
                <div className="waitlist-step-title">Verify your email</div>
                <div className="waitlist-step-desc">Check your inbox for a confirmation&nbsp;link.</div>
              </div>
            </div>

            <div className="waitlist-step">
              <div className="waitlist-step-marker">2</div>
              <div className="waitlist-step-content">
                <div className="waitlist-step-title">Wait for approval</div>
                <div className="waitlist-step-desc">We onboard cohorts daily to ensure stability.</div>
              </div>
            </div>

            <div className="waitlist-step">
              <div className="waitlist-step-marker">3</div>
              <div className="waitlist-step-content">
                <div className="waitlist-step-title">Open the Studio</div>
                <div className="waitlist-step-desc">Your workspace unlocks automatically once&nbsp;approved.</div>
              </div>
            </div>
          </div>

          {/* Actions */}
          <div className="waitlist-actions">
            <a
              href="https://www.buildstack.live"
              target="_blank"
              rel="noreferrer"
              className="waitlist-btn waitlist-btn--ghost"
            >
              Documentation
            </a>
            <button
              onClick={handleLogout}
              className="waitlist-btn waitlist-btn--primary"
            >
              Sign out
            </button>
          </div>
        </div>
      </motion.div>
    </div>
  );
};
