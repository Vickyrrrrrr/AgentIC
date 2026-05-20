import { useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { fetchEventSource } from '@microsoft/fetch-event-source';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
    AlertCircle,
    ArrowUp,
    Check,
    ChevronDown,
    ChevronLeft,
    ChevronRight,
    CircleStop,
    Cpu,
    FileText,
    Folder,
    History,
    KeyRound,
    PanelLeft,
    Plus,
    RotateCcw,
    Settings,
    Sparkles,
} from 'lucide-react';
import { BillingModal } from '../components/BillingModal';
import { api, API_BASE, getSseHeaders } from '../api';
import { isNetworkError, toUserError } from '../utils/errorFormatter';

type Phase = 'idle' | 'building' | 'done';
type ModelChoice = 'infinite' | 'byok';
type BillingMode = 'agentic' | 'byok';
type JobStatus = 'queued' | 'running' | 'done' | 'failed' | 'cancelled' | 'cancelling';
type InspectorTab = 'overview' | 'files';

interface ChatMessage {
    role: 'user' | 'assistant';
    content: string;
    tone?: 'normal' | 'success' | 'error';
}

interface BuildEvent {
    type: string;
    state: string;
    message: string;
    step: number;
    total_steps: number;
    timestamp: number | string;
    status?: string;
    agent_name?: string;
    content?: string;
}

interface StageSchemaItem {
    state: string;
    label: string;
    icon: string;
}

interface Artifact {
    name: string;
    size?: number;
    type?: string;
}

interface JobSummary {
    job_id: string;
    design_name: string;
    status: string;
    current_state: string;
    created_at: number;
    event_count: number;
}

interface PdkOption {
    key: string;
    gds_ready?: boolean;
}

const FALLBACK_STAGES: StageSchemaItem[] = [
    { state: 'INIT', label: 'Init', icon: '01' },
    { state: 'SPEC', label: 'Spec', icon: '02' },
    { state: 'RTL_GEN', label: 'RTL', icon: '03' },
    { state: 'VERIFICATION', label: 'Verify', icon: '04' },
    { state: 'FORMAL_VERIFY', label: 'Formal', icon: '05' },
    { state: 'SYNTHESIS', label: 'Synth', icon: '06' },
    { state: 'HARDENING', label: 'Layout', icon: '07' },
    { state: 'SIGNOFF', label: 'Signoff', icon: '08' },
    { state: 'IP_PACKAGE', label: 'Package', icon: '09' },
];

const STAGE_NOTES: Record<string, string> = {
    INIT: 'Preparing the workspace and execution context.',
    SPEC: 'Converting your request into a precise chip specification.',
    SPEC_VALIDATE: 'Checking architecture assumptions before code generation.',
    RTL_GEN: 'Generating synthesizable RTL.',
    RTL_FIX: 'Repairing syntax, width, and tool feedback.',
    VERIFICATION: 'Running simulation and testbench checks.',
    FORMAL_VERIFY: 'Proving core behavior with formal checks.',
    SYNTHESIS: 'Converting RTL into gate-level structure.',
    FLOORPLAN: 'Planning physical placement.',
    HARDENING: 'Creating physical layout toward GDSII.',
    TIMING_ANALYSIS: 'Checking timing closure.',
    PHYSICAL_VERIFY: 'Running physical verification.',
    SIGNOFF: 'Collecting final signoff status.',
    IP_PACKAGE: 'Packaging deliverables and documentation.',
};

const EXAMPLES = [
    'SPI master controller with configurable divider and loopback testbench',
    '8-bit RISC CPU with interrupt support and memory-mapped IO',
    'AXI4-lite timer peripheral with formal checks and GDSII output',
];

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

