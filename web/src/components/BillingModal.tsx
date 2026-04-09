import { useEffect, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, ChevronDown, ChevronUp, Eye, EyeOff, Check } from 'lucide-react';

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

  useEffect(() => {
    if (!isOpen) { setSaved(false); return; }
    setError('');
    try {
      const raw = localStorage.getItem('agentic_byok_key');
      if (!raw) { setGroups(DEFAULT_GROUPS); return; }
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== 'object') return;

      const nextGroups: GroupState = {
        group1: { model: parsed.group1?.model || '', apiKey: parsed.group1?.api_key || '', baseUrl: parsed.group1?.base_url || '' },
        group2: { model: parsed.group2?.model || '', apiKey: parsed.group2?.api_key || '', baseUrl: parsed.group2?.base_url || '' },
        group3: { model: parsed.group3?.model || '', apiKey: parsed.group3?.api_key || '', baseUrl: parsed.group3?.base_url || '' },
      };
      setGroups(nextGroups);

      // If all keys are the same, show quick mode
      const k1 = nextGroups.group1.apiKey;
      if (k1 && k1 === nextGroups.group2.apiKey && k1 === nextGroups.group3.apiKey) {
        setQuickKey(k1);
        setQuickModel(nextGroups.group1.model);
        setQuickBaseUrl(nextGroups.group1.baseUrl);
        setQuickMode(true);
      }
    } catch {
      setGroups(DEFAULT_GROUPS);
    }
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
      localStorage.setItem('agentic_byok_key', JSON.stringify(payload));
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
                Bring your own LLM keys. Works with any OpenAI-compatible provider.
              </p>
            </div>
            <button className="byok-close" onClick={onClose} aria-label="Close">
              <X size={18} />
            </button>
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
                One key for all agent groups. Perfect if you're using a single provider.
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
                </div>
                <div className="byok-field">
                  <label className="byok-field-label">Base URL <span className="byok-optional">(optional)</span></label>
                  <input
                    className="byok-field-input"
                    placeholder="Leave blank for hosted providers"
                    value={quickBaseUrl}
                    onChange={(e) => setQuickBaseUrl(e.target.value)}
                  />
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
              </div>
            </div>
          )}

          {/* Advanced mode */}
          {!quickMode && (
            <div className="byok-advanced">
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
                'Save Keys'
              )}
            </button>
          </div>
        </motion.div>
      </div>
    </AnimatePresence>
  );
};
