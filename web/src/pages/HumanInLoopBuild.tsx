import { useState, useEffect, useRef } from 'react';
import { fetchEventSource } from '@microsoft/fetch-event-source';
import { ActivityFeed } from '../components/ActivityFeed';
import { StageProgressBar } from '../components/StageProgressBar';
import { ApprovalCard } from '../components/ApprovalCard';
import { api, API_BASE } from '../api';
import '../hitl.css';

const PIPELINE_STAGES = [
    'INIT', 'SPEC', 'SPEC_VALIDATE', 'HIERARCHY_EXPAND', 'FEASIBILITY_CHECK', 'CDC_ANALYZE', 'VERIFICATION_PLAN', 'RTL_GEN', 'RTL_FIX', 'VERIFICATION', 'FORMAL_VERIFY',
    'COVERAGE_CHECK', 'REGRESSION', 'SDC_GEN', 'FLOORPLAN', 'HARDENING',
    'CONVERGENCE_REVIEW', 'ECO_PATCH', 'SIGNOFF',
];
const TOTAL = PIPELINE_STAGES.length;

// Contextual messages for the bottom status bar — what the agent is doing right now
const STAGE_ENCOURAGEMENTS: Record<string, string> = {
    INIT:              'Setting up your build environment…',
    SPEC:              'Translating your description into a chip specification…',
    SPEC_VALIDATE:     'Validating spec — classifying design, checking completeness, generating assertions…',
    HIERARCHY_EXPAND:   'Expanding complex submodules into nested specifications…',
    FEASIBILITY_CHECK:  'Evaluating Sky130 physical design feasibility…',
    CDC_ANALYZE:        'Analyzing clock domain crossings…',
    VERIFICATION_PLAN:  'Building verification plan & SVA properties…',
    RTL_GEN:           'Writing Verilog — your chip is taking shape…',
    RTL_FIX:           'Fixing any RTL issues automatically…',
    VERIFICATION:      'Running simulation — making sure your logic is correct…',
    FORMAL_VERIFY:     'Proving your chip is correct with formal methods…',
    COVERAGE_CHECK:    'Measuring how thoroughly the tests cover your design…',
    REGRESSION:        'Running the full regression suite…',
    SDC_GEN:           'Generating timing constraints for physical design…',
    FLOORPLAN:         'Planning the physical layout of your chip…',
    HARDENING:         'Running place-and-route — turning RTL into real silicon…',
    CONVERGENCE_REVIEW:'Checking that timing is met across all corners…',
    ECO_PATCH:         'Applying final tweaks for clean sign-off…',
    SIGNOFF:           'Almost there — running final LVS/DRC checks…',
};

// Milestone stages that deserve a special celebration toast
const MILESTONE_TOASTS: Record<string, { title: string; msg: string }> = {
    RTL_GEN:   { title: 'RTL Complete', msg: 'Your chip can now run instructions. Verilog is ready.' },
    VERIFICATION: { title: 'Verification Passed', msg: 'All simulation tests passed. Design is logically correct.' },
    HARDENING: { title: 'Silicon Layout Done', msg: 'Place-and-route complete. Your chip has a physical form.' },
    SIGNOFF:   { title: 'Chip Signed Off', msg: 'All checks passed. Ready for tape-out.' },
};

// Human-readable stage names
const STAGE_LABELS: Record<string, string> = {
    INIT: 'Initialization', SPEC: 'Specification', SPEC_VALIDATE: 'Spec Validation', HIERARCHY_EXPAND: 'Hierarchy Expansion', FEASIBILITY_CHECK: 'Feasibility Check', CDC_ANALYZE: 'CDC Analysis', VERIFICATION_PLAN: 'Verification Plan', RTL_GEN: 'RTL Generation',
    RTL_FIX: 'RTL Fix', VERIFICATION: 'Verification', FORMAL_VERIFY: 'Formal Verification',
    COVERAGE_CHECK: 'Coverage Check', REGRESSION: 'Regression', SDC_GEN: 'SDC Generation',
    FLOORPLAN: 'Floorplan', HARDENING: 'Hardening', CONVERGENCE_REVIEW: 'Convergence',
    ECO_PATCH: 'ECO Patch', SIGNOFF: 'Signoff', FAIL: 'Failed',
};

