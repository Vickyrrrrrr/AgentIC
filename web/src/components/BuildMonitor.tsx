import React, { useEffect, useRef } from 'react';
import { motion } from 'framer-motion';
import { api } from '../api';

const STATES_DISPLAY: Record<string, { label: string; icon: string }> = {
    INIT: { label: 'Initializing Workspace', icon: '01' },
    SPEC: { label: 'Architectural Planning', icon: '02' },
    SPEC_VALIDATE: { label: 'Specification Validation', icon: '03' },
    HIERARCHY_EXPAND: { label: 'Hierarchy Expansion', icon: '04' },
    FEASIBILITY_CHECK: { label: 'Feasibility Check', icon: '05' },
    CDC_ANALYZE: { label: 'CDC Analysis', icon: '06' },
    VERIFICATION_PLAN: { label: 'Verification Planning', icon: '07' },
    RTL_GEN: { label: 'RTL Generation', icon: '08' },
    RTL_FIX: { label: 'RTL Syntax Fixing', icon: '09' },
    VERIFICATION: { label: 'Verification & Testbench', icon: '10' },
    FORMAL_VERIFY: { label: 'Formal Verification', icon: '11' },
    COVERAGE_CHECK: { label: 'Coverage Analysis', icon: '12' },
    REGRESSION: { label: 'Regression Testing', icon: '13' },
    SDC_GEN: { label: 'SDC Generation', icon: '14' },
    FLOORPLAN: { label: 'Floorplanning', icon: '15' },
    HARDENING: { label: 'GDSII Hardening', icon: '16' },
    CONVERGENCE_REVIEW: { label: 'Convergence Review', icon: '17' },
    ECO_PATCH: { label: 'ECO Patch', icon: '18' },
    SIGNOFF: { label: 'DRC/LVS Signoff', icon: '19' },
    SUCCESS: { label: 'Build Complete', icon: '20' },
};

interface BuildEvent {
    type: string;
    state: string;
    message: string;
    step: number;
    total_steps: number;
    timestamp: number;
}

interface StageSchemaItem {
    state: string;
    label: string;
    icon: string;
}

interface Props {
    designName: string;
    jobId: string;
    events: BuildEvent[];
    jobStatus: string;
    stageSchema?: StageSchemaItem[];
}

