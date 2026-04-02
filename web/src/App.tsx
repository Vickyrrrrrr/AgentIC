import { useEffect, useMemo, useState } from 'react';
import type { Session, AuthChangeEvent } from '@supabase/supabase-js';
import { supabase } from './supabaseClient';
import { HomeComponent } from './components/HomeComponent';
import { AuthPage } from './components/AuthPage';
import { Dashboard } from './pages/Dashboard';
import { DesignStudio } from './pages/DesignStudio';
import { HumanInLoopBuild } from './pages/HumanInLoopBuild';
import { Benchmarking } from './pages/Benchmarking';
import { Fabrication } from './pages/Fabrication';
import { Documentation } from './pages/Documentation';
import { api } from './api';
import './index.css';
import { Home, Zap, Users, BarChart2, BookOpen, Scaling, Factory } from 'lucide-react';

const AUTH_ENABLED = Boolean(import.meta.env.VITE_SUPABASE_URL);

const App = () => {
  const [session, setSession] = useState<Session | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [selectedPage, setSelectedPage] = useState('Design Studio');
  const [designs, setDesigns] = useState<{ name: string, has_gds: boolean }[]>([]);
  const [selectedDesign, setSelectedDesign] = useState<string>('');
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    const saved = localStorage.getItem('agentic-theme');
    return saved === 'dark' ? 'dark' : 'light';
  });

  // ── Auth state (skip when Supabase not configured) ──
  useEffect(() => {
    if (!AUTH_ENABLED) {
      setAuthLoading(false);
      return;
    }
    supabase.auth.getSession().then(({ data: { session: s } }: { data: { session: Session | null } }) => {
      setSession(s);
      setAuthLoading(false);
    }).catch(() => setAuthLoading(false));
    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event: AuthChangeEvent, s: Session | null) => {
      setSession(s);
    });
    return () => subscription.unsubscribe();
  }, []);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('agentic-theme', theme);
  }, [theme]);

  // Fetch designs when authenticated (or always in local dev without auth)
  useEffect(() => {
    if (AUTH_ENABLED && !session) return;
    api.get('/designs')
      .then(res => {
        const data = res.data?.designs || [];
        setDesigns(data);
        if (data.length > 0) {
          const withGds = data.find((d: any) => d.has_gds);
          setSelectedDesign(withGds ? withGds.name : data[0].name);
        }
      })
      .catch(err => console.error("Failed to fetch designs", err));
  }, [session]);

  const handleLogout = async () => {
    await supabase.auth.signOut();
    setSession(null);
    setSelectedPage('Design Studio');
  };

  const navItems = useMemo(
    () => [
      { name: 'Home', icon: Home },
      { name: 'Design Studio', icon: Zap },
      { name: 'HITL Build', icon: Users },
      { name: 'Dashboard', icon: BarChart2 },
      { name: 'Documentation', icon: BookOpen },
      { name: 'Benchmarking', icon: Scaling },
      { name: 'Fabrication', icon: Factory },
    ],
    []
  );

  // ── Auth loading spinner ──
  if (authLoading) {
    return (
      <div className="auth-loading">
        <div className="auth-loading-spinner" />
        <span>Loading AgentIC…</span>
      </div>
    );
  }

  if (AUTH_ENABLED && !session) {
    return <AuthPage onAuth={() => supabase.auth.getSession().then(({ data: { session: s } }: { data: { session: Session | null } }) => setSession(s))} />;
  }

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
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.name}
                className={`app-nav-btn ${selectedPage === item.name ? 'active' : ''}`}
                onClick={() => setSelectedPage(item.name)}
              >
                <Icon size={18} strokeWidth={2} className="nav-icon" />
                <span>{item.name}</span>
              </button>
            );
          })}
        </nav>

        <div className="app-sidebar-footer">
          {/* User info — only show when authenticated */}
          {session && (
            <div className="app-user-info">
              <div className="app-user-avatar">
                {session.user.email?.[0]?.toUpperCase() || '?'}
              </div>
              <div className="app-user-details">
                <div className="app-user-email">{session.user.email}</div>
              </div>
            </div>
          )}
          <button
            className="theme-toggle"
            onClick={() => setTheme((t) => (t === 'light' ? 'dark' : 'light'))}
          >
            {theme === 'light' ? '🌙 Dark' : '☀️ Light'}
          </button>
          {session && (
            <button className="logout-btn" onClick={handleLogout}>
              ↩ Sign Out
            </button>
          )}
          <div className="app-version">AgentIC · 2026</div>
        </div>
      </aside>

      <main className="app-main">
        <header className="app-topbar">
          <h1>{selectedPage}</h1>
          <div className="app-topbar-meta">Multi-Agent Autonomous Silicon</div>
        </header>

        <section className="app-content">
          {selectedPage === 'Home' && <HomeComponent designsLength={designs.length} setSelectedPage={setSelectedPage} />}
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
