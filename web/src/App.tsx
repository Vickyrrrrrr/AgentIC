import { Suspense, lazy, useEffect, useState } from 'react';
import type { Session, AuthChangeEvent } from '@supabase/supabase-js';
import { supabase } from './supabaseClient';
import { LandingPage } from './pages/LandingPage';
import { WaitlistDashboard } from './pages/WaitlistDashboard';
import { api } from './api';
import { BillingModal } from './components/BillingModal';
import './index.css';
import type { LucideIcon } from 'lucide-react';
import {
  Home,
  Zap,
  Users,
  BarChart2,
  BookOpen,
  Scaling,
  Factory,
  TerminalSquare,
  ClipboardList,
  Settings2,
} from 'lucide-react';

const AUTH_ENABLED = Boolean(import.meta.env.VITE_SUPABASE_URL);

const HomeComponent = lazy(() =>
  import('./components/HomeComponent').then((m) => ({ default: m.HomeComponent }))
);
const Dashboard = lazy(() =>
  import('./pages/Dashboard').then((m) => ({ default: m.Dashboard }))
);
const DesignStudio = lazy(() =>
  import('./pages/DesignStudio').then((m) => ({ default: m.DesignStudio }))
);
const HumanInLoopBuild = lazy(() =>
  import('./pages/HumanInLoopBuild').then((m) => ({ default: m.HumanInLoopBuild }))
);
const Benchmarking = lazy(() =>
  import('./pages/Benchmarking').then((m) => ({ default: m.Benchmarking }))
);
const Fabrication = lazy(() =>
  import('./pages/Fabrication').then((m) => ({ default: m.Fabrication }))
);
const Documentation = lazy(() =>
  import('./pages/Documentation').then((m) => ({ default: m.Documentation }))
);
const EDALab = lazy(() =>
  import('./pages/EDALab').then((m) => ({ default: m.EDALab }))
);
const BuildHistory = lazy(() =>
  import('./pages/BuildHistory').then((m) => ({ default: m.BuildHistory }))
);
const WorkspaceSettings = lazy(() =>
  import('./pages/WorkspaceSettings').then((m) => ({ default: m.WorkspaceSettings }))
);

type PageKey =
  | 'Home'
  | 'Design Studio'
  | 'HITL Build'
  | 'Build History'
  | 'Dashboard'
  | 'Fabrication'
  | 'Benchmarking'
  | 'Manual EDA Lab'
  | 'Documentation'
  | 'Workspace Settings';

type DesignOption = { name: string; has_gds: boolean };

type JobSummary = {
  job_id: string;
  design_name: string;
  status: string;
  current_state: string;
  created_at: number;
  event_count: number;
};

type ProfileSummary = {
  auth_enabled: boolean;
  plan?: string;
  successful_builds?: number;
  has_byok_key?: boolean;
  email?: string;
};

type NavGroup = {
  label: string;
  items: Array<{ page: PageKey; label: string; icon: LucideIcon }>;
};

const NAV_GROUPS: NavGroup[] = [
  {
    label: 'Build',
    items: [
      { page: 'Home', label: 'Overview', icon: Home },
      { page: 'Design Studio', label: 'Quick Build', icon: Zap },
      { page: 'HITL Build', label: 'HITL Pipeline', icon: Users },
      { page: 'Build History', label: 'Jobs & History', icon: ClipboardList },
    ],
  },
  {
    label: 'Designs',
    items: [
      { page: 'Dashboard', label: 'Design Insights', icon: BarChart2 },
      { page: 'Fabrication', label: 'Fabrication', icon: Factory },
      { page: 'Benchmarking', label: 'Benchmarking', icon: Scaling },
    ],
  },
  {
    label: 'System',
    items: [
      { page: 'Manual EDA Lab', label: 'Manual EDA Lab', icon: TerminalSquare },
      { page: 'Documentation', label: 'Documentation', icon: BookOpen },
      { page: 'Workspace Settings', label: 'Workspace Settings', icon: Settings2 },
    ],
  },
];

