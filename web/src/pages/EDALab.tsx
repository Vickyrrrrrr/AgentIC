import { useState, useEffect } from 'react';
import Editor from '@monaco-editor/react';
import { api } from '../api';
import { Play, CheckCircle, Wand2, TerminalSquare, Cpu, XCircle, Layers, FileDown, Eye, ExternalLink } from 'lucide-react';

const IS_CLOUD_DEPLOY = import.meta.env.VITE_IS_CLOUD === 'true';

export const EDALab = () => {
    const [code, setCode] = useState<string>(
`module simple_counter (
    input clk,
    input rst,
    output logic [3:0] count
);

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            count <= 4'b0000;
        end else begin
            count <= count + 1'b1;
        end
    end

endmodule

// Testbench
module tb_simple_counter;
    logic clk;
    logic rst;
    logic [3:0] count;

    simple_counter dut (
        .clk(clk),
        .rst(rst),
        .count(count)
    );

    initial begin
        clk = 0;
        forever #5 clk = ~clk;
    end

    initial begin
        $dumpfile("dump.vcd");
        $dumpvars(0, tb_simple_counter);
        
        rst = 1;
        #10;
        rst = 0;
        
        #100;
        $display("Completed simulation. Final count = %d", count);
        $finish;
    end
endmodule
`);
    const [output, setOutput] = useState<string>('');
    const [vcdData, setVcdData] = useState<string | null>(null);
    const [viewerOpen, setViewerOpen] = useState<boolean>(false);
    const [loading, setLoading] = useState<'syntax' | 'synthesize' | 'simulate' | 'ai' | null>(null);
    const [theme, setTheme] = useState<'vs-dark' | 'vs-light'>('vs-dark');

    useEffect(() => {
        // Observers for theme change to update monaco
        const observer = new MutationObserver(() => {
            setTheme(document.documentElement.getAttribute('data-theme') === 'dark' ? 'vs-dark' : 'vs-light');
        });
        observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
        setTheme(document.documentElement.getAttribute('data-theme') === 'dark' ? 'vs-dark' : 'vs-light');
        return () => observer.disconnect();
    }, []);

    const runSyntaxCheck = async () => {
        setLoading('syntax');
        setOutput('Running Verilator syntax check...\n');
        try {
            const res = await api.post('/lab/syntax-check', { code, top_module: 'simple_counter' });
            if (res.data.success) {
                setOutput(prev => prev + '\n✅ Syntax Pass (Verilator)\n' + res.data.logs);
            } else {
                setOutput(prev => prev + '\n❌ Syntax Failed (Verilator)\n' + res.data.logs);
            }
        } catch (e: any) {
            setOutput(prev => prev + '\n❌ Error: ' + (e.response?.data?.detail || e.message));
        }
        setLoading(null);
    };

    const runSynthesis = async () => {
        setLoading('synthesize');
        setOutput('Synthesizing RTL (Yosys)...\n');
        try {
            const res = await api.post('/lab/synthesize', { code, top_module: 'simple_counter' });
            if (res.data.success) {
                setOutput(prev => prev + '\n✅ Synthesis Pass (Yosys)\n' + res.data.logs);
            } else {
                setOutput(prev => prev + '\n❌ Synthesis Failed (Yosys)\n' + res.data.logs);
            }
        } catch (e: any) {
            setOutput(prev => prev + '\n❌ Error: ' + (e.response?.data?.detail || e.message));
        }
        setLoading(null);
    };

    const runSimulate = async () => {
        setLoading('simulate');
        setOutput('Setting up Icarus Verilog Simulation...\n');
        setVcdData(null);
        try {
            const res = await api.post('/lab/simulate', { code, top_module: 'tb_simple_counter' });
            if (res.data.success) {
                setOutput(prev => prev + '\n✅ Simulation Complete\n' + res.data.logs);
                if (res.data.vcd) {
                    setVcdData(res.data.vcd);
                    setOutput(prev => prev + '\n\n🌊 VCD Waveform captured! Click the download button above to view locally.');
                }
            } else {
                setOutput(prev => prev + '\n❌ Simulation Failed\n' + res.data.logs);
            }
        } catch (e: any) {
            setOutput(prev => prev + '\n❌ Error: ' + (e.response?.data?.detail || e.message));
        }
        setLoading(null);
    };

    const askAIAssist = async () => {
        setLoading('ai');
        setOutput(prev => prev + '\n\nQuerying AgentIC AI array for code fixes...\n');
        try {
            const res = await api.post('/lab/ai-assist', { 
                query: "Analyze this Verilog code for bugs. Format your response strictly by placing the fully corrected Verilog code inside exactly one ```verilog codeblock, followed by a concise markdown explanation of what you changed.",
                code 
            });
            
            const responseText = res.data.response;
            // Extract the Verilog code block from the LLM output
            const codeMatch = responseText.match(/```verilog\n([\s\S]*?)```/);
            
            if (codeMatch && codeMatch[1]) {
                const fixedCode = codeMatch[1].trim();
                setCode(fixedCode); // Update the code editor with the fixed code
                
                // Show the explanation in the console (strip out the huge code block)
                const explanation = responseText.replace(/```verilog\n[\s\S]*?```/, '').trim();
                setOutput(prev => prev + '\n🤖 [AI Code Fixer]: I have updated the editor with the fixed code!\n\n📋 Changes Made:\n' + explanation);
            } else {
                // Fallback if the LLM didn't format correctly
                setOutput(prev => prev + '\n🤖 [AI Code Fixer]:\n' + responseText);
            }
        } catch (e: any) {
            // Re-throw or ignore
            setOutput(prev => prev + '\n❌ Error calling AI: ' + (e.response?.data?.detail || e.message));
        }
        setLoading(null);
    };

    const downloadVCD = () => {
        if (!vcdData) return;
        const blob = new Blob([vcdData], { type: 'text/plain' });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.style.display = 'none';
        a.href = url;
        a.download = 'dump.vcd';
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
    };

    const openGTKWave = async () => {
        if (!vcdData) return;
        setOutput(prev => prev + '\n\nLaunch command sent to GTKWave locally on server...');
        try {
            const res = await api.post('/lab/gtkwave', { vcd_data: vcdData });
            if (res.data.success) {
                setOutput(prev => prev + `\n🌊 ${res.data.message}`);
            } else {
                setOutput(prev => prev + '\n❌ GTKWave failed to launch... Make sure the AgentIC backend is running on a desktop UI.');
            }
        } catch (e: any) {
            setOutput(prev => prev + '\n❌ API Error: ' + (e.response?.data?.detail || e.message));
        }
    };

    const openCloudViewer = () => {
        setViewerOpen(true);
    };

    // Whenever viewer is open, we send the VCD data into the iframe via postMessage
    useEffect(() => {
        if (viewerOpen && vcdData) {
            // Need a slight delay to ensure iframe mounted
            const iframe = document.getElementById('vcdIframe') as HTMLIFrameElement;
            if (iframe) {
               iframe.onload = () => {
                   iframe.contentWindow?.postMessage({ type: 'load_vcd', vcd: vcdData }, '*');
               };
               // If already loaded
               if (iframe.contentWindow) {
                   setTimeout(() => {
                       iframe.contentWindow?.postMessage({ type: 'load_vcd', vcd: vcdData }, '*');
                   }, 500);
               }
            }
        }
    }, [viewerOpen, vcdData]);

    return (
        <div style={{ display: 'flex', flexDirection: 'column', height: '100%', gap: '1rem', padding: '1.5rem', boxSizing: 'border-box' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
                    <h2 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', margin: 0, fontSize: '1.4rem', color: 'var(--text)' }}>
                        <Cpu size={24}/> Manual EDA Testing Lab
                    </h2>
                    <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>Write Verilog, compile instantly natively, and chat directly with AI hardware engineers.</span>
                </div>
                <div style={{ display: 'flex', gap: '0.5rem' }}>
                    <button 
                        onClick={runSyntaxCheck} 
                        disabled={!!loading}
                        className="btn-primary"
                        style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', background: 'var(--accent)', color: 'white', padding: '0.5rem 1rem', borderRadius: '6px', border: 'none', cursor: loading ? 'not-allowed' : 'pointer', opacity: loading && loading !== 'syntax' ? 0.6 : 1 }}>
                        {loading === 'syntax' ? <span className="spinner"/> : <CheckCircle size={16} />}
                        Syntax Check
                    </button>
                    <button 
                        onClick={runSynthesis} 
                        disabled={!!loading}
                        className="btn-primary"
                        style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', background: '#eab308', color: 'black', padding: '0.5rem 1rem', borderRadius: '6px', border: 'none', cursor: loading ? 'not-allowed' : 'pointer', opacity: loading && loading !== 'synthesize' ? 0.6 : 1 }}>
                        {loading === 'synthesize' ? <span className="spinner"/> : <Layers size={16} />}
                        Synthesize (Yosys)
                    </button>
                    <button 
                        onClick={runSimulate} 
                        disabled={!!loading}
                        className="btn-primary"
                        style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', background: '#10b981', color: 'white', padding: '0.5rem 1rem', borderRadius: '6px', border: 'none', cursor: loading ? 'not-allowed' : 'pointer', opacity: loading && loading !== 'simulate' ? 0.6 : 1 }}>
                        {loading === 'simulate' ? <span className="spinner"/> : <Play size={16} />}
                        Simulate
                    </button>
                    {vcdData && (
                        <div style={{ display: 'flex', gap: '0.5rem', marginLeft: '0.5rem', paddingLeft: '0.5rem', borderLeft: '1px solid var(--border)' }}>
                            <button 
                                onClick={openCloudViewer}
                                className="btn-primary"
                                style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', background: '#0284c7', color: 'white', padding: '0.5rem 1rem', borderRadius: '6px', border: 'none', cursor: 'pointer' }}>
                                <ExternalLink size={16} />
                                In-Browser Viewer
                            </button>
                            {!IS_CLOUD_DEPLOY && (
                                <button 
                                    onClick={openGTKWave}
                                    className="btn-primary"
                                    style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', background: '#3b82f6', color: 'white', padding: '0.5rem 1rem', borderRadius: '6px', border: 'none', cursor: 'pointer' }}>
                                    <Eye size={16} />
                                    Desktop GTKWave
                                </button>
                            )}
                            <button 
                                onClick={downloadVCD}
                                className="btn-primary"
                                style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', background: '#8b5cf6', color: 'white', padding: '0.5rem 1rem', borderRadius: '6px', border: 'none', cursor: 'pointer' }}>
                                <FileDown size={16} />
                                (.VCD)
                            </button>
                        </div>
                    )}
                    <div style={{ flex: 1 }} />
                    <button 
                        onClick={askAIAssist}  
                        disabled={!!loading}
                        className="btn-primary"
                        style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', background: '#3b82f6', color: 'white', padding: '0.5rem 1rem', borderRadius: '6px', border: 'none', cursor: loading ? 'not-allowed' : 'pointer', opacity: loading && loading !== 'ai' ? 0.6 : 1 }}>
                        {loading === 'ai' ? <span className="spinner"/> : <Wand2 size={16} />}
                        AI Code Fixer
                    </button>
                </div>
            </div>

            <div style={{ display: 'flex', gap: '1rem', flex: 1, minHeight: 0 }}>
                {viewerOpen ? (
                    <div style={{ flex: '1', display: 'flex', flexDirection: 'column', border: '1px solid var(--border)', borderRadius: '8px', overflow: 'hidden', background: '#0f172a' }}>
                        <div style={{ padding: '0.5rem 1rem', background: '#1e293b', borderBottom: '1px solid #334155', fontWeight: 600, fontSize: '0.85rem', color: '#94a3b8', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                                <ExternalLink size={16}/> Cloud VCD Viewer (VCDrom)
                            </div>
                            <button 
                                onClick={() => setViewerOpen(false)}
                                style={{ background: '#334155', border: 'none', borderRadius: '4px', color: '#e2e8f0', padding: '0.2rem 0.5rem', cursor: 'pointer', fontSize: '0.75rem' }}>
                                Close Viewer
                            </button>
                        </div>
                        <iframe 
                            id="vcdIframe"
                            src="/vcdrom/index.html" 
                            style={{ flex: 1, border: 'none', background: 'white' }}
                            title="VCD Viewer"
                        />
                    </div>
                ) : (
                    <div style={{ flex: '1.2', display: 'flex', flexDirection: 'column', border: '1px solid var(--border)', borderRadius: '8px', overflow: 'hidden' }}>
                        <div style={{ padding: '0.5rem 1rem', background: 'var(--bg-surface)', borderBottom: '1px solid var(--border)', fontWeight: 600, fontSize: '0.85rem', color: 'var(--text-muted)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                            <span>testbench.sv</span>
                            <span style={{ fontSize: '0.75rem', fontWeight: 'normal' }}>Monaco Editor</span>
                        </div>
                        <Editor
                            height="100%"
                            language="verilog"
                            theme={theme}
                            value={code}
                            onChange={(val) => setCode(val || '')}
                            options={{ 
                                minimap: { enabled: false }, 
                                fontSize: 14, 
                                fontFamily: 'monospace',
                                scrollBeyondLastLine: false,
                                smoothScrolling: true
                            }}
                        />
                    </div>
                )}

                <div style={{ flex: '0.8', display: 'flex', flexDirection: 'column', border: '1px solid var(--border)', borderRadius: '8px', overflow: 'hidden', background: '#0f172a' }}>
                    <div style={{ padding: '0.5rem 1rem', background: '#1e293b', borderBottom: '1px solid #334155', fontWeight: 600, fontSize: '0.85rem', color: '#94a3b8', display: 'flex', alignItems: 'center', gap: '0.5rem', justifyContent: 'space-between' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                            <TerminalSquare size={16}/> Console Output
                        </div>
                        <button 
                            onClick={() => setOutput('')}
                            style={{ background: 'none', border: 'none', color: '#94a3b8', cursor: 'pointer', display: 'flex', alignItems: 'center', padding: '0' }}
                            title="Clear Console">
                            <XCircle size={16} />
                        </button>
                    </div>
                    <div style={{ flex: 1, padding: '1rem', overflowY: 'auto', color: '#e2e8f0', fontFamily: 'monospace', fontSize: '0.85rem', whiteSpace: 'pre-wrap', lineHeight: '1.5' }}>
                        {output || 'System ready.\nWrite Verilog hardware code and hit Simulation to test execution locally on your laptop or server environment...'}
                    </div>
                </div>
            </div>
        </div>
    );
};
