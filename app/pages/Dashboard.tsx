import { useState } from "react";
import { Link } from "react-router-dom";
import {
  Cpu, Plus, Clock, CheckCircle2, XCircle, Loader2,
  Layers, Zap, ArrowRight, Trash2,
} from "lucide-react";
import { useDesigns, useDashboardStats } from "@app/lib/hooks";
import { supabase } from "@app/lib/supabase";
import type { Design } from "@app/lib/types";
import { PDK_OPTIONS } from "@app/lib/types";

function timeAgo(date: string) {
  const s = Math.floor((Date.now() - new Date(date).getTime()) / 1000);
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

function statusBadge(status: string) {
  const map: Record<string, string> = {
    SUCCESS: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
    FAIL: "bg-red-500/15 text-red-400 border-red-500/30",
    INIT: "bg-slate-500/15 text-slate-400 border-slate-500/30",
  };
  const cls = map[status] ?? "bg-amber-500/15 text-amber-400 border-amber-500/30";
  return (
    <span className={`text-[11px] font-medium px-2 py-0.5 rounded-full border ${cls}`}>
      {status}
    </span>
  );
}

const statCards = [
  { key: "total", label: "Total Designs", icon: Layers, color: "chip" },
  { key: "passing", label: "Passing", icon: CheckCircle2, color: "emerald" },
  { key: "failing", label: "Failing", icon: XCircle, color: "red" },
  { key: "running", label: "In Progress", icon: Loader2, color: "amber" },
] as const;

const colorMap: Record<string, { bg: string; text: string }> = {
  chip: { bg: "bg-chip-500/15", text: "text-chip-400" },
  emerald: { bg: "bg-emerald-500/15", text: "text-emerald-400" },
  red: { bg: "bg-red-500/15", text: "text-red-400" },
  amber: { bg: "bg-amber-500/15", text: "text-amber-400" },
};

export default function Dashboard() {
  const { designs, loading, refetch } = useDesigns();
  const stats = useDashboardStats();
  const [deleting, setDeleting] = useState<string | null>(null);

  const handleDelete = async (e: React.MouseEvent, id: string) => {
    e.preventDefault();
    e.stopPropagation();
    if (!confirm("Delete this design? This cannot be undone.")) return;
    setDeleting(id);
    await supabase.from("designs").delete().eq("id", id);
    setDeleting(null);
    refetch();
  };

  const pdkLabel = (value: string) =>
    PDK_OPTIONS.find((p) => p.value === value)?.label ?? value;

  return (
    <div className="max-w-5xl mx-auto space-y-8 animate-slide-up">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Cpu className="h-6 w-6 text-chip-400" />
            Chip Design Dashboard
          </h1>
          <p className="text-sm text-slate-400 mt-1">AI-powered autonomous VLSI design pipeline</p>
        </div>
        <Link to="/new" className="btn-primary flex items-center gap-2 w-fit">
          <Plus className="h-4 w-4" /> New Design
        </Link>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {statCards.map(({ key, label, icon: Icon, color }) => {
          const c = colorMap[color];
          return (
            <div key={key} className="glass-panel p-5 flex flex-col gap-2">
              <div className={`w-9 h-9 rounded-lg ${c.bg} flex items-center justify-center`}>
                <Icon className={`h-4.5 w-4.5 ${c.text}`} />
              </div>
              <span className="text-2xl font-bold">{stats[key]}</span>
              <span className="text-xs text-slate-400">{label}</span>
            </div>
          );
        })}
      </div>

      {/* Recent Designs */}
      <div className="space-y-4">
        <h2 className="text-lg font-semibold">Recent Designs</h2>

        {loading ? (
          <div className="space-y-3">
            {[0, 1, 2].map((i) => (
              <div key={i} className="glass-panel h-24 animate-pulse" />
            ))}
          </div>
        ) : designs.length === 0 ? (
          <div className="glass-panel p-8 text-center space-y-3">
            <p className="text-slate-400">No designs yet. Create your first chip design to get started.</p>
            <Link to="/new" className="btn-primary inline-flex items-center gap-2">
              <Plus className="h-4 w-4" /> New Design
            </Link>
          </div>
        ) : (
          <div className="space-y-3">
            {designs.map((d) => (
              <Link
                key={d.id}
                to={`/design/${d.id}`}
                className="glass-panel p-4 flex items-center gap-4 hover:border-slate-600 transition-colors group"
              >
                <div className="flex-1 min-w-0 space-y-1">
                  <p className="text-lg font-semibold truncate">{d.name}</p>
                  {d.description && (
                    <p className="text-sm text-slate-400 truncate">{d.description}</p>
                  )}
                  <div className="flex flex-wrap items-center gap-2 pt-1">
                    <span className="text-[11px] px-2 py-0.5 rounded-full bg-slate-800 text-slate-300 border border-slate-700/50">
                      {pdkLabel(d.pdk)}
                    </span>
                    <span className="text-[11px] text-slate-500 flex items-center gap-1">
                      <Zap className="h-3 w-3" /> {d.clock_mhz} MHz
                    </span>
                    {statusBadge(d.status)}
                    <span className="text-[11px] text-slate-500 flex items-center gap-1">
                      <Clock className="h-3 w-3" /> {timeAgo(d.created_at)}
                    </span>
                  </div>
                </div>

                <button
                  onClick={(e) => handleDelete(e, d.id)}
                  disabled={deleting === d.id}
                  className="btn-ghost p-2 text-slate-500 hover:text-red-400 shrink-0"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
                <ArrowRight className="h-5 w-5 text-slate-600 group-hover:text-slate-400 transition-colors shrink-0" />
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
