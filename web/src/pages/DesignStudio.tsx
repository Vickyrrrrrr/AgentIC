import { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import axios from 'axios';
import { BuildMonitor } from '../components/BuildMonitor';
import { ChipSummary } from '../components/ChipSummary';
import { fetchEventSource } from '@microsoft/fetch-event-source';

const API = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:7860').replace(/\/$/, '');

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
    const [error, setError] = useState('');

    // Build Options
    const [skipOpenlane, setSkipOpenlane] = useState(false);
    const [skipCoverage, setSkipCoverage] = useState(false);
    const [showAdvanced, setShowAdvanced] = useState(false);
    const [fullSignoff, setFullSignoff] = useState(false);
    const [maxRetries, setMaxRetries] = useState(5);
    const [showThinking, setShowThinking] = useState(false);
    const [minCoverage, setMinCoverage] = useState(80.0);
    const [strictGates, setStrictGates] = useState(true);
    const [pdkProfile, setPdkProfile] = useState("sky130");
    const [maxPivots, setMaxPivots] = useState(2);
    const [congestionThreshold, setCongestionThreshold] = useState(10.0);
    const [hierarchical, setHierarchical] = useState("auto");
    const [tbGateMode, setTbGateMode] = useState("strict");
    const [tbMaxRetries, setTbMaxRetries] = useState(3);
    const [tbFallbackTemplate, setTbFallbackTemplate] = useState("uvm_lite");
    const [coverageBackend, setCoverageBackend] = useState("auto");
    const [coverageFallbackPolicy, setCoverageFallbackPolicy] = useState("fail_closed");
    const [coverageProfile, setCoverageProfile] = useState("balanced");
    const [stageSchema, setStageSchema] = useState<StageSchemaItem[]>([]);

    const abortCtrlRef = useRef<AbortController | null>(null);

    // Auto-generate design name from prompt
    useEffect(() => {
        if (prompt.length > 8) {
            setDesignName(slugify(prompt));
        }
    }, [prompt]);

    const handleLaunch = async () => {
        if (!prompt.trim()) return;
        setError('');
        try {
            const res = await axios.post(`${API}/build`, {
                design_name: designName || slugify(prompt),
                description: prompt,
                skip_openlane: skipOpenlane,
                skip_coverage: skipCoverage,
                full_signoff: fullSignoff,
                max_retries: maxRetries,
                show_thinking: showThinking,
                min_coverage: minCoverage,
                strict_gates: strictGates,
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
            setError(e?.response?.data?.detail || 'Failed to start build. Is the backend running?');
        }
    };

    const startStreaming = (jid: string) => {
        if (abortCtrlRef.current) abortCtrlRef.current.abort();
        const ctrl = new AbortController();
        abortCtrlRef.current = ctrl;

        // Clear previous events on reconnect to prevent duplicates
        // (server replays all events from the beginning on each connection)
        setEvents([]);

        fetchEventSource(`${API}/build/stream/${jid}`, {
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
            const res = await axios.get(`${API}/build/result/${jid}`);
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
        axios.get(`${API}/pipeline/schema`)
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
                        className="prompt-screen"
                        initial={{ opacity: 0, y: 40 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -40 }}
                        transition={{ duration: 0.5 }}
                    >
                        <div className="prompt-hero">
                            <div className="chip-icon-glow">⚙️</div>
                            <h1 className="prompt-title">Design Your Chip</h1>
                            <p className="prompt-sub">
                                Describe any digital chip in plain English.<br />
                                AgentIC will autonomously write RTL, verify, and harden it to silicon.
                            </p>
                        </div>

                        <div className="prompt-card">
                            <div className="prompt-examples">
                                {['8-bit RISC CPU with Harvard architecture', 'AXI4 DMA engine with 4 channels', 'UART controller at 115200 baud'].map(ex => (
                                    <button key={ex} className="example-chip" onClick={() => setPrompt(ex)}>
                                        {ex}
                                    </button>
                                ))}
                            </div>

                            <textarea
                                className="prompt-textarea"
                                placeholder="Describe the chip you want to build in plain English... (e.g. 'A 4-bit counter with synchronous reset and clock enable')"
                                value={prompt}
                                onChange={e => setPrompt(e.target.value)}
                                rows={5}
                                autoFocus
                            />

                            {designName && (
                                <div className="design-name-preview">
                                    <span className="design-name-label">Design ID:</span>
                                    <input
                                        className="design-name-input"
                                        value={designName}
                                        onChange={e => setDesignName(e.target.value.replace(/[^a-z0-9_]/g, ''))}
                                    />
                                </div>
                            )}

                            <div className="prompt-options">
                                <label className="toggle-label" style={{ marginBottom: '1rem', display: 'flex' }}>
                                    <input
                                        type="checkbox"
                                        checked={skipOpenlane}
                                        onChange={e => setSkipOpenlane(e.target.checked)}
                                    />
                                    <span>Skip OpenLane (RTL + Verify only, faster)</span>
                                </label>

                                <button
                                    className="advanced-toggle-btn"
                                    onClick={() => setShowAdvanced(!showAdvanced)}
                                    style={{ background: 'transparent', border: '1px solid var(--border-mid)', color: 'var(--text-mid)', padding: '0.5rem 0.8rem', borderRadius: 'var(--radius)', cursor: 'pointer', fontSize: '0.9rem', width: '100%', textAlign: 'left', marginBottom: '1rem', transition: 'all var(--fast)', fontWeight: 500 }}
                                    onMouseOver={e => { e.currentTarget.style.borderColor = 'var(--text-dim)'; e.currentTarget.style.color = 'var(--text)'; }}
                                    onMouseOut={e => { e.currentTarget.style.borderColor = 'var(--border-mid)'; e.currentTarget.style.color = 'var(--text-mid)'; }}
                                >
                                    {showAdvanced ? '▼ Hide Advanced Options' : '▶ Show Advanced Options'}
                                </button>

                                {showAdvanced && (
                                    <div className="advanced-options-panel" style={{ background: 'var(--bg-card)', padding: '1.25rem', borderRadius: 'var(--radius-md)', fontSize: '0.9rem', display: 'flex', flexDirection: 'column', gap: '1rem', marginBottom: '1.5rem', border: '1px solid var(--border)', boxShadow: 'var(--shadow-xs)' }}>

                                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.25rem' }}>
                                            <label style={{ display: 'flex', flexDirection: 'column' }}>
                                                <span style={{ color: 'var(--text-mid)', marginBottom: '0.4rem', fontWeight: 500, fontSize: '0.8rem' }}>Max Retries</span>
                                                <input type="number" value={maxRetries} onChange={e => setMaxRetries(Number(e.target.value))} style={{ padding: '0.5rem 0.75rem', background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text)', borderRadius: 'var(--radius-xs)', fontSize: '0.9rem', outline: 'none', transition: 'border-color var(--fast)' }} />
                                            </label>
                                            <label style={{ display: 'flex', flexDirection: 'column' }}>
                                                <span style={{ color: 'var(--text-mid)', marginBottom: '0.4rem', fontWeight: 500, fontSize: '0.8rem' }}>Min Coverage (%)</span>
                                                <input type="number" step="0.1" value={minCoverage} onChange={e => setMinCoverage(Number(e.target.value))} style={{ padding: '0.5rem 0.75rem', background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text)', borderRadius: 'var(--radius-xs)', fontSize: '0.9rem', outline: 'none', transition: 'border-color var(--fast)' }} />
                                            </label>
                                        </div>

                                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.25rem' }}>
                                            <label style={{ display: 'flex', flexDirection: 'column' }}>
                                                <span style={{ color: 'var(--text-mid)', marginBottom: '0.4rem', fontWeight: 500, fontSize: '0.8rem' }}>PDK Profile</span>
                                                <select value={pdkProfile} onChange={e => setPdkProfile(e.target.value)} style={{ padding: '0.5rem 0.75rem', background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text)', borderRadius: 'var(--radius-xs)', fontSize: '0.9rem', outline: 'none' }}>
                                                    <option value="sky130">sky130</option>
                                                    <option value="gf180">gf180</option>
                                                </select>
                                            </label>
                                            <label style={{ display: 'flex', flexDirection: 'column' }}>
                                                <span style={{ color: 'var(--text-mid)', marginBottom: '0.4rem', fontWeight: 500, fontSize: '0.8rem' }}>Coverage Profile</span>
                                                <select value={coverageProfile} onChange={e => setCoverageProfile(e.target.value)} style={{ padding: '0.5rem 0.75rem', background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text)', borderRadius: 'var(--radius-xs)', fontSize: '0.9rem', outline: 'none' }}>
                                                    <option value="balanced">Balanced</option>
                                                    <option value="aggressive">Aggressive</option>
                                                    <option value="relaxed">Relaxed</option>
                                                </select>
                                            </label>
                                        </div>

                                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.25rem' }}>
                                            <label style={{ display: 'flex', flexDirection: 'column' }}>
                                                <span style={{ color: 'var(--text-mid)', marginBottom: '0.4rem', fontWeight: 500, fontSize: '0.8rem' }}>TB Gate Mode</span>
                                                <select value={tbGateMode} onChange={e => setTbGateMode(e.target.value)} style={{ padding: '0.5rem 0.75rem', background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text)', borderRadius: 'var(--radius-xs)', fontSize: '0.9rem', outline: 'none' }}>
                                                    <option value="strict">Strict</option>
                                                    <option value="relaxed">Relaxed</option>
                                                </select>
                                            </label>
                                            <label style={{ display: 'flex', flexDirection: 'column' }}>
                                                <span style={{ color: 'var(--text-mid)', marginBottom: '0.4rem', fontWeight: 500, fontSize: '0.8rem' }}>TB Fallback Template</span>
                                                <select value={tbFallbackTemplate} onChange={e => setTbFallbackTemplate(e.target.value)} style={{ padding: '0.5rem 0.75rem', background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text)', borderRadius: 'var(--radius-xs)', fontSize: '0.9rem', outline: 'none' }}>
                                                    <option value="uvm_lite">UVM Lite</option>
                                                    <option value="classic">Classic</option>
                                                </select>
                                            </label>
                                        </div>

                                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.25rem' }}>
                                            <label style={{ display: 'flex', flexDirection: 'column' }}>
                                                <span style={{ color: 'var(--text-mid)', marginBottom: '0.4rem', fontWeight: 500, fontSize: '0.8rem' }}>Coverage Backend</span>
                                                <select value={coverageBackend} onChange={e => setCoverageBackend(e.target.value)} style={{ padding: '0.5rem 0.75rem', background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text)', borderRadius: 'var(--radius-xs)', fontSize: '0.9rem', outline: 'none' }}>
                                                    <option value="auto">Auto</option>
                                                    <option value="verilator">Verilator</option>
                                                    <option value="iverilog">Icarus Verilog</option>
                                                </select>
                                            </label>
                                            <label style={{ display: 'flex', flexDirection: 'column' }}>
                                                <span style={{ color: 'var(--text-mid)', marginBottom: '0.4rem', fontWeight: 500, fontSize: '0.8rem' }}>Fallback Policy</span>
                                                <select value={coverageFallbackPolicy} onChange={e => setCoverageFallbackPolicy(e.target.value)} style={{ padding: '0.5rem 0.75rem', background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text)', borderRadius: 'var(--radius-xs)', fontSize: '0.9rem', outline: 'none' }}>
                                                    <option value="fail_closed">Fail Closed</option>
                                                    <option value="fallback_oss">Fallback OSS</option>
                                                    <option value="skip">Skip</option>
                                                </select>
                                            </label>
                                        </div>

                                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.25rem' }}>
                                            <label style={{ display: 'flex', flexDirection: 'column' }}>
                                                <span style={{ color: 'var(--text-mid)', marginBottom: '0.4rem', fontWeight: 500, fontSize: '0.8rem' }}>Hierarchical</span>
                                                <select value={hierarchical} onChange={e => setHierarchical(e.target.value)} style={{ padding: '0.5rem 0.75rem', background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text)', borderRadius: 'var(--radius-xs)', fontSize: '0.9rem', outline: 'none' }}>
                                                    <option value="auto">Auto</option>
                                                    <option value="on">On</option>
                                                    <option value="off">Off</option>
                                                </select>
                                            </label>
                                            <label style={{ display: 'flex', flexDirection: 'column' }}>
                                                <span style={{ color: 'var(--text-mid)', marginBottom: '0.4rem', fontWeight: 500, fontSize: '0.8rem' }}>Congestion Threshold (%)</span>
                                                <input type="number" step="0.1" value={congestionThreshold} onChange={e => setCongestionThreshold(Number(e.target.value))} style={{ padding: '0.5rem 0.75rem', background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text)', borderRadius: 'var(--radius-xs)', fontSize: '0.9rem', outline: 'none', transition: 'border-color var(--fast)' }} />
                                            </label>
                                        </div>

                                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.25rem' }}>
                                            <label style={{ display: 'flex', flexDirection: 'column' }}>
                                                <span style={{ color: 'var(--text-mid)', marginBottom: '0.4rem', fontWeight: 500, fontSize: '0.8rem' }}>Max Pivots</span>
                                                <input type="number" value={maxPivots} onChange={e => setMaxPivots(Number(e.target.value))} style={{ padding: '0.5rem 0.75rem', background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text)', borderRadius: 'var(--radius-xs)', fontSize: '0.9rem', outline: 'none', transition: 'border-color var(--fast)' }} />
                                            </label>
                                            <label style={{ display: 'flex', flexDirection: 'column' }}>
                                                <span style={{ color: 'var(--text-mid)', marginBottom: '0.4rem', fontWeight: 500, fontSize: '0.8rem' }}>TB Max Retries</span>
                                                <input type="number" value={tbMaxRetries} onChange={e => setTbMaxRetries(Number(e.target.value))} style={{ padding: '0.5rem 0.75rem', background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text)', borderRadius: 'var(--radius-xs)', fontSize: '0.9rem', outline: 'none', transition: 'border-color var(--fast)' }} />
                                            </label>
                                        </div>

                                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '1.5rem', marginTop: '0.5rem', background: 'var(--bg)', padding: '1rem', borderRadius: 'var(--radius)', border: '1px solid var(--border-mid)' }}>
                                            <label className="toggle-label" style={{ display: 'flex', alignItems: 'center' }}>
                                                <input type="checkbox" checked={skipCoverage} onChange={e => setSkipCoverage(e.target.checked)} />
                                                <span style={{ marginLeft: '0.5rem', color: 'var(--text)', fontWeight: 500 }}>Skip Coverage</span>
                                            </label>
                                            <label className="toggle-label" style={{ display: 'flex', alignItems: 'center' }}>
                                                <input type="checkbox" checked={fullSignoff} onChange={e => setFullSignoff(e.target.checked)} />
                                                <span style={{ marginLeft: '0.5rem', color: 'var(--text)', fontWeight: 500 }}>Full Signoff</span>
                                            </label>
                                            <label className="toggle-label" style={{ display: 'flex', alignItems: 'center' }}>
                                                <input type="checkbox" checked={strictGates} onChange={e => setStrictGates(e.target.checked)} />
                                                <span style={{ marginLeft: '0.5rem', color: 'var(--text)', fontWeight: 500 }}>Strict Gates</span>
                                            </label>
                                            <label className="toggle-label" style={{ display: 'flex', alignItems: 'center' }}>
                                                <input type="checkbox" checked={showThinking} onChange={e => setShowThinking(e.target.checked)} />
                                                <span style={{ marginLeft: '0.5rem', color: 'var(--text)', fontWeight: 500 }}>Show Thinking</span>
                                            </label>
                                        </div>
                                    </div>
                                )}
                            </div>

                            {error && <div className="error-banner">⚠️ {error}</div>}

                            <motion.button
                                className="launch-btn"
                                onClick={handleLaunch}
                                disabled={!prompt.trim()}
                                whileHover={{ scale: 1.03 }}
                                whileTap={{ scale: 0.97 }}
                            >
                                <span style={{ fontSize: '1.4rem' }}>🚀</span>
                                Launch Autonomous Build
                            </motion.button>
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
                            onReset={handleReset}
                        />
                    </motion.div>
                )}

            </AnimatePresence>
        </div>
    );
};
