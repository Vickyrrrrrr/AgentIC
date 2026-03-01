import React, { useState } from 'react';

interface FabricationProps {
    selectedDesign: string;
    hasGds?: boolean;
}

export const Fabrication: React.FC<FabricationProps> = ({ selectedDesign, hasGds }) => {
    const [viewMode, setViewMode] = useState('2D');

    const handleDownloadGDS = () => {
        if (!hasGds) return alert('No GDSII available for this design yet.');
        // TODO: Create FastAPI endpoint to trigger download
        alert(`Initiating download for ${selectedDesign}.gds...`);
    };

    return (
        <div className="page-container">
            <h2 className="app-title">🏗️ Fabrication & GDSII</h2>

            <div className="sci-fi-card" style={{ marginBottom: '20px' }}>
                <h3>Tapeout Ready Files</h3>
                <p className="app-subtitle">Download your final GDSII layout for physical manufacturing.</p>

                <div style={{ display: 'flex', gap: '15px', marginTop: '15px', alignItems: 'center' }}>
                    <input
                        type="text"
                        value={selectedDesign || 'No Design Selected'}
                        readOnly
                        style={{
                            background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text)',
                            padding: '10px', borderRadius: 'var(--radius)', fontFamily: 'Fira Code', width: '300px'
                        }}
                    />
                    <button
                        className="btn-primary"
                        onClick={handleDownloadGDS}
                        style={{ opacity: hasGds ? 1 : 0.5, cursor: hasGds ? 'pointer' : 'not-allowed' }}
                    >
                        {hasGds ? '📥 Download .gds' : '❌ GDS Not Available'}
                    </button>
                </div>
            </div>

            <div className="sci-fi-card">
                <h3>Layout Viewer</h3>
                <div style={{ display: 'flex', gap: '10px', marginBottom: '20px' }}>
                    <button
                        className="btn-primary"
                        style={{ border: '1px solid var(--border)', background: viewMode === '2D' ? 'var(--accent-soft)' : 'var(--bg)', color: viewMode === '2D' ? 'var(--accent)' : 'var(--text-mid)' }}
                        onClick={() => setViewMode('2D')}
                    >
                        2D Top-Down (SVG)
                    </button>
                    <button
                        className="btn-primary"
                        style={{ border: '1px solid var(--border)', background: viewMode === '3D' ? 'var(--accent-soft)' : 'var(--bg)', color: viewMode === '3D' ? 'var(--accent)' : 'var(--text-mid)' }}
                        onClick={() => setViewMode('3D')}
                    >
                        3D Layer Stack
                    </button>
                </div>

                <div style={{
                    width: '100%', height: '400px', backgroundColor: 'var(--bg)',
                    border: '1px dashed var(--border-mid)', borderRadius: 'var(--radius)', display: 'flex', alignItems: 'center', justifyContent: 'center'
                }}>
                    <p style={{ color: '#555', fontFamily: 'Fira Code' }}>
                        [{viewMode} Render Canvas Placeholder - Awaiting FastAPI GDS parser]
                    </p>
                </div>
            </div>
        </div>
    );
};
