import { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
    ArrowRight,
    Bot,
    Cpu,
    Fingerprint,
    KeyRound,
    Layers3,
    Rocket,
    ShieldCheck,
    Sparkles,
    Waypoints,
} from 'lucide-react';
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

const QUICK_STARTS = [
    {
        title: 'RISC compute core',
        prompt: '8-bit RISC CPU with Harvard architecture',
        note: 'Balanced control core with instruction and data separation.',
    },
    {
        title: 'DMA fabric',
        prompt: 'AXI4 DMA engine with 4 channels',
        note: 'Throughput-focused transport block for embedded systems.',
    },
    {
        title: 'Peripheral controller',
        prompt: 'UART controller at 115200 baud',
        note: 'Fast path for a clean verification-first peripheral bring-up.',
    },
];

const DELIVERY_MODES = [
    {
        title: 'Verification-first',
        detail: 'RTL generation, lint, simulation, and coverage-focused validation.',
    },
    {
        title: 'Silicon path',
        detail: 'Carry the design into physical implementation and signoff artifacts when enabled.',
    },
    {
        title: 'BYOK protected',
        detail: 'Public deployments require the operator to provide a valid LLM key before launch.',
    },
];

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
    const [profile, setProfile] = useState<{
        auth_enabled: boolean,
        plan?: string,
        successful_builds?: number,
        workspace_successful_builds?: number,
        total_builds?: number,
        running_builds?: number,
        has_byok_key?: boolean,
        plan_type?: string,
        build_limit?: number | null,
    } | null>(null);
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
        // Load profile + billing status in parallel
        Promise.allSettled([
            api.get('/profile'),
            api.get('/billing/status'),
        ]).then(([profileRes, billingRes]) => {
            const prof = profileRes.status === 'fulfilled' ? profileRes.value.data : null;
            const billing = billingRes.status === 'fulfilled' ? billingRes.value.data : null;
            if (prof && billing) {
                setProfile({ ...prof, plan_type: billing.plan_type, build_limit: billing.build_limit });
            } else if (prof) {
                setProfile(prof);
            }
        }).catch(() => setProfile(null));
    }, []);

    const hasLocalByok = Boolean(localStorage.getItem('agentic_byok_key'));
    const hasServerByok = Boolean(profile?.has_byok_key);
    const isAgenticPaid = profile?.plan_type === 'agentic_paid';
    const hasActivePlan = isAgenticPaid || hasLocalByok || hasServerByok;
    const launchStatus = isAgenticPaid ? 'AgentIC Model active' : hasActivePlan ? 'BYOK configured' : 'Configure required';
    const launchModeLabel = skipOpenlane ? 'Verification-first run' : 'Full silicon path';
    const workspaceSuccessfulBuilds = profile?.workspace_successful_builds ?? profile?.successful_builds ?? 0;
    const usageLabel = profile
        ? `${workspaceSuccessfulBuilds} successful · ${profile.total_builds ?? 0} total builds on ${profile.plan ?? 'local'}`
        : 'Local workspace mode';


    const handleLaunch = async () => {
        if (!prompt.trim()) return;
        setError('');

        // Guard: check if they selected AgentIC model without paying
        if (aiModel === 'AgentIC' && !isAgenticPaid) {
            window.history.pushState({}, '', '/pricing');
            window.dispatchEvent(new Event('popstate'));
            return;
        }

        // Guard: require BYOK configured if BYOK mode is selected
        if (aiModel === 'BYOK' && !hasLocalByok && !hasServerByok) {
            setShowBillingModal(true);
            return;
        }

        // Billing Guard: enforce build limit for AgentIC-paid users
        if (aiModel === 'AgentIC' && profile) {
            const { successful_builds, build_limit } = profile;
            if (build_limit !== null && build_limit !== undefined && (successful_builds ?? 0) >= build_limit) {
                setShowBillingModal(true);
                return;
            }
        }

        try {
            // Build the BYOK payload if user has BYOK configured
            const byokRaw = localStorage.getItem('agentic_byok_key');
            const byokKey = byokRaw ? JSON.parse(byokRaw) : null;

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
                coverage_profile: coverageProfile,
                // Send BYOK key as JSON string in body
                api_key: byokKey ? JSON.stringify(byokKey) : null,
                // Tell backend which path: agentic_paid or byok
                plan_type: aiModel === 'AgentIC' ? 'agentic_paid' : 'byok',
            });
            const { job_id } = res.data;
            setJobId(job_id);
            setPhase('building');
            startStreaming(job_id, byokKey);
        } catch (e: any) {
            if (e?.code === 'ERR_NETWORK' || !e?.response) {
                setError('Backend is offline. Please check your connection and try again.');
            } else {
                const detail = e?.response?.data?.detail;
                if (typeof detail === 'object' && detail?.error === 'build_limit_reached') {
                    setShowBillingModal(true);
                    return;
                }
                setError(detail || 'Build failed. Check the backend logs.');
            }
        }
    };

    const startStreaming = (jid: string, byokKey: any = null) => {
        if (abortCtrlRef.current) abortCtrlRef.current.abort();
        const ctrl = new AbortController();
        abortCtrlRef.current = ctrl;

        setEvents([]);

        const headers: Record<string, string> = {
            'ngrok-skip-browser-warning': 'true',
            'Accept': 'text/event-stream',
        };

        // Forward BYOK key as header for SSE stream
        if (byokKey) {
            headers['X-LLM-API-Key'] = JSON.stringify(byokKey);
        }

        fetchEventSource(`${API_BASE}/build/stream/${jid}`, {
            method: 'GET',
            headers,
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
                        const isDuplicate = prev.some(e => e.timestamp === data.timestamp && e.type === data.type && e.message === data.message);
                        if (isDuplicate) return prev;
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
                        <div className="studio-launch-shell">
                            <section className="studio-launch-header">
                                <div className="studio-launch-copy">
                                    <span className="studio-kicker">
                                        <Sparkles size={14} />
                                        QUICK BUILD STUDIO
                                    </span>
                                    <h1 className="studio-launch-title">Describe the system. Launch a disciplined silicon run.</h1>
                                    <p className="studio-launch-subtitle">
                                        AgentIC turns a natural-language specification into a structured build with verification,
                                        operator-safe BYOK routing, and an optional path to fabrication-ready artifacts.
                                    </p>
                                </div>
                                <div className="studio-launch-actions">
                                    <button className="studio-control-btn studio-control-btn--primary" onClick={() => setShowBillingModal(true)}>
                                        <KeyRound size={16} />
                                        Configure BYOK
                                    </button>
                                    <div className="studio-status-cluster">
                                        <span className={`studio-status-pill ${(hasLocalByok || hasServerByok) ? 'is-ready' : 'is-warn'}`}>
                                            <Fingerprint size={14} />
                                            {launchStatus}
                                        </span>
                                        <span className="studio-status-pill">
                                            <Cpu size={14} />
                                            {launchModeLabel}
                                        </span>
                                    </div>
                                </div>
                            </section>

                            <div className="studio-launch-grid">
                                <section className="studio-compose-card">
                                    <div className="studio-section-heading">
                                        <div>
                                            <span className="studio-section-label">Build Brief</span>
                                            <h2 className="studio-section-title">Specification input</h2>
                                        </div>
                                        <span className="studio-muted-chip">{usageLabel}</span>
                                    </div>

                                    <label className="studio-field-label" htmlFor="design-prompt">
                                        Describe the circuit in plain English
                                    </label>
                                    <div className="studio-textarea-wrap">
                                        <Bot size={18} className="studio-textarea-icon" />
                                        <textarea
                                            id="design-prompt"
                                            className="studio-textarea"
                                            placeholder="Example: A low-power UART bridge with FIFO buffering, parity checks, and an APB register interface."
                                            value={prompt}
                                            onChange={e => setPrompt(e.target.value)}
                                            rows={5}
                                            onInput={(e) => {
                                                const target = e.currentTarget;
                                                target.style.height = 'auto';
                                                target.style.height = `${Math.min(target.scrollHeight, 320)}px`;
                                            }}
                                            onKeyDown={(e) => {
                                                if (e.key === 'Enter' && !e.shiftKey) {
                                                    e.preventDefault();
                                                    if (prompt.trim()) handleLaunch();
                                                }
                                            }}
                                            autoFocus
                                        />
                                    </div>

                                    <div className="studio-design-row">
                                        <div className="studio-design-card">
                                            <span className="studio-field-label">Design identifier</span>
                                            <span className="studio-design-value">
                                                {designName || 'Generated automatically after you describe the system'}
                                            </span>
                                        </div>
                                        <div className="studio-design-card">
                                            <span className="studio-field-label">Execution mode</span>
                                            <span className="studio-design-value">{aiModel === 'BYOK' ? 'Bring your own model key' : 'AgentIC-managed orchestration'}</span>
                                        </div>
                                    </div>

                                    <div className="studio-option-groups">
                                        <div className="studio-option-group">
                                            <span className="studio-field-label">Routing mode</span>
                                            <div className="studio-chip-row">
                                                <button
                                                    className={`studio-chip-btn ${aiModel === 'AgentIC' ? 'is-active' : ''}`}
                                                    onClick={() => {
                                                        if (!isAgenticPaid) {
                                                            window.history.pushState({}, '', '/pricing');
                                                            window.dispatchEvent(new Event('popstate'));
                                                        } else {
                                                            setAiModel('AgentIC');
                                                        }
                                                    }}
                                                >
                                                    <Rocket size={15} />
                                                    AgentIC orchestration
                                                </button>
                                                <button
                                                    className={`studio-chip-btn ${aiModel === 'BYOK' ? 'is-active' : ''}`}
                                                    onClick={() => setAiModel('BYOK')}
                                                >
                                                    <KeyRound size={15} />
                                                    Operator BYOK
                                                </button>
                                            </div>
                                        </div>

                                        <div className="studio-option-group">
                                            <span className="studio-field-label">Delivery scope</span>
                                            <div className="studio-chip-row">
                                                <button
                                                    className={`studio-chip-btn ${skipOpenlane ? 'is-active' : ''}`}
                                                    onClick={() => setSkipOpenlane(true)}
                                                    title="Stop after verification"
                                                >
                                                    <ShieldCheck size={15} />
                                                    Verification-first
                                                </button>
                                                <button
                                                    className={`studio-chip-btn ${!skipOpenlane ? 'is-active' : ''}`}
                                                    onClick={() => {
                                                        if (isHuggingFace) {
                                                            alert("GDS Layout is temporarily under maintenance on the cloud platform. It will be available back in a few days. Using RTL & Verification mode for now.");
                                                        } else {
                                                            setSkipOpenlane(false);
                                                        }
                                                    }}
                                                    title={isHuggingFace ? "Full silicon flow to GDS (Under Cloud Maintenance)" : "Full silicon flow to GDS"}
                                                >
                                                    <Layers3 size={15} />
                                                    {isHuggingFace ? 'Full signoff unavailable on cloud' : 'Full silicon path'}
                                                </button>
                                            </div>
                                        </div>
                                    </div>

                                    {error ? <div className="studio-error-banner">{error}</div> : null}

                                    <div className="studio-launch-row">
                                        <button
                                            className="studio-launch-btn"
                                            onClick={handleLaunch}
                                            disabled={!prompt.trim()}
                                        >
                                            Launch Build
                                            <ArrowRight size={16} />
                                        </button>
                                        <p className="studio-launch-note">
                                            Press <span>Enter</span> to launch, or <span>Shift + Enter</span> for a new line.
                                        </p>
                                    </div>
                                </section>

                                <aside className="studio-briefing-card">
                                    <div className="studio-section-heading studio-section-heading--stacked">
                                        <span className="studio-section-label">Execution Brief</span>
                                        <h2 className="studio-section-title">How this run will behave</h2>
                                    </div>

                                    <div className="studio-briefing-list">
                                        {DELIVERY_MODES.map((mode, index) => (
                                            <div key={mode.title} className="studio-briefing-item">
                                                <span className="studio-briefing-index">0{index + 1}</span>
                                                <div>
                                                    <h3>{mode.title}</h3>
                                                    <p>{mode.detail}</p>
                                                </div>
                                            </div>
                                        ))}
                                    </div>

                                    <div className="studio-quickstart-block">
                                        <div className="studio-quickstart-head">
                                            <Waypoints size={16} />
                                            <span>Quick starts</span>
                                        </div>
                                        <div className="studio-quickstart-list">
                                            {QUICK_STARTS.map((example) => (
                                                <button
                                                    key={example.prompt}
                                                    className="studio-example-card"
                                                    onClick={() => setPrompt(example.prompt)}
                                                >
                                                    <div className="studio-example-copy">
                                                        <strong>{example.title}</strong>
                                                        <span>{example.note}</span>
                                                    </div>
                                                    <p>{example.prompt}</p>
                                                </button>
                                            ))}
                                        </div>
                                    </div>
                                </aside>
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
                    // Refresh profile + billing status
                    Promise.allSettled([
                        api.get('/profile'),
                        api.get('/billing/status'),
                    ]).then(([profileRes, billingRes]) => {
                        const prof = profileRes.status === 'fulfilled' ? profileRes.value.data : null;
                        const billing = billingRes.status === 'fulfilled' ? billingRes.value.data : null;
                        if (prof && billing) {
                            setProfile({ ...prof, plan_type: billing.plan_type, build_limit: billing.build_limit });
                        } else if (prof) {
                            setProfile(prof);
                        }
                    });
                }}
            />
        </div>
    );
};
