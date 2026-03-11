import { useState } from 'react';
import { api } from '../api';
import { motion, AnimatePresence } from 'framer-motion';

export const BillingModal = ({ isOpen, onClose, onKeySaved }: { isOpen: boolean, onClose: () => void, onKeySaved: () => void }) => {
  const [apiKey, setApiKey] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const handleSaveKey = async () => {
    if (!apiKey.trim()) return;
    setSaving(true);
    setError('');
    try {
      await api.post('/profile/api-key', { api_key: apiKey.trim() });
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
      <div className="billing-modal-overlay">
        <motion.div 
          className="sci-fi-card billing-modal-content"
          initial={{ opacity: 0, scale: 0.95, y: 10 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.95, y: 10 }}
          transition={{ duration: 0.2 }}
        >
          <button className="billing-modal-close" onClick={onClose}>✕</button>
          
          <div className="billing-header">
            <div className="billing-icon">⚠️</div>
            <h2 className="billing-title">Build Limit Reached</h2>
          </div>
          
          <p className="billing-sub">
            You've used your 2 free autonomous chip builds! To continue generating RTL and running Silicon validations, you must provide your own LLM API key or upgrade your plan.
          </p>

          <div className="byok-section">
            <h3 className="byok-title">Bring Your Own Key (BYOK)</h3>
            <p className="byok-desc">
              Enter an OpenAI, Anthropic, or Groq API key. Your key is <strong>AES-256 encrypted at rest</strong> using a secure server-side key and is never logged or exposed.
            </p>
            
            <input
              className="byok-input"
              type="password"
              placeholder="sk-..."
              value={apiKey}
              onChange={e => setApiKey(e.target.value)}
              autoFocus
            />
            
            {error && <div className="byok-error">{error}</div>}
            
            <button
              className="action-btn byok-submit"
              onClick={handleSaveKey}
              disabled={saving || !apiKey.trim()}
            >
              {saving ? (
                <span>Encrypting & Saving...</span>
              ) : (
                <span>Save Encrypted Key →</span>
              )}
            </button>
          </div>

          <div className="billing-footer">
            or <a href="mailto:sales@agentic.ai" className="billing-link">contact sales</a> for an Enterprise/Pro Plan.
          </div>
        </motion.div>
      </div>
    </AnimatePresence>
  );
};