function formatBytes(size?: number): string {
    if (!size) return 'ready';
    if (size < 1024) return `${size} B`;
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
    return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function readByokConfig(): Record<string, { model?: string; api_key?: string; base_url?: string }> | null {
    const raw = localStorage.getItem('agentic_byok_key');
    if (!raw) return null;
    try {
        const parsed = JSON.parse(raw);
        return parsed && typeof parsed === 'object' ? parsed : null;
    } catch {
        return null;
    }
}

function getByokModelLabel(): string {
    const config = readByokConfig();
    if (!config) return 'BYOK';
    const models = Object.values(config)
        .map((group) => group?.model?.trim())
        .filter(Boolean) as string[];
    const unique = Array.from(new Set(models));
    if (unique.length === 0) return 'BYOK';
    if (unique.length === 1) return unique[0];
    return `${unique[0]} + ${unique.length - 1}`;
}

function serializeByokConfig(): string | null {
    const config = readByokConfig();
    return config ? JSON.stringify(config) : null;
}

function isTextArtifact(name: string): boolean {
    return /\.(v|sv|sby|sdc|tcl|json|md|txt|log|rpt|csv|ys|cfg|lef|def)$/i.test(name);
}

export const DesignStudio = () => {
    const [phase, setPhase] = useState<Phase>('idle');
    const [modelChoice, setModelChoice] = useState<ModelChoice>('infinite');
    const [modelMenuOpen, setModelMenuOpen] = useState(false);
    const [prompt, setPrompt] = useState('');
    const [lastBuildPrompt, setLastBuildPrompt] = useState('');
    const [designName, setDesignName] = useState('');
    const [messages, setMessages] = useState<ChatMessage[]>([
        {
            role: 'assistant',
            content: 'Start building',
        },
    ]);
    const [events, setEvents] = useState<BuildEvent[]>([]);
    const [thinking, setThinking] = useState('');
    const [isChatting, setIsChatting] = useState(false);
    const [jobId, setJobId] = useState('');
    const [jobStatus, setJobStatus] = useState<JobStatus>('queued');
    const [artifacts, setArtifacts] = useState<Artifact[]>([]);
    const [selectedArtifact, setSelectedArtifact] = useState<Artifact | null>(null);
    const [artifactPreview, setArtifactPreview] = useState('');
    const [error, setError] = useState('');
    const [stageSchema, setStageSchema] = useState<StageSchemaItem[]>([]);
    const [jobs, setJobs] = useState<JobSummary[]>([]);
    const [pdkProfile, setPdkProfile] = useState('sky130');
    const [skipOpenlane, setSkipOpenlane] = useState(false);
    const [pdkOptions, setPdkOptions] = useState<PdkOption[]>([]);
    const [showBillingModal, setShowBillingModal] = useState(false);
    const [billingMode, setBillingMode] = useState<BillingMode>('byok');
    const [profile, setProfile] = useState<{ plan_type?: string; has_byok_key?: boolean } | null>(null);
    const [studioSidebarCollapsed, setStudioSidebarCollapsed] = useState(false);
    const [inspectorTab, setInspectorTab] = useState<InspectorTab>('overview');
    const [inspectorCollapsed, setInspectorCollapsed] = useState(true);
    const [statusDropdownOpen, setStatusDropdownOpen] = useState(false);

    const scrollRef = useRef<HTMLDivElement | null>(null);
    const abortRef = useRef<AbortController | null>(null);
    const announcedStages = useRef<Set<string>>(new Set());
    const artifactFetchAt = useRef(0);

    const isInfiniteAvailable = profile?.plan_type === 'agentic_paid';
    const byokLabel = getByokModelLabel();
    const hasByok = Boolean(profile?.has_byok_key) || Boolean(readByokConfig());
    const activeStages = stageSchema.length ? stageSchema : FALLBACK_STAGES;
    const currentEvent = [...events].reverse().find((event) => event.state && event.state !== 'UNKNOWN');
    const currentStage = currentEvent?.state || 'INIT';
    const currentStageIndex = Math.max(0, activeStages.findIndex((stage) => stage.state === currentStage));
    const currentStageLabel = activeStages.find((stage) => stage.state === currentStage)?.label || currentStage;
    const liveStatusText = thinking || STAGE_NOTES[currentStage] || currentEvent?.message || 'AgentIC is working through the next chip-build step.';
    const progress = phase === 'done' && jobStatus === 'done'
        ? 100
        : phase === 'building'
            ? Math.min(98, Math.round(((currentStageIndex + 1) / Math.max(activeStages.length, 1)) * 100))
            : 0;
            
    const completedStages = useMemo(() => {
        const done = new Set<string>();
        events.forEach(e => {
            if (e.status === 'done' && e.state && e.state !== 'UNKNOWN') done.add(e.state);
        });
        // Also add all stages prior to current stage if we are advancing
        if (currentStageIndex > 0) {
            for (let i = 0; i < currentStageIndex; i++) {
                done.add(activeStages[i].state);
            }
        }
        return done;
    }, [events, currentStageIndex, activeStages]);

    const visibleArtifacts = useMemo(() => {
        const priority = ['.v', '.sv', '.sby', '.sdc', '.gds', '.def', '.lef', '.rpt', '.pdf', '.docx', '.json', '.log'];
        return [...artifacts].sort((a, b) => {
            const ap = priority.findIndex((ext) => a.name.toLowerCase().endsWith(ext));
            const bp = priority.findIndex((ext) => b.name.toLowerCase().endsWith(ext));
            return (ap === -1 ? 99 : ap) - (bp === -1 ? 99 : bp);
        });
    }, [artifacts]);

    const artifactSections = useMemo(() => {
        const sections = [
            { label: 'RTL', types: ['rtl'] },
            { label: 'Verification', types: ['formal', 'waveform'] },
            { label: 'Physical', types: ['layout', 'timing', 'constraints'] },
            { label: 'Reports', types: ['report', 'log', 'config', 'script', 'other'] },
        ];
        return sections
            .map((section) => ({
                ...section,
                files: visibleArtifacts.filter((artifact) => section.types.includes(artifact.type || 'other')),
            }))
            .filter((section) => section.files.length > 0);
    }, [visibleArtifacts]);

    useEffect(() => {
        scrollRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
    }, [messages, thinking, events.length]);

    useEffect(() => {
        if (selectedArtifact && visibleArtifacts.some((artifact) => artifact.name === selectedArtifact.name)) {
            return;
        }
        setSelectedArtifact(visibleArtifacts[0] || null);
        setArtifactPreview('');
    }, [selectedArtifact, visibleArtifacts]);

    useEffect(() => {
        Promise.allSettled([
            api.get('/profile'),
            api.get('/billing/status'),
            api.get('/pipeline/schema'),
            api.get('/pdks'),
            api.get('/jobs'),
        ]).then(([profileRes, billingRes, schemaRes, pdksRes, jobsRes]) => {
            const prof = profileRes.status === 'fulfilled' ? profileRes.value.data : null;
            const billing = billingRes.status === 'fulfilled' ? billingRes.value.data : null;
            if (prof && billing) setProfile({ ...prof, plan_type: billing.plan_type });
            else if (prof) setProfile(prof);
            if (schemaRes.status === 'fulfilled') setStageSchema(schemaRes.value.data?.stages || []);
            if (jobsRes.status === 'fulfilled') setJobs(jobsRes.value.data?.jobs || []);
            if (pdksRes.status === 'fulfilled') {
                const pdks: PdkOption[] = pdksRes.value.data?.pdks || [];
                const defaultPdk = pdksRes.value.data?.default || 'sky130';
                const readyDefault = pdks.find((pdk) => pdk.key === defaultPdk && pdk.gds_ready);
                const firstReady = pdks.find((pdk) => pdk.gds_ready);
                setPdkOptions(pdks);
                setPdkProfile(readyDefault?.key || firstReady?.key || defaultPdk);
                setSkipOpenlane(!readyDefault && !firstReady);
            }
        });

        // Resolve landing page carried prompts
        const initialPrompt = localStorage.getItem('agentic_studio_initial_prompt');
        const initialPdk = localStorage.getItem('agentic_studio_initial_pdk');
        if (initialPrompt) {
            setPrompt(initialPrompt);
            setDesignName(slugify(initialPrompt));
            localStorage.removeItem('agentic_studio_initial_prompt');
        }
        if (initialPdk) {
            setPdkProfile(initialPdk);
            localStorage.removeItem('agentic_studio_initial_pdk');
        }

        return () => abortRef.current?.abort();
    }, []);

    useEffect(() => {
        if (!selectedArtifact || !designName) {
            return;
        }
        if (!isTextArtifact(selectedArtifact.name)) {
            window.setTimeout(() => setArtifactPreview('Binary artifact. Download it from the artifact list.'), 0);
            return;
        }
        api.get(`/build/artifacts/${designName}/${selectedArtifact.name}`, { responseType: 'text' })
            .then((res) => {
                const raw = typeof res.data === 'string' ? res.data : JSON.stringify(res.data, null, 2);
                setArtifactPreview(raw.length > 14000 ? `${raw.slice(0, 14000)}\n\n[Preview truncated]` : raw);
            })
            .catch(() => setArtifactPreview('Preview unavailable.'));
    }, [selectedArtifact, designName]);

    const addAssistant = (content: string, tone: ChatMessage['tone'] = 'normal') => {
        setMessages((previous) => [...previous, { role: 'assistant', content, tone }]);
    };

    const requestAgenticSubscription = () => {
        setBillingMode('agentic');
        setShowBillingModal(true);
        addAssistant(
            '**Infinite requires AgentIC paid.** Subscribe to AgentIC paid to use the hosted Infinite model, or switch to BYOK to run with your own model key.',
            'normal'
        );
    };

    const requestByokSetup = () => {
        setBillingMode('byok');
        setShowBillingModal(true);
    };

    const fetchArtifacts = async (targetDesign = designName, force = false) => {
        if (!targetDesign) return;
        const now = Date.now();
        if (!force && now - artifactFetchAt.current < 1400) return;
        artifactFetchAt.current = now;
        try {
            const res = await api.get(`/build/artifacts/${targetDesign}`);
            setArtifacts(Array.isArray(res.data?.artifacts) ? res.data.artifacts : []);
        } catch {
            // Artifacts are created incrementally; early misses are expected.
        }
    };

    const sendMessage = async () => {
        const text = prompt.trim();
        if (!text || phase === 'building' || isChatting) return;

        if (modelChoice === 'infinite' && !isInfiniteAvailable) {
            requestAgenticSubscription();
            return;
        }
        if (modelChoice === 'byok' && !hasByok) {
            requestByokSetup();
            return;
        }

        const nextMessages: ChatMessage[] = [...messages, { role: 'user', content: text }];
        setMessages(nextMessages);
        setLastBuildPrompt(text);
        setPrompt('');
        setIsChatting(true);
        setThinking('Thinking through the chip specification.');

        try {
            const byokPayload = modelChoice === 'byok' ? serializeByokConfig() : null;
            const res = await api.post('/chat/converse', {
                messages: nextMessages.map((message) => ({ role: message.role, content: message.content })),
                plan_type: modelChoice === 'infinite' ? 'agentic_paid' : 'byok',
                api_key: byokPayload,
            });
            const reply = res.data?.reply || 'I refined the chip specification. It is ready to run through AgentIC.';
            setMessages((previous) => [...previous, { role: 'assistant', content: reply }]);
        } catch (err: unknown) {
            const detail = typeof err === 'object' && err !== null && 'response' in err
                ? (err as { response?: { data?: { detail?: unknown } } }).response?.data?.detail
                : undefined;
            setMessages((previous) => [
                ...previous,
                {
                    role: 'assistant',
                    tone: 'error',
                    content: toUserError(detail, 'I could not reach the model, but your prompt is still ready for the AgentIC pipeline.'),
                },
            ]);
        } finally {
            setThinking('');
            setIsChatting(false);
        }
    };

    const launch = async () => {
        const description = prompt.trim() || lastBuildPrompt.trim();
        if (!description || phase === 'building') return;

        if (modelChoice === 'infinite' && !isInfiniteAvailable) {
            requestAgenticSubscription();
            return;
        }
        if (modelChoice === 'byok' && !hasByok) {
            requestByokSetup();
            return;
        }

        const selectedPdk = pdkOptions.find((pdk) => pdk.key === pdkProfile);
        if (!skipOpenlane && selectedPdk && selectedPdk.gds_ready === false) {
            setError(`${selectedPdk.key} is not ready for GDSII. Switch to RTL-only or choose an installed PDK.`);
            return;
        }

        const nextDesignName = (designName.trim() || slugify(description) || 'agentic_chip').slice(0, 64);
        abortRef.current?.abort();
        announcedStages.current = new Set();
        setPhase('building');
        setInspectorCollapsed(false);
        setError('');
        setEvents([]);
        setArtifacts([]);
        setSelectedArtifact(null);
        setArtifactPreview('');
        setJobStatus('queued');
        setMessages((previous) => [
            ...previous,
            { role: 'user', content: description },
            {
                role: 'assistant',
                content: `Running **${nextDesignName}** on **${modelChoice === 'infinite' ? 'Infinite' : byokLabel}**. I will surface stage changes, errors, and generated chip documents as they arrive.`,
            },
        ]);

        try {
            const byok = modelChoice === 'byok' ? serializeByokConfig() : null;
            const res = await api.post('/build', {
                design_name: nextDesignName,
                description,
                skip_openlane: skipOpenlane,
                show_thinking: true,
                max_retries: 5,
                min_coverage: 80.0,
                pdk_profile: pdkProfile,
                plan_type: modelChoice === 'infinite' ? 'agentic_paid' : 'byok',
                api_key: byok,
            });
            const activeJobId = res.data.job_id;
            const activeDesignName = res.data.design_name || nextDesignName;
            setJobId(activeJobId);
            setDesignName(activeDesignName);
            setJobStatus('running');
            void stream(activeJobId, byok, activeDesignName);
        } catch (err: unknown) {
            setPhase('idle');
            if (isNetworkError(err)) {
                setError('Unable to connect to the build service.');
            } else {
                const detail = typeof err === 'object' && err !== null && 'response' in err
                    ? (err as { response?: { data?: { detail?: unknown } } }).response?.data?.detail
                    : undefined;
                setError(toUserError(detail, 'Build failed to start.'));
            }
        }
    };

    const stream = async (activeJobId: string, byokPayload: string | null, activeDesignName: string) => {
        const ctrl = new AbortController();
        abortRef.current = ctrl;
        const headers = await getSseHeaders(byokPayload ? { 'X-LLM-API-Key': byokPayload } : {});
        let retries = 0;

        fetchEventSource(`${API_BASE}/build/stream/${activeJobId}`, {
            method: 'GET',
            headers,
            signal: ctrl.signal,
            openWhenHidden: true,
            async onopen(response) {
                const type = response.headers.get('content-type') || '';
                if (response.ok && type.includes('text/event-stream')) {
                    retries = 0;
                    return;
                }
                throw new Error('Stream unavailable');
            },
            onmessage(event) {
                try {
                    const data: BuildEvent = JSON.parse(event.data);
                    if (data.type === 'ping') return;
                    if (data.type === 'stream_end') {
                        ctrl.abort();
                        void finish(data.status || 'failed', activeDesignName);
                        return;
                    }
                    if (data.type === 'agent_thinking') {
                        setThinking(data.message || data.content || 'Reasoning through the next pipeline action.');
                        return;
                    }
                    setThinking('');
                    setEvents((previous) => {
                        const duplicate = previous.some((item) => (
                            item.timestamp === data.timestamp && item.type === data.type && item.message === data.message
                        ));
                        return duplicate ? previous : [...previous, data];
                    });
                    setJobStatus(data.type === 'error' ? 'failed' : 'running');

                    const stage = data.state && data.state !== 'UNKNOWN' ? data.state : '';
                    if (stage && !announcedStages.current.has(stage) && data.type !== 'log') {
                        announcedStages.current.add(stage);
                        const label = activeStages.find((item) => item.state === stage)?.label || stage;
                        addAssistant(`**${label}**\n\n${STAGE_NOTES[stage] || data.message || 'Pipeline stage started.'}`);
                    }
                    if (data.type === 'error') {
                        const message = data.message || 'The pipeline reported an error.';
                        setError(message);
                        addAssistant(`**Pipeline error**\n\n${message}`, 'error');
                    }
                    void fetchArtifacts(activeDesignName);
                } catch {
                    // Ignore malformed keepalive payloads.
                }
            },
            onerror(err) {
                if (ctrl.signal.aborted) return;
                retries += 1;
                if (retries > 8) {
                    ctrl.abort();
                    setError('Live connection lost. Checking final build status.');
                    void finish('failed', activeDesignName);
                    throw err;
                }
                return Math.min(1000 * retries, 5000);
            },
        }).catch(() => {
            if (ctrl.signal.aborted) return;
            setError('Live connection lost. Checking final build status.');
            void finish('failed', activeDesignName);
        });
    };

    const finish = async (status: string, activeDesignName: string) => {
        const finalStatus: JobStatus = status === 'done' ? 'done' : status === 'cancelled' ? 'cancelled' : 'failed';
        setJobStatus(finalStatus);
        setPhase('done');
        setThinking('');
        await fetchArtifacts(activeDesignName, true);
        addAssistant(
            finalStatus === 'done'
                ? `**Build complete.** Generated files are ready below: RTL, testbench/formal collateral, reports, and layout outputs when enabled.`
                : `**Build stopped with status ${finalStatus}.** Partial artifacts are preserved below for inspection.`,
            finalStatus === 'done' ? 'success' : 'error'
        );
    };

    const cancel = async () => {
        if (!jobId) return;
        try {
            await api.post(`/build/cancel/${jobId}`);
            setJobStatus('cancelling');
            addAssistant('Cancellation requested. I will keep any generated artifacts.', 'normal');
        } catch {
            setError('Unable to cancel this run.');
        }
    };

    const reset = () => {
        abortRef.current?.abort();
        setPhase('idle');
        setEvents([]);
        setArtifacts([]);
        setSelectedArtifact(null);
        setArtifactPreview('');
        setThinking('');
        setJobId('');
        setJobStatus('queued');
        setError('');
        setInspectorCollapsed(true);
    };

    const downloadArtifact = async (artifact: Artifact) => {
        try {
            const res = await api.get(`/build/artifacts/${designName}/${artifact.name}`, { responseType: 'blob' });
            const url = window.URL.createObjectURL(res.data);
            const link = document.createElement('a');
            link.href = url;
            link.download = artifact.name;
            document.body.appendChild(link);
            link.click();
            link.remove();
            window.URL.revokeObjectURL(url);
        } catch {
            setError(`Unable to download ${artifact.name}.`);
        }
    };
    return (
        <div className="studio-min-root">
            {/* Drawer Backdrop */}
            <div 
                className={`studio-drawer-backdrop${!inspectorCollapsed ? ' active' : ''}`} 
                onClick={() => setInspectorCollapsed(true)} 
            />

            {/* Lab Drawer Toggle (Floating) */}
            <button 
                className="studio-drawer-toggle" 
                onClick={() => setInspectorCollapsed(false)}
            >
                <Folder size={14} />
                Code & Lab
                <em>{artifacts.length}</em>
            </button>

            <main className="studio-min-canvas">
                <div className="studio-min-thread">
                    {phase === 'idle' && (
                        <div className="studio-greeting">
                            <h1>Good to see you 👋</h1>
                            <p>Describe your custom silicon. AgentIC handles RTL generation, formal verification, and layout.</p>
                        </div>
                    )}

                    {messages.map((message, index) => (
                        <motion.article
                            key={`${message.role}-${index}`}
                            className={`studio-min-message ${message.role} ${message.tone || ''}`}
                            initial={{ opacity: 0, y: 8 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ duration: 0.18 }}
                        >
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
                        </motion.article>
                    ))}
                    {(thinking || phase === 'building') && (
                        <div className="studio-min-thinking">
                            <span><i /><i /><i /></span>
                            <p>{thinking || 'AgentIC is working through the pipeline.'}</p>
                        </div>
                    )}
                    <div ref={scrollRef} />
                </div>

                <section className={`studio-min-launch ${phase !== 'idle' ? 'is-running' : ''}`}>
                    <div className="studio-min-project-label" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', width: '100%' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                            <Folder size={16} />
                            AgentIC
                            {phase === 'building' && (
                                <motion.span
                                    key={currentStage}
                                    className="studio-min-inline-status"
                                    initial={{ opacity: 0, y: 4 }}
                                    animate={{ opacity: 1, y: 0 }}
                                    transition={{ duration: 0.28 }}
                                >
                                    {currentStageLabel}: {liveStatusText}
                                </motion.span>
                            )}
                        </div>
                        {(phase !== 'idle' || artifacts.length > 0 || error) && (
                            <button 
                                className="studio-inspector-toggle-btn"
                                onClick={() => setInspectorCollapsed(!inspectorCollapsed)}
                                style={{ 
                                    background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.06)', 
                                    color: 'var(--text-mid)', fontSize: '0.75rem', cursor: 'pointer', display: 'flex', 
                                    alignItems: 'center', gap: '0.35rem', padding: '3px 8px',
                                    borderRadius: '6px', fontFamily: 'var(--font-sans)', transition: 'all 0.15s ease'
                                }}
                            >
                                <span>{inspectorCollapsed ? 'Show Lab Drawer' : 'Hide Lab Drawer'}</span>
                            </button>
                        )}
                    </div>
                    <div className="studio-min-composer">
                        <textarea
                            value={prompt}
                            onChange={(event) => {
                                const nextPrompt = event.target.value;
                                setPrompt(nextPrompt);
                                if (!designName && nextPrompt.length > 8) setDesignName(slugify(nextPrompt));
                            }}
                            onKeyDown={(event) => {
                                if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
                                    event.preventDefault();
                                    void launch();
                                }
                            }}
                            placeholder="Ask AgentIC to build a chip..."
                            rows={3}
                        />
                        <div className="studio-min-composer-bar">
                            <div className="studio-min-left-tools">
                                <button type="button" className="studio-min-plus" onClick={sendMessage} disabled={isChatting || !prompt.trim()}>
                                    <Plus size={16} />
                                </button>
                                <div className="studio-min-model">
                                    <button type="button" onClick={() => setModelMenuOpen((open) => !open)}>
                                        {modelChoice === 'infinite' ? <Sparkles size={15} /> : <KeyRound size={15} />}
                                        <span>{modelChoice === 'infinite' ? 'Infinite' : byokLabel}</span>
                                        <ChevronDown size={14} />
                                    </button>
                                    {modelMenuOpen && (
                                        <div className="studio-min-model-menu">
                                            <button
                                                className={modelChoice === 'infinite' ? 'active' : ''}
                                                onClick={() => {
                                                    setModelMenuOpen(false);
                                                    if (!isInfiniteAvailable) {
                                                        requestAgenticSubscription();
                                                        return;
                                                    }
                                                    setModelChoice('infinite');
                                                }}
                                            >
                                                <Sparkles size={15} />
                                                <div>
                                                    <strong>Infinite</strong>
                                                    <span>AgentIC hosted model</span>
                                                </div>
                                                {modelChoice === 'infinite' && <Check size={14} />}
                                            </button>
                                            <button
                                                className={modelChoice === 'byok' ? 'active' : ''}
                                                onClick={() => {
                                                    setModelChoice('byok');
                                                    setModelMenuOpen(false);
                                                    if (!hasByok) requestByokSetup();
                                                }}
                                            >
                                                <KeyRound size={15} />
                                                <div>
                                                    <strong>{hasByok ? byokLabel : 'BYOK'}</strong>
                                                    <span>{hasByok ? 'Your configured model' : 'Add your own key'}</span>
                                                </div>
                                                {modelChoice === 'byok' && <Check size={14} />}
                                            </button>
                                        </div>
                                    )}
                                </div>
                                <button className="studio-min-local" type="button" onClick={() => setSkipOpenlane((value) => !value)}>
                                    <Cpu size={15} />
                                    {skipOpenlane ? 'RTL only' : pdkProfile}
                                </button>
                            </div>
                            <div className="studio-min-actions">
                                <input
                                    value={designName}
                                    onChange={(event) => setDesignName(event.target.value)}
                                    placeholder="design_name"
                                    aria-label="Design name"
                                />
                                <button className="studio-min-send" disabled={!(prompt.trim() || lastBuildPrompt.trim()) || phase === 'building'} onClick={launch}>
                                    <ArrowUp size={17} />
                                </button>
                            </div>
                        </div>
                    </div>
                    {EXAMPLES.length > 0 && phase === 'idle' && (
                        <div className="studio-min-examples">
                            {EXAMPLES.map((example) => (
                                <button
                                    key={example}
                                    onClick={() => {
                                        setPrompt(example);
                                        if (!designName) setDesignName(slugify(example));
                                    }}
                                >
                                    {example}
                                </button>
                            ))}
                        </div>
                    )}
                </section>

                <AnimatePresence>
                    {(phase !== 'idle' || artifacts.length > 0 || error) && (
                        <motion.div
                            className={`studio-status-pill ${statusDropdownOpen ? 'expanded' : ''}`}
                            initial={{ opacity: 0, y: -20 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, y: -20 }}
                            onClick={() => setStatusDropdownOpen(!statusDropdownOpen)}
                        >
                            <div className={`studio-status-dot ${error ? 'failed' : jobStatus === 'done' ? 'done' : ''}`} />
                            <span className="studio-status-label">Building ·</span>
                            <span className="studio-status-stage">{currentStageLabel}</span>
                            <span className="studio-status-pct">({progress}%)</span>
                            <ChevronDown size={14} className="studio-status-chevron" />
                            
                            <AnimatePresence>
                                {statusDropdownOpen && (
                                    <motion.div 
                                        className="studio-status-dropdown"
                                        initial={{ opacity: 0, y: 10, scale: 0.98 }}
                                        animate={{ opacity: 1, y: 0, scale: 1 }}
                                        exit={{ opacity: 0, y: 10, scale: 0.98 }}
                                        transition={{ duration: 0.15 }}
                                        onClick={(e) => e.stopPropagation()}
                                    >
                                        <div className="studio-pipeline-grid">
                                            {FALLBACK_STAGES.map((s) => {
                                                const isActive = currentStage === s.state;
                                                const isDone = completedStages.has(s.state);
                                                return (
                                                    <div key={s.state} className={`studio-pipeline-stage ${isActive ? 'active' : ''} ${isDone ? 'done' : ''}`}>
                                                        <div className="stage-icon">{s.icon}</div>
                                                        <span className="stage-label">{s.label}</span>
                                                    </div>
                                                );
                                            })}
                                        </div>
                                        
                                        {error && (
                                            <div className="studio-min-error" style={{ marginBottom: '1rem' }}>
                                                <AlertCircle size={16} />
                                                {error}
                                            </div>
                                        )}
                                        
                                        <div className="studio-status-actions">
                                            <span className="studio-status-note">{liveStatusText}</span>
                                            {phase === 'building' ? (
                                                <button onClick={cancel}><CircleStop size={14} /> Stop Build</button>
                                            ) : (
                                                <button onClick={reset}><RotateCcw size={14} /> New Run</button>
                                            )}
                                        </div>
                                    </motion.div>
                                )}
                            </AnimatePresence>
                        </motion.div>
                    )}
                </AnimatePresence>
            </main>

            <aside className={`studio-min-inspector${!inspectorCollapsed ? ' drawer-open' : ''}`}>
                <button className="studio-drawer-close" onClick={() => setInspectorCollapsed(true)}>
                    <ChevronRight size={16} />
                </button>

                <div className="studio-min-inspector-tabs">
                    <button
                        className={inspectorTab === 'overview' ? 'active' : ''}
                        onClick={() => setInspectorTab('overview')}
                    >
                        Overview
                    </button>
                    <button
                        className={inspectorTab === 'files' ? 'active' : ''}
                        onClick={() => setInspectorTab('files')}
                    >
                        Files
                    </button>
                </div>

                <div className="studio-min-inspector-scroll">
                    <section className="studio-min-inspector-section">
                        <button className="studio-min-inspector-heading" type="button">
                            <span>Files Changed</span>
                            <em>{visibleArtifacts.length}</em>
                            <ChevronDown size={14} />
                        </button>
                        {visibleArtifacts.length === 0 ? (
                            <p className="studio-min-inspector-empty">Generated chip files will appear here as the pipeline writes them.</p>
                        ) : (
                            <div className="studio-min-file-list">
                                {visibleArtifacts.slice(0, inspectorTab === 'overview' ? 7 : 40).map((artifact) => (
                                    <button
                                        key={artifact.name}
                                        className={selectedArtifact?.name === artifact.name ? 'active' : ''}
                                        onClick={() => {
                                            setArtifactPreview('');
                                            setSelectedArtifact(artifact);
                                            setInspectorTab('files');
                                        }}
                                    >
                                        <FileText size={15} />
                                        <span>{artifact.name}</span>
                                        <em>{artifact.type || 'file'}</em>
                                    </button>
                                ))}
                            </div>
                        )}
                    </section>

                    <section className="studio-min-inspector-section">
                        <button className="studio-min-inspector-heading" type="button">
                            <span>Artifacts</span>
                            <em>{artifactSections.length}</em>
                            <ChevronDown size={14} />
                        </button>
                        {artifactSections.length === 0 ? (
                            <p className="studio-min-inspector-empty">RTL, testbench, formal, reports, and layout outputs will collect here.</p>
                        ) : (
                            <div className="studio-min-artifact-groups">
                                {artifactSections.map((section) => (
                                    <div key={section.label} className="studio-min-artifact-group">
                                        <strong>{section.label}</strong>
                                        {section.files.slice(0, inspectorTab === 'overview' ? 3 : 12).map((artifact) => (
                                            <button
                                                key={`${section.label}-${artifact.name}`}
                                                className={selectedArtifact?.name === artifact.name ? 'active' : ''}
                                                onClick={() => {
                                                    setArtifactPreview('');
                                                    setSelectedArtifact(artifact);
                                                    setInspectorTab('files');
                                                }}
                                                onDoubleClick={() => downloadArtifact(artifact)}
                                            >
                                                <span>{artifact.name}</span>
                                                <em>{formatBytes(artifact.size)}</em>
                                            </button>
                                        ))}
                                    </div>
                                ))}
                            </div>
                        )}
                    </section>

                    {selectedArtifact && (
                        <section className="studio-min-inspector-section studio-min-inspector-preview">
                            <div className="studio-min-preview-head">
                                <div>
                                    <span>{selectedArtifact.name}</span>
                                    <em>{formatBytes(selectedArtifact.size)}</em>
                                </div>
                                <button onClick={() => downloadArtifact(selectedArtifact)}>Download</button>
                            </div>
                            <pre className="studio-min-preview">{artifactPreview || 'Loading preview...'}</pre>
                        </section>
                    )}
                </div>
            </aside>

            <BillingModal
                isOpen={showBillingModal}
                onClose={() => setShowBillingModal(false)}
                initialMode={billingMode}
                onKeySaved={() => {
                    setModelChoice('byok');
                    api.get('/profile')
                        .then((res) => setProfile((previous) => ({ ...previous, ...res.data, has_byok_key: true })))
                        .catch(() => setProfile((previous) => ({ ...previous, has_byok_key: true })));
                    setShowBillingModal(false);
                }}
            />
        </div>
    );
};