const PAGE_META: Record<PageKey, { title: string; subtitle: string }> = {
  Home: {
    title: 'AgentIC Workspace',
    subtitle: 'Autonomous silicon operations center',
  },
  'Design Studio': {
    title: 'Quick Build Studio',
    subtitle: 'Launch fully autonomous chip builds from natural language',
  },
  'HITL Build': {
    title: 'Human-in-the-Loop Pipeline',
    subtitle: 'Review and approve each stage with full operator control',
  },
  'Build History': {
    title: 'Jobs & Build History',
    subtitle: 'Track build outcomes, states, and recent execution history',
  },
  Dashboard: {
    title: 'Design Insights',
    subtitle: 'Metrics, signoff intelligence, and recent activity',
  },
  Fabrication: {
    title: 'Fabrication Workspace',
    subtitle: 'Inspect and prepare final manufacturing artifacts',
  },
  Benchmarking: {
    title: 'Benchmarking',
    subtitle: 'Compare AgentIC flow performance against traditional workflows',
  },
  'Manual EDA Lab': {
    title: 'Manual EDA Lab',
    subtitle: 'Run syntax, synthesis, simulation, and waveform analysis directly',
  },
  Documentation: {
    title: 'Technical Documentation',
    subtitle: 'Architecture references, pipeline specs, and config contracts',
  },
  'Workspace Settings': {
    title: 'Workspace Settings',
    subtitle: 'Manage model keys, plan context, and operator configuration',
  },
};

