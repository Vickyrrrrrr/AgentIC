import { useState } from "react";
import { Outlet, NavLink, useLocation } from "react-router-dom";
import {
  Cpu, LayoutDashboard, Plus, ChevronLeft, ChevronRight,
  Layers, Activity, Menu, X, Zap, GitBranch,
} from "lucide-react";

const navItems = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/new", label: "New Design", icon: Plus },
];

const pipelineStages = ["Spec", "RTL", "Verify", "Synth", "STA", "Signoff"];

const linkClass = ({ isActive }: { isActive: boolean }) =>
  `flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-all duration-200 ${
    isActive
      ? "bg-chip-600/20 text-chip-400 border-l-2 border-chip-500"
      : "text-slate-400 hover:text-slate-200 hover:bg-slate-800/60"
  }`;

export default function Layout() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const location = useLocation();

  return (
    <div className="flex h-screen bg-slate-950 text-slate-100 overflow-hidden">
      {/* Mobile overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/60 backdrop-blur-sm lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside
        className={`fixed inset-y-0 left-0 z-40 w-64 flex flex-col border-r border-slate-800/80
          bg-slate-950 transition-transform duration-300 lg:static lg:translate-x-0
          ${sidebarOpen ? "translate-x-0" : "-translate-x-full"}`}
      >
        {/* Sidebar header */}
        <div className="flex items-center justify-between px-4 h-14 border-b border-slate-800/80">
          <div className="flex items-center gap-2">
            <Cpu className="h-5 w-5 text-chip-400" />
            <span className="font-bold tracking-tight text-sm">AgentIC</span>
            <span className="text-[10px] text-slate-500 font-mono">Harness</span>
          </div>
          <button
            className="btn-ghost p-1 lg:hidden"
            onClick={() => setSidebarOpen(false)}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Nav links */}
        <nav className="flex-1 flex flex-col gap-1 px-3 py-4 overflow-y-auto">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={linkClass}
              onClick={() => setSidebarOpen(false)}
            >
              <item.icon className="h-4 w-4 shrink-0" />
              {item.label}
            </NavLink>
          ))}

          {/* Pipeline stages section */}
          <div className="mt-6 mb-2 px-1">
            <p className="text-[10px] font-semibold tracking-widest text-slate-600 uppercase">
              Pipeline Stages
            </p>
          </div>
          <div className="flex flex-col gap-0.5 px-1">
            {pipelineStages.map((stage, i) => (
              <div key={stage} className="flex items-center gap-2 py-1 text-[11px] text-slate-500">
                <GitBranch className="h-3 w-3 text-slate-600" />
                <span className="font-mono">{stage}</span>
                {i < pipelineStages.length - 1 && (
                  <ChevronRight className="h-2.5 w-2.5 text-slate-700 ml-auto" />
                )}
              </div>
            ))}
          </div>
        </nav>

        {/* Sidebar bottom – plugin badge */}
        <div className="px-3 pb-4">
          <div className="glass-panel-sm px-3 py-2.5 flex items-center gap-2">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
            </span>
            <div className="flex flex-col">
              <span className="text-[11px] font-semibold text-slate-300">DSH Plugin</span>
              <span className="text-[10px] text-slate-500">VLSI Tools: 11 registered</span>
            </div>
          </div>
        </div>
      </aside>

      {/* Main area */}
      <div className="flex flex-1 flex-col min-w-0">
        {/* Header */}
        <header className="flex items-center justify-between h-14 px-4 border-b border-slate-800/80 bg-slate-950/90 backdrop-blur-md shrink-0">
          <div className="flex items-center gap-3">
            <button
              className="btn-ghost p-1.5 lg:hidden"
              onClick={() => setSidebarOpen(true)}
            >
              <Menu className="h-5 w-5" />
            </button>
            <div className="hidden sm:flex items-center gap-2 text-sm text-slate-400">
              <Layers className="h-4 w-4" />
              <span className="font-mono">
                {location.pathname === "/" ? "dashboard" : location.pathname.slice(1)}
              </span>
            </div>
          </div>

          <div className="flex items-center gap-2 text-[11px] text-slate-500">
            <Zap className="h-3.5 w-3.5 text-chip-500" />
            <span>Powered by <span className="text-slate-400 font-medium">DeepSeek Harness</span></span>
          </div>
        </header>

        {/* Content */}
        <main className="flex-1 overflow-y-auto p-4 md:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
