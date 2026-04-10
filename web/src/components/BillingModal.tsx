import { useEffect, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, ChevronDown, ChevronUp, Eye, EyeOff, Check, Fingerprint, LockKeyhole, Sparkles } from 'lucide-react';
import { API_BASE } from '../api';
import { supabase } from '../supabaseClient';

type GroupKey = 'group1' | 'group2' | 'group3';
type GroupState = Record<GroupKey, { model: string; apiKey: string; baseUrl: string }>;

const DEFAULT_GROUPS: GroupState = {
  group1: { model: '', apiKey: '', baseUrl: '' },
  group2: { model: '', apiKey: '', baseUrl: '' },
  group3: { model: '', apiKey: '', baseUrl: '' },
};

const GROUP_META: Record<
  GroupKey,
  { title: string; roles: string; example: string; modelHint: string }
> = {
  group1: {
    title: 'Fix & Debug Agents',
    roles: 'Fixer · Debugger · Reasoner',
    example: 'openai/deepseek-ai/deepseek-v3.2',
    modelHint: 'Strong reasoning model',
  },
  group2: {
    title: 'Core Build Agents',
    roles: 'Architect · Designer · Testbench · Verifier · Manager · Physical',
    example: 'glm-4-plus',
    modelHint: 'Strong coding/planning model',
  },
  group3: {
    title: 'Documentation Agents',
    roles: 'Documenter · Reporter',
    example: 'groq/llama-3.3-70b-versatile',
    modelHint: 'Fast text generation model',
  },
};

const MaskedKey = ({ value }: { value: string }) => {
  const [visible, setVisible] = useState(false);
  if (!value) return null;
  const masked = value.length > 8 ? `${value.slice(0, 6)}${'•'.repeat(Math.min(20, value.length - 10))}${value.slice(-4)}` : '••••••••';
  return (
    <button
      className="byok-key-peek"
      onClick={() => setVisible(!visible)}
      type="button"
      title={visible ? 'Hide key' : 'Show key'}
    >
      {visible ? <EyeOff size={14} /> : <Eye size={14} />}
      <code>{visible ? value : masked}</code>
    </button>
  );
};

