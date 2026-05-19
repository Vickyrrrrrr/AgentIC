import { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
    // ArrowRight,
    Bot,
    Cpu,
    Fingerprint,
    KeyRound,
    // Layers3,
    Rocket,
    // ShieldCheck,
    Sparkles,
    // Waypoints,
} from 'lucide-react';
import { BuildMonitor } from '../components/BuildMonitor';
import { ChipSummary } from '../components/ChipSummary';
import { BillingModal } from '../components/BillingModal';
import { fetchEventSource } from '@microsoft/fetch-event-source';
import { api, API_BASE, getSseHeaders } from '../api';
import { toUserError, isNetworkError } from '../utils/errorFormatter';

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

interface PdkOption {
    key: string;
    pdk: string;
    std_cell_library: string;
    description: string;
    available: boolean;
    gds_ready: boolean;
    tech_ok: boolean;
    proprietary: boolean;
    status: string;
    reason: string;
    maturity?: string;
    fabrication_ready?: boolean;
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



export const DesignStudio = () => {
    const [phase, setPhase] = useState<Phase>('prompt');
    const [prompt, setPrompt] = useState('');
    
    // Chat Copilot States
    const [chatHistory, setChatHistory] = useState<Array<{ role: 'user' | 'assistant'; content: string }>>([
        {
            role: 'assistant',
            content: "Hello! I am your AgentIC VLSI Copilot. I can help you design, refine, and perfect your custom silicon architecture before launching an autonomous run.\n\nWhat kind of hardware block or digital circuit would you like to design today? For example, we can build an AXI4 DMA Fabric, an SPI Controller, or a custom RISC compute core."
        }
    ]);
    const [chatInput, setChatInput] = useState('');
    const [isTyping, setIsTyping] = useState(false);
    const chatEndRef = useRef<HTMLDivElement | null>(null);

    useEffect(() => {
        chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [chatHistory, isTyping]);

    const handleSendChat = async () => {
        if (!chatInput.trim() || isTyping) return;
        
        const userMsg = chatInput.trim();
        setChatInput('');
        const updatedHistory = [...chatHistory, { role: 'user' as const, content: userMsg }];
        setChatHistory(updatedHistory);
        setIsTyping(true);

        // Keep latest chat input as the running description prompt
        setPrompt(userMsg);

        try {
            const byokRaw = localStorage.getItem('agentic_byok_key');
            const byokKey = byokRaw ? JSON.parse(byokRaw) : null;
            const effectiveAiModel = aiModel === 'AgentIC' && !isAgenticPaid ? 'BYOK' : aiModel;

            const res = await api.post('/chat/converse', {
                messages: updatedHistory.map(m => ({ role: m.role, content: m.content })),
                plan_type: effectiveAiModel === 'AgentIC' ? 'agentic_paid' : 'byok',
                api_key: byokKey ? JSON.stringify(byokKey) : null,
            });

            setChatHistory(prev => [...prev, { role: 'assistant' as const, content: res.data.reply }]);
            
            // Search copilot response for a code block to update prompt
            if (res.data.reply.includes("```")) {
                const parts = res.data.reply.split("```");
                if (parts.length > 1) {
                    const code = parts[1].replace(/^(verilog|sv|markdown|text|json)?\n/, '');
                    if (code.length > 30) {
                        setPrompt(code);
                    }
                }
            }
        } catch (e) {
            setChatHistory(prev => [...prev, { role: 'assistant' as const, content: "My apologies, I encountered a connection issue. Please check your network and API keys, and try again." }]);
        } finally {
            setIsTyping(false);
        }
    };
    const [designName, setDesignName] = useState('');
    const [jobId, setJobId] = useState('');
    const [events, setEvents] = useState<BuildEvent[]>([]);
    const [jobStatus, setJobStatus] = useState<'queued' | 'running' | 'done' | 'failed' | 'cancelled' | 'cancelling'>('queued');
    const [result, setResult] = useState<any>(null);
    const [error, setError] = useState('');

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
    const [aiModel, setAiModel] = useState<'AgentIC' | 'BYOK'>('AgentIC');
    const [pdkProfile, setPdkProfile] = useState("sky130");
    const [pdkOptions, setPdkOptions] = useState<PdkOption[]>([]);
    const [stageSchema, setStageSchema] = useState<StageSchemaItem[]>([]);

    // Advanced build parameters (sent to API with stable defaults, no UI controls)
    const maxRetries = 5;
    const minCoverage = 80.0;
    const maxPivots = 2;
    const congestionThreshold = 10.0;
    const hierarchical = "auto";
    const tbGateMode = "strict";
    const tbMaxRetries = 3;
    const tbFallbackTemplate = "uvm_lite";
    const coverageBackend = "auto";
    const coverageFallbackPolicy = "fail_closed";
    const coverageProfile = "balanced";

    const abortCtrlRef = useRef<AbortController | null>(null);
    const pendingLaunchAfterByokRef = useRef(false);

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
                if (billing.plan_type !== 'agentic_paid') setAiModel('BYOK');
            } else if (prof) {
                setProfile(prof);
                setAiModel('BYOK');
            }
        }).catch(() => setProfile(null));
    }, []);

    const hasLocalByok = Boolean(localStorage.getItem('agentic_byok_key'));
    const hasServerByok = Boolean(profile?.has_byok_key);
    const isAgenticPaid = profile?.plan_type === 'agentic_paid';
    const hasActivePlan = isAgenticPaid || hasLocalByok || hasServerByok;
    const launchStatus = isAgenticPaid ? 'AgentIC Model active' : hasActivePlan ? 'BYOK configured' : 'Configure required';
    const launchModeLabel = skipOpenlane ? 'Verification-first run' : 'Full silicon path';
    const selectedPdk = pdkOptions.find((pdk) => pdk.key === pdkProfile);
    // const gdsReadyPdks = pdkOptions.filter((pdk) => pdk.gds_ready);
    const workspaceSuccessfulBuilds = profile?.workspace_successful_builds ?? profile?.successful_builds ?? 0;
    const usageLabel = profile
        ? `${workspaceSuccessfulBuilds} successful · ${profile.total_builds ?? 0} total builds on ${profile.plan ?? 'local'}`
        : 'Local workspace mode';


    const handleLaunch = async () => {
        if (!prompt.trim()) return;
        setError('');
        setEvents([]);
        setResult(null);
        setJobStatus('queued');

        const byokRaw = localStorage.getItem('agentic_byok_key');
        const hasByokNow = Boolean(byokRaw) || hasServerByok;
        const effectiveAiModel = aiModel === 'AgentIC' && !isAgenticPaid ? 'BYOK' : aiModel;

        // Guard: require BYOK configured if BYOK mode is selected
        if (effectiveAiModel === 'BYOK' && !hasByokNow) {
            pendingLaunchAfterByokRef.current = true;
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
            const byokKey = byokRaw ? JSON.parse(byokRaw) : null;

            const requestedDesignName = designName || slugify(prompt);
            const res = await api.post(`/build`, {
                design_name: requestedDesignName,
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
                plan_type: effectiveAiModel === 'AgentIC' ? 'agentic_paid' : 'byok',
            });
            const { job_id, design_name: serverDesignName } = res.data;
            const activeDesignName = serverDesignName || requestedDesignName;
            setJobId(job_id);
            setDesignName(activeDesignName);
            setJobStatus('running');
            setPhase('building');
            void startStreaming(job_id, byokKey, activeDesignName);
        } catch (e: any) {
            if (isNetworkError(e)) {
                setError('Unable to connect to the build service. Please check your connection and try again.');
            } else {
                const detail = e?.response?.data?.detail;
                if (typeof detail === 'object' && detail?.error === 'build_limit_reached') {
                    setShowBillingModal(true);
                    return;
                }
                setError(toUserError(detail, 'Build failed. Please try again or contact support.'));
            }
        }
    };

    const startStreaming = async (jid: string, byokKey: any = null, activeDesignName = designName) => {
        if (abortCtrlRef.current) abortCtrlRef.current.abort();
        const ctrl = new AbortController();
        abortCtrlRef.current = ctrl;
        let retryCount = 0;

        setEvents([]);

        const extraHeaders: Record<string, string> = {};
        // Forward BYOK key as header for SSE stream
        if (byokKey) {
            extraHeaders['X-LLM-API-Key'] = JSON.stringify(byokKey);
        }

        const headers = await getSseHeaders(extraHeaders);

        fetchEventSource(`${API_BASE}/build/stream/${jid}`, {
            method: 'GET',
            headers,
            signal: ctrl.signal,
            openWhenHidden: true,
            async onopen(response) {
                const contentType = response.headers.get('content-type') || '';
                if (response.ok && contentType.includes('text/event-stream')) {
                    retryCount = 0;
                    return;
                }
                if ([401, 403, 404].includes(response.status)) {
                    throw new Error('Live stream unavailable. Please try again.');
                }
                throw new Error('Live stream temporarily unavailable.');
            },
            onmessage(evt) {
                try {
                    const data: BuildEvent = JSON.parse(evt.data);
                    if (data.type === 'ping') return;
                    if (data.type === 'stream_end') {
                        ctrl.abort();
                        void fetchResult(jid, data.status as any, activeDesignName);
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
                if (ctrl.signal.aborted) return;
                retryCount += 1;
                if (retryCount > 8) {
                    ctrl.abort();
                    setJobStatus('failed');
                    setError('Live connection lost. Checking final build status...');
                    void fetchResult(jid, 'failed', activeDesignName);
                    throw err;
                }
                return Math.min(1000 * retryCount, 5000);
            }
        }).catch(() => {
            if (ctrl.signal.aborted) return;
            setJobStatus('failed');
            setError('Live connection lost. Checking final build status...');
            void fetchResult(jid, 'failed', activeDesignName);
        });
    };

    const fetchResult = async (jid: string, status: string, activeDesignName = designName) => {
        setJobStatus(status === 'done' ? 'done' : 'failed');
        try {
            const res = await api.get(`/build/result/${jid}`);
            setResult(res.data.result);
        } catch { /* result might not exist if failed early */ }
        setPhase('done');

        // Browser notification
        if ('Notification' in window && Notification.permission === 'granted') {
            new Notification('AgentIC chip build complete', {
                body: `Your chip "${activeDesignName}" finished ${status === 'done' ? 'successfully.' : 'with errors.'}`,
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
        api.get('/pdks')
            .then(res => {
                const pdks: PdkOption[] = res.data?.pdks || [];
                const defaultPdk = res.data?.default || 'sky130';
                const readyDefault = pdks.find((pdk) => pdk.key === defaultPdk && pdk.gds_ready);
                const firstReady = pdks.find((pdk) => pdk.gds_ready);
                setPdkOptions(pdks);
                setPdkProfile((current) => {
                    if (pdks.some((pdk) => pdk.key === current && pdk.gds_ready)) return current;
                    return readyDefault?.key || firstReady?.key || defaultPdk;
                });
            })
            .catch(() => setPdkOptions([]));
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

                            <div className="studio-launch-grid" style={{ display: 'grid', gridTemplateColumns: '1.2fr 0.8fr', gap: '1.5rem', height: 'calc(100vh - 180px)', minHeight: '650px' }}>
                                {/* Left Panel: Chat Terminal */}
                                <section className="studio-compose-card" style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '1.5rem', overflow: 'hidden' }}>
                                    <div className="studio-section-heading" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem', flexShrink: 0 }}>
                                        <div>
                                            <span className="studio-section-label">Silicon AI Copilot</span>
                                            <h2 className="studio-section-title" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                                                <Bot size={20} style={{ color: 'var(--accent)' }} />
                                                VLSI Expert Terminal
                                            </h2>
                                        </div>
                                        <span className="studio-muted-chip">{usageLabel}</span>
                                    </div>

                                    {/* Chat History List */}
                                    <div style={{ flex: 1, overflowY: 'auto', paddingRight: '0.5rem', display: 'flex', flexDirection: 'column', gap: '1rem', marginBottom: '1rem' }}>
                                        {chatHistory.map((msg, idx) => (
                                            <div
                                                key={idx}
                                                style={{
                                                    display: 'flex',
                                                    justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start',
                                                    alignItems: 'flex-start',
                                                    gap: '0.75rem',
                                                    maxWidth: '100%'
                                                }}
                                            >
                                                {msg.role === 'assistant' && (
                                                    <div style={{
                                                        width: '32px', height: '32px', borderRadius: '8px',
                                                        background: 'linear-gradient(135deg, var(--accent) 0%, #3b82f6 100%)',
                                                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                                                        flexShrink: 0, boxShadow: '0 4px 12px rgba(59, 130, 246, 0.25)'
                                                    }}>
                                                        <Bot size={16} style={{ color: '#fff' }} />
                                                    </div>
                                                )}
                                                <div
                                                    style={{
                                                        background: msg.role === 'user' 
                                                            ? 'linear-gradient(135deg, var(--accent) 0%, #1d4ed8 100%)' 
                                                            : 'rgba(39, 39, 42, 0.65)',
                                                        border: msg.role === 'user' ? 'none' : '1px solid rgba(63, 63, 70, 0.5)',
                                                        color: msg.role === 'user' ? '#fff' : 'rgba(255, 255, 255, 0.9)',
                                                        padding: '0.85rem 1.1rem',
                                                        borderRadius: '12px',
                                                        borderTopRightRadius: msg.role === 'user' ? '2px' : '12px',
                                                        borderTopLeftRadius: msg.role === 'assistant' ? '2px' : '12px',
                                                        fontSize: '0.86rem',
                                                        lineHeight: '1.45',
                                                        maxWidth: '82%',
                                                        boxShadow: '0 4px 12px rgba(0, 0, 0, 0.05)',
                                                        backdropFilter: msg.role === 'user' ? 'none' : 'blur(8px)',
                                                        whiteSpace: 'pre-wrap',
                                                        wordBreak: 'break-word',
                                                    }}
                                                >
                                                    {msg.content}
                                                </div>
                                            </div>
                                        ))}

                                        {isTyping && (
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                                                <div style={{
                                                    width: '32px', height: '32px', borderRadius: '8px',
                                                    background: 'rgba(39, 39, 42, 0.65)',
                                                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                                                    flexShrink: 0, border: '1px solid rgba(63, 63, 70, 0.5)'
                                                }}>
                                                    <Bot size={16} style={{ color: 'var(--accent)' }} />
                                                </div>
                                                <div style={{
                                                    background: 'rgba(39, 39, 42, 0.4)',
                                                    border: '1px solid rgba(63, 63, 70, 0.3)',
                                                    padding: '0.75rem 1rem',
                                                    borderRadius: '12px',
                                                    borderTopLeftRadius: '2px',
                                                    display: 'flex',
                                                    alignItems: 'center',
                                                    gap: '0.4rem'
                                                }}>
                                                    <span className="premium-loader-dot" style={{ width: '6px', height: '6px', background: 'var(--accent)', borderRadius: '50%', display: 'inline-block' }} />
                                                    <span className="premium-loader-dot" style={{ width: '6px', height: '6px', background: 'var(--accent)', borderRadius: '50%', display: 'inline-block' }} />
                                                    <span className="premium-loader-dot" style={{ width: '6px', height: '6px', background: 'var(--accent)', borderRadius: '50%', display: 'inline-block' }} />
                                                </div>
                                            </div>
                                        )}
                                        <div ref={chatEndRef} />
                                    </div>

                                    {/* Bottom Quick-actions & Input Bar */}
                                    <div style={{ flexShrink: 0 }}>
                                        {chatHistory.length <= 1 && (
                                            <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.75rem', overflowX: 'auto', paddingBottom: '0.25rem' }}>
                                                {QUICK_STARTS.map((qs) => (
                                                    <button
                                                        key={qs.title}
                                                        onClick={() => {
                                                            setChatInput(`I want to design a ${qs.prompt.toLowerCase()}`);
                                                        }}
                                                        style={{
                                                            background: 'rgba(24, 24, 27, 0.8)',
                                                            border: '1px solid rgba(63, 63, 70, 0.6)',
                                                            borderRadius: '20px',
                                                            padding: '0.4rem 0.85rem',
                                                            fontSize: '0.75rem',
                                                            color: 'rgba(255, 255, 255, 0.75)',
                                                            cursor: 'pointer',
                                                            whiteSpace: 'nowrap',
                                                        }}
                                                    >
                                                        {qs.title}
                                                    </button>
                                                ))}
                                            </div>
                                        )}

                                        <div style={{
                                            display: 'flex',
                                            alignItems: 'center',
                                            background: 'rgba(24, 24, 27, 0.8)',
                                            border: '1px solid rgba(63, 63, 70, 0.8)',
                                            borderRadius: '10px',
                                            padding: '0.35rem 0.5rem 0.35rem 1rem',
                                            boxShadow: 'inset 0 1px 2px rgba(0, 0, 0, 0.1)',
                                        }}>
                                            <input
                                                type="text"
                                                placeholder="Ask Copilot to build or modify a silicon architecture..."
                                                value={chatInput}
                                                onChange={e => setChatInput(e.target.value)}
                                                onKeyDown={e => {
                                                    if (e.key === 'Enter') handleSendChat();
                                                }}
                                                style={{
                                                    flex: 1,
                                                    background: 'transparent',
                                                    border: 'none',
                                                    color: '#fff',
                                                    fontSize: '0.85rem',
                                                    outline: 'none',
                                                    padding: '0.4rem 0'
                                                }}
                                                disabled={isTyping}
                                            />
                                            <button
                                                onClick={handleSendChat}
                                                disabled={!chatInput.trim() || isTyping}
                                                style={{
                                                    background: chatInput.trim() ? 'var(--accent)' : 'rgba(63, 63, 70, 0.5)',
                                                    color: '#fff',
                                                    border: 'none',
                                                    borderRadius: '8px',
                                                    padding: '0.45rem 1rem',
                                                    fontSize: '0.8rem',
                                                    fontWeight: '600',
                                                    cursor: chatInput.trim() ? 'pointer' : 'not-allowed',
                                                }}
                                            >
                                                Send
                                            </button>
                                        </div>
                                    </div>
                                </section>

                                {/* Right Panel: Spec Brief & Launch */}
                                <aside className="studio-briefing-card" style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '1.5rem', overflowY: 'auto' }}>
                                    <div className="studio-section-heading studio-section-heading--stacked" style={{ marginBottom: '1.25rem', flexShrink: 0 }}>
                                        <span className="studio-section-label">Silicon Specification Brief</span>
                                        <h2 className="studio-section-title">Execution Controls</h2>
                                    </div>

                                    {/* Editable Identifier & Summary Block */}
                                    <div style={{ background: 'rgba(24, 24, 27, 0.4)', border: '1px solid rgba(63, 63, 70, 0.4)', borderRadius: '8px', padding: '1rem', marginBottom: '1rem' }}>
                                        <div style={{ marginBottom: '0.75rem' }}>
                                            <label style={{ display: 'block', fontSize: '0.72rem', color: 'rgba(255, 255, 255, 0.4)', textTransform: 'uppercase', marginBottom: '0.25rem' }}>Design Identifier</label>
                                            <input
                                                type="text"
                                                value={designName}
                                                onChange={e => setDesignName(e.target.value)}
                                                placeholder="e.g. spi_controller"
                                                style={{
                                                    width: '100%',
                                                    background: 'rgba(39, 39, 42, 0.6)',
                                                    border: '1px solid rgba(63, 63, 70, 0.8)',
                                                    borderRadius: '6px',
                                                    color: '#fff',
                                                    fontSize: '0.82rem',
                                                    padding: '0.4rem 0.6rem',
                                                    outline: 'none',
                                                }}
                                            />
                                        </div>
                                        <div>
                                            <label style={{ display: 'block', fontSize: '0.72rem', color: 'rgba(255, 255, 255, 0.4)', textTransform: 'uppercase', marginBottom: '0.25rem' }}>Compiled Specification Prompt</label>
                                            <textarea
                                                value={prompt}
                                                onChange={e => setPrompt(e.target.value)}
                                                placeholder="Copilot will generate final spec here as you chat, or you can paste one directly."
                                                rows={4}
                                                style={{
                                                    width: '100%',
                                                    background: 'rgba(39, 39, 42, 0.6)',
                                                    border: '1px solid rgba(63, 63, 70, 0.8)',
                                                    borderRadius: '6px',
                                                    color: 'rgba(255, 255, 255, 0.85)',
                                                    fontSize: '0.78rem',
                                                    padding: '0.5rem 0.6rem',
                                                    outline: 'none',
                                                    resize: 'none',
                                                    lineHeight: '1.4'
                                                }}
                                            />
                                        </div>
                                    </div>

                                    {/* Quick config options */}
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.85rem', marginBottom: '1.25rem' }}>
                                        <div>
                                            <span className="studio-field-label" style={{ marginBottom: '0.25rem' }}>Execution Mode</span>
                                            <div className="studio-chip-row" style={{ gap: '0.4rem' }}>
                                                <button
                                                    className={`studio-chip-btn ${aiModel === 'AgentIC' ? 'is-active' : ''}`}
                                                    onClick={() => isAgenticPaid ? setAiModel('AgentIC') : setShowBillingModal(true)}
                                                    style={{ flex: 1, padding: '0.4rem', fontSize: '0.75rem' }}
                                                >
                                                    AgentIC Paid
                                                </button>
                                                <button
                                                    className={`studio-chip-btn ${aiModel === 'BYOK' ? 'is-active' : ''}`}
                                                    onClick={() => setAiModel('BYOK')}
                                                    style={{ flex: 1, padding: '0.4rem', fontSize: '0.75rem' }}
                                                >
                                                    Operator BYOK
                                                </button>
                                            </div>
                                        </div>

                                        <div>
                                            <span className="studio-field-label" style={{ marginBottom: '0.25rem' }}>Delivery Scope</span>
                                            <div className="studio-chip-row" style={{ gap: '0.4rem' }}>
                                                <button
                                                    className={`studio-chip-btn ${skipOpenlane ? 'is-active' : ''}`}
                                                    onClick={() => setSkipOpenlane(true)}
                                                    style={{ flex: 1, padding: '0.4rem', fontSize: '0.75rem' }}
                                                >
                                                    Verification Only
                                                </button>
                                                <button
                                                    className={`studio-chip-btn ${!skipOpenlane ? 'is-active' : ''}`}
                                                    onClick={() => {
                                                        if (selectedPdk && !selectedPdk.gds_ready) {
                                                            setError(`${selectedPdk.key} is not ready for GDSII on this VPS.`);
                                                        } else {
                                                            setSkipOpenlane(false);
                                                        }
                                                    }}
                                                    style={{ flex: 1, padding: '0.4rem', fontSize: '0.75rem' }}
                                                >
                                                    Full Silicon Path
                                                </button>
                                            </div>
                                        </div>

                                        <div>
                                            <span className="studio-field-label" style={{ marginBottom: '0.25rem' }}>PDK Target</span>
                                            <select
                                                className="studio-select"
                                                value={pdkProfile}
                                                onChange={(e) => setPdkProfile(e.target.value)}
                                                style={{ width: '100%', padding: '0.4rem 0.6rem', fontSize: '0.78rem', background: 'rgba(39, 39, 42, 0.6)', border: '1px solid rgba(63, 63, 70, 0.8)', color: '#fff', borderRadius: '6px' }}
                                            >
                                                {(pdkOptions.length ? pdkOptions.filter(p => p.gds_ready) : [{key: 'sky130'}]).map((p) => (
                                                    <option key={p.key} value={p.key}>{p.key}</option>
                                                ))}
                                            </select>
                                        </div>
                                    </div>

                                    {error ? <div className="studio-error-banner" style={{ marginBottom: '1rem' }}>{error}</div> : null}

                                    {/* Glowing Silicon Launch Button */}
                                    <div style={{ marginTop: 'auto', flexShrink: 0 }}>
                                        <button
                                            className="studio-launch-btn"
                                            onClick={handleLaunch}
                                            disabled={!prompt.trim()}
                                            style={{
                                                width: '100%',
                                                padding: '0.9rem',
                                                fontSize: '0.9rem',
                                                fontWeight: '700',
                                                textTransform: 'uppercase',
                                                letterSpacing: '1px',
                                                borderRadius: '8px',
                                                background: prompt.trim() 
                                                    ? 'linear-gradient(135deg, #3b82f6 0%, var(--accent) 50%, #1d4ed8 100%)' 
                                                    : 'rgba(63, 63, 70, 0.4)',
                                                border: 'none',
                                                color: prompt.trim() ? '#fff' : 'rgba(255, 255, 255, 0.35)',
                                                cursor: prompt.trim() ? 'pointer' : 'not-allowed',
                                                display: 'flex',
                                                alignItems: 'center',
                                                justifyContent: 'center',
                                                gap: '0.6rem',
                                                boxShadow: prompt.trim() ? '0 4px 20px rgba(59, 130, 246, 0.4)' : 'none',
                                                transition: 'all 0.3s'
                                            }}
                                        >
                                            <Rocket size={18} />
                                            Launch Autonomous Build
                                        </button>
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
                    setAiModel('BYOK');
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
                        if (pendingLaunchAfterByokRef.current) {
                            pendingLaunchAfterByokRef.current = false;
                            void handleLaunch();
                        }
                    });
                }}
            />
        </div>
    );
};
