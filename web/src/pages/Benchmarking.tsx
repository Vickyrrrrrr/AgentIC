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
                            <td style={{ color: 'var(--success)', fontWeight: 600 }}>ArchitectModule SID (automated)</td>
                            <td>Manual architecture review (weeks)</td>
                        </tr>
                        <tr>
                            <td>Verification Methodology</td>
                            <td style={{ color: 'var(--success)', fontWeight: 600 }}>Multi-agent diagnosis (5-class)</td>
                            <td>Manual waveform debugging</td>
                        </tr>
                        <tr>
                            <td>Agent Collaboration</td>
                            <td style={{ color: 'var(--success)', fontWeight: 600 }}>12 agents with tools + Crews</td>
                            <td>Siloed engineer teams</td>
                        </tr>
                        <tr>
                            <td>Self-Healing</td>
                            <td style={{ color: 'var(--success)', fontWeight: 600 }}>SelfReflectPipeline + convergence</td>
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
                            <th>Based On</th>
                            <th>Stage</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td style={{ fontWeight: 600 }}>ArchitectModule</td>
                            <td>Spec2RTL-Agent</td>
                            <td>SPEC → SID JSON decomposition</td>
                        </tr>
                        <tr>
                            <td style={{ fontWeight: 600 }}>ReActAgent</td>
                            <td>Yao et al., 2023</td>
                            <td>Thought→Action→Observation loops</td>
                        </tr>
                        <tr>
                            <td style={{ fontWeight: 600 }}>SelfReflectPipeline</td>
                            <td>Self-Reflection Retry</td>
                            <td>HARDENING with convergence tracking</td>
                        </tr>
                        <tr>
                            <td style={{ fontWeight: 600 }}>DeepDebuggerModule</td>
                            <td>FVDebug</td>
                            <td>Formal — causal graphs + For-and-Against</td>
                        </tr>
                        <tr>
                            <td style={{ fontWeight: 600 }}>WaveformExpertModule</td>
                            <td>VerilogCoder</td>
                            <td>VCD + AST back-trace diagnosis</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>
    );
};