// Mandatory stages (cannot be skipped)
const MANDATORY_STAGES = new Set([
    'INIT', 'SPEC', 'SPEC_VALIDATE', 'HIERARCHY_EXPAND', 'FEASIBILITY_CHECK', 'CDC_ANALYZE', 'VERIFICATION_PLAN', 'RTL_GEN', 'RTL_FIX', 'VERIFICATION', 'HARDENING', 'SIGNOFF',
]);

// Build mode presets
type BuildMode = 'quick' | 'verified' | 'full';
const BUILD_MODE_SKIPS: Record<BuildMode, string[]> = {
    quick: ['FORMAL_VERIFY', 'COVERAGE_CHECK', 'REGRESSION', 'SDC_GEN', 'FLOORPLAN', 'HARDENING', 'CONVERGENCE_REVIEW', 'ECO_PATCH', 'SIGNOFF'],
    verified: ['REGRESSION', 'ECO_PATCH', 'CONVERGENCE_REVIEW'],
    full: [],
};

interface BuildEvent {
    type: string;
    state: string;
    message: string;
    step: number;
    total_steps: number;
    timestamp: number | string;
    status?: string;
    // agent_thought fields
    agent_name?: string;
    thought_type?: string;
    content?: string;
    // stage_complete fields
    stage_name?: string;
    summary?: string;
    artifacts?: Array<{ name: string; path: string; description: string }>;
    decisions?: string[];
    warnings?: string[];
    next_stage_name?: string;
    next_stage_preview?: string;
}

