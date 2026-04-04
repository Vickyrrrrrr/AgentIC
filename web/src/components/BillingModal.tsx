import { useState } from 'react';

import { motion, AnimatePresence } from 'framer-motion';

export const BillingModal = ({ isOpen, onClose, onKeySaved }: { isOpen: boolean, onClose: () => void, onKeySaved: () => void }) => {
  const [apiKey] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const [group1, setGroup1] = useState({ model: '', apiKey: '', baseUrl: '' });
  const [group2, setGroup2] = useState({ model: '', apiKey: '', baseUrl: '' });
  const [group3, setGroup3] = useState({ model: '', apiKey: '', baseUrl: '' });

  const handleSaveKey = async () => {
    if (!group1.apiKey.trim() && !apiKey.trim()) return;
    setSaving(true);
    setError('');
    
    // Fallback to legacy single key if they just filled the first box or something
    let payload = apiKey;
    if (group1.apiKey || group2.apiKey || group3.apiKey) {
      payload = JSON.stringify({
        group1: { model: group1.model, api_key: group1.apiKey, base_url: group1.baseUrl },
        group2: { model: group2.model, api_key: group2.apiKey, base_url: group2.baseUrl },
        group3: { model: group3.model, api_key: group3.apiKey, base_url: group3.baseUrl }
      });
    }

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
      <div className="billing-modal-overlay">
        <motion.div 
          className="sci-fi-card billing-modal-content"
          initial={{ opacity: 0, scale: 0.95, y: 10 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.95, y: 10 }}
          transition={{ duration: 0.2 }}
          style={{ width: "90%", maxWidth: "600px", maxHeight: "90vh", overflowY: "auto" }}
        >
          <button className="billing-modal-close" onClick={onClose}>✕</button>
          
          <div className="billing-header">
            <div className="billing-icon">⚠️</div>
            <h2 className="billing-title">Bring Your Own Key (BYOK)</h2>
          </div>
          
          <p className="billing-sub" style={{marginBottom: "20px"}}>
            AgentIC requires 3 separate LLM configurations to run efficiently. 
            You can configure different models (e.g. Claude, OpenAI, Groq) for each group of multi-agent roles.
          </p>

          <div className="byok-section">
            <h3 className="byok-title">1. Reasoning Agents (Architect, Debugger, Manager)</h3>
            <p className="byok-desc">Used for deep reasoning and planning. (Recommended: <strong>openai/gpt-4o</strong> or <strong>anthropic/claude-3-5-sonnet-20241022</strong>)</p>
            <div style={{display: 'flex', gap: '10px', marginBottom: '10px'}}>
               <input className="byok-input" placeholder="Model (e.g. openai/gpt-4o)" value={group1.model} onChange={e => setGroup1({...group1, model: e.target.value})} />
               <input className="byok-input" placeholder="Base URL (optional, for custom endpoints)" value={group1.baseUrl} onChange={e => setGroup1({...group1, baseUrl: e.target.value})} />
            </div>
            <input className="byok-input" type="password" placeholder="API Key (sk-...)" value={group1.apiKey} onChange={e => setGroup1({...group1, apiKey: e.target.value})} autoFocus />
            
            <h3 className="byok-title" style={{marginTop: "20px"}}>2. Coding Agents (Designer, Testbench, Verifier)</h3>
            <p className="byok-desc">Used for heavy code generation. (Recommended: <strong>openai/gpt-4o</strong> or <strong>anthropic/claude-3-5-sonnet-20241022</strong>)</p>
            <div style={{display: 'flex', gap: '10px', marginBottom: '10px'}}>
               <input className="byok-input" placeholder="Model (e.g. anthropic/claude-3-5-sonnet-20241022)" value={group2.model} onChange={e => setGroup2({...group2, model: e.target.value})} />
               <input className="byok-input" placeholder="Base URL (optional)" value={group2.baseUrl} onChange={e => setGroup2({...group2, baseUrl: e.target.value})} />
            </div>
            <input className="byok-input" type="password" placeholder="API Key (sk-...)" value={group2.apiKey} onChange={e => setGroup2({...group2, apiKey: e.target.value})} />

            <h3 className="byok-title" style={{marginTop: "20px"}}>3. Iterative Agents (Fixer, Physical)</h3>
            <p className="byok-desc">Used for blazing fast iteration and syntax fixing. (Recommended: <strong>groq/llama-3.3-70b-versatile</strong>)</p>
            <div style={{display: 'flex', gap: '10px', marginBottom: '10px'}}>
               <input className="byok-input" placeholder="Model (e.g. groq/llama-3.3-70b-versatile)" value={group3.model} onChange={e => setGroup3({...group3, model: e.target.value})} />
               <input className="byok-input" placeholder="Base URL (optional)" value={group3.baseUrl} onChange={e => setGroup3({...group3, baseUrl: e.target.value})} />
            </div>
            <input className="byok-input" type="password" placeholder="API Key (sk-...)" value={group3.apiKey} onChange={e => setGroup3({...group3, apiKey: e.target.value})} />

            {error && <div className="byok-error">{error}</div>}
            
            <button
              className="action-btn byok-submit"
              onClick={handleSaveKey}
              style={{marginTop: "20px"}}
              disabled={saving || !group1.apiKey.trim()}
            >
              {saving ? (
                <span>Encrypting & Saving...</span>
              ) : (
                <span>Save All Encrypted Keys →</span>
              )}
            </button>
          </div>
        </motion.div>
      </div>
    </AnimatePresence>
  );
};
