import { useState } from 'react';

interface Props {
    options: Array<Record<string, string>>;
    message: string;
    onSelect: (choice: string) => void;
    isSubmitting: boolean;
}

export default function ElaborationCard({ options, message, onSelect, isSubmitting }: Props) {
    const [customChoice, setCustomChoice] = useState("");

    return (
        <div className="approval-card">
            <div className="card-header">
                <span className="card-icon">📋</span>
                <span className="card-title">Architectural Elaboration Required</span>
                <span className="card-stage-badge pulse">AWAITING DECISION</span>
            </div>

            <div className="card-body">
                <div className="approval-summary">
                    <p>{message}</p>
                </div>

                <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', marginTop: '1rem' }}>
                    {options.map((opt, i) => {
                        const optionNum = i + 1;
                        const titleKey = `OPTION_` + optionNum; // Actually let's just find the OPTION_ key
                        const title = opt[titleKey] || opt['OPTION_1'] || opt['OPTION_2'] || opt['OPTION_3'] || `Option ${optionNum}`;
                        const category = opt['Category'] || "Category";
                        const freq = opt['Freq'] || "Freq";
                        const details = opt['Details'] || "Details...";

                        return (
                            <div key={i} className="elaboration-option" style={{ padding: '1rem', border: '1px solid #333', borderRadius: '4px', cursor: 'pointer' }} onClick={() => !isSubmitting && onSelect(String(optionNum))}>
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
                                    <strong style={{ color: '#F5781F' }}>Option {optionNum}: {title}</strong>
                                    <span style={{ fontSize: '0.8rem', color: '#888' }}>{category} | {freq}</span>
                                </div>
                                <div style={{ fontSize: '0.9rem', color: '#e0e0e0' }}>{details}</div>
                                <button 
                                    className="btn-approve" 
                                    style={{ marginTop: '0.8rem', width: '100%', padding: '0.5rem' }}
                                    disabled={isSubmitting}
                                >
                                    Select Option {optionNum}
                                </button>
                            </div>
                        );
                    })}
                </div>

                <div className="approval-reject" style={{ marginTop: '1.5rem', borderTop: '1px solid #333', paddingTop: '1rem' }}>
                    <div style={{ marginBottom: '0.5rem', color: '#a0a0a0', fontSize: '0.9rem' }}>Or provide your own custom requirements:</div>
                    <textarea 
                        className="reject-textarea" 
                        placeholder="e.g. Combine Option 1 and 2, but use a 128-word FIFO instead..."
                        value={customChoice}
                        onChange={e => setCustomChoice(e.target.value)}
                        disabled={isSubmitting}
                        rows={3}
                    />
                    <button 
                        className="btn-reject" 
                        style={{ marginTop: '0.5rem', width: '100%', backgroundColor: '#444' }}
                        onClick={() => onSelect(customChoice)}
                        disabled={isSubmitting || !customChoice.trim()}
                    >
                        Submit Custom Requirements
                    </button>
                </div>
            </div>
        </div>
    );
}
