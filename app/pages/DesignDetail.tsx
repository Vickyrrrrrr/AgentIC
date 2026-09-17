import { useState, useMemo } from "react";
import { useParams, Link } from "react-router-dom";
import {
  ArrowLeft, Play, Loader2, Code2, GitBranch, BarChart3,
  FileText, Terminal, Clock, Cpu, Zap, Layers,
} from "lucide-react";
import { useDesign, useAddRun, useUpdateDesign } from "@app/lib/hooks";
import { PIPELINE_STAGES, type StageStatus } from "@app/lib/types";
import PipelineGraph from "@app/components/PipelineGraph";
import CodeEditor from "@app/components/CodeEditor";
import MetricsPanel from "@app/components/MetricsPanel";
import LogTerminal from "@app/components/LogTerminal";

const TABS = [
  { id: "rtl", label: "RTL Code", icon: Code2 },
  { id: "testbench", label: "Testbench", icon: FileText },
  { id: "constraints", label: "Constraints", icon: Clock },
  { id: "metrics", label: "Metrics", icon: BarChart3 },
  { id: "logs", label: "Logs", icon: Terminal },
] as const;

type TabId = (typeof TABS)[number]["id"];

const STAGE_TO_TAB: Record<string, TabId> = {
  RTL_GEN: "rtl", VERIFICATION: "testbench", SDC_GEN: "constraints",
  SYNTHESIS: "metrics", TIMING_ANALYSIS: "metrics", POWER_ANALYSIS: "metrics",
  SIGNOFF: "logs",
};

const SAMPLE_METRICS: Record<string, unknown>[] = [
  { cell_count: 1842 }, { area_um2: 48210 }, {},
  {}, {}, { cell_count: 4200, area_um2: 96500, max_freq_mhz: 150 },
  { wns_setup: -0.12 }, {}, {}, {}, { total_power_uw: 320 }, {},
];

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    SUCCESS: "bg-emerald-500/20 text-emerald-400",
    FAIL: "bg-red-500/20 text-red-400",
    RUNNING: "bg-chip-500/20 text-chip-400",
    INIT: "bg-slate-500/20 text-slate-400",
  };
  const cls = map[status] ?? map.INIT;
  return <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${cls}`}>{status}</span>;
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="flex items-center justify-center py-16 text-slate-500 text-sm italic">
      {text}
    </div>
  );
}

function Skeleton() {
  return (
    <div className="space-y-6">
      {[1, 2, 3].map((i) => (
        <div key={i} className="glass-panel h-40 animate-pulse rounded-xl bg-slate-800/50" />
      ))}
    </div>
  );
}

export default function DesignDetail() {
  const { id } = useParams<{ id: string }>();
  const { design, runs, loading, refetch } = useDesign(id);
  const { update } = useUpdateDesign();
  const { add } = useAddRun();
  const [tab, setTab] = useState<TabId>("rtl");
  const [running, setRunning] = useState(false);

  const allMetrics = useMemo(
    () => runs.reduce<Record<string, unknown>>((acc, r) => ({ ...acc, ...r.metrics }), {}),
    [runs],
  );

  const allLogs = useMemo(
    () => runs.flatMap((r) => r.diagnostics ?? []),
    [runs],
  );

  const runPipeline = async () => {
    if (!design || running) return;
    setRunning(true);
    await update(design.id, { status: "RUNNING" });

    for (let i = 0; i < PIPELINE_STAGES.length; i++) {
      const stage = PIPELINE_STAGES[i];
      const duration_ms = Math.floor(Math.random() * 1800) + 200;
      await new Promise((r) => setTimeout(r, 500));
      await add({
        design_id: design.id,
        stage: stage.id,
        status: "PASS" as StageStatus,
        failure_class: null,
        diagnostics: [`[${stage.label}] completed in ${duration_ms}ms`],
        metrics: (SAMPLE_METRICS[i] ?? {}) as Record<string, unknown>,
        artifacts: {},
        duration_ms,
      });
    }

    await update(design.id, { status: "SUCCESS" });
    await refetch();
    setRunning(false);
  };

  if (loading) return <Skeleton />;
  if (!design) return <EmptyState text="Design not found." />;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center gap-3">
        <Link to="/" className="text-slate-400 hover:text-slate-200 transition-colors">
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <h1 className="text-2xl font-bold text-slate-100">{design.name}</h1>
        <span className="rounded-full bg-chip-500/20 text-chip-400 px-2.5 py-0.5 text-xs font-medium">
          {design.pdk}
        </span>
        <span className="rounded-full bg-slate-500/20 text-slate-300 px-2.5 py-0.5 text-xs font-medium flex items-center gap-1">
          <Clock className="h-3 w-3" /> {design.clock_mhz} MHz
        </span>
        <StatusBadge status={design.status} />
        <div className="ml-auto">
          <button onClick={runPipeline} disabled={running} className="btn-primary flex items-center gap-2">
            {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            {running ? "Running…" : "Run Pipeline"}
          </button>
        </div>
      </div>

      {/* Pipeline Graph */}
      <div className="glass-panel overflow-hidden rounded-xl">
        <PipelineGraph runs={runs} onStageClick={(s) => setTab(STAGE_TO_TAB[s] ?? "rtl")} />
      </div>

      {/* Tabs */}
      <div className="flex gap-1 overflow-x-auto border-b border-slate-700/50 pb-px">
        {TABS.map((t) => {
          const Icon = t.icon;
          const active = tab === t.id;
          return (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`flex items-center gap-1.5 whitespace-nowrap px-4 py-2 text-sm font-medium transition-colors ${
                active
                  ? "border-b-2 border-chip-500 text-chip-400"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              <Icon className="h-4 w-4" /> {t.label}
            </button>
          );
        })}
      </div>

      {/* Tab panels */}
      <div className="glass-panel rounded-xl p-4">
        {tab === "rtl" &&
          (design.rtl_code
            ? <CodeEditor code={design.rtl_code} language="systemverilog" readOnly />
            : <EmptyState text="No RTL generated yet. Run the pipeline to generate code." />)}

        {tab === "testbench" &&
          (design.testbench_code
            ? <CodeEditor code={design.testbench_code} language="systemverilog" readOnly />
            : <EmptyState text="No testbench generated yet. Run the pipeline to generate code." />)}

        {tab === "constraints" &&
          (design.sdc_code
            ? <CodeEditor code={design.sdc_code} language="tcl" readOnly />
            : <EmptyState text="No SDC constraints generated yet. Run the pipeline to generate code." />)}

        {tab === "metrics" &&
          (runs.length
            ? <MetricsPanel metrics={allMetrics} title="Aggregated Pipeline Metrics" />
            : <EmptyState text="No pipeline runs yet." />)}

        {tab === "logs" &&
          (runs.length
            ? <LogTerminal logs={allLogs} title="Pipeline Logs" />
            : <EmptyState text="No pipeline runs yet." />)}
      </div>
    </div>
  );
}
