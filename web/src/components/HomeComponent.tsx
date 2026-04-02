import { useState, useEffect } from 'react';
import { Activity, BookOpen, Cpu, Layers, GitMerge, Zap, Shield, Workflow } from 'lucide-react';
import { useRevealOnScroll, useCountUp, useTilt3D } from '../utils/useAnimations';

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
      {/* Horizontal traces */}
      <line x1="0" y1="100" x2="400" y2="100" />
      <line x1="200" y1="200" x2="800" y2="200" />
      <line x1="100" y1="400" x2="600" y2="400" />
      <line x1="300" y1="500" x2="900" y2="500" />
      <line x1="0" y1="650" x2="500" y2="650" />
      <line x1="600" y1="300" x2="1200" y2="300" />
      <line x1="700" y1="600" x2="1100" y2="600" />
      <line x1="400" y1="700" x2="1200" y2="700" />
      {/* Vertical traces */}
      <line x1="400" y1="100" x2="400" y2="400" />
      <line x1="600" y1="200" x2="600" y2="500" />
      <line x1="800" y1="200" x2="800" y2="500" />
      <line x1="900" y1="300" x2="900" y2="600" />
      <line x1="200" y1="200" x2="200" y2="650" />
      <line x1="1100" y1="300" x2="1100" y2="700" />
      {/* Junction nodes */}
      <circle cx="400" cy="100" r="4" />
      <circle cx="200" cy="200" r="4" />
      <circle cx="600" cy="200" r="4" />
      <circle cx="800" cy="200" r="4" />
      <circle cx="400" cy="400" r="4" />
      <circle cx="600" cy="400" r="4" />
      <circle cx="600" cy="500" r="4" />
      <circle cx="900" cy="500" r="4" />
      <circle cx="900" cy="300" r="4" />
      <circle cx="200" cy="650" r="4" />
      <circle cx="1100" cy="600" r="4" />
      <circle cx="800" cy="500" r="4" />
    </svg>
    <div className="orb orb-1" />
    <div className="orb orb-2" />
    <div className="orb orb-3" />
  </div>
);

/* ── Feature Card with 3D tilt ──────────────────────────────────── */
const FeatureCard = ({ icon, title, desc, delay }: { icon: React.ReactNode; title: string; desc: string; delay: number }) => {
  const { ref: tiltRef, style: tiltStyle } = useTilt3D(6);
  const { ref: revealRef, isVisible } = useRevealOnScroll(0.15);

  return (
    <div ref={revealRef} className={`reveal ${isVisible ? 'visible' : ''}`} style={{ transitionDelay: `${delay}ms` }}>
      <div ref={tiltRef} className="feature-card" style={tiltStyle}>
        <div className="feature-card-icon">{icon}</div>
        <div className="feature-card-title">{title}</div>
        <div className="feature-card-desc">{desc}</div>
      </div>
    </div>
  );
};

/* ── Stat Card with animated counter ────────────────────────────── */
const StatCard = ({ icon, target, label, suffix = '' }: { icon: React.ReactNode; target: number; label: string; suffix?: string }) => {
  const { value, ref } = useCountUp(target, 1800);
  return (
    <div ref={ref} className="stat-glass">
      <div className="stat-glass-icon">{icon}</div>
      <div className="stat-glass-value metric-animated">{value}{suffix}</div>
      <div className="stat-glass-label">{label}</div>
    </div>
  );
};

/* ── Pipeline Stage Node ────────────────────────────────────────── */
const PipelineStage = ({ icon, label }: { icon: string; label: string }) => (
  <div className="pipeline-node">
    <div className="pipeline-node-icon">{icon}</div>
    <div className="pipeline-node-label">{label}</div>
  </div>
);

