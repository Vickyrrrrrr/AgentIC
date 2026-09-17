import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from 'recharts';
import { Activity, Zap, Cpu, Clock, Layers, type LucideIcon } from 'lucide-react';

interface MetricsPanelProps {
  metrics: Record<string, unknown>;
  title?: string;
}

interface MetricDef {
  key: string;
  label: string;
  icon: LucideIcon;
}

const KNOWN_METRICS: MetricDef[] = [
  { key: 'cell_count', label: 'Cells', icon: Cpu },
  { key: 'area_um2', label: 'Area (µm²)', icon: Layers },
  { key: 'max_freq_mhz', label: 'Max Freq', icon: Zap },
  { key: 'wns_setup', label: 'WNS Setup', icon: Clock },
  { key: 'total_power_uw', label: 'Power (µW)', icon: Activity },
];

const CHART_COLOR = '#0ea5e9';

function formatValue(value: unknown): string {
  if (typeof value === 'number') {
    return value % 1 === 0 ? value.toLocaleString() : value.toFixed(3);
  }
  return String(value ?? '—');
}

function MetricCard({
  def,
  value,
}: {
  def: MetricDef;
  value: unknown;
}) {
  const Icon = def.icon;
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-slate-700/50 bg-slate-800/60 p-4">
      <div className="flex items-center gap-2 text-slate-400">
        <Icon className="h-4 w-4" />
        <span className="text-xs font-medium uppercase tracking-wide">
          {def.label}
        </span>
      </div>
      <span className="text-lg font-semibold text-slate-100">
        {formatValue(value)}
      </span>
    </div>
  );
}

export default function MetricsPanel({
  metrics,
  title = 'Synthesis Metrics',
}: MetricsPanelProps) {
  const knownEntries = KNOWN_METRICS.filter((m) => m.key in metrics);

  const numericEntries = Object.entries(metrics).filter(
    ([, v]) => typeof v === 'number'
  ) as [string, number][];

  const chartData = numericEntries.map(([key, value]) => {
    const known = KNOWN_METRICS.find((m) => m.key === key);
    return { name: known?.label ?? key, value };
  });

  return (
    <div className="space-y-4">
      {title && (
        <h3 className="text-sm font-semibold uppercase tracking-wider text-slate-400">
          {title}
        </h3>
      )}

      {/* Metric cards */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5">
        {knownEntries.map((def) => (
          <MetricCard key={def.key} def={def} value={metrics[def.key]} />
        ))}
      </div>

      {/* Bar chart for numeric metrics */}
      {chartData.length > 2 && (
        <div className="rounded-lg border border-slate-700/50 bg-slate-800/60 p-4">
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={chartData} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
              <XAxis
                dataKey="name"
                tick={{ fill: '#94a3b8', fontSize: 11 }}
                axisLine={{ stroke: '#334155' }}
                tickLine={false}
              />
              <YAxis
                tick={{ fill: '#94a3b8', fontSize: 11 }}
                axisLine={{ stroke: '#334155' }}
                tickLine={false}
                width={60}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: '#1e293b',
                  border: '1px solid #334155',
                  borderRadius: 8,
                  color: '#e2e8f0',
                  fontSize: 12,
                }}
                cursor={{ fill: 'rgba(148,163,184,0.08)' }}
              />
              <Bar dataKey="value" radius={[4, 4, 0, 0]}>
                {chartData.map((_, idx) => (
                  <Cell key={idx} fill={CHART_COLOR} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
