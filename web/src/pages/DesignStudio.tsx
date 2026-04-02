import { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { BuildMonitor } from '../components/BuildMonitor';
import { ChipSummary } from '../components/ChipSummary';
import { BillingModal } from '../components/BillingModal';
import { fetchEventSource } from '@microsoft/fetch-event-source';
import { api, API_BASE } from '../api';

type Phase = 'prompt' | 'building' | 'done';

interface BuildEvent {
    type: string;
    state: string;
    message: string;
    step: number;
    total_steps: number;
    timestamp: number;
    status?: string;  // present on stream_end events
}

interface StageSchemaItem {
    state: string;
    label: string;
    icon: string;
}

function slugify(text: string): string {
    return text
        .toLowerCase()
        .replace(/[^a-z0-9\s_]/g, '')
        .trim()
        .split(/\s+/)
        .slice(0, 4)
        .join('_')
        .substring(0, 48);
}

export const DesignStudio = () => {
    const [phase, setPhase] = useState<Phase>('prompt');
    const [prompt, setPrompt] = useState('');
    const [designName, setDesignName] = useState('');
    const [jobId, setJobId] = useState('');
    const [events, setEvents] = useState<BuildEvent[]>([]);
    const [jobStatus, setJobStatus] = useState<'queued' | 'running' | 'done' | 'failed' | 'cancelled' | 'cancelling'>('queued');
    const [result, setResult] = useState<any>(null);
    const [ // @ts-ignore
    error, setError] = useState('');

    // Billing / Profile State
    const [profile, setProfile] = useState<{ auth_enabled: boolean, plan: string, successful_builds: number, has_byok_key: boolean } | null>(null);
    const [showBillingModal, setShowBillingModal] = useState(false);

    // Build Options
    const isHuggingFace = window.location.hostname.includes("hf.space") || window.location.hostname.includes("huggingface.co");
    const [skipOpenlane, setSkipOpenlane] = useState(isHuggingFace);
    const [maxRetries, _setMaxRetries] = useState(5);
    const [minCoverage, _setMinCoverage] = useState(80.0);
    const [aiModel, setAiModel] = useState<'AgentIC' | 'BYOK'>('AgentIC');
    const [pdkProfile, _setPdkProfile] = useState("sky130");
    const [maxPivots, _setMaxPivots] = useState(2);
    const [congestionThreshold, _setCongestionThreshold] = useState(10.0);
    const [hierarchical, _setHierarchical] = useState("auto");
    const [tbGateMode, _setTbGateMode] = useState("strict");
    const [tbMaxRetries, _setTbMaxRetries] = useState(3);
    const [tbFallbackTemplate, _setTbFallbackTemplate] = useState("uvm_lite");
    const [coverageBackend, _setCoverageBackend] = useState("auto");
    const [coverageFallbackPolicy, _setCoverageFallbackPolicy] = useState("fail_closed");
    const [coverageProfile, _setCoverageProfile] = useState("balanced");
    const [stageSchema, setStageSchema] = useState<StageSchemaItem[]>([]);

    const abortCtrlRef = useRef<AbortController | null>(null);

    // Auto-generate design name from prompt
    useEffect(() => {
        if (prompt.length > 8) {
            setDesignName(slugify(prompt));
        }
    }, [prompt]);

    // Fetch Profile Limits
    useEffect(() => {
        api.get('/profile')
            .then(res => setProfile(res.data))
            .catch(() => setProfile(null)); // Ignored explicitly if no auth
    }, []);

    const handleLaunch = async () => {
        if (!prompt.trim()) return;
        setError('');

        // Billing Guard: enforce 2 free successful builds
        if (profile?.auth_enabled) {
            const { plan, successful_builds, has_byok_key } = profile;
            if (plan === 'free' && successful_builds >= 2 && !has_byok_key) {
                setShowBillingModal(true);
                return;
            }
        }

        try {
            const res = await api.post(`/build`, {
                design_name: designName || slugify(prompt),
                description: prompt,
                skip_openlane: skipOpenlane,
                skip_coverage: false,
                full_signoff: false,
                max_retries: maxRetries,
                show_thinking: false,
                min_coverage: minCoverage,
                strict_gates: false,
                pdk_profile: pdkProfile,
                max_pivots: maxPivots,
                congestion_threshold: congestionThreshold,
                hierarchical: hierarchical,
                tb_gate_mode: tbGateMode,
                tb_max_retries: tbMaxRetries,
                tb_fallback_template: tbFallbackTemplate,
                coverage_backend: coverageBackend,
                coverage_fallback_policy: coverageFallbackPolicy,
                coverage_profile: coverageProfile
            });
            const { job_id } = res.data;
            setJobId(job_id);
            setPhase('building');
            startStreaming(job_id);
        } catch (e: any) {
            if (e?.code === 'ERR_NETWORK' || !e?.response) {
                setError('Backend is offline. Start the server with: uvicorn server.api:app --port 7860');
            } else {
                setError(e?.response?.data?.detail || 'Build failed. Check the backend logs.');
            }
        }
    };

    const startStreaming = (jid: string) => {
        if (abortCtrlRef.current) abortCtrlRef.current.abort();
        const ctrl = new AbortController();
        abortCtrlRef.current = ctrl;

        // Clear previous events on reconnect to prevent duplicates
        // (server replays all events from the beginning on each connection)
        setEvents([]);

        fetchEventSource(`${API_BASE}/build/stream/${jid}`, {
            method: 'GET',
            headers: {
                'ngrok-skip-browser-warning': 'true',
                'Accept': 'text/event-stream',
            },
            signal: ctrl.signal,
            onmessage(evt) {
                try {
                    const data: BuildEvent = JSON.parse(evt.data);
                    if (data.type === 'ping') return;
                    if (data.type === 'stream_end') {
                        ctrl.abort();
                        fetchResult(jid, data.status as any);
                        return;
                    }
                    setEvents(prev => {
                        // Deduplicate: skip if last event has same message + type
                        const last = prev[prev.length - 1];
                        if (last && last.message === data.message && last.type === data.type) {
                            return prev;
                        }
                        return [...prev, data];
                    });
                    setJobStatus(data.type === 'error' ? 'failed' : 'running');
                } catch { /* ignore parse errors */ }
            },
            onerror(err) {
                ctrl.abort();
                throw err;
            }
        });
    };

    const fetchResult = async (jid: string, status: string) => {
        setJobStatus(status === 'done' ? 'done' : 'failed');
        try {
            const res = await api.get(`/build/result/${jid}`);
            setResult(res.data.result);
        } catch { /* result might not exist if failed early */ }
        setPhase('done');

        // Browser notification
        if ('Notification' in window && Notification.permission === 'granted') {
            new Notification('AgentIC — Chip Build Complete 🎉', {
                body: `Your chip "${designName}" has finished ${status === 'done' ? 'successfully!' : 'with errors.'}`,
                icon: '/chip-icon.png',
            });
        }
    };

    const handleReset = () => {
        abortCtrlRef.current?.abort();
        setPhase('prompt');
        setEvents([]);
        setResult(null);
        setJobId('');
        setJobStatus('queued');
        setError('');
        setPrompt('');
    };

    // Request notification permission on mount
    useEffect(() => {
        if ('Notification' in window && Notification.permission === 'default') {
            Notification.requestPermission();
        }
        api.get(`/pipeline/schema`)
            .then(res => setStageSchema(res.data?.stages || []))
            .catch(() => setStageSchema([]));
        return () => abortCtrlRef.current?.abort();
    }, []);

    return (
        <div className="studio-root">
            <AnimatePresence mode="wait">

                {/* ── PHASE A: Prompt ──────────────────────────── */}
                {phase === 'prompt' && (
                    <motion.div
                        key="prompt"
                        className="prompt-screen-modern"
                        initial={{ opacity: 0, y: 40 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -40 }}
                        transition={{ duration: 0.5 }}
                    >
                        <div className="prompt-modern-container">
                            <h1 className="prompt-title-modern">AgentIC Studio</h1>
                            
                            <div className="prompt-input-wrapper">
                                <div className="prompt-input-inner">
                                    <span className="prompt-input-icon">➕</span>
                                    <textarea
                                        className="prompt-textarea-modern"
                                        placeholder="Describe the chip you want to build..."
                                        value={prompt}
                                        onChange={e => setPrompt(e.target.value)}
                                        rows={1}
                                        onKeyDown={(e) => {
                                            if (e.key === 'Enter' && !e.shiftKey) {
                                                e.preventDefault();
                                                if (prompt.trim()) handleLaunch();
                                            }
                                            e.currentTarget.style.height = 'auto';
                                            e.currentTarget.style.height = e.currentTarget.scrollHeight + 'px';
                                        }}
                                        autoFocus
                                    />
                                    <button 
                                        className="prompt-submit-btn" 
                                        onClick={handleLaunch} 
                                        disabled={!prompt.trim()}
                                    >
                                        <div className="submit-arrow">↑</div>
                                    </button>
                                </div>
                                <div className="prompt-input-footer">
                                    <div className="model-selector">
                                        <button 
                                            className={`footer-btn ${aiModel === 'AgentIC' ? 'active' : ''}`}
                                            onClick={() => setAiModel('AgentIC')}
                                        >
                                            🚀 AgentIC Neural
                                        </button>
                                        <button 
                                            className={`footer-btn ${aiModel === 'BYOK' ? 'active' : ''}`}
                                            onClick={() => setAiModel('BYOK')}
                                        >
                                            🔑 BYOK Model
                                        </button>
                                    </div>
                                    
                                    <div className="stage-selector">
                                        <button 
                                            className={`footer-btn ${skipOpenlane ? 'active' : ''}`}
                                            onClick={() => setSkipOpenlane(true)}
                                            title="Stop after verification"
                                        >
                                            💻 RTL Generation & Verification
                                        </button>
                                        <button 
                                            className={`footer-btn ${!skipOpenlane ? 'active' : ''}`}
                                            onClick={() => {
                                                if (isHuggingFace) {
                                                    alert("GDS Layout is temporarily under maintenance on the cloud platform. It will be available back in a few days. Using RTL & Verification mode for now.");
                                                } else {
                                                    setSkipOpenlane(false);
                                                }
                                            }}
                                            title={isHuggingFace ? "Full silicon flow to GDS (Under Cloud Maintenance)" : "Full silicon flow to GDS"}
                                        >
                                            {isHuggingFace ? "🏗️ Full GDS Signoff (Cloud Disabled)" : "🏗️ Full GDS Signoff"}
                                        </button>
                                    </div>
                                </div>
                            </div>

                            <div className="prompt-quick-links">
                                <div className="quick-links-header">
                                    <span className="icon">⏱️</span> Quick Starts
                                </div>
                                <div className="quick-links-grid">
                                    {['8-bit RISC CPU with Harvard architecture', 'AXI4 DMA engine with 4 channels', 'UART controller at 115200 baud'].map(ex => (
                                        <button key={ex} className="quick-link-card" onClick={() => setPrompt(ex)}>
                                            <span className="card-icon">⚡</span>
                                            <span className="card-text">{ex}</span>
                                        </button>
                                    ))}
                                </div>
                            </div>
                        </div>
                    </motion.div>
                )}

                {/* ── PHASE B: Building ────────────────────────── */}
                {phase === 'building' && (
                    <motion.div
                        key="building"
                        initial={{ opacity: 0, x: 40 }}
                        animate={{ opacity: 1, x: 0 }}
                        exit={{ opacity: 0, x: -40 }}
                        transition={{ duration: 0.4 }}
                    >
                        <BuildMonitor
                            designName={designName}
                            jobId={jobId}
                            events={events}
                            jobStatus={jobStatus}
                            stageSchema={stageSchema}
                        />
                    </motion.div>
                )}

                {/* ── PHASE C: Result ──────────────────────────── */}
                {phase === 'done' && (
                    <motion.div
                        key="done"
                        initial={{ opacity: 0, scale: 0.96 }}
                        animate={{ opacity: 1, scale: 1 }}
                        exit={{ opacity: 0 }}
                        transition={{ duration: 0.5 }}
                    >
                        <ChipSummary
                            designName={designName}
                            result={result}
                            jobStatus={jobStatus}
                            events={events}
                            jobId={jobId}
                            onReset={handleReset}
                        />
                    </motion.div>
                )}

            </AnimatePresence>

            <BillingModal 
                isOpen={showBillingModal}
                onClose={() => setShowBillingModal(false)}
                onKeySaved={() => {
                    // Update profile locally to unblock
                    setProfile(prev => prev ? { ...prev, has_byok_key: true } : null);
                }}
            />
        </div>
    );
};
