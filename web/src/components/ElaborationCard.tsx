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
        <div className="approval-card" style={{ maxWidth: '450px', margin: '0 auto', boxShadow: '0 8px 16px rgba(0,0,0,0.5)' }}>
            <div className="card-header" style={{ padding: '0.6rem 1rem' }}>
                <span className="card-icon">⚙️</span>
                <span className="card-title" style={{ fontSize: '0.9rem' }}>Pick Architecture</span>
            </div>

            <div className="card-body" style={{ padding: '0.8rem' }}>
                <div className="approval-summary" style={{ fontSize: '0.85rem', marginBottom: '0.5rem', border: 'none', background: 'none' }}>
                    <p style={{ margin: 0 }}>{message}</p>
                </div>

                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
                    {options.map((opt, i) => {
                        const optionNum = i + 1;
                        const titleKey = `OPTION_` + optionNum; // Actually let's just find the OPTION_ key
                        const title = opt[titleKey] || opt['OPTION_1'] || opt['OPTION_2'] || opt['OPTION_3'] || `Option ${optionNum}`;
                        const category = opt['Category'] || "Category";
                        const freq = opt['Freq'] || "Freq";
                        const details = opt['Details'] || "Details...";

                        return (
                            <div 
                                key={i} 
                                className="elaboration-option" 
                                style={{ 
                                    padding: '0.5rem 0.8rem', 
                                    border: '1px solid #444', 
                                    borderRadius: '6px', 
                                    cursor: 'pointer',
                                    display: 'flex',
                                    justifyContent: 'space-between',
                                    alignItems: 'center',
                                    backgroundColor: 'rgba(255,255,255,0.03)'
                                }} 
                                onClick={() => !isSubmitting && onSelect(String(optionNum))}
                                title={details}
                            >
                                <div style={{ display: 'flex', flexDirection: 'column' }}>
                                    <strong style={{ color: '#F5781F', fontSize: '0.9rem' }}>Op {optionNum}: {title.substring(0, 30)}{title.length > 30 ? '...' : ''}</strong>
                                    <span style={{ fontSize: '0.75rem', color: '#888' }}>{category}</span>
                                </div>
                                <div style={{ textAlign: 'right' }}>
                                    <div style={{ fontWeight: 'bold', color: '#fff', fontSize: '0.85rem' }}>{freq}</div>
                                    <button 
                                        className="btn-approve" 
                                        style={{ marginTop: '0.3rem', padding: '0.2rem 0.6rem', fontSize: '0.75rem' }}
                                        disabled={isSubmitting}
                                    >
                                        Select
                                    </button>
                                </div>
                            </div>
                        );
                    })}
                </div>

                <div className="approval-reject" style={{ marginTop: '0.8rem', borderTop: '1px solid #333', paddingTop: '0.6rem' }}>
                    <div style={{ marginBottom: '0.3rem', color: '#888', fontSize: '0.75rem' }}>Or custom:</div>
                    <textarea 
                        className="reject-textarea" 
                        placeholder="e.g. Combine Op1 & 2..."
                        value={customChoice}
                        onChange={e => setCustomChoice(e.target.value)}
                        disabled={isSubmitting}
                        rows={1}
                        style={{ fontSize: '0.8rem', padding: '0.4rem' }}
                    />
                    <button 
                        className="btn-reject" 
                        style={{ marginTop: '0.4rem', width: '100%', backgroundColor: '#444', padding: '0.4rem', fontSize: '0.8rem' }}
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