export const BuildMonitor: React.FC<Props> = ({ designName, jobId, events, jobStatus, stageSchema }) => {
    const logsRef = useRef<HTMLDivElement>(null);
    const [cancelling, setCancelling] = React.useState(false);
    const [localCancelled, setLocalCancelled] = React.useState(false);

    const mergedDisplay: Record<string, { label: string; icon: string }> = React.useMemo(() => {
        if (!stageSchema || stageSchema.length === 0) return STATES_DISPLAY;
        const map: Record<string, { label: string; icon: string }> = {};
        for (const stage of stageSchema) {
            map[stage.state] = { label: stage.label, icon: stage.icon };
        }
        if (!map.SUCCESS) map.SUCCESS = STATES_DISPLAY.SUCCESS;
        return map;
    }, [stageSchema]);

    const stateOrder = React.useMemo(() => Object.keys(mergedDisplay), [mergedDisplay]);

    // unused variables removed for cleanup
    const currentState = events.length > 0 ? events[events.length - 1].state : 'INIT';
    const currentStateIndex = stateOrder.indexOf(currentState);
    const furthestReachedIndex = Math.max(
        0,
        ...events
            .map(e => stateOrder.indexOf(e.state))
            .filter(idx => idx >= 0)
    );
    const currentStep = Math.max(1, (currentStateIndex >= 0 ? currentStateIndex : furthestReachedIndex) + 1);
    const logEvents = events.filter(e => e.message && e.message.trim().length > 0);
    const effectiveJobStatus = localCancelled ? 'cancelled' : jobStatus;
    const isDone = ['done', 'failed', 'cancelled', 'cancelling'].includes(effectiveJobStatus);

    useEffect(() => {
        if (logsRef.current) {
            logsRef.current.scrollTop = logsRef.current.scrollHeight;
        }
    }, [events]);

    const activeActor = React.useMemo(() => {
        const KNOWN_TOOLS = ['OPENROAD', 'MAGIC', 'YOSYS', 'IVERILOG', 'VERILATOR', 'SBY', 'SYMBIOASIS', 'KLAYOUT', 'NETGEN'];
        const LOG_LEVELS = ['WARNING', 'CRITICAL', 'ERROR', 'INFO', 'DEBUG'];
        for (let i = logEvents.length - 1; i >= 0; i--) {
            const msg = (logEvents[i].message || '').trim();
            if (msg.startsWith('agentic:')) return 'agentic';
            if (msg.startsWith('[')) {
                const match = msg.match(/^\[(.*?)\]/);
                if (match) {
                    const prefixUpper = match[1].toUpperCase();
                    if (KNOWN_TOOLS.includes(prefixUpper)) return match[1]; // e.g. 'OPENROAD'
                    if (!LOG_LEVELS.includes(prefixUpper)) return 'agentic'; // Rewrite 'Architect' to 'agentic'
                }
            }
        }
        return 'System';
    }, [logEvents]);

    const handleCancel = async () => {
        if (!jobId || cancelling) return;
        setCancelling(true);
        try {
            await api.post(`/build/cancel/${jobId}`);
            setLocalCancelled(true);
            setCancelling(false);
        } catch {
            setCancelling(false);
        }
    };

    const processedLogs = React.useMemo(() => {
        const clean: string[] = [];
        const KNOWN_TOOLS = ['OPENROAD', 'MAGIC', 'YOSYS', 'IVERILOG', 'VERILATOR', 'SBY', 'SYMBIOASIS', 'KLAYOUT', 'NETGEN'];
        const LOG_LEVELS = ['WARNING', 'CRITICAL', 'ERROR', 'INFO', 'DEBUG'];
        
        for (let i = 0; i < logEvents.length; i++) {
            let msg = (logEvents[i].message || '').trim();
            if (!msg) continue;
            
            const bracketMatch = msg.match(/^\[(.*?)\]\s*(.*)/);
            if (bracketMatch) {
                const prefixUpper = bracketMatch[1].toUpperCase();
                if (LOG_LEVELS.includes(prefixUpper)) {
                    msg = `[${prefixUpper}] ${bracketMatch[2]}`;
                } else if (!KNOWN_TOOLS.includes(prefixUpper)) {
                    msg = `agentic: ${bracketMatch[2]}`;
                }
            }

            msg = msg.replace(/⚠️\s*/g, '[WARNING] ');
            msg = msg.replace(/^agentic:\s*\[WARNING\]/i, '[WARNING]');
            msg = msg.replace(/^agentic:\s*\[CRITICAL\]/i, '[CRITICAL]');
            msg = msg.replace(/^agentic:\s*\[INFO\]/i, '[INFO]');

            const cleanMsg = msg.replace(/^agentic:\s*/, '').trim();
            const prev = clean.length > 0 ? clean[clean.length - 1] : null;
            if (prev) {
                const cleanPrev = prev.replace(/^agentic:\s*/, '').trim();
                if (cleanMsg === cleanPrev) {
                    if (msg.startsWith('agentic:') && !msg.includes('[WARNING]') && !msg.includes('[CRITICAL]') && !msg.includes('[INFO]')) {
                        clean[clean.length - 1] = msg;
                    }
                    continue; 
                }
            }

            if (/^'[a-z_]+',\s+'[a-z_]+',/.test(msg)) continue;
            if (msg.startsWith('Transitioning: ')) msg = `[INFO] ${msg}`;
            if (msg.startsWith('Running HierarchyExpander') || msg.startsWith('Spec validation complete')) msg = `[INFO] ${msg}`;

            clean.push(msg);
        }
        return clean;
    }, [logEvents]);

    return (
        <div className="monitor-root">
            {/* Header */}
            <div className="monitor-header">
                <div className="monitor-chip-badge">
                    <span className="badge-dot" data-status={effectiveJobStatus} />
                    <span className="badge-name">{designName}</span>
                </div>
                <div className="monitor-status">
                    {!isDone ? (
                        <>
                            <span className="spinner" />
                            <span>Step {currentStep} / {stateOrder.length}</span>
                            <button
                                className="cancel-btn"
                                onClick={handleCancel}
                                disabled={cancelling}
                            >
                                {cancelling ? 'Stopping…' : '✕ Cancel'}
                            </button>
                        </>
                    ) : (
                        <span style={{ color: effectiveJobStatus === 'done' ? 'var(--success)' : 'var(--fail)', fontWeight: 600 }}>
                            {effectiveJobStatus === 'done' ? '✓ Complete' : effectiveJobStatus === 'cancelled' ? '⊘ Cancelled' : '✕ Failed'}
                        </span>
                    )}
                </div>
            </div>

            {/* Body */}
            <div className="monitor-body">
                {/* Checkpoint Timeline removed to maximize terminal width */}

                {/* Live Terminal */}
                <div className="terminal-column">
                    <div className="section-heading" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <span>Live Log</span>
                        {!isDone && logEvents.length > 0 && (
                            <div className="active-actor-pill">
                                <span className={activeActor === 'agentic' ? 'actor-indicator-agent' : 'actor-indicator-tool'} />
                                Active: {activeActor}
                            </div>
                        )}
                    </div>
                    <div className="live-terminal" ref={logsRef}>
                        {logEvents.length === 0 ? (
                            <span className="terminal-waiting">Waiting for AgentIC to start…</span>
                        ) : (
                            <>
                                {processedLogs.map((msg, i) => {
                                    let msgType = 'normal';
                                    if (msg.startsWith('[WARNING]')) msgType = 'warn';
                                    else if (msg.startsWith('[CRITICAL]') || msg.startsWith('[ERROR]')) msgType = 'crit';
                                    else if (msg.includes('WNS is now MET') || msg.includes('0 violations found') || msg.includes('successfully') || msg.startsWith('[INFO]')) msgType = 'success';
                                    else if (msg.startsWith('agentic:')) {
                                        const isAction = /(Expanding|Patch applied|Pipeline fully repaired|Synthesizing|Fixing|Regenerating|Extracting)/i.test(msg);
                                        msgType = isAction ? 'agent-action' : 'agent-thought';
                                    }
                                    else if (msg.startsWith('[')) msgType = 'tool';

                                    return (
                                        <motion.div
                                            key={i}
                                            className="terminal-line"
                                            initial={{ opacity: 0 }}
                                            animate={{ opacity: 1 }}
                                            transition={{ duration: 0.15 }}
                                        >
                                            <span className={`terminal-msg type-${msgType}`}>
                                                {msg}
                                            </span>
                                        </motion.div>
                                    );
                                })}
                                {!isDone && (
                                    <div className="terminal-prompt">
                                        <span className="prompt-char">$</span>
                                        <span className="cursor-blink">_</span>
                                    </div>
                                )}
                            </>
                        )}
                    </div>

                    {/* Progress */}
                    <div className="progress-bar-wrap">
                        <div
                            className="progress-bar-fill"
                            style={{ width: `${(currentStep / stateOrder.length) * 100}%` }}
                        />
                    </div>
                    <div className="progress-label">
                        {Math.round((currentStep / stateOrder.length) * 100)}% complete
                    </div>
                </div>
            </div>

            {/* Footer */}
            {!isDone && (
                <div className="monitor-footer">
                    <span className="spinner-small" />
                    AgentIC is building your chip autonomously. This takes 10–30 min.
                    You'll get a browser notification when done — you can leave this tab open.
                </div>
            )}
        </div>
    );
};
