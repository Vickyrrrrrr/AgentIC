import { useState, useEffect } from 'react';
import { Activity, BookOpen, Cpu, Layers, GitMerge, Zap, ArrowRight, Terminal, Shield, Workflow } from 'lucide-react';

/* ── Typewriter ─────────────────────────────────────────────────── */
const TypewriterText = ({ texts }: { texts: string[] }) => {
  const [textIndex, setTextIndex] = useState(0);
  const [text, setText] = useState('');
  const [isDeleting, setIsDeleting] = useState(false);

  useEffect(() => {
    const currentFullText = texts[textIndex];
    const typingSpeed = isDeleting ? 25 : 65;

    if (!isDeleting && text === currentFullText) {
      const id = setTimeout(() => setIsDeleting(true), 2200);
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
    <span className="home-typewriter-text">
      {text}
      <span className="home-typewriter-cursor">|</span>
    </span>
  );
};

/* ── Main Component ─────────────────────────────────────────────── */
export const HomeComponent = ({ designsLength, setSelectedPage }: { designsLength: number; setSelectedPage: (page: string) => void }) => {
  return (
    <div className="home-minimal">
      {/* Grid background */}
      <div className="home-grid-bg" />

      {/* ── Hero ─── */}
      <section className="home-hero">
        <div className="home-hero-badge">
          <span className="home-hero-badge-dot" />
          v3.0 — Autonomous Pipeline
        </div>

        <h1 className="home-hero-title">
          <TypewriterText texts={[
            "Natural Language to GDSII",
            "Autonomous Silicon Design",
            "Multi-Agent EDA Pipeline",
          ]} />
        </h1>

        <p className="home-hero-desc">
          Describe any digital circuit in plain English. AgentIC writes synthesizable
          RTL, verifies logic, runs formal proofs, and generates fabrication-ready
          GDSII — fully autonomously.
        </p>

        <div className="home-hero-actions">
          <button className="home-btn-primary" onClick={() => setSelectedPage('Design Studio')}>
            <Terminal size={16} /> Enter Studio
            <ArrowRight size={14} />
          </button>
          <button className="home-btn-ghost" onClick={() => setSelectedPage('HITL Build')}>
            <Workflow size={16} /> Human-in-Loop
          </button>
          <button className="home-btn-ghost" onClick={() => setSelectedPage('Documentation')}>
            <BookOpen size={16} /> Docs
          </button>
        </div>
      </section>

      {/* ── Stats ─── */}
      <section className="home-stats">
        <div className="home-stat">
          <Cpu size={18} className="home-stat-icon" />
          <span className="home-stat-value">{designsLength || 0}</span>
          <span className="home-stat-label">Active Designs</span>
        </div>
        <div className="home-stat-sep" />
        <div className="home-stat">
          <GitMerge size={18} className="home-stat-icon" />
          <span className="home-stat-value">5</span>
          <span className="home-stat-label">Core Agents</span>
        </div>
        <div className="home-stat-sep" />
        <div className="home-stat">
          <Layers size={18} className="home-stat-icon" />
          <span className="home-stat-value">Sky130</span>
          <span className="home-stat-label">Target PDK</span>
        </div>
        <div className="home-stat-sep" />
        <div className="home-stat">
          <Zap size={18} className="home-stat-icon" />
          <span className="home-stat-value">BYOK</span>
          <span className="home-stat-label">Any LLM Provider</span>
        </div>
      </section>

      {/* ── Capabilities ─── */}
      <section className="home-capabilities">
        <h2 className="home-section-title">Capabilities</h2>
        <div className="home-cap-grid">
          <div className="home-cap-card">
            <Activity size={20} className="home-cap-icon" />
            <h3 className="home-cap-title">Spec to Silicon</h3>
            <p className="home-cap-desc">
              From natural language specification to fabrication-ready GDSII layout.
              No HDL expertise required.
            </p>
          </div>
          <div className="home-cap-card">
            <Shield size={20} className="home-cap-icon" />
            <h3 className="home-cap-title">Self-Healing Pipeline</h3>
            <p className="home-cap-desc">
              Convergence-aware retry with stagnation detection. The system
              automatically debugs and fixes failing stages.
            </p>
          </div>
          <div className="home-cap-card">
            <Workflow size={20} className="home-cap-icon" />
            <h3 className="home-cap-title">Human-in-the-Loop</h3>
            <p className="home-cap-desc">
              Full approval gates at every pipeline stage. Review, reject with
              feedback, or run fully autonomous.
            </p>
          </div>
        </div>
      </section>

      {/* ── Agents ─── */}
      <section className="home-agents">
        <h2 className="home-section-title">Agent Architecture</h2>
        <div className="home-agent-list">
          {[
            { icon: <Cpu size={18} />, name: 'Architect', desc: 'Decomposes specs into chip architecture with ports, FSMs, and sub-modules' },
            { icon: <Terminal size={18} />, name: 'RTL Designer', desc: 'Writes synthesizable Verilog with collaborative design-review pattern' },
            { icon: <Shield size={18} />, name: 'Verifier', desc: 'Creates testbenches, runs simulation, and diagnoses failures' },
            { icon: <Activity size={18} />, name: 'Formal Prover', desc: 'Mathematically proves design correctness using SVA assertions' },
            { icon: <Zap size={18} />, name: 'Deep Debugger', desc: 'Causal failure analysis with multi-perspective reasoning' },
          ].map((agent) => (
            <div key={agent.name} className="home-agent-row">
              <div className="home-agent-icon">{agent.icon}</div>
              <div className="home-agent-info">
                <span className="home-agent-name">{agent.name}</span>
                <span className="home-agent-desc">{agent.desc}</span>
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
};
