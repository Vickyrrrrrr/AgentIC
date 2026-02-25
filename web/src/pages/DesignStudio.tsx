import { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import axios from 'axios';
import { BuildMonitor } from '../components/BuildMonitor';
import { ChipSummary } from '../components/ChipSummary';

const API = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

type Phase = 'prompt' | 'building' | 'done';

interface BuildEvent {
    type: string;
    state: string;
    message: string;
    step: number;
    total_steps: number;
    timestamp: number;
    status?: string;  // present on stream_end events
}

function slugify(text: string): string {
    return text
        .toLowerCase()
        .replace(/[^a-z0-9\s_]/g, '')
        .trim()
        .split(/\s+/)
        .slice(0, 4)
        .join('_')
        .substring(0, 48);
}

export const DesignStudio = () => {
    const [phase, setPhase] = useState<Phase>('prompt');
    const [prompt, setPrompt] = useState('');
    const [designName, setDesignName] = useState('');
    const [jobId, setJobId] = useState('');
    const [events, setEvents] = useState<BuildEvent[]>([]);
    const [jobStatus, setJobStatus] = useState<'queued' | 'running' | 'done' | 'failed' | 'cancelled' | 'cancelling'>('queued');
    const [result, setResult] = useState<any>(null);
    const [error, setError] = useState('');
    const [skipOpenlane, setSkipOpenlane] = useState(false);
    const esRef = useRef<EventSource | null>(null);

    // Auto-generate design name from prompt
    useEffect(() => {
        if (prompt.length > 8) {
            setDesignName(slugify(prompt));
        }
    }, [prompt]);

    const handleLaunch = async () => {
        if (!prompt.trim()) return;
        setError('');
        try {
            const res = await axios.post(`${API}/build`, {
                design_name: designName || slugify(prompt),
                description: prompt,
                skip_openlane: skipOpenlane,
                full_signoff: false,
            });
            const { job_id } = res.data;
            setJobId(job_id);
            setPhase('building');
            startStreaming(job_id);
        } catch (e: any) {
            setError(e?.response?.data?.detail || 'Failed to start build. Is the backend running?');
        }
    };

    const startStreaming = (jid: string) => {
        if (esRef.current) esRef.current.close();
        const es = new EventSource(`${API}/build/stream/${jid}`);
        esRef.current = es;

        es.onmessage = (evt) => {
            try {
                const data: BuildEvent = JSON.parse(evt.data);
                if (data.type === 'ping') return;
                if (data.type === 'stream_end') {
                    es.close();
                    fetchResult(jid, data.status as any);
                    return;
                }
                setEvents(prev => [...prev, data]);
                setJobStatus(data.type === 'error' ? 'failed' : 'running');
            } catch { /* ignore parse errors */ }
        };

        es.onerror = () => {
            es.close();
        };
    };

    const fetchResult = async (jid: string, status: string) => {
        setJobStatus(status === 'done' ? 'done' : 'failed');
        try {
            const res = await axios.get(`${API}/build/result/${jid}`);
            setResult(res.data.result);
        } catch { /* result might not exist if failed early */ }
        setPhase('done');

        // Browser notification
        if ('Notification' in window && Notification.permission === 'granted') {
            new Notification('AgentIC — Chip Build Complete 🎉', {
                body: `Your chip "${designName}" has finished ${status === 'done' ? 'successfully!' : 'with errors.'}`,
                icon: '/chip-icon.png',
            });
        }
    };

    const handleReset = () => {
        esRef.current?.close();
        setPhase('prompt');
        setEvents([]);
        setResult(null);
        setJobId('');
        setJobStatus('queued');
        setError('');
        setPrompt('');
    };

    // Request notification permission on mount
    useEffect(() => {
        if ('Notification' in window && Notification.permission === 'default') {
            Notification.requestPermission();
        }
        return () => esRef.current?.close();
    }, []);

    return (
        <div className="studio-root">
            <AnimatePresence mode="wait">

                {/* ── PHASE A: Prompt ──────────────────────────── */}
                {phase === 'prompt' && (
                    <motion.div
                        key="prompt"
                        className="prompt-screen"
                        initial={{ opacity: 0, y: 40 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -40 }}
                        transition={{ duration: 0.5 }}
                    >
                        <div className="prompt-hero">
                            <div className="chip-icon-glow">⚙️</div>
                            <h1 className="prompt-title">Design Your Chip</h1>
                            <p className="prompt-sub">
                                Describe any digital chip in plain English.<br />
                                AgentIC will autonomously write RTL, verify, and harden it to silicon.
                            </p>
                        </div>

                        <div className="prompt-card">
                            <div className="prompt-examples">
                                {['8-bit RISC CPU with Harvard architecture', 'AXI4 DMA engine with 4 channels', 'UART controller at 115200 baud'].map(ex => (
                                    <button key={ex} className="example-chip" onClick={() => setPrompt(ex)}>
                                        {ex}
                                    </button>
                                ))}
                            </div>

                            <textarea
                                className="prompt-textarea"
                                placeholder="Describe the chip you want to build in plain English... (e.g. 'A 4-bit counter with synchronous reset and clock enable')"
                                value={prompt}
                                onChange={e => setPrompt(e.target.value)}
                                rows={5}
                                autoFocus
                            />

                            {designName && (
                                <div className="design-name-preview">
                                    <span className="design-name-label">Design ID:</span>
                                    <input
                                        className="design-name-input"
                                        value={designName}
                                        onChange={e => setDesignName(e.target.value.replace(/[^a-z0-9_]/g, ''))}
                                    />
                                </div>
                            )}

                            <div className="prompt-options">
                                <label className="toggle-label">
                                    <input
                                        type="checkbox"
                                        checked={skipOpenlane}
                                        onChange={e => setSkipOpenlane(e.target.checked)}
                                    />
                                    <span>Skip OpenLane (RTL + Verify only, faster)</span>
                                </label>
                            </div>

                            {error && <div className="error-banner">⚠️ {error}</div>}

                            <motion.button
                                className="launch-btn"
                                onClick={handleLaunch}
                                disabled={!prompt.trim()}
                                whileHover={{ scale: 1.03 }}
                                whileTap={{ scale: 0.97 }}
                            >
                                <span style={{ fontSize: '1.4rem' }}>🚀</span>
                                Launch Autonomous Build
                            </motion.button>
                        </div>
                    </motion.div>
                )}

                {/* ── PHASE B: Building ────────────────────────── */}
                {phase === 'building' && (
                    <motion.div
                        key="building"
                        initial={{ opacity: 0, x: 40 }}
                        animate={{ opacity: 1, x: 0 }}
                        exit={{ opacity: 0, x: -40 }}
                        transition={{ duration: 0.4 }}
                    >
                        <BuildMonitor
                            designName={designName}
                            jobId={jobId}
                            events={events}
                            jobStatus={jobStatus}
                        />
                    </motion.div>
                )}

                {/* ── PHASE C: Result ──────────────────────────── */}
                {phase === 'done' && (
                    <motion.div
                        key="done"
                        initial={{ opacity: 0, scale: 0.96 }}
                        animate={{ opacity: 1, scale: 1 }}
                        exit={{ opacity: 0 }}
                        transition={{ duration: 0.5 }}
                    >
                        <ChipSummary
                            designName={designName}
                            result={result}
                            jobStatus={jobStatus}
                            events={events}
                            onReset={handleReset}
                        />
                    </motion.div>
                )}

            </AnimatePresence>
        </div>
    );
};