/* ── Main Component ─────────────────────────────────────────────── */
export const HomeComponent = ({ designsLength, setSelectedPage }: { designsLength: number; setSelectedPage: (page: string) => void }) => {
  const features = useRevealOnScroll(0.1);
  const pipeline = useRevealOnScroll(0.1);
  const agents = useRevealOnScroll(0.1);

  const AGENTS = [
    { icon: '📐', name: 'Architect', desc: 'Decomposes specs into chip architecture with ports, FSMs, and sub-modules' },
    { icon: '💻', name: 'RTL Designer', desc: 'Writes synthesizable Verilog with collaborative design-review pattern' },
    { icon: '🧪', name: 'Verifier', desc: 'Creates testbenches, runs simulation, and diagnoses failures' },
    { icon: '🔬', name: 'Formal Prover', desc: 'Mathematically proves design correctness using assertions' },
    { icon: '🧠', name: 'Deep Debugger', desc: 'Causal failure analysis with multi-perspective reasoning' },
  ];

  const PIPELINE_GROUPS = [
    {
      title: 'Frontend Design',
      stages: [
        { icon: '📋', label: 'Init' },
        { icon: '📐', label: 'Spec' },
        { icon: '✅', label: 'Validate' },
        { icon: '🌲', label: 'Hierarchy' },
        { icon: '🔎', label: 'Feasibility' },
        { icon: '⏱️', label: 'CDC' },
        { icon: '📝', label: 'V-Plan' },
        { icon: '💻', label: 'RTL Gen' },
        { icon: '🔧', label: 'RTL Fix' },
      ],
    },
    {
      title: 'Verification',
      stages: [
        { icon: '🧪', label: 'Verify' },
        { icon: '📊', label: 'Formal' },
        { icon: '📈', label: 'Coverage' },
        { icon: '🔁', label: 'Improve TB' },
        { icon: '🔄', label: 'Regression' },
        { icon: '🧠', label: 'Deep Debug' },
        { icon: '🌊', label: 'Waveform' },
      ],
    },
    {
      title: 'Physical Design',
      stages: [
        { icon: '🕒', label: 'SDC Gen' },
        { icon: '📐', label: 'Floorplan' },
        { icon: '🏗️', label: 'Harden' },
        { icon: '🔍', label: 'Converge' },
        { icon: '🩹', label: 'ECO Patch' },
      ],
    },
    {
      title: 'Signoff',
      stages: [
        { icon: '✔️', label: 'DRC' },
        { icon: '⚡', label: 'LVS' },
        { icon: '📜', label: 'Timing' },
        { icon: '🏆', label: 'Signoff' },
      ],
    },
  ];

  return (
    <div className="home-premium">
      <CircuitBackground />

      {/* ── Hero ─────────────────────────────────────────── */}
      <section className="hero-premium">
        <div className="hero-badge">
          <span className="hero-badge-dot" />
          Core System Online · v3.0
        </div>

        <h1 className="hero-title">
          <TypewriterText texts={[
            "Natural Language to GDSII",
            "Autonomous Silicon Design",
            "AgentIC Studio",
            "Multi-Agent EDA Pipeline",
          ]} />
        </h1>

        <p className="hero-subtitle">
          The world's first autonomous chip design platform. Describe any digital circuit in 
          plain English — our multi-agent AI writes RTL, verifies logic, runs formal proofs, 
          and generates fabrication-ready GDSII in minutes.
        </p>

        <div className="hero-actions">
          <button className="hero-btn-primary" onClick={() => setSelectedPage('Design Studio')}>
            <Activity size={16} /> Enter Design Studio
          </button>
          <button className="hero-btn-secondary" onClick={() => setSelectedPage('HITL Build')}>
            <Workflow size={16} /> Human-in-Loop Mode
          </button>
          <button className="hero-btn-secondary" onClick={() => setSelectedPage('Documentation')}>
            <BookOpen size={16} /> Documentation
          </button>
        </div>

        {/* Stats */}
        <div className="stats-bar">
          <StatCard icon={<Cpu size={24} />} target={designsLength || 0} label="Active Designs" />
          <StatCard icon={<Layers size={24} />} target={26} label="Pipeline Stages" />
          <StatCard icon={<GitMerge size={24} />} target={5} label="Core Agents" />
          <StatCard icon={<Zap size={24} />} target={15} label="Min Build Time" suffix="m" />
        </div>
      </section>

      {/* ── Features ─────────────────────────────────────── */}
      <div ref={features.ref} className={`feature-grid reveal ${features.isVisible ? 'visible' : ''}`}>
        <FeatureCard
          icon="⚡"
          title="Spec to Silicon in Minutes"
          desc="26-stage autonomous pipeline transforms natural language into fabrication-ready chip layouts. No HDL expertise required."
          delay={0}
        />
        <FeatureCard
          icon={<Shield size={20} />}
          title="Self-Healing Pipeline"
          desc="Convergence-aware retry with fingerprinting and stagnation detection. The system automatically debugs and fixes failing stages."
          delay={80}
        />
        <FeatureCard
          icon={<Workflow size={20} />}
          title="Human-in-the-Loop Control"
          desc="Full approval gates at every pipeline stage. Review, reject with feedback, or let the AI run autonomously end-to-end."
          delay={160}
        />
        <FeatureCard
          icon="🔬"
          title="Sky130 PDK · Fab-Ready"
          desc="Generates real GDSII layouts on the open-source Sky130 PDK. DRC/LVS clean output ready for actual fabrication."
          delay={240}
        />
      </div>

      <div className="section-divider" />

      {/* ── Pipeline Visualization (26 stages) ────────────── */}
      <div ref={pipeline.ref} className={`pipeline-visual reveal ${pipeline.isVisible ? 'visible' : ''}`}>
        <h2 className="pipeline-visual-title">26-Stage Autonomous Pipeline</h2>
        <div className="pipeline-groups">
          {PIPELINE_GROUPS.map((group, gi) => (
            <div key={group.title} className="pipeline-group" style={{ animationDelay: `${gi * 120}ms` }}>
              <div className="pipeline-group-title">{group.title}</div>
              <div className="pipeline-group-stages">
                {group.stages.map((stage, si) => (
                  <div key={stage.label} style={{ display: 'contents' }}>
                    <PipelineStage icon={stage.icon} label={stage.label} />
                    {si < group.stages.length - 1 && <div className="pipeline-connector" />}
                  </div>
                ))}
              </div>
              {gi < PIPELINE_GROUPS.length - 1 && <div className="pipeline-group-arrow">→</div>}
            </div>
          ))}
        </div>
      </div>

      <div className="section-divider" />

      {/* ── Agent Architecture ────────────────────────────── */}
      <div ref={agents.ref} className={`reveal ${agents.isVisible ? 'visible' : ''}`} style={{ padding: '0 2rem', maxWidth: '1100px', margin: '0 auto', position: 'relative', zIndex: 1 }}>
        <h2 className="pipeline-visual-title">Multi-Agent Intelligence</h2>
      </div>
      <div className="agent-grid-premium">
        {AGENTS.map((agent, i) => (
          <div key={agent.name} className="agent-card-premium" style={{ animationDelay: `${i * 80}ms` }}>
            <div className="agent-card-premium-icon">{agent.icon}</div>
            <div className="agent-card-premium-name">{agent.name}</div>
            <div className="agent-card-premium-desc">{agent.desc}</div>
          </div>
        ))}
      </div>
    </div>
  );
};
