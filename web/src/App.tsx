import { useEffect, useMemo, useState } from 'react';
import type { Session, AuthChangeEvent } from '@supabase/supabase-js';
import { supabase } from './supabaseClient';
import { HomeComponent } from './components/HomeComponent';

import { Dashboard } from './pages/Dashboard';
import { DesignStudio } from './pages/DesignStudio';
import { HumanInLoopBuild } from './pages/HumanInLoopBuild';
import { Benchmarking } from './pages/Benchmarking';
import { Fabrication } from './pages/Fabrication';
import { Documentation } from './pages/Documentation';
import { EDALab } from './pages/EDALab';
import { DockItem, PulseDot } from './components/MagicUI';
import { api } from './api';
import { useCursorGlow } from './utils/useAnimations';
import './index.css';
import { Home, Zap, Users, BarChart2, BookOpen, Scaling, Factory, TerminalSquare } from 'lucide-react';

import { LandingPage } from './pages/LandingPage';
import { WaitlistDashboard } from './pages/WaitlistDashboard';

const AUTH_ENABLED = Boolean(import.meta.env.VITE_SUPABASE_URL);
const WAITLIST_MODE = import.meta.env.VITE_WAITLIST_MODE === 'true';
const WHITELISTED_EMAILS = (import.meta.env.VITE_WHITELISTED_EMAILS || '').split(',').map((e: string) => e.trim().toLowerCase());

const App = () => {
  const [session, setSession] = useState<Session | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [selectedPage, setSelectedPage] = useState('Home');
  const [designs, setDesigns] = useState<{ name: string, has_gds: boolean }[]>([]);
  const [selectedDesign, setSelectedDesign] = useState<string>('');
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    const saved = localStorage.getItem('agentic-theme');
    return saved === 'dark' ? 'dark' : 'light';
  });

  // Cursor glow effect
  useCursorGlow();

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

  // Fetch designs
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
    setSelectedPage('Home');
  };

  const navItems = useMemo(
    () => [
      { name: 'Home', icon: Home },
      { name: 'Design Studio', icon: Zap },
      { name: 'HITL Build', icon: Users },
      { name: 'Dashboard', icon: BarChart2 },
      { name: 'Manual EDA Lab', icon: TerminalSquare },
      { name: 'Documentation', icon: BookOpen },
      { name: 'Benchmarking', icon: Scaling },
      { name: 'Fabrication', icon: Factory },
    ],
    []
  );

  // ── Premium loading spinner ──
  if (authLoading) {
    return (
      <div style={{
        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
        height: '100vh', gap: '1rem', background: 'var(--bg)'
      }}>
        <div style={{
          width: 48, height: 48, borderRadius: '50%',
          border: '3px solid var(--border)',
          borderTopColor: 'var(--accent)',
          animation: 'spin-slow 0.8s linear infinite'
        }} />
        <span style={{ color: 'var(--text-mid)', fontSize: '0.9rem', fontWeight: 500 }}>Loading AgentIC…</span>
      </div>
    );
  }

  // ── Unauthenticated State: Show Landing Page ──
  if (AUTH_ENABLED && !session) {
    return (
      <LandingPage onAuthSuccess={() => 
        supabase.auth.getSession().then(({ data: { session: s } }: { data: { session: Session | null } }) => setSession(s))
      } />
    );
  }

  // ── Waitlist Check: Show Waitlist Dashboard index if mode on ──
  if (WAITLIST_MODE && session) {
    const isWhitelisted = WHITELISTED_EMAILS.includes(session.user.email?.toLowerCase() || '');
    if (!isWhitelisted) {
      return <WaitlistDashboard email={session.user.email || 'Explorer'} />;
    }
  }

  const renderPage = () => {
    switch (selectedPage) {
      case 'Home':
        return <HomeComponent designsLength={designs.length} setSelectedPage={setSelectedPage} />;
      case 'Dashboard':
        return <Dashboard selectedDesign={selectedDesign} />;
      case 'Design Studio':
        return <DesignStudio />;
      case 'Manual EDA Lab':
        return <EDALab />;
      case 'HITL Build':
        return <HumanInLoopBuild />;
      case 'Documentation':
        return <Documentation />;
      case 'Benchmarking':
        return <Benchmarking selectedDesign={selectedDesign} />;
      case 'Fabrication':
        return <Fabrication selectedDesign={selectedDesign} hasGds={designs.find((d) => d.name === selectedDesign)?.has_gds} />;
      default:
        return <HomeComponent designsLength={designs.length} setSelectedPage={setSelectedPage} />;
    }
  };


  return (
    <div className="app-shell">
      {/* ── Top Navigation Bar ── */}
      <nav className="top-nav">
        {/* Brand */}
        <div className="top-nav-brand">
          <div className="top-nav-brand-logo">A</div>
          <div className="top-nav-brand-text">
            <span className="top-nav-brand-title">AgentIC</span>
            <span className="top-nav-brand-sub">Autonomous Silicon Studio</span>
          </div>
        </div>

        {/* Dock Navigation */}
        <div className="dock-nav">
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <DockItem
                key={item.name}
                icon={<Icon size={16} strokeWidth={2} />}
                label={item.name}
                active={selectedPage === item.name}
                onClick={() => setSelectedPage(item.name)}
              />
            );
          })}
        </div>

        {/* Right Actions */}
        <div className="top-nav-actions">
          {designs.length > 0 && (
            <select
              className="top-nav-select"
              value={selectedDesign}
              onChange={(e) => setSelectedDesign(e.target.value)}
            >
              {designs.map((d) => (
                <option key={d.name} value={d.name}>
                  {d.name} {d.has_gds ? '• GDS' : ''}
                </option>
              ))}
            </select>
          )}

          <button
            className="top-nav-btn"
            onClick={() => setTheme((t) => (t === 'light' ? 'dark' : 'light'))}
            title={theme === 'light' ? 'Dark mode' : 'Light mode'}
          >
            {theme === 'light' ? '🌙' : '☀️'}
          </button>

          {session && (
            <>
              <div className="top-nav-avatar" title={session.user.email || ''}>
                {session.user.email?.[0]?.toUpperCase() || '?'}
              </div>
              <button className="top-nav-btn" onClick={handleLogout} title="Sign Out">
                ↩
              </button>
            </>
          )}

          <PulseDot color="var(--success)" />
        </div>
      </nav>

      {/* ── Main Content ── */}
      <main className="app-main">
        <section className="app-content">
          <div key={selectedPage} className="page-transition">
            {renderPage()}
          </div>
        </section>
      </main>
    </div>
  );
};

export default App;
