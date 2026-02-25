import React from 'react';
import { motion } from 'framer-motion';

interface Props {
    designName: string;
    result: any;
    jobStatus: string;
    events: any[];
    onReset: () => void;
}

function MetricCard({ label, value, icon, color }: { label: string; value: any; icon: string; color: string }) {
    return (
        <motion.div
            className="metric-card"
            style={{ borderColor: color }}
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4 }}
        >
            <div className="metric-icon" style={{ color }}>{icon}</div>
            <div className="metric-value" style={{ color }}>{value ?? 'N/A'}</div>
            <div className="metric-label">{label}</div>
        </motion.div>
    );
}

export const ChipSummary: React.FC<Props> = ({ designName, result, jobStatus, events, onReset }) => {
    const success = jobStatus === 'done';
    const metrics = result?.metrics || {};
    const convergence = result?.convergence_history || [];
    const spec = result?.spec || '';
    const rtlSnippet = result?.rtl_snippet || '';
    const strategy = result?.strategy || '';
    const buildTimeSec = result?.build_time_s || 0;
    const buildTimeMin = Math.round(buildTimeSec / 60);

    // Count checkpoints passed
    const checkpointCount = events.filter(e => e.type === 'transition' || e.type === 'checkpoint').length;

    return (
        <div className="summary-root">
            {/* ── Banner ─────────────────────────────────────── */}
            <motion.div
                className={`summary-banner ${success ? 'banner-success' : 'banner-fail'}`}
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ duration: 0.5 }}
            >
                <div className="banner-icon">{success ? '🎉' : '❌'}</div>
                <div>
                    <h1 className="banner-title">
                        {success ? 'Your Chip is Ready!' : 'Build Encountered Errors'}
                    </h1>
                    <p className="banner-sub">
                        <strong>{designName}</strong> ·{' '}
                        {success
                            ? `Completed in ~${buildTimeMin} min · ${checkpointCount} checkpoints passed`
                            : 'Review the terminal logs for details'}
                    </p>
                </div>
            </motion.div>

            {/* ── Metrics ────────────────────────────────────── */}
            {success && (
                <div className="summary-section">
                    <h2 className="section-heading">📊 Silicon Metrics</h2>
                    <div className="metrics-grid">
                        <MetricCard label="Worst Negative Slack" value={metrics.wns !== undefined ? `${metrics.wns} ns` : 'N/A'} icon="⏱️" color="#00D1FF" />
                        <MetricCard label="Die Area" value={metrics.area} icon="📐" color="#00FF88" />
                        <MetricCard label="Total Power" value={metrics.power} icon="⚡" color="#FFD700" />
                        <MetricCard label="Gate Count" value={metrics.gate_count} icon="🔲" color="#FF6B9D" />
                    </div>
                </div>
            )}

            {/* ── Strategy & Build Info ──────────────────────── */}
            {(strategy || buildTimeSec > 0) && (
                <div className="summary-section">
                    <h2 className="section-heading">🛠️ Build Info</h2>
                    <div className="info-pills">
                        {strategy && <span className="info-pill">{strategy}</span>}
                        {buildTimeSec > 0 && <span className="info-pill">⏳ {buildTimeMin} min build</span>}
                        <span className="info-pill">🏗️ {checkpointCount} pipeline steps</span>
                    </div>
                </div>
            )}

            {/* ── Architecture Spec ──────────────────────────── */}
            {spec && (
                <div className="summary-section">
                    <h2 className="section-heading">📐 Architecture Specification</h2>
                    <div className="spec-box">
                        <pre className="spec-text">{spec.substring(0, 1200)}{spec.length > 1200 ? '\n…(truncated)' : ''}</pre>
                    </div>
                </div>
            )}

            {/* ── RTL Preview ────────────────────────────────── */}
            {rtlSnippet && (
                <div className="summary-section">
                    <h2 className="section-heading">🖥️ RTL Preview</h2>
                    <div className="rtl-box">
                        <pre className="rtl-code">{rtlSnippet.substring(0, 1200)}{rtlSnippet.length > 1200 ? '\n// …(truncated)' : ''}</pre>
                    </div>
                </div>
            )}

            {/* ── Convergence History ────────────────────────── */}
            {convergence.length > 0 && (
                <div className="summary-section">
                    <h2 className="section-heading">📈 Convergence History</h2>
                    <div className="convergence-table-wrap">
                        <table className="convergence-table">
                            <thead>
                                <tr>
                                    <th>Iter</th><th>WNS (ns)</th><th>TNS (ns)</th>
                                    <th>Congestion %</th><th>Area (µm²)</th><th>Power (W)</th>
                                </tr>
                            </thead>
                            <tbody>
                                {convergence.slice(-5).map((s: any, i: number) => (
                                    <tr key={i}>
                                        <td>{s.iteration}</td>
                                        <td style={{ color: s.wns >= 0 ? '#00FF88' : '#FF4444' }}>{s.wns?.toFixed(3)}</td>
                                        <td>{s.tns?.toFixed(3)}</td>
                                        <td>{s.congestion?.toFixed(2)}</td>
                                        <td>{s.area_um2?.toFixed(0)}</td>
                                        <td>{s.power_w?.toExponential(2)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}

            {/* ── Error detail ───────────────────────────────── */}
            {!success && result?.error && (
                <div className="summary-section">
                    <h2 className="section-heading">⚠️ Error Details</h2>
                    <pre className="error-box">{result.error}</pre>
                </div>
            )}

            {/* ── Actions ────────────────────────────────────── */}
            <div className="summary-actions">
                <motion.button
                    className="action-btn action-primary"
                    onClick={onReset}
                    whileHover={{ scale: 1.04 }}
                    whileTap={{ scale: 0.97 }}
                >
                    🚀 Build Another Chip
                </motion.button>
            </div>
        </div>
    );
};
