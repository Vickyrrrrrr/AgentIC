import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Cpu, Zap, Layers, ChevronRight, Sparkles, Settings2, Play } from "lucide-react";
import { useCreateDesign } from "@app/lib/hooks";
import { PDK_OPTIONS } from "@app/lib/types";
import CodeEditor from "@app/components/CodeEditor";

const TEMPLATES = [
  { name: "uart_tx", desc: "UART transmitter with configurable baud rate", pdk: "sky130", clock: 50 },
  { name: "spi_master", desc: "SPI master controller with CPOL/CPHA modes", pdk: "sky130", clock: 100 },
  { name: "sync_fifo", desc: "Synchronous FIFO with parameterized depth", pdk: "sky130", clock: 200 },
  { name: "timer_module", desc: "Programmable countdown timer with interrupt", pdk: "sky130", clock: 50 },
  { name: "shift_reg", desc: "Parallel-in serial-out shift register", pdk: "sky130", clock: 100 },
  { name: "counter_8bit", desc: "8-bit up/down counter with overflow flag", pdk: "sky130", clock: 50 },
];

function stubRtl(name: string) {
  const mod = name || "my_module";
  return `module ${mod} (\n  input  wire clk,\n  input  wire rst_n,\n  input  wire [7:0] data_in,\n  output reg  [7:0] data_out\n);\n\n  // TODO: implement ${mod}\n  always @(posedge clk or negedge rst_n) begin\n    if (!rst_n) data_out <= 8'b0;\n    else        data_out <= data_in;\n  end\n\nendmodule`;
}

type Errors = { name?: string; description?: string; general?: string };

