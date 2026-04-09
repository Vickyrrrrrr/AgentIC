import { LogOut, Mail, BookOpen, ShieldCheck } from 'lucide-react';
import { supabase } from '../supabaseClient';
import { motion } from 'framer-motion';

const STEPS = [
  {
    num: 1,
    title: 'Verify Your Email',
    desc: 'Check your inbox for a confirmation link to activate your account.',
    done: true,
  },
  {
    num: 2,
    title: 'Wait for Invitation',
    desc: "We onboard new cohorts weekly. You will be notified when your workspace is ready.",
    done: false,
  },
  {
    num: 3,
    title: 'Enter the Studio',
    desc: 'Once approved, your full workspace unlocks automatically — no extra steps needed.',
    done: false,
  },
];

export const WaitlistDashboard = ({ email }: { email: string }) => {
  const handleLogout = async () => {
    await supabase.auth.signOut();
    window.location.reload();
  };

  return (
    <div className="waitlist-page">
      <div className="waitlist-page-grid" />

      <motion.div
        className="waitlist-card"
        initial={{ opacity: 0, y: 24 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: [0.25, 0.46, 0.45, 0.94] }}
      >
        {/* Status badge */}
        <motion.div
          className="waitlist-status"
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
        >
          <span className="waitlist-status-dot" />
          Waitlist Confirmed
        </motion.div>

        {/* Icon */}
        <motion.div
          className="waitlist-icon"
          initial={{ scale: 0 }}
          animate={{ scale: 1 }}
          transition={{ type: 'spring', stiffness: 200, damping: 20, delay: 0.3 }}
        >
          <Mail size={48} strokeWidth={1.2} />
        </motion.div>

        {/* Title */}
        <motion.h1
          className="waitlist-title"
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.35, duration: 0.4 }}
        >
          You're on the list
        </motion.h1>

        <motion.p
          className="waitlist-body"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.45, duration: 0.5 }}
        >
          We've registered <strong>{email}</strong> for early access to AgentIC.
          You'll receive an email when your workspace is ready.
        </motion.p>

        {/* Timeline */}
        <div className="waitlist-timeline">
          {STEPS.map((step, idx) => (
            <motion.div
              key={step.num}
              className={`waitlist-step${step.done ? ' waitlist-step--done' : ''}`}
              initial={{ opacity: 0, x: -16 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.55 + idx * 0.1, type: 'spring', stiffness: 120 }}
            >
              <div className="waitlist-step-indicator">
                <div className="waitlist-step-num">{step.done ? '✓' : step.num}</div>
                {idx < STEPS.length - 1 && <div className="waitlist-step-line" />}
              </div>
              <div className="waitlist-step-content">
                <div className="waitlist-step-title">{step.title}</div>
                <div className="waitlist-step-desc">{step.desc}</div>
              </div>
            </motion.div>
          ))}
        </div>

        {/* Actions */}
        <motion.div
          className="waitlist-actions"
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.9, duration: 0.4 }}
        >
          <a
            className="waitlist-btn waitlist-btn--secondary"
            href="https://www.buildstack.live"
            target="_blank"
            rel="noopener noreferrer"
          >
            <BookOpen size={16} />
            Documentation
          </a>
          <button className="waitlist-btn waitlist-btn--primary" onClick={handleLogout}>
            <LogOut size={16} />
            Sign Out
          </button>
        </motion.div>

        {/* Footer */}
        <motion.div
          className="waitlist-footer"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 1.1 }}
        >
          <ShieldCheck size={14} />
          <span>Secured by AgentIC Identity Protocol</span>
        </motion.div>
      </motion.div>
    </div>
  );
};
