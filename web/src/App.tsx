import { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { Dashboard } from './pages/Dashboard';
import { DesignStudio } from './pages/DesignStudio';
import { HumanInLoopBuild } from './pages/HumanInLoopBuild';
import { Benchmarking } from './pages/Benchmarking';
import { Fabrication } from './pages/Fabrication';
import { Documentation } from './pages/Documentation';
import './index.css';

const App = () => {
  const [selectedPage, setSelectedPage] = useState('Design Studio');
  const [designs, setDesigns] = useState<{ name: string, has_gds: boolean }[]>([]);
  const [selectedDesign, setSelectedDesign] = useState<string>('');
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    const saved = localStorage.getItem('agentic-theme');
    return saved === 'dark' ? 'dark' : 'light';
  });

  // Bypass Ngrok browser warning for all Axios requests
  axios.defaults.headers.common['ngrok-skip-browser-warning'] = 'true';

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('agentic-theme', theme);
  }, [theme]);

  useEffect(() => {
    const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:7860').replace(/\/$/, '');
    axios.get(`${API_BASE_URL}/designs`)
      .then(res => {
        const data = res.data?.designs || [];
        setDesigns(data);
        if (data.length > 0) {
          const withGds = data.find((d: any) => d.has_gds);
          setSelectedDesign(withGds ? withGds.name : data[0].name);
        }
      })
      .catch(err => console.error("Failed to fetch designs", err));
  }, []);

  const navItems = useMemo(
    () => [
      { name: 'Home', icon: '🏠' },
      { name: 'Design Studio', icon: '⚡' },
      { name: 'HITL Build', icon: '🧑‍💻' },
      { name: 'Dashboard', icon: '📊' },
      { name: 'Documentation', icon: '📚' },
      { name: 'Benchmarking', icon: '📈' },
      { name: 'Fabrication', icon: '🏗️' },
    ],
    []
  );

  return (
    <div className="app-shell">
      <aside className="app-sidebar">
        <div className="app-brand">
          <div className="app-brand-logo">A</div>
          <div>
            <div className="app-brand-title">AgentIC</div>
            <div className="app-brand-sub">Autonomous Silicon Studio</div>
          </div>
        </div>

        <div className="app-sidebar-group">
          <div className="app-sidebar-label">Active Design</div>
          <select
            className="app-design-select"
            value={selectedDesign}
            onChange={(e) => setSelectedDesign(e.target.value)}
          >
            {designs.map((d) => (
              <option key={d.name} value={d.name}>
                {d.name} {d.has_gds ? '• GDS' : ''}
              </option>
            ))}
          </select>
        </div>

        <nav className="app-nav">
          {navItems.map((item) => (
            <button
              key={item.name}
              className={`app-nav-btn ${selectedPage === item.name ? 'active' : ''}`}
              onClick={() => setSelectedPage(item.name)}
            >
              <span>{item.icon}</span>
              <span>{item.name}</span>
            </button>
          ))}
        </nav>

        <div className="app-sidebar-footer">
          <button
            className="theme-toggle"
            onClick={() => setTheme((t) => (t === 'light' ? 'dark' : 'light'))}
          >
            {theme === 'light' ? '🌙 Dark' : '☀️ Light'}
          </button>
          <div className="app-version">AgentIC · 2026</div>
        </div>
      </aside>

      <main className="app-main">
        <header className="app-topbar">
          <h1>{selectedPage}</h1>
          <div className="app-topbar-meta">Multi-Agent Autonomous Silicon</div>
        </header>

        <section className="app-content">
          {selectedPage === 'Home' && (
            <div className="home-overview">
              <div className="home-hero">
                <div className="home-hero-badge">Text → Silicon</div>
                <h2 className="home-hero-title">Autonomous Chip Design Studio</h2>
                <p className="home-hero-desc">
                  From natural language to fabrication-ready GDSII — powered by multi-agent
                  collaboration, intelligent specification analysis, self-healing loops, and
                  a fully autonomous pipeline.
                </p>
              </div>

              <div className="home-card-grid">
                <div className="home-kpi">{designs.length}<span>Designs</span></div>
                <div className="home-kpi">15<span>Pipeline Stages</span></div>
                <div className="home-kpi">5<span>Core Modules</span></div>
                <div className="home-kpi">AI<span>Agents</span></div>
              </div>

              <div className="home-section">
                <h3 className="home-section-title">Multi-Agent Architecture</h3>
                <div className="home-agent-grid">
                  <div className="agent-card">
                    <div className="agent-icon">🏗️</div>
                    <div className="agent-name">Architect</div>
                    <div className="agent-desc">Specification analysis and structured design decomposition</div>
                  </div>
                  <div className="agent-card">
                    <div className="agent-icon">💻</div>
                    <div className="agent-name">RTL Designer + Reviewer</div>
                    <div className="agent-desc">Multi-agent collaborative generation</div>
                  </div>
                  <div className="agent-card">
                    <div className="agent-icon">🧪</div>
                    <div className="agent-name">TB Designer</div>
                    <div className="agent-desc">Automated testbench generation and validation</div>
                  </div>
                  <div className="agent-card">
                    <div className="agent-icon">🔍</div>
                    <div className="agent-name">Error Analyst</div>
                    <div className="agent-desc">Intelligent failure classification</div>
                  </div>
                  <div className="agent-card">
                    <div className="agent-icon">🔄</div>
                    <div className="agent-name">Self-Healing Engine</div>
                    <div className="agent-desc">Convergence-aware optimization and recovery</div>
                  </div>
                  <div className="agent-card">
                    <div className="agent-icon">🧠</div>
                    <div className="agent-name">Deep Debugger</div>
                    <div className="agent-desc">Causal failure analysis</div>
                  </div>
                </div>
              </div>

              <div className="home-section">
                <h3 className="home-section-title">Pipeline Flow</h3>
                <div className="pipeline-flow">
                  {[
                    { icon: '📐', label: 'SPEC', sub: 'Spec Analysis' },
                    { icon: '💻', label: 'RTL_GEN', sub: 'RTL Generation' },
                    { icon: '🔨', label: 'RTL_FIX', sub: 'Code Quality' },
                    { icon: '🧪', label: 'VERIFY', sub: 'Functional Verify' },
                    { icon: '📊', label: 'FORMAL', sub: 'Formal Verify' },
                    { icon: '📈', label: 'COVERAGE', sub: 'Coverage' },
                    { icon: '🗺️', label: 'FLOOR', sub: 'Floorplan' },
                    { icon: '🏗️', label: 'HARDEN', sub: 'Optimization' },
                    { icon: '✅', label: 'SIGNOFF', sub: 'Signoff' },
                  ].map((s, i) => (
                    <div className="pipeline-stage" key={s.label}>
                      <div className="pipeline-stage-icon">{s.icon}</div>
                      <div className="pipeline-stage-label">{s.label}</div>
                      <div className="pipeline-stage-sub">{s.sub}</div>
                      {i < 8 && <div className="pipeline-arrow">→</div>}
                    </div>
                  ))}
                </div>
              </div>

              <div className="home-section">
                <h3 className="home-section-title">Quick Start</h3>
                <div className="home-quickstart">
                  <div className="quickstart-step">
                    <div className="quickstart-num">1</div>
                    <div>Go to <strong>Design Studio</strong> and describe any chip</div>
                  </div>
                  <div className="quickstart-step">
                    <div className="quickstart-num">2</div>
                    <div>Watch AI agents autonomously build and verify your chip</div>
                  </div>
                  <div className="quickstart-step">
                    <div className="quickstart-num">3</div>
                    <div>Check <strong>Dashboard</strong> for silicon metrics and signoff</div>
                  </div>
                </div>
                <button className="btn-primary home-cta" onClick={() => setSelectedPage('Design Studio')}>
                  Start New Build →
                </button>
              </div>
            </div>
          )}

          {selectedPage === 'Dashboard' && <Dashboard selectedDesign={selectedDesign} />}
          {selectedPage === 'Design Studio' && <DesignStudio />}
          {selectedPage === 'HITL Build' && <HumanInLoopBuild />}
          {selectedPage === 'Documentation' && <Documentation />}
          {selectedPage === 'Benchmarking' && <Benchmarking selectedDesign={selectedDesign} />}
          {selectedPage === 'Fabrication' && (
            <Fabrication selectedDesign={selectedDesign} hasGds={designs.find((d) => d.name === selectedDesign)?.has_gds} />
          )}
        </section>
      </main>
    </div>
  );
};

export default App;
