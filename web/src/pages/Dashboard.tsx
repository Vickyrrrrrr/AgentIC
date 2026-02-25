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

    useEffect(() => {
        if (!selectedDesign) return;
        setLoading(true);

        // Fetch Quick Metrics
        const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/$/, '');
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

    }, [selectedDesign]);

    return (
        <div className="page-container">
            <div className="header-container">
                <h2 className="app-title">📡 Mission Control: {selectedDesign || 'No Design'}</h2>
            </div>

            {loading ? <div style={{ color: '#00D1FF', margin: '20px 0' }}>Loading metrics...</div> : (
                <div className="grid-4">
                    <div className="sci-fi-card">
                        <div className="metric-label">Worst Negative Slack</div>
                        <div className="metric-value" style={{ color: '#00FF99' }}>{metrics.wns}</div>
                        <div style={{ color: '#888', fontSize: '12px', marginTop: '10px' }}>Timing</div>
                    </div>

                    <div className="sci-fi-card">
                        <div className="metric-label">Total Power</div>
                        <div className="metric-value" style={{ color: '#00D1FF' }}>{metrics.power}</div>
                        <div style={{ color: '#888', fontSize: '12px', marginTop: '10px' }}>Energy</div>
                    </div>

                    <div className="sci-fi-card">
                        <div className="metric-label">Die Area</div>
                        <div className="metric-value" style={{ color: '#7000FF' }}>{metrics.area}</div>
                        <div style={{ color: '#888', fontSize: '12px', marginTop: '10px' }}>Silicon Footprint</div>
                    </div>

                    <div className="sci-fi-card">
                        <div className="metric-label">Gate Count</div>
                        <div className="metric-value" style={{ color: '#FF0055' }}>{metrics.gate_count}</div>
                        <div style={{ color: '#888', fontSize: '12px', marginTop: '10px' }}>Logic Cells</div>
                    </div>
                </div>
            )}

            <div className="sci-fi-card" style={{ marginBottom: '20px' }}>
                <h3>💡 AgentIC Signoff Report {signoffData.pass === true ? '<✅ PASSED>' : signoffData.pass === false ? '<❌ FAILED>' : ''}</h3>
                <pre style={{
                    background: '#050505', padding: '15px', border: '1px solid #333',
                    borderRadius: '4px', color: '#00FF88', fontFamily: 'Fira Code',
                    whiteSpace: 'pre-wrap', maxHeight: '400px', overflowY: 'auto'
                }}>
                    {signoffData.report}
                </pre>
            </div>
        </div>
    );
};
