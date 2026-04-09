import React, { useState, Suspense } from 'react';
import { Canvas } from '@react-three/fiber';
import { Chip3D } from '../components/Chip3D';
import { OrbitControls } from '@react-three/drei';
import { Download, XCircle } from 'lucide-react';

interface FabricationProps {
    selectedDesign: string;
    hasGds?: boolean;
}

export const Fabrication: React.FC<FabricationProps> = ({ selectedDesign, hasGds }) => {
    const [viewMode, setViewMode] = useState('3D');

    const handleDownloadGDS = () => {
        if (!hasGds) return alert('No GDSII available for this design yet.');
        alert(`Initiating download for ${selectedDesign}.gds...`);
    };

    return (
        <div className="fab-page">
            <div className="fab-header">
                <h2 className="fab-title">Fabrication & GDSII</h2>
            </div>

            <div className="fab-card">
                <h3 className="fab-card-title">Tapeout Ready Files</h3>
                <p className="fab-card-desc">
                    Download your final GDSII layout for physical manufacturing.
                </p>

                <div className="fab-download-row">
                    <input
                        type="text"
                        className="fab-design-input"
                        value={selectedDesign || 'No Design Selected'}
                        readOnly
                    />
                    <button
                        className={`fab-download-btn ${hasGds ? '' : 'fab-download-btn--disabled'}`}
                        onClick={handleDownloadGDS}
                        disabled={!hasGds}
                    >
                        {hasGds ? <><Download size={14} /> Download .gds</> : <><XCircle size={14} /> GDS Not Available</>}
                    </button>
                </div>
            </div>

            <div className="fab-card">
                <div className="fab-viewer-header">
                    <h3 className="fab-card-title">Layout Viewer</h3>
                    <div className="fab-view-toggle">
                        {['2D', '3D'].map(mode => (
                            <button
                                key={mode}
                                className={`fab-view-btn ${viewMode === mode ? 'fab-view-btn--active' : ''}`}
                                onClick={() => setViewMode(mode)}
                            >
                                {mode === '2D' ? '2D Top-Down' : '3D Stack'}
                            </button>
                        ))}
                    </div>
                </div>

                <div className="fab-canvas-wrap">
                    {viewMode === '3D' ? (
                        <Suspense fallback={
                            <div className="fab-canvas-loader">
                                <div className="premium-loader">
                                    <span className="premium-loader-dot" />
                                    <span className="premium-loader-dot" />
                                    <span className="premium-loader-dot" />
                                </div>
                                Loading 3D Viewer...
                            </div>
                        }>
                            <Canvas
                                camera={{ position: [0, 2.5, 6], fov: 50 }}
                                style={{ background: 'transparent' }}
                            >
                                <ambientLight intensity={0.5} />
                                <directionalLight position={[5, 5, 5]} intensity={1} />
                                <pointLight position={[-5, 5, -5]} intensity={0.5} color="#00D1FF" />
                                <Chip3D />
                                <OrbitControls enableZoom enablePan={false} autoRotate autoRotateSpeed={1} />
                            </Canvas>
                        </Suspense>
                    ) : (
                        <div className="fab-canvas-placeholder">
                            2D Render — Awaiting GDS Parser Integration
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
};