const App = () => {
  const [session, setSession] = useState<Session | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [selectedPage, setSelectedPage] = useState<PageKey>('Home');
  const [designs, setDesigns] = useState<DesignOption[]>([]);
  const [selectedDesign, setSelectedDesign] = useState<string>('');
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [profile, setProfile] = useState<ProfileSummary | null>(null);
  const [showBillingModal, setShowBillingModal] = useState(false);
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    const saved = localStorage.getItem('agentic-theme');
    return saved === 'dark' ? 'dark' : 'light';
  });

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

  useEffect(() => {
    if (AUTH_ENABLED && !session) return;
    let cancelled = false;

    const loadWorkspaceData = async () => {
      const [designRes, jobsRes, profileRes] = await Promise.allSettled([
        api.get('/designs'),
        api.get('/jobs'),
        api.get('/profile'),
      ]);
      if (cancelled) return;

      const rawDesigns: DesignOption[] =
        designRes.status === 'fulfilled' ? designRes.value.data?.designs || [] : [];
      const rawJobs: JobSummary[] =
        jobsRes.status === 'fulfilled' ? jobsRes.value.data?.jobs || [] : [];
      const nextProfile: ProfileSummary | null =
        profileRes.status === 'fulfilled' ? profileRes.value.data || null : null;

      setJobs(rawJobs);
      setProfile(nextProfile);

      const designMap = new Map<string, DesignOption>();
      for (const design of rawDesigns) {
        if (!design?.name) continue;
        designMap.set(design.name, {
          name: design.name,
          has_gds: Boolean(design.has_gds),
        });
      }

      // Fallback when /designs is empty: derive design names from /jobs.
      for (const job of rawJobs) {
        if (!job.design_name || designMap.has(job.design_name)) continue;
        designMap.set(job.design_name, { name: job.design_name, has_gds: false });
      }

      const mergedDesigns = Array.from(designMap.values()).sort((a, b) => a.name.localeCompare(b.name));
      setDesigns(mergedDesigns);
      setSelectedDesign((prev) => {
        if (prev && mergedDesigns.some((d) => d.name === prev)) return prev;
        if (mergedDesigns.length === 0) return '';
        const withGds = mergedDesigns.find((d) => d.has_gds);
        return withGds ? withGds.name : mergedDesigns[0].name;
      });
    };

    loadWorkspaceData().catch((err) => {
      // Non-fatal: keep workspace usable even if API context fetch fails.
      console.error('Failed to load workspace context', err);
      setDesigns([]);
      setJobs([]);
      setProfile(null);
      setSelectedDesign('');
    });

    return () => {
      cancelled = true;
    };
  }, [session]);

  const handleLogout = async () => {
    await supabase.auth.signOut();
    setSession(null);
    setSelectedPage('Home');
  };

  if (authLoading) {
    return (
      <div className="workspace-page-loader">
        <div className="premium-loader">
          <span className="premium-loader-dot" />
          <span className="premium-loader-dot" />
          <span className="premium-loader-dot" />
        </div>
        <span>Loading AgentIC...</span>
      </div>
    );
  }

  if (AUTH_ENABLED && !session) {
    return (
      <LandingPage
        onAuthSuccess={() =>
          supabase.auth
            .getSession()
            .then(({ data: { session: s } }: { data: { session: Session | null } }) => setSession(s))
        }
      />
    );
  }

  if (AUTH_ENABLED && session) {
    const userEmail = session.user.email?.toLowerCase() || '';
    const isApproved = userEmail === 'vickynishad110@gmail.com';
    if (!isApproved) {
      return <WaitlistDashboard email={session.user.email || ''} />;
    }
  }

  const handleHomeNavigation = (page: string) => {
    const exists = NAV_GROUPS.some((group) => group.items.some((item) => item.page === page));
    if (exists) {
      setSelectedPage(page as PageKey);
    }
  };

  const selectedDesignHasGds = designs.find((d) => d.name === selectedDesign)?.has_gds;
  const currentPageMeta = PAGE_META[selectedPage];

  const renderPage = () => {
    switch (selectedPage) {
      case 'Home':
        return <HomeComponent designsLength={designs.length} setSelectedPage={handleHomeNavigation} />;
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
        return <Fabrication selectedDesign={selectedDesign} hasGds={selectedDesignHasGds} />;
      case 'Build History':
        return (
          <BuildHistory
            jobs={jobs}
            selectedDesign={selectedDesign}
            onSelectDesign={setSelectedDesign}
            onOpenPage={(page) => {
              if (page === 'Dashboard' || page === 'Design Studio') {
                setSelectedPage(page);
              }
            }}
          />
        );
      case 'Workspace Settings':
        return (
          <WorkspaceSettings
            profile={profile}
            sessionEmail={session?.user.email || ''}
            onOpenByok={() => setShowBillingModal(true)}
          />
        );
      default:
        return <HomeComponent designsLength={designs.length} setSelectedPage={handleHomeNavigation} />;
    }
  };

  return (
    <div className="app-shell workspace-shell">
      <aside className="app-sidebar">
        <div className="app-brand">
          <div className="app-brand-logo">A</div>
          <div>
            <div className="app-brand-title">AgentIC</div>
            <div className="app-brand-sub">Autonomous Silicon Workspace</div>
          </div>
        </div>

        {NAV_GROUPS.map((group) => (
          <div className="app-sidebar-group" key={group.label}>
            <div className="app-sidebar-label">{group.label}</div>
            <div className="app-nav">
              {group.items.map((item) => {
                const Icon = item.icon;
                return (
                  <button
                    key={item.page}
                    className={`app-nav-btn${selectedPage === item.page ? ' active' : ''}`}
                    onClick={() => setSelectedPage(item.page)}
                  >
                    <Icon size={16} className="nav-icon" />
                    <span>{item.label}</span>
                  </button>
                );
              })}
            </div>
          </div>
        ))}

        <div className="app-sidebar-footer">
          <button className="theme-toggle" onClick={() => setShowBillingModal(true)}>
            Configure BYOK Keys
          </button>
          <div className="app-version">
            {profile?.plan ? `Plan: ${profile.plan}` : 'Local Workspace'} · v3.0
          </div>
        </div>
      </aside>

      <main className="app-main">
        <header className="app-topbar workspace-topbar">
          <div className="workspace-title-wrap">
            <h1>{currentPageMeta.title}</h1>
            <div className="app-topbar-meta">{currentPageMeta.subtitle}</div>
          </div>

          <div className="workspace-topbar-actions">
            {designs.length > 0 && (
              <select
                className="app-design-select"
                value={selectedDesign}
                onChange={(e) => setSelectedDesign(e.target.value)}
                title="Select design context"
              >
                {designs.map((d) => (
                  <option key={d.name} value={d.name}>
                    {d.name}
                    {d.has_gds ? ' · GDS' : ''}
                  </option>
                ))}
              </select>
            )}

            <span className="workspace-plan-badge">
              {profile?.auth_enabled ? (profile?.plan || 'free') : 'local'}
            </span>

            <button
              className="top-nav-btn"
              onClick={() => setTheme((t) => (t === 'light' ? 'dark' : 'light'))}
              title={theme === 'light' ? 'Switch to dark mode' : 'Switch to light mode'}
            >
              {theme === 'light' ? 'Dark' : 'Light'}
            </button>

            {session && (
              <button className="top-nav-btn" onClick={handleLogout} title="Sign out">
                Sign Out
              </button>
            )}
          </div>
        </header>

        <section className="app-content">
          <Suspense
            fallback={
              <div className="workspace-page-loader">
                <div className="premium-loader">
                  <span className="premium-loader-dot" />
                  <span className="premium-loader-dot" />
                  <span className="premium-loader-dot" />
                </div>
                <span>Loading workspace...</span>
              </div>
            }
          >
            <div key={selectedPage} className="page-transition">
              {renderPage()}
            </div>
          </Suspense>
        </section>
      </main>

      <BillingModal
        isOpen={showBillingModal}
        onClose={() => setShowBillingModal(false)}
        onKeySaved={() => {
          setProfile((prev) => (prev ? { ...prev, has_byok_key: true } : prev));
        }}
      />
    </div>
  );
};

export default App;
