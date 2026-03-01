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

function fmt(name: string): string {
    return name.replace(/_/g, ' ');
}

export const ApprovalCard: React.FC<Props> = ({ data, onApprove, onReject, isSubmitting }) => {
    const [showFeedback, setShowFeedback] = useState(false);
    const [feedback, setFeedback] = useState('');

    const hasWarnings = data.warnings && data.warnings.length > 0;
    const hasErrors = data.decisions?.some(d => /error|fail/i.test(d));

    const handleReject = () => {
        onReject(feedback);
        setShowFeedback(false);
        setFeedback('');
    };

    return (
        <div className="hitl-approval">
            {/* Main single-line content */}
            <div className="hitl-approval-row">
                <div className="hitl-approval-left">
                    {(hasWarnings || hasErrors) && (
                        <span
                            className={`hitl-approval-dot ${hasErrors ? 'hitl-dot--error' : 'hitl-dot--warn'}`}
                        />
                    )}
                    <span className="hitl-approval-stage">{fmt(data.stage_name)}</span>
                </div>
                <p className="hitl-approval-summary">
                    {data.summary || `${fmt(data.stage_name)} completed successfully.`}
                </p>
                <div className="hitl-approval-actions">
                    {!showFeedback && (
                        <button className="hitl-fb-link" onClick={() => setShowFeedback(true)}>
                            give feedback
                        </button>
                    )}
                    <button
                        className="hitl-continue"
                        onClick={onApprove}
                        disabled={isSubmitting}
                    >
                        {isSubmitting ? 'Continuing…' : 'Continue →'}
                    </button>
                </div>
            </div>

            {/* Inline feedback field */}
            {showFeedback && (
                <div className="hitl-approval-feedback">
                    <input
                        className="hitl-fb-input"
                        type="text"
                        placeholder="What should the agent do differently?"
                        value={feedback}
                        onChange={e => setFeedback(e.target.value)}
                        autoFocus
                        onKeyDown={e => {
                            if (e.key === 'Enter') handleReject();
                            if (e.key === 'Escape') {
                                setShowFeedback(false);
                                setFeedback('');
                            }
                        }}
                    />
                    <button
                        className="hitl-reject-pill"
                        onClick={handleReject}
                        disabled={isSubmitting}
                    >
                        {isSubmitting ? 'Rejecting…' : 'Reject'}
                    </button>
                    <button
                        className="hitl-fb-cancel"
                        onClick={() => {
                            setShowFeedback(false);
                            setFeedback('');
                        }}
                    >
                        ×
                    </button>
                </div>
            )}
        </div>
    );
};
