import React, { useState } from 'react';

interface StageCompleteData {
    stage_name: string;
    summary: string;
    artifacts: Array<{ name: string; path: string; description: string }>;
    decisions: string[];
    warnings: string[];
    next_stage_name: string;
    next_stage_preview: string;
}

interface Props {
    data: StageCompleteData;
    designName: string;
    onApprove: () => void;
    onReject: (feedback: string) => void;
    isSubmitting: boolean;
}

const STAGE_ICONS: Record<string, string> = {
    INIT: '⚙', SPEC: '◈', RTL_GEN: '⌨', RTL_FIX: '◪',
    VERIFICATION: '◉', FORMAL_VERIFY: '◈', COVERAGE_CHECK: '◎',
    REGRESSION: '↺', SDC_GEN: '⧗', FLOORPLAN: '▣',
    HARDENING: '⬡', CONVERGENCE_REVIEW: '◎', ECO_PATCH: '⟴',
    SIGNOFF: '✓', SUCCESS: '✦', FAIL: '✗',
};

function fmtStage(name: string): string {
    return name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

export const ApprovalCard: React.FC<Props> = ({ data, onApprove, onReject, isSubmitting }) => {
    const [showFeedback, setShowFeedback] = useState(false);
    const [feedback, setFeedback] = useState('');

    const hasWarnings = data.warnings && data.warnings.length > 0;
    const hasErrors = data.decisions?.some((d: string) => /error|fail/i.test(d));
    const artifactCount = data.artifacts?.length || 0;
    const hasNext = data.next_stage_name && data.next_stage_name !== 'DONE';
    const icon = STAGE_ICONS[data.stage_name] || '◆';

    const handleReject = () => {
        onReject(feedback);
        setShowFeedback(false);
        setFeedback('');
    };

    return (
        <div className={`ac-card ${hasErrors ? 'ac-card--error' : hasWarnings ? 'ac-card--warn' : 'ac-card--ok'}`}>

            {/* Header — stage identity */}
            <div className="ac-header">
                <div className="ac-stage-id">
                    <span className="ac-stage-symbol">{icon}</span>
                    <span className="ac-stage-label">{fmtStage(data.stage_name)}</span>
                    {hasErrors && <span className="ac-badge ac-badge--error">Issue detected</span>}
                    {!hasErrors && hasWarnings && <span className="ac-badge ac-badge--warn">Warning</span>}
                </div>
                {artifactCount > 0 && (
                    <span className="ac-artifact-pill">
                        {artifactCount} artifact{artifactCount !== 1 ? 's' : ''}
                    </span>
                )}
            </div>

            {/* Summary — primary content */}
            <p className="ac-summary">
                {data.summary || `${fmtStage(data.stage_name)} completed successfully.`}
            </p>

            {/* Next stage preview */}
            {hasNext && data.next_stage_preview && (
                <div className="ac-next-hint">
                    <span className="ac-next-arrow">↓</span>
                    <span className="ac-next-text">{data.next_stage_preview}</span>
                </div>
            )}

            {/* Action footer */}
            <div className="ac-footer">
                {!showFeedback ? (
                    <>
                        <button className="ac-give-feedback" onClick={() => setShowFeedback(true)}>
                            Give feedback
                        </button>
                        <button className="ac-continue-btn" onClick={onApprove} disabled={isSubmitting}>
                            {isSubmitting ? 'Continuing…' : 'Continue'}
                            {!isSubmitting && <span className="ac-chevron">→</span>}
                        </button>
                    </>
                ) : (
                    <div className="ac-feedback-row">
                        <input
                            className="ac-feedback-input"
                            type="text"
                            placeholder="What should the agent do differently?"
                            value={feedback}
                            onChange={e => setFeedback(e.target.value)}
                            autoFocus
                            onKeyDown={e => {
                                if (e.key === 'Enter') handleReject();
                                if (e.key === 'Escape') { setShowFeedback(false); setFeedback(''); }
                            }}
                        />
                        <button className="ac-reject-btn" onClick={handleReject} disabled={isSubmitting}>
                            {isSubmitting ? 'Sending…' : 'Reject & redirect'}
                        </button>
                        <button className="ac-cancel-btn" onClick={() => { setShowFeedback(false); setFeedback(''); }}>
                            Cancel
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
};
