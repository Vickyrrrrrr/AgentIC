import React from 'react';

interface BenchmarkingProps {
    selectedDesign: string;
}

export const Benchmarking: React.FC<BenchmarkingProps> = ({ selectedDesign }) => {
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
        <div className="bench-page">
            <div className="bench-header">
                <h2 className="bench-title">
                    Benchmarking: <span className="bench-design-name">{selectedDesign || 'No Design'}</span>
                </h2>
                <p className="bench-subtitle">
                    Compare AgentIC-generated flows against conventional enterprise chip flows.
                </p>
            </div>

            <div className="bench-card">
                <h3 className="bench-card-title">Cost & Efficiency Analysis</h3>
                <div className="bench-table-wrap">
                    <table className="bench-table">
                        <thead>
                            <tr>
                                <th>Metric</th>
                                <th>AgentIC</th>
                                <th>Traditional Flow</th>
                            </tr>
                        </thead>
                        <tbody>
                            {comparisons.map((c) => (
                                <tr key={c.metric}>
                                    <td className="bench-metric-name">{c.metric}</td>
                                    <td className="bench-val-good">{c.agenticVal}</td>
                                    <td className="bench-val-dim">{c.tradVal}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>

            <div className="bench-card">
                <h3 className="bench-card-title">Core Module Architecture</h3>
                <div className="bench-table-wrap">
                    <table className="bench-table">
                        <thead>
                            <tr>
                                <th>Module</th>
                                <th>Capability</th>
                            </tr>
                        </thead>
                        <tbody>
                            {modules.map((m) => (
                                <tr key={m.name}>
                                    <td className="bench-metric-name">{m.name}</td>
                                    <td>{m.cap}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    );
};
