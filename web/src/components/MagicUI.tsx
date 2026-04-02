import { useEffect, useRef, useState, type ReactNode } from 'react';

/* ── Animated Border Gradient (magic-ui inspired) ──────────────── */
export const BorderBeam = ({ children, className = '', duration = 4 }: { children: ReactNode; className?: string; duration?: number }) => (
  <div className={`border-beam-wrapper ${className}`} style={{ '--beam-duration': `${duration}s` } as React.CSSProperties}>
    <div className="border-beam-content">{children}</div>
  </div>
);

/* ── Marquee (magic-ui style) ──────────────────────────────────── */
export const Marquee = ({ children, speed = 40, reverse = false, className = '' }: {
  children: ReactNode; speed?: number; reverse?: boolean; className?: string;
}) => (
  <div className={`marquee-container ${className}`}>
    <div className="marquee-track" style={{
      animationDuration: `${speed}s`,
      animationDirection: reverse ? 'reverse' : 'normal',
    }}>
      {children}
      {children}
    </div>
  </div>
);

/* ── Meteors Background (magic-ui inspired) ────────────────────── */
export const Meteors = ({ count = 12 }: { count?: number }) => (
  <div className="meteors-container">
    {Array.from({ length: count }).map((_, i) => (
      <span key={i} className="meteor" style={{
        top: `${Math.random() * 100}%`,
        left: `${Math.random() * 100}%`,
        animationDelay: `${Math.random() * 5}s`,
        animationDuration: `${2 + Math.random() * 3}s`,
      }} />
    ))}
  </div>
);

/* ── Animated Number Ticker ────────────────────────────────────── */
export const NumberTicker = ({ value, suffix = '', duration = 1500 }: { value: number; suffix?: string; duration?: number }) => {
  const [current, setCurrent] = useState(0);
  const ref = useRef<HTMLSpanElement>(null);
  const started = useRef(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting && !started.current) {
        started.current = true;
        let startTime: number;
        const animate = (ts: number) => {
          if (!startTime) startTime = ts;
          const progress = Math.min((ts - startTime) / duration, 1);
          const eased = 1 - Math.pow(1 - progress, 3);
          setCurrent(Math.round(eased * value));
          if (progress < 1) requestAnimationFrame(animate);
        };
        requestAnimationFrame(animate);
      }
    }, { threshold: 0.3 });

    observer.observe(el);
    return () => observer.disconnect();
  }, [value, duration]);

  return <span ref={ref} className="number-ticker">{current}{suffix}</span>;
};

/* ── Spotlight Card (hover spotlight effect) ───────────────────── */
export const SpotlightCard = ({ children, className = '' }: { children: ReactNode; className?: string }) => {
  const ref = useRef<HTMLDivElement>(null);

  const handleMouseMove = (e: React.MouseEvent) => {
    const el = ref.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    el.style.setProperty('--spotlight-x', `${e.clientX - rect.left}px`);
    el.style.setProperty('--spotlight-y', `${e.clientY - rect.top}px`);
  };

  return (
    <div ref={ref} className={`spotlight-card ${className}`} onMouseMove={handleMouseMove}>
      {children}
    </div>
  );
};

/* ── Dock Nav Item (macOS dock style magnify) ──────────────────── */
export const DockItem = ({ icon, label, active, onClick }: {
  icon: ReactNode; label: string; active: boolean; onClick: () => void;
}) => (
  <button className={`dock-item ${active ? 'dock-item-active' : ''}`} onClick={onClick} title={label}>
    <span className="dock-item-icon">{icon}</span>
    <span className="dock-item-label">{label}</span>
    {active && <span className="dock-item-indicator" />}
  </button>
);

/* ── Shimmer Button ────────────────────────────────────────────── */
export const ShimmerButton = ({ children, onClick, disabled, className = '' }: {
  children: ReactNode; onClick?: () => void; disabled?: boolean; className?: string;
}) => (
  <button className={`shimmer-btn ${className}`} onClick={onClick} disabled={disabled}>
    <span className="shimmer-btn-content">{children}</span>
  </button>
);

/* ── Bento Grid ────────────────────────────────────────────────── */
export const BentoGrid = ({ children, className = '' }: { children: ReactNode; className?: string }) => (
  <div className={`bento-grid ${className}`}>{children}</div>
);

export const BentoCard = ({ children, className = '', span = 1 }: { children: ReactNode; className?: string; span?: number }) => (
  <div className={`bento-card ${className}`} style={{ gridColumn: `span ${span}` }}>{children}</div>
);

/* ── Pulsating Dot ─────────────────────────────────────────────── */
export const PulseDot = ({ color = 'var(--success)' }: { color?: string }) => (
  <span className="pulse-dot" style={{ '--pulse-color': color } as React.CSSProperties}>
    <span className="pulse-dot-inner" />
    <span className="pulse-dot-ring" />
  </span>
);

/* ── Typing Animation ──────────────────────────────────────────── */
export const TypingDots = () => (
  <span className="typing-dots">
    <span className="typing-dot" />
    <span className="typing-dot" />
    <span className="typing-dot" />
  </span>
);

/* ── Animated Gradient Badge ───────────────────────────────────── */
export const GradientBadge = ({ children }: { children: ReactNode }) => (
  <span className="gradient-badge">{children}</span>
);
