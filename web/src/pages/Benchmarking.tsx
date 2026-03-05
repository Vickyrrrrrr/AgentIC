import React from 'react';

interface BenchmarkingProps {
    selectedDesign: string;
}

export const Benchmarking: React.FC<BenchmarkingProps> = ({ selectedDesign }) => {
    return (
        <div className="page-container">
            <h2 className="app-title">📊 Market Benchmarking: {selectedDesign || 'No Design'}</h2>
            <p className="app-subtitle">Compare AgentIC-generated flows against conventional enterprise chip flows.</p>

            <div className="sci-fi-card" style={{ marginBottom: '1.5rem' }}>
                <h3>Cost & Efficiency Analysis</h3>
                <table className="enterprise-table" style={{ marginTop: '10px' }}>
                    <thead>
                        <tr>
                            <th>Metric</th>
                            <th>AgentIC</th>
                            <th>Traditional Flow</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td>RTL to GDSII Time</td>
                            <td style={{ color: 'var(--success)', fontWeight: 600 }}>~15 Minutes</td>
                            <td>Days/Weeks</td>
                        </tr>
                        <tr>
                            <td>Spec Decomposition</td>
                            <td style={{ color: 'var(--success)', fontWeight: 600 }}>Automated specification analysis</td>
                            <td>Manual architecture review (weeks)</td>
                        </tr>
                        <tr>
                            <td>Verification Methodology</td>
                            <td style={{ color: 'var(--success)', fontWeight: 600 }}>Intelligent multi-class diagnosis</td>
                            <td>Manual waveform debugging</td>
                        </tr>
                        <tr>
                            <td>Agent Collaboration</td>
                            <td style={{ color: 'var(--success)', fontWeight: 600 }}>Multi-agent collaborative pipeline</td>
                            <td>Siloed engineer teams</td>
                        </tr>
                        <tr>
                            <td>Self-Healing</td>
                            <td style={{ color: 'var(--success)', fontWeight: 600 }}>Convergence-aware automated recovery</td>
                            <td>Manual iteration</td>
                        </tr>
                        <tr>
                            <td>Log Triage</td>
                            <td style={{ color: 'var(--success)', fontWeight: 600 }}>Automated LLM Parsing</td>
                            <td>Manual Grepping</td>
                        </tr>
                        <tr>
                            <td>Licensing Cost</td>
                            <td style={{ color: 'var(--success)', fontWeight: 600 }}>Open Source + API</td>
                            <td>$1M+ / seat</td>
                        </tr>
                        <tr>
                            <td>DRC / LVS Violations</td>
                            <td style={{ color: 'var(--success)', fontWeight: 600 }}>Auto-heal assisted</td>
                            <td>Manual closure process</td>
                        </tr>
                    </tbody>
                </table>
            </div>

            <div className="sci-fi-card">
                <h3>Core Module Architecture</h3>
                <table className="enterprise-table" style={{ marginTop: '10px' }}>
                    <thead>
                        <tr>
                            <th>Module</th>
                            <th>Capability</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td style={{ fontWeight: 600 }}>Architect</td>
                            <td>Natural language specification decomposition and structured contract generation</td>
                        </tr>
                        <tr>
                            <td style={{ fontWeight: 600 }}>Reasoning Agent</td>
                            <td>Iterative reasoning with observation-driven action planning</td>
                        </tr>
                        <tr>
                            <td style={{ fontWeight: 600 }}>Self-Healing Pipeline</td>
                            <td>Convergence-aware retry with metric-driven optimization</td>
                        </tr>
                        <tr>
                            <td style={{ fontWeight: 600 }}>Deep Debugger</td>
                            <td>Causal failure analysis with multi-perspective reasoning</td>
                        </tr>
                        <tr>
                            <td style={{ fontWeight: 600 }}>Waveform Analyst</td>
                            <td>Signal-level diagnostic analysis and root cause identification</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>
    );
};
