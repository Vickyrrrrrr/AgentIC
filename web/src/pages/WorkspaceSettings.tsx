import React, { useEffect, useState } from 'react';
import { KeyRound, User, Info } from 'lucide-react';

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
    <div className="ws-page">
      <div className="ws-grid">
        {/* Account */}
        <div className="ws-card">
          <div className="ws-card-header">
            <User size={16} className="ws-card-icon" />
            <span className="ws-card-label">Account Context</span>
          </div>
          <h3 className="ws-card-title">{resolvedEmail}</h3>
          <p className="ws-card-desc">
            Auth mode: {profile?.auth_enabled ? 'Supabase' : 'Local / Auth disabled'}
          </p>
          <div className="ws-plan-row">
            <span className="workspace-plan-badge">{resolvedPlan}</span>
            {profile?.auth_enabled && (
              <span className="ws-builds-count">
                Successful builds: {profile?.successful_builds ?? 0}
              </span>
            )}
          </div>
          <p className="ws-note">
            Plan restrictions and billing features are enforced server-side when auth is enabled.
          </p>
        </div>

        {/* Model Keys */}
        <div className="ws-card">
          <div className="ws-card-header">
            <KeyRound size={16} className="ws-card-icon" />
            <span className="ws-card-label">Model Keys</span>
          </div>
          <h3 className="ws-card-title">BYOK Configuration</h3>
          <p className="ws-card-desc">
            Configure model keys for reasoning, coding, and iterative agents.
          </p>
          <div className="ws-key-status">
            <strong className={byokReady ? 'ws-key-ok' : 'ws-key-missing'}>
              {byokReady ? 'Keys configured' : 'No keys configured'}
            </strong>
            <span className="ws-key-hint">
              Builds and Lab AI actions require a valid provider key.
            </span>
          </div>
          <button className="ws-btn-primary" onClick={onOpenByok}>
            Open BYOK Manager
          </button>
        </div>
      </div>

      {/* Operational Notes */}
      <div className="ws-card ws-notes-card">
        <div className="ws-card-header">
          <Info size={16} className="ws-card-icon" />
          <span className="ws-card-label">Operational Notes</span>
        </div>
        <ul className="ws-notes-list">
          <li>Long-running builds stream progress over SSE and recover from reconnects.</li>
          <li>HITL mode requires stage approvals before pipeline progression.</li>
          <li>Manual EDA Lab waveform behavior differs between cloud and local desktop environments.</li>
          <li>Artifacts and reports are generated per design and remain downloadable from build outputs.</li>
        </ul>
      </div>
    </div>
  );
};
