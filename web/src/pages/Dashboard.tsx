import React, { useState, useEffect } from 'react';
import axios from 'axios';

interface DashboardProps {
    selectedDesign: string;
}

export const Dashboard: React.FC<DashboardProps> = ({ selectedDesign }) => {
    const [metrics, setMetrics] = useState<any>({
        wns: 'N/A', power: 'N/A', area: 'N/A', gate_count: 'N/A'
    });
    const [signoffData, setSignoffData] = useState<{ report: string, pass: boolean | null }>({ report: 'Fetching full sign-off analysis...', pass: null });
    const [loading, setLoading] = useState(false);
    const [recentJobs, setRecentJobs] = useState<any[]>([]);

    useEffect(() => {
        if (!selectedDesign) return;
        setLoading(true);

        const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:7860').replace(/\/$/, '');

        // Fetch Quick Metrics
        axios.get(`${API_BASE_URL}/metrics/${selectedDesign}`)
            .then(res => {
                if (res.data.metrics) setMetrics(res.data.metrics);
            })
            .catch(() => {
                setMetrics({ wns: 'N/A', power: 'N/A', area: 'N/A', gate_count: 'N/A' });
            });

        // Fetch Full LLM Signoff Report
        axios.get(`${API_BASE_URL}/signoff/${selectedDesign}`)
            .then(res => {
                setSignoffData({ report: res.data.report, pass: res.data.success });
            })
            .catch(() => {
                setSignoffData({ report: 'Failed to retrieve Signoff Report. Has the device been fully hardened via OpenLane yet?', pass: false });
            })
            .finally(() => setLoading(false));

        // Fetch recent jobs
        axios.get(`${API_BASE_URL}/jobs`)
            .then(res => {
                const jobs = (res.data?.jobs || [])
                    .filter((j: any) => j.design_name === selectedDesign)
                    .slice(0, 5);
                setRecentJobs(jobs);
            })
            .catch(() => setRecentJobs([]));

    }, [selectedDesign]);

    const statusColor = (status: string) => {
        if (status === 'done') return 'var(--success)';
        if (status === 'failed') return 'var(--fail)';
        if (status === 'running') return 'var(--accent)';
        return 'var(--text-dim)';
    };

    return (
        <div className="page-container">
            <div className="header-container">
                <h2 className="app-title">📡 Mission Control: {selectedDesign || 'No Design'}</h2>
                <p className="app-subtitle">Silicon metrics, signoff analysis, and agent intelligence for this design.</p>
            </div>

            {loading ? <div style={{ color: 'var(--text-mid)', margin: '20px 0' }}>Loading metrics...</div> : (
                <div className="grid-4" style={{ marginBottom: '1.5rem' }}>
                    <div className="sci-fi-card metric-highlight">
                        <div className="metric-label">Worst Negative Slack</div>
                        <div className="metric-value" style={{ color: 'var(--success)' }}>{metrics.wns}</div>
                        <div className="metric-tag">Timing</div>
                    </div>

                    <div className="sci-fi-card metric-highlight">
                        <div className="metric-label">Total Power</div>
                        <div className="metric-value" style={{ color: 'var(--accent)' }}>{metrics.power}</div>
                        <div className="metric-tag">Energy</div>
                    </div>

                    <div className="sci-fi-card metric-highlight">
                        <div className="metric-label">Die Area</div>
                        <div className="metric-value" style={{ color: 'var(--text)' }}>{metrics.area}</div>
                        <div className="metric-tag">Silicon Footprint</div>
                    </div>

                    <div className="sci-fi-card metric-highlight">
                        <div className="metric-label">Gate Count</div>
                        <div className="metric-value" style={{ color: 'var(--text)' }}>{metrics.gate_count}</div>
                        <div className="metric-tag">Logic Cells</div>
                    </div>
                </div>
            )}

            {/* Agent Intelligence Card */}
            <div className="sci-fi-card" style={{ marginBottom: '1.5rem' }}>
                <h3 style={{ marginBottom: '0.75rem' }}>🧠 Agent Architecture</h3>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '0.75rem' }}>
                    <div className="dash-insight-card">
                        <div className="dash-insight-icon">📐</div>
                        <div className="dash-insight-title">Spec Decomposition</div>
                        <div className="dash-insight-value">SID/JSON Contract</div>
                        <div className="dash-insight-detail">ArchitectModule → validated ports, FSMs, sub-modules</div>
                    </div>
                    <div className="dash-insight-card">
                        <div className="dash-insight-icon">👥</div>
                        <div className="dash-insight-title">Collaborative RTL</div>
                        <div className="dash-insight-value">Designer + Reviewer</div>
                        <div className="dash-insight-detail">2-agent Crew with syntax_check and read_file tools</div>
                    </div>
                    <div className="dash-insight-card">
                        <div className="dash-insight-icon">🔄</div>
                        <div className="dash-insight-title">Self-Healing</div>
                        <div className="dash-insight-value">Convergence-Aware</div>
                        <div className="dash-insight-detail">SelfReflectPipeline with fingerprinting + stagnation detection</div>
                    </div>
                </div>
            </div>

            <div className="sci-fi-card" style={{ marginBottom: '1.5rem' }}>
                <h3 style={{ marginBottom: '0.75rem' }}>
                    AgentIC Signoff Report
                    {signoffData.pass === true && <span style={{ color: 'var(--success)', marginLeft: '0.5rem', fontSize: '0.85rem' }}>✅ PASSED</span>}
                    {signoffData.pass === false && <span style={{ color: 'var(--fail)', marginLeft: '0.5rem', fontSize: '0.85rem' }}>❌ FAILED</span>}
                </h3>
                <pre className="dash-signoff-report">
                    {signoffData.report}
                </pre>
            </div>

            {/* Recent Build History */}
            {recentJobs.length > 0 && (
                <div className="sci-fi-card">
                    <h3 style={{ marginBottom: '0.75rem' }}>Recent Builds</h3>
                    <table className="enterprise-table">
                        <thead>
                            <tr>
                                <th>Job ID</th>
                                <th>Status</th>
                                <th>Current Stage</th>
                                <th>Events</th>
                            </tr>
                        </thead>
                        <tbody>
                            {recentJobs.map((job: any) => (
                                <tr key={job.job_id}>
                                    <td style={{ fontFamily: 'Fira Code, monospace', fontSize: '0.78rem' }}>
                                        {job.job_id.substring(0, 8)}…
                                    </td>
                                    <td>
                                        <span style={{ color: statusColor(job.status), fontWeight: 600 }}>
                                            {job.status}
                                        </span>
                                    </td>
                                    <td>{job.current_state}</td>
                                    <td>{job.event_count}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
};
