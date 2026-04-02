import React from 'react';
import { useRevealOnScroll } from '../utils/useAnimations';

interface BenchmarkingProps {
    selectedDesign: string;
}

const ComparisonRow = ({ metric, agenticVal, tradVal, delay }: { metric: string; agenticVal: string; tradVal: string; delay: number }) => {
    return (
        <tr style={{ animation: `reveal-up 0.4s var(--ease) ${delay}ms both` }}>
            <td style={{ fontWeight: 600 }}>{metric}</td>
            <td>
                <span style={{ 
                    color: 'var(--success)', fontWeight: 600,
                    display: 'inline-flex', alignItems: 'center', gap: '0.4rem'
                }}>
                    <span style={{
                        width: 6, height: 6, borderRadius: '50%',
                        background: 'var(--success)',
                        boxShadow: '0 0 6px var(--success)',
                        display: 'inline-block'
                    }} />
                    {agenticVal}
                </span>
            </td>
            <td style={{ color: 'var(--text-dim)' }}>{tradVal}</td>
        </tr>
    );
};

export const Benchmarking: React.FC<BenchmarkingProps> = ({ selectedDesign }) => {
    const header = useRevealOnScroll(0.1);
    const section1 = useRevealOnScroll(0.1);
    const section2 = useRevealOnScroll(0.1);

    const comparisons = [
        { metric: 'RTL to GDSII Time', agenticVal: '~15 Minutes', tradVal: 'Days/Weeks' },
        { metric: 'Spec Decomposition', agenticVal: 'Automated specification analysis', tradVal: 'Manual architecture review (weeks)' },
        { metric: 'Verification Methodology', agenticVal: 'Intelligent multi-class diagnosis', tradVal: 'Manual waveform debugging' },
        { metric: 'Agent Collaboration', agenticVal: 'Multi-agent collaborative pipeline', tradVal: 'Siloed engineer teams' },
        { metric: 'Self-Healing', agenticVal: 'Convergence-aware automated recovery', tradVal: 'Manual iteration' },
        { metric: 'Log Triage', agenticVal: 'Automated LLM Parsing', tradVal: 'Manual Grepping' },
        { metric: 'Licensing Cost', agenticVal: 'Open Source + API', tradVal: '$1M+ / seat' },
        { metric: 'DRC / LVS Violations', agenticVal: 'Auto-heal assisted', tradVal: 'Manual closure process' },
    ];

    const modules = [
        { name: 'Architect', cap: 'Natural language specification decomposition and structured contract generation' },
        { name: 'Reasoning Agent', cap: 'Iterative reasoning with observation-driven action planning' },
        { name: 'Self-Healing Pipeline', cap: 'Convergence-aware retry with metric-driven optimization' },
        { name: 'Deep Debugger', cap: 'Causal failure analysis with multi-perspective reasoning' },
        { name: 'Waveform Analyst', cap: 'Signal-level diagnostic analysis and root cause identification' },
    ];

    return (
        <div className="page-container" style={{ padding: '1.5rem', maxWidth: '1100px' }}>
            <div ref={header.ref} className={`reveal ${header.isVisible ? 'visible' : ''}`}>
                <h2 className="app-title" style={{ fontSize: '1.4rem', fontWeight: 700, letterSpacing: '-0.02em' }}>
                    📊 Market Benchmarking: <span className="gradient-text">{selectedDesign || 'No Design'}</span>
                </h2>
                <p className="app-subtitle" style={{ color: 'var(--text-mid)', fontSize: '0.88rem', marginBottom: '1.5rem' }}>
                    Compare AgentIC-generated flows against conventional enterprise chip flows.
                </p>
            </div>

            <div ref={section1.ref} className={`sci-fi-card reveal ${section1.isVisible ? 'visible' : ''}`} style={{ marginBottom: '1.5rem', padding: '1.25rem' }}>
                <h3 style={{ fontWeight: 700, marginBottom: '0.75rem' }}>Cost & Efficiency Analysis</h3>
                <table className="enterprise-table" style={{ marginTop: '10px' }}>
                    <thead>
                        <tr>
                            <th>Metric</th>
                            <th>AgentIC</th>
                            <th>Traditional Flow</th>
                        </tr>
                    </thead>
                    <tbody>
                        {comparisons.map((c, i) => (
                            <ComparisonRow key={c.metric} {...c} delay={i * 60} />
                        ))}
                    </tbody>
                </table>
            </div>

            <div ref={section2.ref} className={`sci-fi-card reveal ${section2.isVisible ? 'visible' : ''}`} style={{ padding: '1.25rem' }}>
                <h3 style={{ fontWeight: 700, marginBottom: '0.75rem' }}>Core Module Architecture</h3>
                <table className="enterprise-table" style={{ marginTop: '10px' }}>
                    <thead>
                        <tr>
                            <th>Module</th>
                            <th>Capability</th>
                        </tr>
                    </thead>
                    <tbody>
                        {modules.map((m, i) => (
                            <tr key={m.name} style={{ animation: `reveal-up 0.4s var(--ease) ${i * 80}ms both` }}>
                                <td style={{ fontWeight: 600 }}>{m.name}</td>
                                <td>{m.cap}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
};
