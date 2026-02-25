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
            <h2 style={{ fontFamily: 'Orbitron', color: '#00D1FF' }}>🏗️ Fabrication & GDSII</h2>

            <div className="sci-fi-card" style={{ marginBottom: '20px' }}>
                <h3 style={{ color: '#E0E0E0' }}>Tapeout Ready Files</h3>
                <p style={{ color: '#888' }}>Download your final GDSII layout for physical manufacturing.</p>

                <div style={{ display: 'flex', gap: '15px', marginTop: '15px', alignItems: 'center' }}>
                    <input
                        type="text"
                        value={selectedDesign || 'No Design Selected'}
                        readOnly
                        style={{
                            background: '#111', border: '1px solid #333', color: '#fff',
                            padding: '10px', borderRadius: '4px', fontFamily: 'Fira Code', width: '300px'
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
                <h3 style={{ color: '#E0E0E0' }}>Layout Viewer</h3>
                <div style={{ display: 'flex', gap: '10px', marginBottom: '20px' }}>
                    <button
                        className="btn-primary"
                        style={{ borderColor: viewMode === '2D' ? '#00FF88' : '#333', color: viewMode === '2D' ? '#00FF88' : '#888' }}
                        onClick={() => setViewMode('2D')}
                    >
                        2D Top-Down (SVG)
                    </button>
                    <button
                        className="btn-primary"
                        style={{ borderColor: viewMode === '3D' ? '#00D1FF' : '#333', color: viewMode === '3D' ? '#00D1FF' : '#888' }}
                        onClick={() => setViewMode('3D')}
                    >
                        3D Layer Stack
                    </button>
                </div>

                <div style={{
                    width: '100%', height: '400px', backgroundColor: '#050505',
                    border: '1px dashed #333', display: 'flex', alignItems: 'center', justifyContent: 'center'
                }}>
                    <p style={{ color: '#555', fontFamily: 'Fira Code' }}>
                        [{viewMode} Render Canvas Placeholder - Awaiting FastAPI GDS parser]
                    </p>
                </div>
            </div>
        </div>
    );
};