export default function NewDesign() {
  const nav = useNavigate();
  const { create, saving } = useCreateDesign();

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [pdk, setPdk] = useState("sky130");
  const [strategy, setStrategy] = useState("SV_MODULAR");
  const [clock, setClock] = useState(50);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [skipOL, setSkipOL] = useState(false);
  const [skipSpice, setSkipSpice] = useState(false);
  const [strictGate, setStrictGate] = useState(false);
  const [errors, setErrors] = useState<Errors>({});

  const validate = (): boolean => {
    const e: Errors = {};
    if (!name.trim()) e.name = "Design name is required";
    if (!description.trim()) e.description = "Description is required";
    else if (description.trim().length < 10) e.description = "Description must be at least 10 characters";
    setErrors(e);
    return Object.keys(e).length === 0;
  };

  const handleSubmit = async () => {
    if (!validate()) return;
    try {
      const d = await create({
        name: name.trim(), description: description.trim(), pdk, strategy,
        clock_mhz: clock, status: "INIT", rtl_code: stubRtl(name.trim()),
      });
      nav(`/design/${d.id}`);
    } catch (err: any) {
      setErrors((p) => ({ ...p, general: err.message ?? "Failed to create design" }));
    }
  };

  const applyTemplate = (t: (typeof TEMPLATES)[number]) => {
    setName(t.name); setDescription(t.desc); setPdk(t.pdk); setClock(t.clock);
    setErrors({});
  };

  const fieldErr = (key: keyof Errors) =>
    errors[key] ? <p className="text-red-400 text-xs mt-1">{errors[key]}</p> : null;
  const errBorder = (key: keyof Errors) => (errors[key] ? "border-red-500/70 focus:ring-red-500/40" : "");

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="p-2 rounded-lg bg-chip-600/20"><Cpu className="h-5 w-5 text-chip-400" /></div>
        <div>
          <h1 className="text-xl font-bold tracking-tight">New Design</h1>
          <p className="text-xs text-slate-500">Configure and launch a chip design pipeline</p>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* ── Left: Config ──────────────────────────── */}
        <div className="glass-panel p-5 space-y-5">
          <h2 className="text-sm font-semibold text-slate-300 flex items-center gap-2">
            <Layers className="h-4 w-4 text-chip-400" /> Design Configuration
          </h2>

          {/* Name */}
          <div>
            <label className="text-xs font-medium text-slate-400">Design Name</label>
            <input className={`input-field mt-1 ${errBorder("name")}`} placeholder="e.g. uart_tx, spi_master, risc_v_core"
              value={name} onChange={(e) => { setName(e.target.value); setErrors((p) => ({ ...p, name: undefined })); }} />
            {fieldErr("name")}
          </div>

          {/* Description */}
          <div>
            <label className="text-xs font-medium text-slate-400">Description</label>
            <textarea rows={4} className={`input-field mt-1 resize-none ${errBorder("description")}`}
              placeholder="Describe your chip in natural language..." value={description}
              onChange={(e) => { setDescription(e.target.value); setErrors((p) => ({ ...p, description: undefined })); }} />
            {fieldErr("description")}
          </div>

          {/* PDK */}
          <div>
            <label className="text-xs font-medium text-slate-400">Target PDK</label>
            <select className="input-field mt-1" value={pdk} onChange={(e) => setPdk(e.target.value)}>
              {PDK_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label} — {o.node}</option>
              ))}
            </select>
          </div>

          {/* Strategy */}
          <div>
            <label className="text-xs font-medium text-slate-400 mb-2 block">Build Strategy</label>
            <div className="flex gap-3">
              {([["SV_MODULAR", "SystemVerilog Modular"], ["VERILOG_CLASSIC", "Verilog Classic"]] as const).map(([v, l]) => (
                <label key={v} className={`flex-1 glass-panel-sm px-3 py-2.5 flex items-center gap-2 cursor-pointer transition-all
                  ${strategy === v ? "border-chip-500/70 bg-chip-600/10" : "hover:border-slate-600"}`}>
                  <input type="radio" name="strategy" className="accent-sky-500" checked={strategy === v}
                    onChange={() => setStrategy(v)} />
                  <span className="text-xs font-medium text-slate-200">{l}</span>
                </label>
              ))}
            </div>
          </div>

          {/* Clock */}
          <div>
            <label className="text-xs font-medium text-slate-400">Clock Frequency</label>
            <div className="flex items-center gap-2 mt-1">
              <input type="number" className="input-field" min={1} value={clock}
                onChange={(e) => setClock(Number(e.target.value) || 1)} />
              <span className="text-xs text-slate-500 font-mono shrink-0">MHz</span>
            </div>
          </div>

          {/* Advanced */}
          <button className="flex items-center gap-2 text-xs text-slate-400 hover:text-slate-200 transition-colors"
            onClick={() => setShowAdvanced(!showAdvanced)}>
            <Settings2 className="h-3.5 w-3.5" />
            Advanced Settings
            <ChevronRight className={`h-3 w-3 transition-transform ${showAdvanced ? "rotate-90" : ""}`} />
          </button>
          {showAdvanced && (
            <div className="glass-panel-sm p-3 space-y-2">
              {([["Skip OpenLane", skipOL, setSkipOL], ["Skip SPICE", skipSpice, setSkipSpice],
                ["Strict Gate Checking", strictGate, setStrictGate]] as [string, boolean, (v: boolean) => void][]).map(
                ([label, val, set]) => (
                  <label key={label} className="flex items-center gap-2 text-xs text-slate-300 cursor-pointer">
                    <input type="checkbox" className="accent-sky-500" checked={val} onChange={(e) => set(e.target.checked)} />
                    {label}
                  </label>
                ),
              )}
            </div>
          )}
        </div>

        {/* ── Right: Templates & Preview ─────────── */}
        <div className="space-y-6">
          {/* Quick Templates */}
          <div className="glass-panel p-5">
            <h2 className="text-sm font-semibold text-slate-300 flex items-center gap-2 mb-3">
              <Sparkles className="h-4 w-4 text-amber-400" /> Quick Templates
            </h2>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
              {TEMPLATES.map((t) => (
                <button key={t.name} onClick={() => applyTemplate(t)}
                  className="glass-panel-sm px-3 py-2.5 text-left hover:border-chip-500/50 transition-all group">
                  <p className="text-xs font-semibold text-slate-200 group-hover:text-chip-300 font-mono">{t.name}</p>
                  <p className="text-[10px] text-slate-500 mt-0.5 line-clamp-1">{t.desc}</p>
                </button>
              ))}
            </div>
          </div>

          {/* RTL Preview */}
          <div className="glass-panel p-5">
            <h2 className="text-sm font-semibold text-slate-300 flex items-center gap-2 mb-3">
              <Zap className="h-4 w-4 text-emerald-400" /> RTL Preview
            </h2>
            <CodeEditor code={name.trim() ? stubRtl(name.trim()) : ""} readOnly height="220px" />
          </div>
        </div>
      </div>

      {/* ── Actions ──────────────────────────────── */}
      {errors.general && (
        <div className="glass-panel-sm border-red-500/50 bg-red-500/5 px-4 py-2 text-xs text-red-300">{errors.general}</div>
      )}
      <div className="flex items-center justify-end gap-3">
        <button className="btn-secondary" onClick={() => nav("/")}>Cancel</button>
        <button className="btn-primary flex items-center gap-2" disabled={saving} onClick={handleSubmit}>
          <Play className="h-4 w-4" />
          {saving ? "Creating…" : "Create Design"}
        </button>
      </div>
    </div>
  );
}