const ByokGroupCard = ({
  groupKey, index, meta, group, onUpdate,
}: {
  groupKey: GroupKey;
  index: number;
  meta: { title: string; roles: string; example: string; modelHint: string };
  group: { model: string; apiKey: string; baseUrl: string };
  onUpdate: (key: GroupKey, field: 'model' | 'apiKey' | 'baseUrl', value: string) => void;
}) => {
  const [open, setOpen] = useState(index === 0 || !!group.apiKey);

  return (
    <div className={`byok-group${open ? ' byok-group--open' : ''}`}>
      <button className="byok-group-header" onClick={() => setOpen(!open)} type="button">
        <div>
          <span className="byok-group-index">{index + 1}</span>
          <span className="byok-group-title">{meta.title}</span>
          <span className="byok-group-roles">{meta.roles}</span>
        </div>
        {open ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
      </button>
      {open && (
        <div className="byok-group-body">
          <p className="byok-group-hint">Recommended: {meta.example}</p>
          <div className="byok-field-row">
            <div className="byok-field">
              <label className="byok-field-label">Model</label>
              <input
                className="byok-field-input"
                placeholder={meta.modelHint}
                value={group.model}
                onChange={(e) => onUpdate(groupKey, 'model', e.target.value)}
              />
            </div>
            <div className="byok-field">
              <label className="byok-field-label">Base URL <span className="byok-optional">(optional)</span></label>
              <input
                className="byok-field-input"
                placeholder="Leave blank for hosted"
                value={group.baseUrl}
                onChange={(e) => onUpdate(groupKey, 'baseUrl', e.target.value)}
              />
            </div>
          </div>
          <div className="byok-field">
            <label className="byok-field-label">API Key</label>
            <input
              className="byok-field-input"
              type="password"
              placeholder="API Key"
              value={group.apiKey}
              onChange={(e) => onUpdate(groupKey, 'apiKey', e.target.value)}
            />
            <MaskedKey value={group.apiKey} />
          </div>
        </div>
      )}
    </div>
  );
};

export const BillingModal = ({
  isOpen,
  onClose,
  onKeySaved,
}: {
  isOpen: boolean;
  onClose: () => void;
  onKeySaved: () => void;
}) => {
  const [groups, setGroups] = useState<GroupState>(DEFAULT_GROUPS);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [quickMode, setQuickMode] = useState(true);
  const [quickKey, setQuickKey] = useState('');
  const [quickModel, setQuickModel] = useState('');
  const [quickBaseUrl, setQuickBaseUrl] = useState('');
  const [saved, setSaved] = useState(false);

  // Helper: apply a parsed BYOK payload to component state
  const applyParsed = (parsed: any) => {
    const nextGroups: GroupState = {
      group1: { model: parsed.group1?.model || '', apiKey: parsed.group1?.api_key || '', baseUrl: parsed.group1?.base_url || '' },
      group2: { model: parsed.group2?.model || '', apiKey: parsed.group2?.api_key || '', baseUrl: parsed.group2?.base_url || '' },
      group3: { model: parsed.group3?.model || '', apiKey: parsed.group3?.api_key || '', baseUrl: parsed.group3?.base_url || '' },
    };
    setGroups(nextGroups);
    const k1 = nextGroups.group1.apiKey;
    if (k1 && k1 === nextGroups.group2.apiKey && k1 === nextGroups.group3.apiKey) {
      setQuickKey(k1);
      setQuickModel(nextGroups.group1.model);
      setQuickBaseUrl(nextGroups.group1.baseUrl);
      setQuickMode(true);
    } else {
      setQuickMode(false);
    }
  };

  useEffect(() => {
    if (!isOpen) { setSaved(false); return; }
    setError('');

    // Try server-side first (so keys sync across devices), then fall back to localStorage
    const loadKeys = async () => {
      try {
        const { data: { session } } = await supabase.auth.getSession();
        if (session?.access_token) {
          const resp = await fetch(`${API_BASE}/profile/byok`, {
            headers: { Authorization: `Bearer ${session.access_token}` },
          });
          if (resp.ok) {
            const serverData = await resp.json();
            if (serverData && serverData.group1) {
              // Mirror to localStorage for offline use
              localStorage.setItem('agentic_byok_key', JSON.stringify(serverData));
              applyParsed(serverData);
              return;
            }
          }
        }
      } catch (_e) {
        // Server unavailable — fall back to localStorage silently
      }

      // Fallback: localStorage
      try {
        const raw = localStorage.getItem('agentic_byok_key');
        if (!raw) {
          setGroups(DEFAULT_GROUPS);
          setQuickKey('');
          setQuickModel('');
          setQuickBaseUrl('');
          setQuickMode(true);
          return;
        }
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === 'object') applyParsed(parsed);
      } catch (_e) {
        setGroups(DEFAULT_GROUPS);
        setQuickKey('');
        setQuickModel('');
        setQuickBaseUrl('');
        setQuickMode(true);
      }
    };

    loadKeys();
  }, [isOpen]);

  const hasAnyKey = quickMode
    ? quickKey.trim().length > 0
    : Object.values(groups).some((g) => g.apiKey.trim());

  const updateGroup = (key: GroupKey, field: 'model' | 'apiKey' | 'baseUrl', value: string) => {
    setGroups((prev) => ({ ...prev, [key]: { ...prev[key], [field]: value } }));
  };

  const handleSave = async () => {
    if (!hasAnyKey) return;
    setSaving(true);
    setError('');

    let payload: any;
    if (quickMode) {
      const common = { model: quickModel, api_key: quickKey, base_url: quickBaseUrl };
      payload = { group1: { ...common }, group2: { ...common }, group3: { ...common } };
    } else {
      payload = {
        group1: { model: groups.group1.model, api_key: groups.group1.apiKey, base_url: groups.group1.baseUrl },
        group2: { model: groups.group2.model, api_key: groups.group2.apiKey, base_url: groups.group2.baseUrl },
        group3: { model: groups.group3.model, api_key: groups.group3.apiKey, base_url: groups.group3.baseUrl },
      };
    }

    try {
      // 1. Always save to localStorage (works offline / no-auth)
      localStorage.setItem('agentic_byok_key', JSON.stringify(payload));

      // 2. Also save to server so keys sync across all devices
      try {
        const { data: { session } } = await supabase.auth.getSession();
        if (session?.access_token) {
          const resp = await fetch(`${API_BASE}/profile/byok`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              Authorization: `Bearer ${session.access_token}`,
            },
            body: JSON.stringify(payload),
          });
          if (!resp.ok) {
            const errData = await resp.json().catch(() => ({}));
            console.warn('BYOK server save failed:', errData);
            // Don't block the user — localStorage save already worked
          }
        }
      } catch (serverErr) {
        console.warn('BYOK server save error (non-fatal):', serverErr);
      }

      setSaved(true);
      setTimeout(() => { onKeySaved(); onClose(); }, 600);
    } catch (err: any) {
      setError(err.message || 'Failed to save keys.');
    } finally {
      setSaving(false);
    }
  };

  if (!isOpen) return null;

  return (
    <AnimatePresence>
      <div className="byok-overlay" onClick={onClose}>
        <motion.div
          className="byok-dialog"
          initial={{ opacity: 0, scale: 0.97, y: 8 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.97, y: 8 }}
          transition={{ duration: 0.18 }}
          onClick={(e) => e.stopPropagation()}
        >
          {/* Header */}
          <div className="byok-header">
            <div>
              <h2 className="byok-title">Configure API Keys</h2>
              <p className="byok-subtitle">
                Bring your own LLM key so AgentIC can run builds without spending a shared backend credential.
              </p>
            </div>
            <button className="byok-close" onClick={onClose} aria-label="Close">
              <X size={18} />
            </button>
          </div>

          <div className="byok-onboarding">
            <div className="byok-onboarding-card">
              <span className="byok-onboarding-icon">
                <Fingerprint size={16} />
              </span>
              <div>
                <strong>Required on public deployments</strong>
                <p>Your browser sends the key with build requests so the system runs on your account, not a shared server key.</p>
              </div>
            </div>
            <div className="byok-onboarding-card">
              <span className="byok-onboarding-icon">
                <LockKeyhole size={16} />
              </span>
              <div>
                <strong>Encrypted &amp; synced to your account</strong>
                <p>Keys are encrypted server-side and synced to your profile so they work on any device you sign in to.</p>
              </div>
            </div>
            <div className="byok-onboarding-card">
              <span className="byok-onboarding-icon">
                <Sparkles size={16} />
              </span>
              <div>
                <strong>Quick setup is enough for most users</strong>
                <p>Use one model and one key for everything unless you want separate providers for debugging, build, and docs agents.</p>
              </div>
            </div>
          </div>

          {/* Mode toggle */}
          <div className="byok-mode-toggle">
            <button
              className={`byok-mode-btn${quickMode ? ' byok-mode-btn--active' : ''}`}
              onClick={() => setQuickMode(true)}
            >
              Quick Setup
            </button>
            <button
              className={`byok-mode-btn${!quickMode ? ' byok-mode-btn--active' : ''}`}
              onClick={() => setQuickMode(false)}
            >
              Advanced
            </button>
          </div>

          {/* Quick mode */}
          {quickMode && (
            <div className="byok-quick">
              <p className="byok-quick-hint">
                One model and one key for all agent groups. This is the recommended setup if you are using a single provider.
              </p>
              <div className="byok-field-row">
                <div className="byok-field">
                  <label className="byok-field-label">Model</label>
                  <input
                    className="byok-field-input"
                    placeholder="e.g. gpt-4o, glm-4-plus, groq/llama-3.3-70b"
                    value={quickModel}
                    onChange={(e) => setQuickModel(e.target.value)}
                  />
                  <span className="byok-field-help">Use the exact model id from your provider dashboard.</span>
                </div>
                <div className="byok-field">
                  <label className="byok-field-label">Base URL <span className="byok-optional">(optional)</span></label>
                  <input
                    className="byok-field-input"
                    placeholder="Leave blank for hosted providers"
                    value={quickBaseUrl}
                    onChange={(e) => setQuickBaseUrl(e.target.value)}
                  />
                  <span className="byok-field-help">Only set this for OpenAI-compatible gateways, self-hosted proxies, or alternate endpoints.</span>
                </div>
              </div>
              <div className="byok-field">
                <label className="byok-field-label">API Key</label>
                <input
                  className="byok-field-input"
                  type="password"
                  placeholder="sk-... or provider-specific key"
                  value={quickKey}
                  onChange={(e) => setQuickKey(e.target.value)}
                  autoFocus
                />
                <MaskedKey value={quickKey} />
                <span className="byok-field-help">Example formats vary by provider. Paste the key exactly as issued.</span>
              </div>
              <div className="byok-guidance-callout">
                <strong>First run checklist</strong>
                <p>1. Paste your key. 2. Add a model id. 3. Save. 4. Return to Design Studio or HITL and launch your build.</p>
              </div>
            </div>
          )}

          {/* Advanced mode */}
          {!quickMode && (
            <div className="byok-advanced">
              <p className="byok-advanced-hint">
                Advanced mode is for operators who want different providers for specific agent groups.
              </p>
              {(['group1', 'group2', 'group3'] as GroupKey[]).map((key, index) => (
                <ByokGroupCard
                  key={key}
                  groupKey={key}
                  index={index}
                  meta={GROUP_META[key]}
                  group={groups[key]}
                  onUpdate={updateGroup}
                />
              ))}
            </div>
          )}

          {error && <div className="byok-error">{error}</div>}

          {/* Actions */}
          <div className="byok-footer">
            <span className="byok-footer-note">
              Keys are encrypted and synced to your account — available on all your devices.
            </span>
            <button className="byok-cancel-btn" onClick={onClose} disabled={saving}>
              Cancel
            </button>
            <button
              className={`byok-save-btn${saved ? ' byok-save-btn--done' : ''}`}
              onClick={handleSave}
              disabled={saving || !hasAnyKey}
            >
              {saved ? (
                <><Check size={16} /> Saved</>
              ) : saving ? (
                'Saving…'
              ) : (
                'Save & Sync Keys'
              )}
            </button>
          </div>
        </motion.div>
      </div>
    </AnimatePresence>
  );
};
