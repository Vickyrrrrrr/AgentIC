import { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Rocket } from 'lucide-react';
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

    // Polled partial silicon artifacts
    const [artifacts, setArtifacts] = useState<any[]>([]);

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

    // Advanced build parameters
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
            const clean = slugify(prompt);
            if (clean && clean.length > 2) {
                setDesignName(clean);
            }
        }
    }, [prompt]);

    // Fetch workspace profile details
    useEffect(() => {
        Promise.allSettled([
            api.get('/profile'),
            api.get('/billing/status')
        ]).then(([profileRes, billingRes]) => {
            const prof = profileRes.status === 'fulfilled' ? profileRes.value.data : null;
            const billing = billingRes.status === 'fulfilled' ? billingRes.value.data : null;
            if (prof && billing) {
                setProfile({ ...prof, plan_type: billing.plan_type, build_limit: billing.build_limit });
            } else if (prof) {
                setProfile(prof);
            }
        });
    }, []);

    const isAgenticPaid = profile?.plan_type === 'agentic_paid';
    const hasLocalByok = profile?.has_byok_key;
    const hasServerByok = localStorage.getItem('agentic_byok_key') !== null;
    const hasByokConfigured = hasLocalByok || hasServerByok;

    let launchStatus = 'BYOK Key Unconfigured';
    if (isAgenticPaid) launchStatus = 'AgentIC Active';
    else if (hasByokConfigured) launchStatus = 'BYOK Active';

    let launchModeLabel = 'Operator BYOK';
    if (aiModel === 'AgentIC' && isAgenticPaid) {
        launchModeLabel = 'AgentIC Cloud';
    }

    let usageLabel = 'Enterprise Tier';
    if (profile?.auth_enabled) {
        const buildLimit = profile?.build_limit;
        const totalBuilds = (profile?.workspace_successful_builds !== undefined) 
            ? profile.workspace_successful_builds 
            : (profile?.successful_builds || 0);
        if (buildLimit !== null && buildLimit !== undefined) {
            usageLabel = `Usage: ${totalBuilds} / ${buildLimit} successful builds`;
        } else {
            usageLabel = `Usage: ${totalBuilds} successful builds`;
        }
    }

    const selectedPdk = pdkOptions.find(p => p.key === pdkProfile);

    const handleLaunch = async () => {
        if (!prompt.trim()) return;

        const effectiveAiModel = aiModel === 'AgentIC' && !isAgenticPaid ? 'BYOK' : aiModel;
        const byokRaw = localStorage.getItem('agentic_byok_key');
        const byokKey = byokRaw ? JSON.parse(byokRaw) : null;

        if (effectiveAiModel === 'BYOK' && !byokKey && !hasLocalByok) {
            pendingLaunchAfterByokRef.current = true;
            setShowBillingModal(true);
            return;
        }

        const requestedDesignName = designName.trim() || 'unnamed_design';
        setError('');
        setResult(null);

        try {
            const res = await api.post('/build', {
                design_name: requestedDesignName,
                prompt: prompt,
                skip_openlane: skipOpenlane,
                plan_type: effectiveAiModel === 'AgentIC' ? 'agentic_paid' : 'byok',
                api_key: byokKey ? JSON.stringify(byokKey) : null,
                pdk: pdkProfile,
                max_retries: maxRetries,
                min_coverage: minCoverage,
                max_pivots: maxPivots,
                congestion_threshold: congestionThreshold,
                hierarchical: hierarchical,
                tb_gate_mode: tbGateMode,
                tb_max_retries: tbMaxRetries,
                tb_fallback_template: tbFallbackTemplate,
                coverage_backend: coverageBackend,
                coverage_fallback_policy: coverageFallbackPolicy,
                coverage_profile: coverageProfile,
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

    const triggerArtifactFetch = async (targetDesignName = designName) => {
        if (!targetDesignName) return;
        try {
            const res = await api.get(`/build/artifacts/${targetDesignName}`);
            if (res.data && Array.isArray(res.data.artifacts)) {
                setArtifacts(res.data.artifacts);
            }
        } catch (err) {
            console.error('Failed to fetch artifacts:', err);
        }
    };

    const startStreaming = async (jid: string, byokKey: any = null, activeDesignName = designName) => {
        if (abortCtrlRef.current) abortCtrlRef.current.abort();
        const ctrl = new AbortController();
        abortCtrlRef.current = ctrl;
        let retryCount = 0;

        setEvents([]);

        const extraHeaders: Record<string, string> = {};
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

                    // Dynamically fetch intermediate files only when build events or logs arrive!
                    void triggerArtifactFetch(activeDesignName);
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
        void triggerArtifactFetch(activeDesignName);

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

    // Load initial artifacts when building or done phase becomes active
    useEffect(() => {
        if (jobId && (phase === 'building' || phase === 'done')) {
            void triggerArtifactFetch(designName);
        } else {
            setArtifacts([]);
        }
    }, [jobId, phase, designName]);

    return (
        <div className="studio-root" style={{ background: '#09090b', height: 'calc(100vh - 62px)', overflowY: 'auto' }}>
            <div className="studio-launch-shell" style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '1rem 2rem' }}>
                <section className="studio-launch-header" style={{ marginBottom: '1rem', borderBottom: '1px solid #27272a', paddingBottom: '0.75rem' }}>
                    <div className="studio-launch-copy">
                        <span className="studio-kicker" style={{ color: '#a1a1aa', fontWeight: 600, fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                            Silicon Workstation
                        </span>
                        <h1 className="studio-launch-title" style={{ fontSize: '1.25rem', color: '#f4f4f5', fontWeight: 700, marginTop: '0.2rem' }}>Disciplined Physical Silicon Co-Design Workspace</h1>
                    </div>
                    <div className="studio-launch-actions" style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
                        <button className="studio-control-btn studio-control-btn--primary" onClick={() => setShowBillingModal(true)} style={{ background: '#18181b', border: '1px solid #27272a', color: '#f4f4f5', borderRadius: '4px', padding: '0.4rem 0.8rem', fontSize: '0.78rem' }}>
                            Configure BYOK
                        </button>
                        <div className="studio-status-cluster" style={{ display: 'flex', gap: '0.5rem' }}>
                            <span className={`studio-status-pill ${(hasLocalByok || hasServerByok) ? 'is-ready' : 'is-warn'}`} style={{ background: '#18181b', border: '1px solid #27272a', color: '#a1a1aa', borderRadius: '4px', padding: '0.4rem 0.6rem', fontSize: '0.78rem' }}>
                                {launchStatus}
                            </span>
                            <span className="studio-status-pill" style={{ background: '#18181b', border: '1px solid #27272a', color: '#a1a1aa', borderRadius: '4px', padding: '0.4rem 0.6rem', fontSize: '0.78rem' }}>
                                {launchModeLabel}
                            </span>
                        </div>
                    </div>
                </section>

                <div className="studio-launch-grid" style={{ display: 'grid', gridTemplateColumns: '1.1fr 0.9fr', gap: '1.5rem', height: 'calc(100vh - 170px)', minHeight: '600px' }}>
                    {/* Left Panel: Chat Terminal (Persistent in all phases!) */}
                    <section className="studio-compose-card" style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '1rem', overflow: 'hidden', background: '#18181b', border: '1px solid #27272a', borderRadius: '4px' }}>
                        <div className="studio-section-heading" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem', flexShrink: 0 }}>
                            <div>
                                <span className="studio-section-label" style={{ fontFamily: 'monospace', fontSize: '0.72rem', color: '#a1a1aa', textTransform: 'uppercase' }}>Silicon AI Copilot</span>
                                <h2 className="studio-section-title" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.9rem', color: '#f4f4f5', marginTop: '0.1rem' }}>
                                    VLSI Expert Terminal
                                </h2>
                            </div>
                            <span className="studio-muted-chip" style={{ fontFamily: 'monospace', fontSize: '0.72rem', color: '#71717a' }}>{usageLabel}</span>
                        </div>

                        {/* Monospace Developer Terminal Panel */}
                        <div style={{
                            flex: 1,
                            overflowY: 'auto',
                            padding: '1rem',
                            background: '#09090b',
                            border: '1px solid #27272a',
                            borderRadius: '4px',
                            display: 'flex',
                            flexDirection: 'column',
                            gap: '1rem',
                            fontFamily: 'monospace',
                            fontSize: '0.78rem',
                            marginBottom: '0.75rem',
                        }}>
                            {chatHistory.map((msg, idx) => (
                                <div
                                    key={idx}
                                    style={{
                                        display: 'flex',
                                        flexDirection: 'column',
                                        gap: '0.25rem',
                                        maxWidth: '100%',
                                    }}
                                >
                                    {msg.role === 'user' ? (
                                        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.15rem' }}>
                                            <div style={{ color: '#60a5fa', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '0.4rem', fontFamily: 'monospace' }}>
                                                <span>[operator] ~ $</span>
                                            </div>
                                            <div style={{ color: '#e4e4e7', paddingLeft: '0.5rem', whiteSpace: 'pre-wrap', lineHeight: '1.45', fontFamily: 'monospace' }}>{msg.content}</div>
                                        </div>
                                    ) : (
                                        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.15rem' }}>
                                            <div style={{ color: '#a1a1aa', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '0.4rem', fontFamily: 'monospace' }}>
                                                <span>[copilot] ~</span>
                                            </div>
                                            <div style={{ color: '#f4f4f5', paddingLeft: '0.5rem', whiteSpace: 'pre-wrap', lineHeight: '1.45', fontFamily: 'monospace' }}>
                                                {msg.content}
                                                {idx === chatHistory.length - 1 && isTyping && (
                                                    <span className="cursor-blink" style={{ marginLeft: '2px', fontWeight: 'bold' }}>▋</span>
                                                )}
                                            </div>
                                        </div>
                                    )}
                                </div>
                            ))}
                            <div ref={chatEndRef} />
                        </div>

                        {/* Monospace Executive Input Area */}
                        <div style={{ flexShrink: 0 }}>
                            <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', background: '#09090b', border: '1px solid #27272a', borderRadius: '4px', padding: '0.4rem 0.6rem' }}>
                                <span style={{ color: '#a1a1aa', fontFamily: 'monospace', fontSize: '0.78rem', userSelect: 'none' }}>$</span>
                                <input
                                    type="text"
                                    value={chatInput}
                                    onChange={e => setChatInput(e.target.value)}
                                    onKeyDown={e => {
                                        if (e.key === 'Enter' && chatInput.trim() && !isTyping) {
                                            void handleSendChat();
                                        }
                                    }}
                                    placeholder="Execute natural-language silicon codegen pipeline directives..."
                                    style={{
                                        flex: 1,
                                        background: 'transparent',
                                        border: 'none',
                                        color: '#f4f4f5',
                                        fontFamily: 'monospace',
                                        fontSize: '0.78rem',
                                        outline: 'none',
                                    }}
                                />
                                <button
                                    onClick={() => chatInput.trim() && !isTyping && handleSendChat()}
                                    disabled={!chatInput.trim() || isTyping}
                                    style={{
                                        background: chatInput.trim() ? '#27272a' : 'transparent',
                                        border: '1px solid #27272a',
                                        color: chatInput.trim() ? '#f4f4f5' : '#52525b',
                                        padding: '0.2rem 0.6rem',
                                        borderRadius: '3px',
                                        fontSize: '0.74rem',
                                        fontFamily: 'monospace',
                                        fontWeight: '600',
                                        cursor: chatInput.trim() ? 'pointer' : 'not-allowed',
                                        transition: 'all 0.1s ease'
                                    }}
                                >
                                    EXEC
                                </button>
                            </div>
                        </div>
                    </section>

                    {/* Right Panel: Dynamic Workspace View (State-driven switches!) */}
                    <AnimatePresence mode="wait">
                        {phase === 'prompt' && (
                            <motion.aside
                                key="prompt"
                                className="studio-briefing-card"
                                initial={{ opacity: 0, x: 20 }}
                                animate={{ opacity: 1, x: 0 }}
                                exit={{ opacity: 0, x: -20 }}
                                transition={{ duration: 0.2 }}
                                style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '1rem', overflowY: 'auto', background: '#18181b', border: '1px solid #27272a', borderRadius: '4px' }}
                            >
                                <div className="studio-section-heading studio-section-heading--stacked" style={{ marginBottom: '0.75rem', flexShrink: 0 }}>
                                    <span className="studio-section-label" style={{ fontFamily: 'monospace', fontSize: '0.72rem', color: '#a1a1aa' }}>Silicon Specification Brief</span>
                                    <h2 className="studio-section-title" style={{ fontSize: '0.9rem', color: '#f4f4f5' }}>Execution Controls</h2>
                                </div>

                                {/* Editable Identifier & Summary Block */}
                                <div style={{ background: '#09090b', border: '1px solid #27272a', borderRadius: '4px', padding: '0.75rem', marginBottom: '0.75rem' }}>
                                    <div style={{ marginBottom: '0.5rem' }}>
                                        <label style={{ display: 'block', fontSize: '0.7rem', color: '#a1a1aa', textTransform: 'uppercase', marginBottom: '0.2rem', fontFamily: 'monospace' }}>Design Identifier</label>
                                        <input
                                            type="text"
                                            value={designName}
                                            onChange={e => setDesignName(e.target.value)}
                                            placeholder="e.g. spi_controller"
                                            style={{
                                                width: '100%',
                                                background: '#18181b',
                                                border: '1px solid #27272a',
                                                borderRadius: '3px',
                                                color: '#f4f4f5',
                                                fontSize: '0.78rem',
                                                padding: '0.35rem 0.5rem',
                                                outline: 'none',
                                                fontFamily: 'monospace',
                                            }}
                                        />
                                    </div>
                                    <div>
                                        <label style={{ display: 'block', fontSize: '0.7rem', color: '#a1a1aa', textTransform: 'uppercase', marginBottom: '0.2rem', fontFamily: 'monospace' }}>Compiled Specification Prompt</label>
                                        <textarea
                                            value={prompt}
                                            onChange={e => setPrompt(e.target.value)}
                                            placeholder="Copilot will generate final spec here as you chat, or you can paste one directly."
                                            rows={5}
                                            style={{
                                                width: '100%',
                                                background: '#18181b',
                                                border: '1px solid #27272a',
                                                borderRadius: '3px',
                                                color: '#f4f4f5',
                                                fontSize: '0.78rem',
                                                padding: '0.4rem 0.5rem',
                                                outline: 'none',
                                                resize: 'none',
                                                lineHeight: '1.4',
                                                fontFamily: 'monospace',
                                            }}
                                        />
                                    </div>
                                </div>

                                {/* Quick config options */}
                                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.65rem', marginBottom: '1rem' }}>
                                    <div>
                                        <span className="studio-field-label" style={{ marginBottom: '0.2rem', fontFamily: 'monospace', fontSize: '0.7rem', textTransform: 'uppercase', color: '#a1a1aa', display: 'block' }}>Execution Mode</span>
                                        <div className="studio-chip-row" style={{ display: 'flex', gap: '0.4rem' }}>
                                            <button
                                                className={`studio-chip-btn ${aiModel === 'AgentIC' ? 'is-active' : ''}`}
                                                onClick={() => isAgenticPaid ? setAiModel('AgentIC') : setShowBillingModal(true)}
                                                style={{ flex: 1, padding: '0.35rem', fontSize: '0.74rem', background: aiModel === 'AgentIC' ? '#27272a' : 'transparent', border: '1px solid #27272a', color: '#f4f4f5', cursor: 'pointer', borderRadius: '3px', fontFamily: 'monospace' }}
                                            >
                                                AgentIC Paid
                                            </button>
                                            <button
                                                className={`studio-chip-btn ${aiModel === 'BYOK' ? 'is-active' : ''}`}
                                                onClick={() => setAiModel('BYOK')}
                                                style={{ flex: 1, padding: '0.35rem', fontSize: '0.74rem', background: aiModel === 'BYOK' ? '#27272a' : 'transparent', border: '1px solid #27272a', color: '#f4f4f5', cursor: 'pointer', borderRadius: '3px', fontFamily: 'monospace' }}
                                            >
                                                Operator BYOK
                                            </button>
                                        </div>
                                    </div>

                                    <div>
                                        <span className="studio-field-label" style={{ marginBottom: '0.2rem', fontFamily: 'monospace', fontSize: '0.7rem', textTransform: 'uppercase', color: '#a1a1aa', display: 'block' }}>Delivery Scope</span>
                                        <div className="studio-chip-row" style={{ display: 'flex', gap: '0.4rem' }}>
                                            <button
                                                className={`studio-chip-btn ${skipOpenlane ? 'is-active' : ''}`}
                                                onClick={() => setSkipOpenlane(true)}
                                                style={{ flex: 1, padding: '0.35rem', fontSize: '0.74rem', background: skipOpenlane ? '#27272a' : 'transparent', border: '1px solid #27272a', color: '#f4f4f5', cursor: 'pointer', borderRadius: '3px', fontFamily: 'monospace' }}
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
                                                style={{ flex: 1, padding: '0.35rem', fontSize: '0.74rem', background: !skipOpenlane ? '#27272a' : 'transparent', border: '1px solid #27272a', color: '#f4f4f5', cursor: 'pointer', borderRadius: '3px', fontFamily: 'monospace' }}
                                            >
                                                Full Silicon Path
                                            </button>
                                        </div>
                                    </div>

                                    <div>
                                        <span className="studio-field-label" style={{ marginBottom: '0.2rem', fontFamily: 'monospace', fontSize: '0.7rem', textTransform: 'uppercase', color: '#a1a1aa', display: 'block' }}>PDK Target</span>
                                        <select
                                            className="studio-select"
                                            value={pdkProfile}
                                            onChange={(e) => setPdkProfile(e.target.value)}
                                            style={{ width: '100%', padding: '0.35rem 0.5rem', fontSize: '0.74rem', background: '#09090b', border: '1px solid #27272a', color: '#f4f4f5', borderRadius: '3px', fontFamily: 'monospace', outline: 'none' }}
                                        >
                                            {(pdkOptions.length ? pdkOptions.filter(p => p.gds_ready) : [{key: 'sky130'}]).map((p) => (
                                                <option key={p.key} value={p.key}>{p.key}</option>
                                            ))}
                                        </select>
                                    </div>
                                </div>

                                {error ? <div className="studio-error-banner" style={{ marginBottom: '0.75rem', background: 'rgba(239, 68, 68, 0.1)', border: '1px solid rgba(239, 68, 68, 0.3)', color: '#ef4444', padding: '0.5rem', borderRadius: '3px', fontSize: '0.74rem', fontFamily: 'monospace' }}>{error}</div> : null}

                                {/* Flat Industrial Silicon Launch Button */}
                                <div style={{ marginTop: 'auto', flexShrink: 0 }}>
                                    <button
                                        className="studio-launch-btn"
                                        onClick={handleLaunch}
                                        disabled={!prompt.trim()}
                                        style={{
                                            width: '100%',
                                            padding: '0.75rem',
                                            fontSize: '0.8rem',
                                            fontWeight: '700',
                                            textTransform: 'uppercase',
                                            letterSpacing: '1px',
                                            borderRadius: '4px',
                                            background: prompt.trim() ? '#27272a' : '#18181b',
                                            border: '1px solid #3f3f46',
                                            color: prompt.trim() ? '#f4f4f5' : '#52525b',
                                            cursor: prompt.trim() ? 'pointer' : 'not-allowed',
                                            display: 'flex',
                                            alignItems: 'center',
                                            justifyContent: 'center',
                                            gap: '0.5rem',
                                            fontFamily: 'monospace',
                                            transition: 'all 0.15s ease'
                                        }}
                                    >
                                        <Rocket size={16} />
                                        Launch Autonomous Build Run
                                    </button>
                                </div>
                            </motion.aside>
                        )}

                        {phase === 'building' && (
                            <motion.aside
                                key="building"
                                className="studio-briefing-card"
                                initial={{ opacity: 0, x: 20 }}
                                animate={{ opacity: 1, x: 0 }}
                                exit={{ opacity: 0, x: -20 }}
                                transition={{ duration: 0.2 }}
                                style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '1rem', overflowY: 'auto', background: '#18181b', border: '1px solid #27272a', borderRadius: '4px' }}
                            >
                                <BuildMonitor
                                    designName={designName}
                                    jobId={jobId}
                                    events={events}
                                    jobStatus={jobStatus}
                                    stageSchema={stageSchema}
                                    onReset={handleReset}
                                    artifacts={artifacts}
                                />
                            </motion.aside>
                        )}

                        {phase === 'done' && (
                            <motion.aside
                                key="done"
                                className="studio-briefing-card"
                                initial={{ opacity: 0, x: 20 }}
                                animate={{ opacity: 1, x: 0 }}
                                exit={{ opacity: 0, x: -20 }}
                                transition={{ duration: 0.2 }}
                                style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '1rem', overflowY: 'auto', background: '#18181b', border: '1px solid #27272a', borderRadius: '4px' }}
                            >
                                <ChipSummary
                                    designName={designName}
                                    result={result}
                                    jobStatus={jobStatus}
                                    events={events}
                                    jobId={jobId}
                                    onReset={handleReset}
                                />
                            </motion.aside>
                        )}
                    </AnimatePresence>
                </div>
            </div>

            <BillingModal 
                isOpen={showBillingModal}
                onClose={() => setShowBillingModal(false)}
                onKeySaved={() => {
                    setAiModel('BYOK');
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
