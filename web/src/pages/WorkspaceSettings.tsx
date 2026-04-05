import React, { useEffect, useState } from 'react';

type ProfileSummary = {
  auth_enabled: boolean;
  plan?: string;
  successful_builds?: number;
  has_byok_key?: boolean;
  email?: string;
};

interface WorkspaceSettingsProps {
  profile: ProfileSummary | null;
  sessionEmail: string;
  onOpenByok: () => void;
}

export const WorkspaceSettings: React.FC<WorkspaceSettingsProps> = ({
  profile,
  sessionEmail,
  onOpenByok,
}) => {
  const [localByokConfigured, setLocalByokConfigured] = useState(false);

  useEffect(() => {
    setLocalByokConfigured(Boolean(localStorage.getItem('agentic_byok_key')));
    const handleStorage = () => setLocalByokConfigured(Boolean(localStorage.getItem('agentic_byok_key')));
    window.addEventListener('storage', handleStorage);
    return () => window.removeEventListener('storage', handleStorage);
  }, []);

  const resolvedEmail = profile?.email || sessionEmail || 'Anonymous workspace';
  const resolvedPlan = profile?.auth_enabled ? profile?.plan || 'free' : 'local';
  const byokReady = Boolean(profile?.has_byok_key) || localByokConfigured;

  return (
    <div className="page-container" style={{ padding: '1.5rem', maxWidth: '1020px' }}>
      <div className="grid-2" style={{ gridTemplateColumns: '1fr 1fr' }}>
        <div className="sci-fi-card">
          <div className="section-heading">Account Context</div>
          <h3 style={{ marginTop: 0, marginBottom: '0.55rem' }}>{resolvedEmail}</h3>
          <p className="app-subtitle" style={{ marginBottom: '1rem' }}>
            Workspace auth mode: {profile?.auth_enabled ? 'Supabase' : 'Local / Auth disabled'}
          </p>
          <div style={{ display: 'flex', gap: '0.45rem', alignItems: 'center', marginBottom: '0.7rem' }}>
            <span className="workspace-plan-badge">{resolvedPlan}</span>
            {profile?.auth_enabled && (
              <span style={{ color: 'var(--text-mid)', fontSize: '0.85rem' }}>
                Successful builds: {profile?.successful_builds ?? 0}
              </span>
            )}
          </div>
          <div style={{ color: 'var(--text-dim)', fontSize: '0.82rem', lineHeight: 1.6 }}>
            Plan restrictions and billing features are enforced server-side when auth is enabled.
          </div>
        </div>

        <div className="sci-fi-card">
          <div className="section-heading">Model Keys</div>
          <h3 style={{ marginTop: 0, marginBottom: '0.55rem' }}>BYOK Configuration</h3>
          <p className="app-subtitle" style={{ marginBottom: '1rem' }}>
            Configure model keys for reasoning, coding, and iterative agents.
          </p>
          <div
            style={{
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius)',
              padding: '0.7rem 0.8rem',
              marginBottom: '0.8rem',
              background: 'var(--bg)',
            }}
          >
            <strong style={{ color: byokReady ? 'var(--success)' : 'var(--warn)' }}>
              {byokReady ? 'Keys configured' : 'No keys configured'}
            </strong>
            <div style={{ color: 'var(--text-dim)', fontSize: '0.82rem', marginTop: '0.2rem' }}>
              Builds and Lab AI actions require a valid provider key.
            </div>
          </div>

          <button className="btn-primary" onClick={onOpenByok}>
            Open BYOK Manager
          </button>
        </div>
      </div>

      <div className="sci-fi-card" style={{ marginTop: '1rem' }}>
        <div className="section-heading">Operational Notes</div>
        <ul style={{ margin: 0, paddingLeft: '1rem', color: 'var(--text-mid)', lineHeight: 1.7, fontSize: '0.86rem' }}>
          <li>Long-running builds stream progress over SSE and recover from reconnects.</li>
          <li>HITL mode requires stage approvals before pipeline progression.</li>
          <li>Manual EDA Lab waveform behavior differs between cloud and local desktop environments.</li>
          <li>Artifacts and reports are generated per design and remain downloadable from build outputs.</li>
        </ul>
      </div>
    </div>
  );
};

