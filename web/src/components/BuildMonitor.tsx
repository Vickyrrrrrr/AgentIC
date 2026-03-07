import React, { useEffect, useRef } from 'react';
import { motion } from 'framer-motion';
import axios from 'axios';

const API = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:7860').replace(/\/$/, '');

const STATES_DISPLAY: Record<string, { label: string; icon: string }> = {
    INIT: { label: 'Initializing Workspace', icon: '🔧' },
    SPEC: { label: 'Architectural Planning', icon: '📐' },
    RTL_GEN: { label: 'RTL Generation', icon: '💻' },
    RTL_FIX: { label: 'RTL Syntax Fixing', icon: '🔨' },
    VERIFICATION: { label: 'Verification & Testbench', icon: '🧪' },
    FORMAL_VERIFY: { label: 'Formal Verification', icon: '📊' },
    COVERAGE_CHECK: { label: 'Coverage Analysis', icon: '📈' },
    REGRESSION: { label: 'Regression Testing', icon: '🔁' },
    SDC_GEN: { label: 'SDC Generation', icon: '🕒' },
    FLOORPLAN: { label: 'Floorplanning', icon: '🗺️' },
    HARDENING: { label: 'GDSII Hardening', icon: '🏗️' },
    CONVERGENCE_REVIEW: { label: 'Convergence Review', icon: '🎯' },
    ECO_PATCH: { label: 'ECO Patch', icon: '🩹' },
    SIGNOFF: { label: 'DRC/LVS Signoff', icon: '✅' },
    SUCCESS: { label: 'Build Complete', icon: '🎉' },
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

    const reachedStates = new Set(events.map(e => e.state));
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
    const isDone = ['done', 'failed', 'cancelled', 'cancelling'].includes(jobStatus);

    const selfHeal = {
        stageExceptions: events.filter(e => /stage .* exception/i.test(e.message || '')).length,
        formalRegens: events.filter(e => /regenerating sva/i.test(e.message || '')).length,
        coverageRestores: events.filter(e => /restoring best testbench/i.test(e.message || '')).length,
        coverageRejects: events.filter(e => /regressed coverage/i.test(e.message || '')).length,
        deterministicFallbacks: events.filter(e => /deterministic tb fallback/i.test(e.message || '')).length,
    };

    useEffect(() => {
        if (logsRef.current) {
            logsRef.current.scrollTop = logsRef.current.scrollHeight;
        }
    }, [events]);

    const handleCancel = async () => {
        if (!jobId || cancelling) return;
        setCancelling(true);
        try {
            await axios.post(`${API}/build/cancel/${jobId}`);
        } catch {
            setCancelling(false);
        }
    };

    return (
        <div className="monitor-root">
            {/* Header */}
            <div className="monitor-header">
                <div className="monitor-chip-badge">
                    <span className="badge-dot" data-status={jobStatus} />
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
                        <span style={{ color: jobStatus === 'done' ? 'var(--success)' : 'var(--fail)', fontWeight: 600 }}>
                            {jobStatus === 'done' ? '✓ Complete' : jobStatus === 'cancelled' ? '⊘ Cancelled' : '✕ Failed'}
                        </span>
                    )}
                </div>
            </div>

            {/* Body */}
            <div className="monitor-body">
                {/* Checkpoint Timeline */}
                <div className="checkpoint-column">
                    <div className="section-heading">Build Pipeline</div>
                    <div className="checkpoint-list">
                        {stateOrder.map((stateKey, idx) => {
                            const info = mergedDisplay[stateKey] || { label: stateKey, icon: '•' };
                            const isPassed = stateOrder.indexOf(stateKey) < stateOrder.indexOf(currentState);
                            const isCurrent = currentState === stateKey && !isDone;
                            const isSuccess = stateKey === 'SUCCESS' && jobStatus === 'done';

                            return (
                                <motion.div
                                    key={stateKey}
                                    className={`checkpoint-item ${isPassed || reachedStates.has(stateKey) ? 'reached' : ''} ${isCurrent ? 'active' : ''}`}
                                    initial={{ opacity: 0, x: -10 }}
                                    animate={{ opacity: 1, x: 0 }}
                                    transition={{ delay: idx * 0.04 }}
                                >
                                    <div className="checkpoint-icon-wrap">
                                        {isPassed || isSuccess ? (
                                            <span className="check-done">✓</span>
                                        ) : isCurrent ? (
                                            <span className="check-pulse">⟳</span>
                                        ) : (
                                            <span className="check-todo" />
                                        )}
                                        {idx < stateOrder.length - 1 && (
                                            <div className={`checkpoint-line ${isPassed ? 'line-done' : ''}`} />
                                        )}
                                    </div>
                                    <div className="checkpoint-label">
                                        <span className="checkpoint-icon-emoji">{info.icon}</span>
                                        <span className={`checkpoint-text ${isCurrent ? 'text-active' : ''}`}>
                                            {info.label}
                                        </span>
                                    </div>
                                </motion.div>
                            );
                        })}
                    </div>
                </div>

                {/* Live Terminal */}
                <div className="terminal-column">
                    <div className="section-heading">Live Log</div>
                    <div style={{
                        border: '1px solid var(--border)',
                        borderRadius: 'var(--radius)',
                        background: 'var(--bg-card)',
                        padding: '0.65rem 0.75rem',
                        marginBottom: '0.65rem',
                        display: 'flex',
                        flexWrap: 'wrap',
                        gap: '0.5rem',
                        color: 'var(--text-mid)',
                        fontSize: '0.78rem',
                    }}>
                        <span style={{ color: 'var(--text)', fontWeight: 600 }}>Self-Healing</span>
                        <span>Stage guards: {selfHeal.stageExceptions}</span>
                        <span>Formal regens: {selfHeal.formalRegens}</span>
                        <span>TB regressions blocked: {selfHeal.coverageRejects}</span>
                        <span>Best TB restores: {selfHeal.coverageRestores}</span>
                        <span>TB fallbacks: {selfHeal.deterministicFallbacks}</span>
                    </div>
                    <div className="live-terminal" ref={logsRef}>
                        {logEvents.length === 0 ? (
                            <span className="terminal-waiting">Waiting for AgentIC to start…</span>
                        ) : (
                            logEvents.map((evt, i) => (
                                <motion.div
                                    key={i}
                                    className={`terminal-line ${evt.type === 'checkpoint' || evt.type === 'transition' ? 'terminal-highlight' : ''}`}
                                    initial={{ opacity: 0 }}
                                    animate={{ opacity: 1 }}
                                    transition={{ duration: 0.15 }}
                                >
                                    <span className="terminal-ts">
                                        {new Date(evt.timestamp * 1000).toLocaleTimeString()}
                                    </span>
                                    <span className="terminal-state">[{evt.state}]</span>
                                    <span className="terminal-msg">{evt.message}</span>
                                </motion.div>
                            ))
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
