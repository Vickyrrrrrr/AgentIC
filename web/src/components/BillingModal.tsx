import { useEffect, useState } from 'react';

import { motion, AnimatePresence } from 'framer-motion';

type GroupKey = 'group1' | 'group2' | 'group3';

type GroupState = Record<GroupKey, { model: string; apiKey: string; baseUrl: string }>;

const DEFAULT_GROUPS: GroupState = {
  group1: { model: '', apiKey: '', baseUrl: '' },
  group2: { model: '', apiKey: '', baseUrl: '' },
  group3: { model: '', apiKey: '', baseUrl: '' },
};

const GROUP_META: Record<
  GroupKey,
  { title: string; subtitle: string; recommended: string; modelPlaceholder: string }
> = {
  group1: {
    title: 'Fix & Debug Agents',
    subtitle: 'Fixer, Debugger, Reasoner',
    recommended: 'openai/deepseek-ai/deepseek-v3.2 or another strong reasoning model',
    modelPlaceholder: 'Model (e.g. openai/deepseek-ai/deepseek-v3.2)',
  },
  group2: {
    title: 'Core Build Agents',
    subtitle: 'Architect, Designer, Testbench, Verifier, Manager, Physical',
    recommended: 'glm-4-plus or another strong coding / planning model',
    modelPlaceholder: 'Model (e.g. glm-4-plus)',
  },
  group3: {
    title: 'Documentation Agents',
    subtitle: 'Documenter, Reporter, fast text tasks',
    recommended: 'groq/llama-3.3-70b-versatile',
    modelPlaceholder: 'Model (e.g. groq/llama-3.3-70b-versatile)',
  },
};

export const BillingModal = ({ isOpen, onClose, onKeySaved }: { isOpen: boolean, onClose: () => void, onKeySaved: () => void }) => {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [groups, setGroups] = useState<GroupState>(DEFAULT_GROUPS);

  useEffect(() => {
    if (!isOpen) return;
    setError('');
    try {
      const raw = localStorage.getItem('agentic_byok_key');
      if (!raw) {
        setGroups(DEFAULT_GROUPS);
        return;
      }
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== 'object') return;

      const nextGroups: GroupState = {
        group1: {
          model: parsed.group1?.model || '',
          apiKey: parsed.group1?.api_key || '',
          baseUrl: parsed.group1?.base_url || '',
        },
        group2: {
          model: parsed.group2?.model || '',
          apiKey: parsed.group2?.api_key || '',
          baseUrl: parsed.group2?.base_url || '',
        },
        group3: {
          model: parsed.group3?.model || '',
          apiKey: parsed.group3?.api_key || '',
          baseUrl: parsed.group3?.base_url || '',
        },
      };
      setGroups(nextGroups);
    } catch {
      // Legacy single-key payload; start with blank grouped UI.
      setGroups(DEFAULT_GROUPS);
    }
  }, [isOpen]);

  const hasAnyKey = Object.values(groups).some((group) => group.apiKey.trim());

  const updateGroup = (key: GroupKey, field: 'model' | 'apiKey' | 'baseUrl', value: string) => {
    setGroups((prev) => ({
      ...prev,
      [key]: { ...prev[key], [field]: value },
    }));
  };

  const handleSaveKey = async () => {
    if (!hasAnyKey) return;
    setSaving(true);
    setError('');

    const payload = JSON.stringify({
      group1: {
        model: groups.group1.model,
        api_key: groups.group1.apiKey,
        base_url: groups.group1.baseUrl,
      },
      group2: {
        model: groups.group2.model,
        api_key: groups.group2.apiKey,
        base_url: groups.group2.baseUrl,
      },
      group3: {
        model: groups.group3.model,
        api_key: groups.group3.apiKey,
        base_url: groups.group3.baseUrl,
      },
    });

    try {
      localStorage.setItem('agentic_byok_key', payload);
      onKeySaved();
      onClose();
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to securely store API key.');
    } finally {
      setSaving(false);
    }
  };

  if (!isOpen) return null;

  return (
    <AnimatePresence>
      <div className="billing-modal-overlay" onClick={onClose}>
        <motion.div
          className="billing-modal-content byok-modal"
          initial={{ opacity: 0, scale: 0.98, y: 8 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.98, y: 8 }}
          transition={{ duration: 0.16 }}
          onClick={(e) => e.stopPropagation()}
        >
          <button className="billing-modal-close" onClick={onClose} aria-label="Close BYOK modal">
            Close
          </button>

          <div className="billing-header byok-header">
            <h2 className="billing-title">Configure BYOK Keys</h2>
            <p className="billing-sub">
              AgentIC routes different agent roles to different model groups. Leave model and base URL blank if you only want to provide keys.
            </p>
          </div>

          <div className="byok-cards">
            {(['group1', 'group2', 'group3'] as GroupKey[]).map((key, index) => {
              const meta = GROUP_META[key];
              const group = groups[key];
              return (
                <section className="byok-card" key={key}>
                  <div className="byok-card-head">
                    <span className="byok-card-index">{index + 1}</span>
                    <div>
                      <h3 className="byok-title">{meta.title}</h3>
                      <p className="byok-desc">{meta.subtitle}</p>
                    </div>
                  </div>
                  <p className="byok-recommendation">Recommended: {meta.recommended}</p>

                  <div className="byok-row">
                    <input
                      className="byok-input"
                      placeholder={meta.modelPlaceholder}
                      value={group.model}
                      onChange={(e) => updateGroup(key, 'model', e.target.value)}
                    />
                    <input
                      className="byok-input"
                      placeholder="Base URL (optional)"
                      value={group.baseUrl}
                      onChange={(e) => updateGroup(key, 'baseUrl', e.target.value)}
                    />
                  </div>
                  <input
                    className="byok-input"
                    type="password"
                    placeholder="API Key"
                    value={group.apiKey}
                    onChange={(e) => updateGroup(key, 'apiKey', e.target.value)}
                    autoFocus={key === 'group1' && !groups.group1.apiKey}
                  />
                </section>
              );
            })}
          </div>

          {error && <div className="byok-error">{error}</div>}

          <div className="byok-actions">
            <button className="byok-cancel" onClick={onClose} disabled={saving}>
              Cancel
            </button>
            <button
              className="action-btn byok-submit"
              onClick={handleSaveKey}
              disabled={saving || !hasAnyKey}
            >
              {saving ? 'Saving...' : 'Save Keys'}
            </button>
          </div>
        </motion.div>
      </div>
    </AnimatePresence>
  );
};
