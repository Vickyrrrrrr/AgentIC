import React, { useState, useEffect } from 'react';
import { api } from '../api';
import { useRevealOnScroll, useCountUp } from '../utils/useAnimations';

interface DashboardProps {
    selectedDesign: string;
}

const AnimatedMetric = ({ value, color, label, tag }: { value: string; color: string; label: string; tag: string }) => {
    const { ref, isVisible } = useRevealOnScroll(0.2);
    const numericMatch = value.match?.(/^([\d.]+)/);
    const numericVal = numericMatch ? parseFloat(numericMatch[1]) : 0;
    const suffix = numericMatch ? value.replace(numericMatch[1], '') : '';
    const counter = useCountUp(Math.round(numericVal * 100) / 100, 1200); // for non-numeric just show value

    return (
        <div ref={ref} className={`sci-fi-card metric-highlight reveal ${isVisible ? 'visible' : ''}`}>
            <div className="metric-label">{label}</div>
            <div className="metric-value metric-animated" style={{ color }}>
                {value === 'N/A' ? 'N/A' : (numericVal > 0 ? `${counter.value}${suffix}` : value)}
            </div>
            <div className="metric-tag">{tag}</div>
        </div>
    );
};

export const Dashboard: React.FC<DashboardProps> = ({ selectedDesign }) => {
    const [metrics, setMetrics] = useState<any>({
        wns: 'N/A', power: 'N/A', area: 'N/A', gate_count: 'N/A'
    });
    const [signoffData, setSignoffData] = useState<{ report: string, pass: boolean | null }>({ report: 'Fetching full sign-off analysis...', pass: null });
    const [loading, setLoading] = useState(false);
    const [recentJobs, setRecentJobs] = useState<any[]>([]);

    const section1 = useRevealOnScroll(0.1);
    const section2 = useRevealOnScroll(0.1);
    const section3 = useRevealOnScroll(0.1);

    useEffect(() => {
        if (!selectedDesign) return;
        setLoading(true);

        api.get(`/metrics/${selectedDesign}`)
            .then(res => {
                if (res.data.metrics) setMetrics(res.data.metrics);
            })
            .catch(() => {
                setMetrics({ wns: 'N/A', power: 'N/A', area: 'N/A', gate_count: 'N/A' });
            });

        api.get(`/signoff/${selectedDesign}`)
            .then(res => {
                setSignoffData({ report: res.data.report, pass: res.data.success });
            })
            .catch(() => {
                setSignoffData({ report: 'Failed to retrieve Signoff Report. Has the device been fully hardened via OpenLane yet?', pass: false });
            })
            .finally(() => setLoading(false));

        api.get(`/jobs`)
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
        <div className="page-container" style={{ padding: '1.5rem', maxWidth: '1100px' }}>
            <div className="header-container" style={{ marginBottom: '1.5rem' }}>
                <h2 className="app-title" style={{ fontSize: '1.4rem', fontWeight: 700, letterSpacing: '-0.02em' }}>
                    📡 Mission Control: <span className="gradient-text">{selectedDesign || 'No Design'}</span>
                </h2>
                <p className="app-subtitle" style={{ color: 'var(--text-mid)', fontSize: '0.88rem' }}>
                    Silicon metrics, signoff analysis, and agent intelligence for this design.
                </p>
            </div>

            {loading ? (
                <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', color: 'var(--text-mid)', margin: '20px 0' }}>
                    <div className="premium-loader">
                        <span className="premium-loader-dot" />
                        <span className="premium-loader-dot" />
                        <span className="premium-loader-dot" />
                    </div>
                    Loading metrics...
                </div>
            ) : (
                <div className="grid-4" style={{ marginBottom: '1.5rem' }}>
                    <AnimatedMetric value={metrics.wns} color="var(--success)" label="Worst Negative Slack" tag="Timing" />
                    <AnimatedMetric value={metrics.power} color="var(--accent)" label="Total Power" tag="Energy" />
                    <AnimatedMetric value={metrics.area} color="var(--text)" label="Die Area" tag="Silicon Footprint" />
                    <AnimatedMetric value={metrics.gate_count} color="var(--text)" label="Gate Count" tag="Logic Cells" />
                </div>
            )}

            {/* Agent Intelligence Card */}
            <div ref={section1.ref} className={`sci-fi-card reveal ${section1.isVisible ? 'visible' : ''}`} style={{ marginBottom: '1.5rem', padding: '1.25rem' }}>
                <h3 style={{ marginBottom: '0.75rem', fontWeight: 700 }}>🧠 Agent Architecture</h3>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '0.75rem' }}>
                    {[
                        { icon: '📐', title: 'Spec Decomposition', value: 'SID/JSON Contract', detail: 'ArchitectModule → validated ports, FSMs, sub-modules' },
                        { icon: '👥', title: 'Collaborative RTL', value: 'Designer + Reviewer', detail: '2-agent Crew with syntax_check and read_file tools' },
                        { icon: '🔄', title: 'Self-Healing', value: 'Convergence-Aware', detail: 'SelfReflectPipeline with fingerprinting + stagnation detection' },
                    ].map((item, i) => (
                        <div key={i} className="dash-insight-card" style={{ transitionDelay: `${i * 80}ms` }}>
                            <div className="dash-insight-icon">{item.icon}</div>
                            <div className="dash-insight-title">{item.title}</div>
                            <div className="dash-insight-value">{item.value}</div>
                            <div className="dash-insight-detail">{item.detail}</div>
                        </div>
                    ))}
                </div>
            </div>

            <div ref={section2.ref} className={`sci-fi-card reveal ${section2.isVisible ? 'visible' : ''}`} style={{ marginBottom: '1.5rem', padding: '1.25rem' }}>
                <h3 style={{ marginBottom: '0.75rem', fontWeight: 700 }}>
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
                <div ref={section3.ref} className={`sci-fi-card reveal ${section3.isVisible ? 'visible' : ''}`} style={{ padding: '1.25rem' }}>
                    <h3 style={{ marginBottom: '0.75rem', fontWeight: 700 }}>Recent Builds</h3>
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