interface StageCompleteData {
    stage_name: string;
    summary: string;
    artifacts: Array<{ name: string; path: string; description: string }>;
    decisions: string[];
    warnings: string[];
    next_stage_name: string;
    next_stage_preview: string;
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

type Phase = 'prompt' | 'building' | 'done';

export const HumanInLoopBuild = () => {
    const [phase, setPhase] = useState<Phase>('prompt');
    const [prompt, setPrompt] = useState('');
    const [designName, setDesignName] = useState('');
    const [jobId, setJobId] = useState('');
    const [events, setEvents] = useState<BuildEvent[]>([]);
    const [jobStatus, setJobStatus] = useState<string>('queued');
    const [result, setResult] = useState<any>(null);
    const [error, setError] = useState('');
    const [isSubmitting, setIsSubmitting] = useState(false);
    const abortCtrlRef = useRef<AbortController | null>(null);

    // Build options
    const [skipOpenlane, setSkipOpenlane] = useState(false);
    const [skipCoverage, setSkipCoverage] = useState(false);
    const [showAdvanced, setShowAdvanced] = useState(false);
    const [maxRetries, setMaxRetries] = useState(5);
    const [showThinking, setShowThinking] = useState(false);
    const [minCoverage, setMinCoverage] = useState(80.0);
    const [strictGates, setStrictGates] = useState(false);
    const [pdkProfile, setPdkProfile] = useState("sky130");

    // Build mode & stage skipping (Improvement 2)
    const [buildMode, setBuildMode] = useState<BuildMode>('verified');
    const [skipStages, setSkipStages] = useState<Set<string>>(new Set(BUILD_MODE_SKIPS.verified));
    const [showStageToggles, setShowStageToggles] = useState(false);

    // Stage tracking
    const [currentStage, setCurrentStage] = useState('INIT');
    const [completedStages, setCompletedStages] = useState<Set<string>>(new Set());
    const [failedStage, setFailedStage] = useState<string | undefined>();
    const [waitingForApproval, setWaitingForApproval] = useState(false);
    const [approvalData, setApprovalData] = useState<StageCompleteData | null>(null);

    // Fetch partial artifacts on build failure
    const [partialArtifacts, setPartialArtifacts] = useState<Array<{name: string; path: string; size: number; type: string}>>([]);
    const [showFullLog, setShowFullLog] = useState(false);

    // Thinking indicator (Improvement 1)
    const [thinkingData, setThinkingData] = useState<{ agent_name: string; message: string } | null>(null);

    // Stall detection — shown when LLM is silent for 5+ minutes
    const [stallWarning, setStallWarning] = useState<string | null>(null);

    // Milestone toast: shown briefly when a key stage completes
    const [milestoneToast, setMilestoneToast] = useState<{ title: string; msg: string } | null>(null);
    const milestoneTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    useEffect(() => {
        if (prompt.length > 8) {
            setDesignName(slugify(prompt));
        }
    }, [prompt]);

    const handleLaunch = async () => {
        if (!prompt.trim()) return;
        setError('');
        // Quick RTL mode implies skip_openlane
        const effectiveSkipOpenlane = buildMode === 'quick' || skipOpenlane;
        const effectiveSkipCoverage = skipCoverage || skipStages.has('COVERAGE_CHECK');
        try {
            const res = await api.post(`/build`, {
                design_name: designName || slugify(prompt),
                description: prompt,
                skip_openlane: effectiveSkipOpenlane,
                skip_coverage: effectiveSkipCoverage,
                max_retries: maxRetries,
                show_thinking: showThinking,
                min_coverage: minCoverage,
                strict_gates: strictGates,
                pdk_profile: pdkProfile,
                human_in_loop: true,
                skip_stages: Array.from(skipStages),
            });
            const { job_id, design_name: dn } = res.data;
            setJobId(job_id);
            if (dn) setDesignName(dn);
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

                    // Handle stall warning: LLM silent for 5+ minutes
                    if (data.type === 'stall_warning') {
                        setStallWarning(data.message || '⚠️ No activity for 5 minutes — the LLM may be stuck. You can cancel and retry.');
                        return;
                    }

                    // Any real event clears the stall warning and thinking indicator
                    if (data.type === 'log' || data.type === 'checkpoint' || data.type === 'transition') {
                        setStallWarning(null);
                    }

                    // Handle agent_thinking: show pulsing indicator
                    if (data.type === 'agent_thinking') {
                        setThinkingData({ agent_name: data.agent_name || '', message: data.message || '' });
                        return; // Don't add to event feed
                    }

                    // Any real event clears the thinking indicator
                    if (data.type !== 'agent_thinking') {
                        setThinkingData(null);
                    }

                    // Handle stage_complete: show approval card
                    if (data.type === 'stage_complete') {
                        setApprovalData({
                            stage_name: data.stage_name || data.state || '',
                            summary: data.summary || '',
                            artifacts: data.artifacts || [],
                            decisions: data.decisions || [],
                            warnings: data.warnings || [],
                            next_stage_name: data.next_stage_name || '',
                            next_stage_preview: data.next_stage_preview || '',
                        });
                        setWaitingForApproval(true);
                        setCurrentStage(data.stage_name || data.state || '');
                    }

                    // Track stage progression
                    if (data.type === 'transition' || data.state) {
                        const newState = data.state;
                        if (newState && newState !== 'UNKNOWN') {
                            setCurrentStage(prev => {
                                if (prev !== newState && prev !== 'INIT') {
                                    setCompletedStages(cs => {
                                        const next = new Set(cs);
                                        next.add(prev);
                                        return next;
                                    });
                                }
                                return newState;
                            });
                        }
                        if (newState === 'FAIL') {
                            setFailedStage(newState);
                        }
                    }

                    // Add event to feed
                    setEvents(prev => {
                        const last = prev[prev.length - 1];
                        if (last && last.message === data.message && last.type === data.type) {
                            return prev;
                        }
                        return [...prev, data];
                    });

                    if (data.type === 'error') {
                        setJobStatus('failed');
                    } else if (data.type !== 'stage_complete') {
                        setJobStatus('running');
                    }
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
        } catch { /* */ }
        // On failure, fetch partial artifacts from disk
        if (status !== 'done' && designName) {
            try {
                const artRes = await api.get(`/build/artifacts/${designName}`);
                setPartialArtifacts(artRes.data.artifacts || []);
            } catch { /* */ }
        }
        setPhase('done');
    };

    const handleApprove = async () => {
        if (!approvalData || isSubmitting) return;
        setIsSubmitting(true);
        try {
            await api.post(`/approve`, {
                stage: approvalData.stage_name,
                design_name: designName,
            });
            // Add user action to feed
            setEvents(prev => [...prev, {
                type: 'user_action',
                state: approvalData.stage_name,
                message: `👤 Approved: ${approvalData.stage_name}`,
                step: 0,
                total_steps: 15,
                timestamp: new Date().toISOString(),
                agent_name: 'User',
                thought_type: 'user_action',
                content: `Approved: ${approvalData.stage_name}`,
            }]);
            setApprovalData(null);
            setWaitingForApproval(false);
            setCompletedStages(prev => {
                const next = new Set(prev);
                next.add(approvalData.stage_name);
                return next;
            });
            // Fire milestone toast if this is a key stage
            const toast = MILESTONE_TOASTS[approvalData.stage_name];
            if (toast) {
                if (milestoneTimerRef.current) clearTimeout(milestoneTimerRef.current);
                setMilestoneToast(toast);
                milestoneTimerRef.current = setTimeout(() => setMilestoneToast(null), 5000);
            }
        } catch (e: any) {
            setError(e?.response?.data?.detail || 'Failed to approve');
        }
        setIsSubmitting(false);
    };

    const handleReject = async (feedback: string) => {
        if (!approvalData || isSubmitting) return;
        setIsSubmitting(true);
        try {
            await api.post(`/reject`, {
                stage: approvalData.stage_name,
                design_name: designName,
                feedback: feedback || undefined,
            });
            const feedbackMsg = feedback ? ` — Feedback: ${feedback}` : '';
            setEvents(prev => [...prev, {
                type: 'user_action',
                state: approvalData.stage_name,
                message: `👤 Rejected: ${approvalData.stage_name}${feedbackMsg}`,
                step: 0,
                total_steps: 15,
                timestamp: new Date().toISOString(),
                agent_name: 'User',
                thought_type: 'user_action',
                content: `Rejected: ${approvalData.stage_name}${feedbackMsg}`,
            }, {
                type: 'agent_thought',
                state: approvalData.stage_name,
                message: 'Retrying stage with your feedback...',
                step: 0,
                total_steps: 15,
                timestamp: new Date().toISOString(),
                agent_name: 'Orchestrator',
                thought_type: 'observation',
                content: 'Retrying stage with your feedback...',
            }]);
            setApprovalData(null);
            setWaitingForApproval(false);
        } catch (e: any) {
            setError(e?.response?.data?.detail || 'Failed to reject');
        }
        setIsSubmitting(false);
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
        setCurrentStage('INIT');
        setCompletedStages(new Set());
        setFailedStage(undefined);
        setWaitingForApproval(false);
        setApprovalData(null);
        setThinkingData(null);
        setStallWarning(null);
        setBuildMode('verified');
        setSkipStages(new Set(BUILD_MODE_SKIPS.verified));
        setSkipCoverage(false);
        setShowStageToggles(false);
        setPartialArtifacts([]);
        setShowFullLog(false);
    };

    const handleCancel = async () => {
        if (abortCtrlRef.current) abortCtrlRef.current.abort();
        if (jobId) {
            try { await api.post(`/build/cancel/${jobId}`); } catch { /* */ }
        }
        handleReset();
    };

    // Computed values for bottom bar
    const stepNum = Math.max(1, PIPELINE_STAGES.indexOf(currentStage) + 1);
    const pct = Math.round((completedStages.size / TOTAL) * 100);
    const remaining = Math.max(0, TOTAL - completedStages.size);
    const estMinutes = remaining * 2;

    // Request notification permission
    useEffect(() => {
        if ('Notification' in window && Notification.permission === 'default') {
            Notification.requestPermission();
        }
        return () => abortCtrlRef.current?.abort();
    }, []);

    return (
        <div className="hitl-root">
            {/* ── PROMPT PHASE ── */}
            {phase === 'prompt' && (
                <div className="hitl-prompt-screen">
                    <div className="hitl-prompt-hero">
                        <h1 className="hitl-hero-title">Design Your Chip</h1>
                        <p className="hitl-hero-sub">
                            Human-in-the-Loop — review and approve every stage of the autonomous pipeline.
                        </p>
                    </div>

                    <div className="hitl-prompt-card">
                        <div className="hitl-examples">
                            {[
                                '8-bit RISC CPU with Harvard architecture',
                                'AXI4 DMA engine with 4 channels',
                                'UART controller at 115200 baud',
                            ].map(ex => (
                                <button key={ex} className="hitl-example-chip" onClick={() => setPrompt(ex)}>
                                    {ex}
                                </button>
                            ))}
                        </div>

                        <textarea
                            className="hitl-prompt-textarea"
                            placeholder="Describe the chip you want to build in plain English…"
                            value={prompt}
                            onChange={e => setPrompt(e.target.value)}
                            rows={4}
                            autoFocus
                        />

                        {designName && (
                            <div className="hitl-design-name-row">
                                <span className="hitl-design-label">Design ID:</span>
                                <input
                                    className="hitl-design-input"
                                    value={designName}
                                    onChange={e => setDesignName(e.target.value.replace(/[^a-z0-9_]/g, ''))}
                                />
                            </div>
                        )}

                        {/* Build mode selector */}
                        <div className="hitl-mode-row">
                            <span className="hitl-mode-label">Build Mode</span>
                            <div className="hitl-mode-pills">
                                {(['quick', 'verified', 'full'] as BuildMode[]).map(mode => (
                                    <button
                                        key={mode}
                                        className={`hitl-mode-pill${buildMode === mode ? ' hitl-mode-pill--active' : ''}`}
                                        onClick={() => {
                                            setBuildMode(mode);
                                            setSkipStages(new Set(BUILD_MODE_SKIPS[mode]));
                                            if (mode === 'quick') setSkipOpenlane(true);
                                        }}
                                    >
                                        <span className="hitl-mode-pill-name">
                                            {mode === 'quick' ? 'Quick RTL' : mode === 'verified' ? 'Verified Design' : 'Fabrication Ready'}
                                        </span>
                                        <span className="hitl-mode-pill-desc">
                                            {mode === 'quick' ? 'RTL + basic verify' : mode === 'verified' ? 'Full verify pipeline' : 'All stages incl. physical'}
                                        </span>
                                    </button>
                                ))}
                            </div>
                        </div>

                        {/* Customize stages toggle */}
                        <button
                            className="hitl-stage-toggle-btn"
                            onClick={() => setShowStageToggles(!showStageToggles)}
                        >
                            {showStageToggles ? '▼ Hide stage details' : '▶ Customize stages'}
                        </button>

                        {showStageToggles && (
                            <div className="hitl-stage-toggles">
                                {PIPELINE_STAGES.map(stage => {
                                    const mandatory = MANDATORY_STAGES.has(stage);
                                    const skipped = skipStages.has(stage);
                                    return (
                                        <button
                                            key={stage}
                                            className={`hitl-stage-chip${skipped ? ' hitl-stage-chip--off' : ' hitl-stage-chip--on'}${mandatory ? ' hitl-stage-chip--locked' : ''}`}
                                            disabled={mandatory}
                                            onClick={() => {
                                                if (mandatory) return;
                                                setSkipStages(prev => {
                                                    const next = new Set(prev);
                                                    next.has(stage) ? next.delete(stage) : next.add(stage);
                                                    return next;
                                                });
                                            }}
                                        >
                                            {mandatory && <span className="hitl-stage-lock">&#x1f512;</span>}
                                            {STAGE_LABELS[stage] || stage}
                                        </button>
                                    );
                                })}
                            </div>
                        )}

                        <div className="hitl-options-row">
                            <label className="hitl-toggle">
                                <input type="checkbox" checked={skipOpenlane} onChange={e => setSkipOpenlane(e.target.checked)} />
                                <span>Skip OpenLane (RTL + Verify only)</span>
                            </label>
                            <label className="hitl-toggle">
                                <input type="checkbox" checked={skipCoverage} onChange={e => setSkipCoverage(e.target.checked)} />
                                <span>Skip Coverage</span>
                            </label>
                            <button
                                className="hitl-advanced-toggle"
                                onClick={() => setShowAdvanced(!showAdvanced)}
                            >
                                {showAdvanced ? '▼ Hide Options' : '▶ Advanced Options'}
                            </button>
                        </div>

                        {showAdvanced && (
                            <div className="hitl-advanced-panel">
                                <div className="hitl-opt-grid">
                                    <label className="hitl-opt">
                                        <span>Max Retries</span>
                                        <input type="number" value={maxRetries} onChange={e => setMaxRetries(Number(e.target.value))} />
                                    </label>
                                    <label className="hitl-opt">
                                        <span>Min Coverage %</span>
                                        <input type="number" step="0.1" value={minCoverage} onChange={e => setMinCoverage(Number(e.target.value))} />
                                    </label>
                                    <label className="hitl-opt">
                                        <span>PDK</span>
                                        <select value={pdkProfile} onChange={e => setPdkProfile(e.target.value)}>
                                            <option value="sky130">sky130</option>
                                            <option value="gf180">gf180</option>
                                        </select>
                                    </label>
                                </div>
                                <div className="hitl-opt-checks">
                                    <label className="hitl-toggle">
                                        <input type="checkbox" checked={strictGates} onChange={e => setStrictGates(e.target.checked)} />
                                        <span>Strict Gates</span>
                                    </label>
                                    <label className="hitl-toggle">
                                        <input type="checkbox" checked={showThinking} onChange={e => setShowThinking(e.target.checked)} />
                                        <span>Show Thinking</span>
                                    </label>
                                </div>
                            </div>
                        )}

                        {error && <div className="hitl-error">{error}</div>}

                        <button
                            className="hitl-launch-btn"
                            onClick={handleLaunch}
                            disabled={!prompt.trim()}
                        >
                            Launch Build with Approval Gates
                        </button>
                    </div>
                </div>
            )}

            {/* ── BUILDING PHASE ── */}
            {phase === 'building' && (
                <div className="hitl-build-layout">
                    {/* Top bar */}
                    <header className="hitl-topbar">
                        <div className="hitl-topbar-left">
                            <span className="hitl-topbar-dot" />
                            <span className="hitl-topbar-name">{designName}</span>
                        </div>
                        <div className="hitl-topbar-right">
                            <span className="hitl-topbar-step">Step {stepNum} of {TOTAL}</span>
                            <button className="hitl-topbar-cancel" onClick={handleCancel}>
                                Cancel
                            </button>
                        </div>
                    </header>

                    {/* Milestone celebration toast */}
                    {milestoneToast && (
                        <div className="hitl-milestone-toast" onClick={() => setMilestoneToast(null)}>
                            <span className="hitl-milestone-toast-icon">✦</span>
                            <div className="hitl-milestone-toast-body">
                                <span className="hitl-milestone-toast-title">{milestoneToast.title}</span>
                                <span className="hitl-milestone-toast-msg">{milestoneToast.msg}</span>
                            </div>
                            <button className="hitl-milestone-toast-close">×</button>
                        </div>
                    )}

                    {/* Body: sidebar + main */}
                    <div className="hitl-build-body">
                        <StageProgressBar
                            currentStage={currentStage}
                            completedStages={completedStages}
                            failedStage={failedStage}
                            waitingForApproval={waitingForApproval}
                            skippedStages={skipStages}
                        />
                        <div className="hitl-main">
                            {stallWarning && (
                                <div className="hitl-stall-banner">
                                    <div className="hitl-stall-body">
                                        <span className="hitl-stall-icon">⚠️</span>
                                        <span className="hitl-stall-msg">{stallWarning}</span>
                                    </div>
                                    <div className="hitl-stall-actions">
                                        <button className="hitl-stall-cancel-btn" onClick={handleCancel}>Cancel Build</button>
                                        <button className="hitl-stall-dismiss-btn" onClick={() => setStallWarning(null)}>Dismiss</button>
                                    </div>
                                </div>
                            )}
                            <ActivityFeed events={events} thinkingData={thinkingData} />
                            {approvalData && (
                                <ApprovalCard
                                    data={approvalData}
                                    designName={designName}
                                    jobId={jobId}
                                    onApprove={handleApprove}
                                    onReject={handleReject}
                                    isSubmitting={isSubmitting}
                                />
                            )}
                        </div>
                    </div>

                    {/* Bottom status bar */}
                    <footer className="hitl-bottombar">
                        <span className="hitl-bottombar-msg">
                            {thinkingData && <span className="hitl-thinking-pulse" />}
                            {waitingForApproval
                                ? 'Your review is needed — inspect the stage output above'
                                : thinkingData
                                    ? thinkingData.message
                                    : (STAGE_ENCOURAGEMENTS[currentStage] || 'Building autonomously…')}
                            {' · '}{pct}% complete
                            {estMinutes > 0 ? ` · ~${estMinutes} min left` : ''}
                        </span>
                        <div className="hitl-bottombar-progress">
                            <div className="hitl-bottombar-track">
                                <div className="hitl-bottombar-fill" style={{ width: `${pct}%` }} />
                            </div>
                            <span className="hitl-bottombar-pct">{pct}%</span>
                        </div>
                    </footer>
                </div>
            )}

            {/* ── DONE PHASE ── */}
            {phase === 'done' && (
                <div className="hitl-done-screen">
                    <div className={`hitl-done-card ${jobStatus === 'done' ? 'hitl-done-success' : 'hitl-done-fail'}`}>
                        {/* ---------- SUCCESS ---------- */}
                        {jobStatus === 'done' && (
                            <>
                                <h2>Chip Build Complete</h2>
                                <p className="hitl-done-design">{designName}</p>
                                {result && (
                                    <div className="hitl-done-details">
                                        {result.strategy && (
                                            <div className="hitl-done-detail">Strategy: {result.strategy}</div>
                                        )}
                                        {result.coverage && typeof result.coverage === 'object' && (
                                            <div className="hitl-done-detail">
                                                Coverage: {result.coverage.line_pct || 'N/A'}% line
                                            </div>
                                        )}
                                        {result.metrics && (
                                            <div className="hitl-done-detail">
                                                Gates: {result.metrics.gate_count || 'N/A'} · Area: {result.metrics.area || 'N/A'}
                                            </div>
                                        )}
                                    </div>
                                )}
                                <button className="hitl-reset-btn" onClick={handleReset}>
                                    ← Build Another Chip
                                </button>
                                {jobId && (
                                    <div className="hitl-report-downloads">
                                        <span className="hitl-report-label">Download Report:</span>
                                        <a
                                            className="hitl-report-btn"
                                            href={`${API_BASE}/report/${jobId}/full.pdf`}
                                            download
                                        >
                                            ↓ PDF
                                        </a>
                                        <a
                                            className="hitl-report-btn"
                                            href={`${API_BASE}/report/${jobId}/full.docx`}
                                            download
                                        >
                                            ↓ DOCX
                                        </a>
                                    </div>
                                )}
                            </>
                        )}

                        {/* ---------- FAILURE (Improvement 3 redesign) ---------- */}
                        {jobStatus !== 'done' && (
                            <div className="hitl-fail-redesign">

                                {/* Heading */}
                                <div className="hitl-fail-heading">
                                    <span className="hitl-fail-heading-dot" />
                                    <h2 className="hitl-fail-heading-text">
                                        Build stopped at {STAGE_LABELS[currentStage] || currentStage.replace(/_/g, ' ')}
                                    </h2>
                                </div>

                                <p className="hitl-fail-design-name">{designName}</p>

                                {/* LLM failure explanation */}
                                {result?.failure_explanation && (
                                    <div className="hitl-fail-explanation">
                                        <p>{result.failure_explanation}</p>
                                    </div>
                                )}

                                {/* Stage chips */}
                                <div className="hitl-fail-stages">
                                    <span className="hitl-fail-section-label">Pipeline progress</span>
                                    <div className="hitl-fail-stage-chips">
                                        {PIPELINE_STAGES.map(stage => {
                                            const isCompleted = completedStages.has(stage);
                                            const isFailed = stage === currentStage && jobStatus !== 'done';
                                            const isSkipped = skipStages.has(stage);
                                            let chipClass = 'hitl-fail-chip--pending';
                                            let icon = '';
                                            if (isCompleted) { chipClass = 'hitl-fail-chip--done'; icon = '✓'; }
                                            else if (isFailed) { chipClass = 'hitl-fail-chip--stopped'; icon = '●'; }
                                            else if (isSkipped) { chipClass = 'hitl-fail-chip--skipped'; icon = '—'; }
                                            return (
                                                <span key={stage} className={`hitl-fail-chip ${chipClass}`}>
                                                    {icon && <span className="hitl-fail-chip-icon">{icon}</span>}
                                                    {STAGE_LABELS[stage] || stage.replace(/_/g, ' ')}
                                                </span>
                                            );
                                        })}
                                    </div>
                                </div>

                                {/* Error message (brief) */}
                                {result?.error && (
                                    <div className="hitl-fail-error-brief">
                                        <span className="hitl-fail-error-label">Error</span>
                                        <p className="hitl-fail-error-msg">{String(result.error).slice(0, 300)}</p>
                                    </div>
                                )}

                                {/* Grouped partial artifacts */}
                                {partialArtifacts.length > 0 && (() => {
                                    const groups: Record<string, typeof partialArtifacts> = {};
                                    partialArtifacts.forEach(a => {
                                        const key = a.type || 'other';
                                        (groups[key] = groups[key] || []).push(a);
                                    });
                                    return (
                                        <div className="hitl-fail-artifacts-grouped">
                                            <span className="hitl-fail-section-label">
                                                Recovered artifacts ({partialArtifacts.length} files)
                                            </span>
                                            {Object.entries(groups).map(([type, files]) => (
                                                <div key={type} className="hitl-fail-artifact-group">
                                                    <span className="hitl-fail-artifact-group-label">{type}</span>
                                                    {files.map((a, i) => (
                                                        <div key={i} className="hitl-fail-artifact-row">
                                                            <span className="hitl-fail-artifact-name">{a.name}</span>
                                                            <span className="hitl-fail-artifact-size">
                                                                {a.size > 1024 ? `${(a.size / 1024).toFixed(1)} KB` : `${a.size} B`}
                                                            </span>
                                                            <a
                                                                href={`${API_BASE}/build/artifacts/${designName}/${encodeURIComponent(a.name)}`}
                                                                className="hitl-fail-artifact-dl"
                                                                download
                                                            >
                                                                ↓
                                                            </a>
                                                        </div>
                                                    ))}
                                                </div>
                                            ))}
                                        </div>
                                    );
                                })()}

                                {/* Build stats (compact) */}
                                {result && (
                                    <div className="hitl-fail-stats">
                                        {result.build_time_s != null && (
                                            <span className="hitl-fail-stat">
                                                Duration: {Math.round(result.build_time_s / 60)} min
                                            </span>
                                        )}
                                        {result.total_steps != null && (
                                            <span className="hitl-fail-stat">Steps: {result.total_steps}</span>
                                        )}
                                        {result.strategy && (
                                            <span className="hitl-fail-stat">Strategy: {result.strategy}</span>
                                        )}
                                    </div>
                                )}

                                {/* What to try next callout */}
                                {result?.failure_suggestion && (
                                    <div className="hitl-fail-suggestion">
                                        <span className="hitl-fail-suggestion-label">What to try next</span>
                                        <p>{result.failure_suggestion}</p>
                                    </div>
                                )}

                                {/* Dual action buttons */}
                                <div className="hitl-fail-actions">
                                    <button className="hitl-fail-btn-primary" onClick={() => {
                                        setPhase('prompt');
                                        setEvents([]);
                                        setResult(null);
                                        setJobId('');
                                        setJobStatus('queued');
                                        setError('');
                                        setCurrentStage('INIT');
                                        setCompletedStages(new Set());
                                        setFailedStage(undefined);
                                        setWaitingForApproval(false);
                                        setApprovalData(null);
                                        setThinkingData(null);
                                        setPartialArtifacts([]);
                                        setShowFullLog(false);
                                        // Keep prompt + design name + build mode for retry
                                    }}>
                                        Try Again
                                    </button>
                                    <button className="hitl-fail-btn-ghost" onClick={handleReset}>
                                        Start New Design
                                    </button>
                                </div>
                            </div>
                        )}
                    </div>

                    {/* Collapsible full log */}
                    <div className="hitl-done-log-section">
                        <button
                            className="hitl-done-log-toggle"
                            onClick={() => setShowFullLog(!showFullLog)}
                        >
                            {showFullLog ? '▼ Hide Full Log' : '▶ Show Full Log'} ({events.length} events)
                        </button>
                        {showFullLog && (
                            <div className="hitl-done-log">
                                <ActivityFeed events={events} />
                            </div>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
};
