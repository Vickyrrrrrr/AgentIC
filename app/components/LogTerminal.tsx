import { useEffect, useRef } from 'react';

interface LogTerminalProps {
  logs: string[];
  title?: string;
}

function classifyLine(line: string): string {
  const lower = line.toLowerCase();
  if (lower.includes('error') || lower.includes('fail')) return 'text-red-400';
  if (lower.includes('warning')) return 'text-amber-400';
  if (lower.includes('pass') || lower.includes('ok')) return 'text-emerald-400';
  return 'text-slate-300';
}

export default function LogTerminal({
  logs,
  title = 'Pipeline Logs',
}: LogTerminalProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs]);

  return (
    <div className="rounded-xl border border-slate-700/50 overflow-hidden">
      {/* Header bar */}
      <div className="flex items-center gap-2 bg-slate-900 px-4 py-2 border-b border-slate-700/50">
        <span className="relative flex h-2.5 w-2.5">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
          <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-emerald-500" />
        </span>
        <span className="text-xs font-medium uppercase tracking-wider text-slate-400">
          {title}
        </span>
        <span className="text-[10px] text-slate-500">live</span>
      </div>

      {/* Log body */}
      <div className="max-h-[400px] overflow-y-auto bg-slate-950 p-4 font-mono text-sm leading-relaxed">
        {logs.length === 0 && (
          <span className="text-slate-600 italic">Waiting for output…</span>
        )}
        {logs.map((line, idx) => (
          <div key={idx} className="flex gap-3">
            <span className="select-none text-slate-600 w-8 text-right shrink-0">
              {idx + 1}
            </span>
            <span className={classifyLine(line)}>{line}</span>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
