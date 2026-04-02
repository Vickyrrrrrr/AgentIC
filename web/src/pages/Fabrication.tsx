import React, { useState, Suspense } from 'react';
import { Canvas } from '@react-three/fiber';
import { Chip3D } from '../components/Chip3D';
import { OrbitControls } from '@react-three/drei';
import { useRevealOnScroll } from '../utils/useAnimations';

interface FabricationProps {
    selectedDesign: string;
    hasGds?: boolean;
}

export const Fabrication: React.FC<FabricationProps> = ({ selectedDesign, hasGds }) => {
    const [viewMode, setViewMode] = useState('3D');

    const header = useRevealOnScroll(0.1);
    const viewer = useRevealOnScroll(0.1);
    const tapeout = useRevealOnScroll(0.1);

    const handleDownloadGDS = () => {
        if (!hasGds) return alert('No GDSII available for this design yet.');
        alert(`Initiating download for ${selectedDesign}.gds...`);
    };

    return (
        <div className="page-container" style={{ padding: '1.5rem', maxWidth: '1100px' }}>
            <div ref={header.ref} className={`reveal ${header.isVisible ? 'visible' : ''}`}>
                <h2 className="app-title" style={{ fontSize: '1.4rem', fontWeight: 700, letterSpacing: '-0.02em' }}>
                    🏗️ Fabrication & GDSII
                </h2>
            </div>

            <div ref={tapeout.ref} className={`sci-fi-card reveal ${tapeout.isVisible ? 'visible' : ''}`} style={{ marginBottom: '20px', padding: '1.25rem' }}>
                <h3 style={{ fontWeight: 700, marginBottom: '0.5rem' }}>Tapeout Ready Files</h3>
                <p className="app-subtitle" style={{ color: 'var(--text-mid)', marginBottom: '1rem' }}>
                    Download your final GDSII layout for physical manufacturing.
                </p>

                <div style={{ display: 'flex', gap: '15px', alignItems: 'center' }}>
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
                        className="hero-btn-primary"
                        onClick={handleDownloadGDS}
                        style={{ opacity: hasGds ? 1 : 0.5, cursor: hasGds ? 'pointer' : 'not-allowed', fontSize: '0.85rem' }}
                    >
                        {hasGds ? '📥 Download .gds' : '❌ GDS Not Available'}
                    </button>
                </div>
            </div>

            <div ref={viewer.ref} className={`sci-fi-card reveal ${viewer.isVisible ? 'visible' : ''}`} style={{ padding: '1.25rem' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
                    <h3 style={{ fontWeight: 700 }}>Layout Viewer</h3>
                    <div style={{ display: 'flex', gap: '8px' }}>
                        {['2D', '3D'].map(mode => (
                            <button
                                key={mode}
                                className="hero-btn-secondary"
                                style={{
                                    padding: '0.4rem 1rem',
                                    fontSize: '0.82rem',
                                    background: viewMode === mode ? 'var(--accent-soft)' : 'var(--bg)',
                                    color: viewMode === mode ? 'var(--accent)' : 'var(--text-mid)',
                                    borderColor: viewMode === mode ? 'var(--accent)' : 'var(--border)',
                                }}
                                onClick={() => setViewMode(mode)}
                            >
                                {mode === '2D' ? '2D Top-Down' : '3D Stack'}
                            </button>
                        ))}
                    </div>
                </div>

                <div style={{
                    width: '100%', height: '400px', backgroundColor: 'var(--bg-dark)',
                    border: '1px solid var(--border)', borderRadius: 'var(--radius-md)',
                    overflow: 'hidden', position: 'relative'
                }}>
                    {viewMode === '3D' ? (
                        <Suspense fallback={
                            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text-dim)' }}>
                                <div className="premium-loader">
                                    <span className="premium-loader-dot" />
                                    <span className="premium-loader-dot" />
                                    <span className="premium-loader-dot" />
                                </div>
                                <span style={{ marginLeft: '0.5rem' }}>Loading 3D Viewer...</span>
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
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
                            <p style={{ color: 'var(--text-dim)', fontFamily: 'Fira Code', fontSize: '0.85rem' }}>
                                [2D Render — Awaiting GDS Parser Integration]
                            </p>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
};
