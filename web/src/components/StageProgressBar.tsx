import React from 'react';

const STAGES = [
    { key: 'INIT', label: 'Initialization' },
    { key: 'SPEC', label: 'Specification' },
    { key: 'RTL_GEN', label: 'RTL Generation' },
    { key: 'RTL_FIX', label: 'RTL Fix' },
    { key: 'VERIFICATION', label: 'Verification' },
    { key: 'FORMAL_VERIFY', label: 'Formal Verify' },
    { key: 'COVERAGE_CHECK', label: 'Coverage Check' },
    { key: 'REGRESSION', label: 'Regression' },
    { key: 'SDC_GEN', label: 'SDC Generation' },
    { key: 'FLOORPLAN', label: 'Floorplan' },
    { key: 'HARDENING', label: 'Hardening' },
    { key: 'CONVERGENCE_REVIEW', label: 'Convergence' },
    { key: 'ECO_PATCH', label: 'ECO Patch' },
    { key: 'SIGNOFF', label: 'Signoff' },
];

interface Props {
    currentStage: string;
    completedStages: Set<string>;
    failedStage?: string;
    waitingForApproval: boolean;
    skippedStages?: Set<string>;
}

export const StageProgressBar: React.FC<Props> = ({
    currentStage,
    completedStages,
    failedStage,
    waitingForApproval,
    skippedStages,
}) => {
    return (
        <aside className="hitl-sidebar">
            <div className="hitl-sidebar-label">Build Pipeline</div>
            <nav className="hitl-sidebar-stages">
                {STAGES.map((stage) => {
                    const isCompleted = completedStages.has(stage.key);
                    const isCurrent = stage.key === currentStage;
                    const isFailed = stage.key === failedStage;
                    const isWaiting = isCurrent && waitingForApproval;
                    const isSkipped = skippedStages?.has(stage.key) && !isCompleted && !isCurrent && !isFailed;

                    let status = 'pending';
                    if (isSkipped) status = 'skipped';
                    if (isCompleted) status = 'completed';
                    if (isCurrent && !isWaiting) status = 'active';
                    if (isWaiting) status = 'waiting';
                    if (isFailed) status = 'failed';

                    return (
                        <div key={stage.key} className={`hitl-sidebar-stage hitl-stage--${status}`}>
                            <div className="hitl-sidebar-indicator">
                                {isCompleted && (
                                    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                                        <circle cx="7" cy="7" r="6" fill="#4a7c59" />
                                        <path d="M4 7l2 2 4-4" stroke="#fff" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                                    </svg>
                                )}
                                {isFailed && (
                                    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                                        <circle cx="7" cy="7" r="6" fill="#c0392b" />
                                        <path d="M5 5l4 4m0-4l-4 4" stroke="#fff" strokeWidth="1.5" strokeLinecap="round" />
                                    </svg>
                                )}
                                {isSkipped && !isCompleted && !isFailed && (
                                    <span className="hitl-sidebar-dot-skipped">—</span>
                                )}
                                {(isCurrent || isWaiting) && !isCompleted && !isFailed && !isSkipped && (
                                    <span className="hitl-sidebar-dot-active" />
                                )}
                                {!isCompleted && !isCurrent && !isWaiting && !isFailed && !isSkipped && (
                                    <span className="hitl-sidebar-dot-empty" />
                                )}
                            </div>
                            <span className="hitl-sidebar-stage-name">{stage.label}</span>
                        </div>
                    );
                })}
            </nav>
        </aside>
    );
};
