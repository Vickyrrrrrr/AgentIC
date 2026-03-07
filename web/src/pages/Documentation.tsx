import { useEffect, useMemo, useState, useRef, useCallback } from 'react';
import axios from 'axios';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const API = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:7860').replace(/\/$/, '');

interface DocItem {
  id: string;
  title: string;
  section: string;
  summary: string;
}

interface BuildOption {
  key: string;
  type: string;
  default: string | number | boolean;
  description: string;
  values?: string[];
  min?: number;
  max?: number;
}

interface BuildOptionGroup {
  name: string;
  options: BuildOption[];
}

interface StageItem {
  state: string;
  label: string;
  icon: string;
}

type Tab = 'overview' | 'pipeline' | 'config' | 'docs';

/* ── Table of contents extractor ─────────────────────────── */
function extractTOC(md: string): { level: number; text: string; id: string }[] {
  const lines = md.split('\n');
  const toc: { level: number; text: string; id: string }[] = [];
  for (const line of lines) {
    const match = line.match(/^(#{1,4})\s+(.+)/);
    if (match) {
      const text = match[2].replace(/[`*_~\[\]]/g, '').trim();
      const id = text.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
      toc.push({ level: match[1].length, text, id });
    }
  }
  return toc;
}

export const Documentation = () => {
  const [tab, setTab] = useState<Tab>('overview');
  const [docs, setDocs] = useState<DocItem[]>([]);
  const [selectedDoc, setSelectedDoc] = useState<string>('readme');
  const [, setDocTitle] = useState<string>('Documentation');
  const [content, setContent] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [optionGroups, setOptionGroups] = useState<BuildOptionGroup[]>([]);
  const [stages, setStages] = useState<StageItem[]>([]);
  const contentRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const loadIndex = async () => {
      try {
        const [docsRes, optionsRes, schemaRes] = await Promise.all([
          axios.get(`${API}/docs/index`),
          axios.get(`${API}/build/options`),
          axios.get(`${API}/pipeline/schema`),
        ]);
        const docsData: DocItem[] = docsRes.data?.docs || [];
        setDocs(docsData);
        setOptionGroups(optionsRes.data?.groups || []);
        setStages(schemaRes.data?.stages || []);
        if (docsData.length > 0) {
          setSelectedDoc(docsData.find((d) => d.id === 'readme')?.id || docsData[0].id);
        }
      } catch {
        setContent('Failed to load documentation index.');
        setLoading(false);
      }
    };
    loadIndex();
  }, []);

  useEffect(() => {
    if (!selectedDoc) return;
    setLoading(true);
    axios.get(`${API}/docs/content/${selectedDoc}`)
      .then((res) => {
        setDocTitle(res.data?.title || selectedDoc);
        setContent(res.data?.content || 'No content available.');
      })
      .catch(() => {
        setDocTitle('Documentation');
        setContent('Failed to load document content.');
      })
      .finally(() => setLoading(false));
  }, [selectedDoc]);

  const toc = useMemo(() => extractTOC(content), [content]);

  const sections = useMemo(() => {
    const grouped = new Map<string, DocItem[]>();
    for (const d of docs) {
      const key = d.section || 'General';
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key)!.push(d);
    }
    return Array.from(grouped.entries());
  }, [docs]);

  const filteredOptions = useMemo(() => {
    if (!searchQuery.trim()) return optionGroups;
    const q = searchQuery.toLowerCase();
    return optionGroups
      .map((g) => ({
        ...g,
        options: g.options.filter(
          (o) =>
            o.key.toLowerCase().includes(q) ||
            o.description.toLowerCase().includes(q)
        ),
      }))
      .filter((g) => g.options.length > 0);
  }, [optionGroups, searchQuery]);

  const scrollToHeading = useCallback((id: string) => {
    const el = contentRef.current?.querySelector(`#${CSS.escape(id)}`);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, []);

  const tabs: { key: Tab; label: string; icon: string }[] = [
    { key: 'overview', label: 'Overview', icon: '📋' },
    { key: 'pipeline', label: 'Pipeline', icon: '🔬' },
    { key: 'config', label: 'Configuration', icon: '⚙️' },
    { key: 'docs', label: 'Documents', icon: '📄' },
  ];

  return (
    <div className="adoc-root">
      {/* ── Header ────────────────────────────────────── */}
      <header className="adoc-hero">
        <div className="adoc-hero-inner">
          <div className="adoc-hero-badge">Technical Reference Manual</div>
          <h1 className="adoc-hero-title">AgentIC Documentation</h1>
          <p className="adoc-hero-sub">
            Autonomous silicon design platform — architecture, pipeline specification,
            configuration reference, and operational guides.
          </p>
          <div className="adoc-hero-stats">
            <span>{stages.length} pipeline stages</span>
            <span className="adoc-hero-dot">·</span>
            <span>{optionGroups.reduce((a, g) => a + g.options.length, 0)} configurable parameters</span>
            <span className="adoc-hero-dot">·</span>
            <span>{docs.length} reference documents</span>
          </div>
        </div>
      </header>

      {/* ── Tab bar ───────────────────────────────────── */}
      <nav className="adoc-tabs">
        {tabs.map((t) => (
          <button
            key={t.key}
            className={`adoc-tab ${tab === t.key ? 'active' : ''}`}
            onClick={() => setTab(t.key)}
          >
            <span className="adoc-tab-icon">{t.icon}</span>
            {t.label}
          </button>
        ))}
      </nav>

      {/* ══════════════════════════════════════════════════ */}
      {/*  TAB: OVERVIEW                                     */}
      {/* ══════════════════════════════════════════════════ */}
      {tab === 'overview' && (
        <div className="adoc-section">
          <div className="adoc-overview-grid">
            {/* Abstract */}
            <div className="adoc-paper-card adoc-abstract">
              <h2>Abstract</h2>
              <p>
                <strong>AgentIC</strong> is an agentic, LLM-driven autonomous chip design platform.
                Given a natural‑language specification, it generates synthesisable RTL, auto‑heals
                syntax and lint errors, runs formal property verification, closes coverage to
                profile‑based thresholds, generates timing constraints, and drives physical
                implementation through OpenLane to produce GDSII — all without human intervention.
              </p>
              <p>
                The platform features a <em>self‑healing orchestrator</em> that tracks per‑stage
                exception budgets, backs up the best testbench for anti‑regression, and applies
                bounded retry loops with deterministic fallbacks at every critical gate.
              </p>
            </div>

            {/* Key Capabilities */}
            <div className="adoc-paper-card">
              <h2>Key Capabilities</h2>
              <div className="adoc-cap-grid">
                {[
                  { icon: '🧠', title: 'AI‑Driven RTL Generation', desc: 'SystemVerilog or Verilog‑2005 from natural language via multi-agent collaboration.' },
                  { icon: '🔁', title: 'Self‑Healing Pipeline', desc: 'Per‑stage guards, fingerprint dedup, bounded retries, and deterministic fallbacks.' },
                  { icon: '📊', title: 'Formal Verification', desc: 'Assertion generation, formal property checking, and clock-domain analysis.' },
                  { icon: '📈', title: 'Coverage Closure', desc: 'Profile‑based (balanced / aggressive / relaxed) with anti‑regression backup.' },
                  { icon: '🏗️', title: 'Physical Implementation', desc: 'RTL-to-GDSII flow integration with convergence review and optimization loops.' },
                  { icon: '✅', title: 'Silicon Signoff', desc: 'DRC/LVS, STA, power/IR‑drop, equivalence checks before tapeout.' },
                ].map((cap) => (
                  <div className="adoc-cap-item" key={cap.title}>
                    <span className="adoc-cap-icon">{cap.icon}</span>
                    <div>
                      <strong>{cap.title}</strong>
                      <p>{cap.desc}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Quality Gates */}
            <div className="adoc-paper-card adoc-full-width">
              <h2>Quality Gates</h2>
              <div className="adoc-table-wrap">
                <table className="adoc-table">
                  <thead>
                    <tr>
                      <th>Gate</th>
                      <th>Check</th>
                      <th>Recovery</th>
                      <th>Fallback</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[
                      ['Syntax', 'Static Analysis', 'Automated Recovery', 'Strategy pivot'],
                      ['TB Compile', 'Compilation Check', 'Automated Recovery', 'Deterministic template TB'],
                      ['Simulation', 'Simulation Check', 'Automated Recovery', 'Dedup + skip'],
                      ['Formal', 'Formal Property Verification', 'Automated Recovery', 'Graceful degrade to coverage'],
                      ['Coverage', 'Profile-based thresholds', 'Automated Recovery', 'Restore best snapshot'],
                      ['DRC/LVS', 'Physical signoff reports', 'Automated Recovery', 'Fail-closed or pivot'],
                    ].map(([gate, check, heal, fallback]) => (
                      <tr key={gate}>
                        <td><strong>{gate}</strong></td>
                        <td><code>{check}</code></td>
                        <td>{heal}</td>
                        <td>{fallback}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ══════════════════════════════════════════════════ */}
      {/*  TAB: PIPELINE                                     */}
      {/* ══════════════════════════════════════════════════ */}
      {tab === 'pipeline' && (
        <div className="adoc-section">
          <div className="adoc-paper-card">
            <h2>Build Pipeline — Stage Reference</h2>
            <p className="adoc-meta-text">
              The AgentIC orchestrator executes a deterministic state‑machine pipeline.
              Each stage has bounded retries, per‑stage exception isolation, and configurable quality gates.
            </p>
            <div className="adoc-pipeline-list">
              {stages.map((stage, idx) => (
                <div className="adoc-pipeline-stage" key={stage.state}>
                  <div className="adoc-stage-num">{String(idx + 1).padStart(2, '0')}</div>
                  <div className="adoc-stage-connector" />
                  <div className="adoc-stage-body">
                    <div className="adoc-stage-header">
                      <span className="adoc-stage-icon">{stage.icon}</span>
                      <strong>{stage.label}</strong>
                      <code className="adoc-stage-key">{stage.state}</code>
                    </div>
                    <p className="adoc-stage-desc">
                      {stageDescriptions[stage.state] || 'Pipeline stage.'}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Flow Diagram */}
          <div className="adoc-paper-card">
            <h2>State Transition Flow</h2>
            <div className="adoc-flow-diagram">
              {stages.map((s, i) => (
                <span key={s.state} className="adoc-flow-node">
                  <span className="adoc-flow-badge">{s.icon}</span>
                  <span className="adoc-flow-label">{s.state}</span>
                  {i < stages.length - 1 && <span className="adoc-flow-arrow">→</span>}
                </span>
              ))}
            </div>
            <p className="adoc-meta-text" style={{ marginTop: '0.75rem' }}>
              <strong>Convergence loops:</strong> SIGNOFF → ECO_PATCH → HARDENING → CONVERGENCE_REVIEW → SIGNOFF. <br />
              <strong>Terminal states:</strong> SUCCESS, FAIL.
            </p>
          </div>
        </div>
      )}

      {/* ══════════════════════════════════════════════════ */}
      {/*  TAB: CONFIGURATION                                */}
      {/* ══════════════════════════════════════════════════ */}
      {tab === 'config' && (
        <div className="adoc-section">
          <div className="adoc-config-header">
            <h2>Configuration Reference</h2>
            <input
              className="adoc-search"
              type="text"
              placeholder="Search parameters…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>

          {filteredOptions.map((group) => (
            <div className="adoc-paper-card adoc-config-group" key={group.name}>
              <h3 className="adoc-config-group-title">{group.name}</h3>
              <div className="adoc-table-wrap">
                <table className="adoc-table adoc-config-table">
                  <thead>
                    <tr>
                      <th>Parameter</th>
                      <th>Type</th>
                      <th>Default</th>
                      <th>Range / Values</th>
                      <th>Description</th>
                    </tr>
                  </thead>
                  <tbody>
                    {group.options.map((opt) => (
                      <tr key={opt.key}>
                        <td><code className="adoc-param-key">{opt.key}</code></td>
                        <td><span className="adoc-type-badge">{opt.type}</span></td>
                        <td><code>{String(opt.default)}</code></td>
                        <td>
                          {opt.values
                            ? opt.values.map((v) => (
                                <span className="adoc-enum-val" key={v}>{v}</span>
                              ))
                            : opt.min !== undefined
                            ? `${opt.min} – ${opt.max}`
                            : '—'}
                        </td>
                        <td>{opt.description}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ══════════════════════════════════════════════════ */}
      {/*  TAB: DOCUMENTS (markdown reader)                  */}
      {/* ══════════════════════════════════════════════════ */}
      {tab === 'docs' && (
        <div className="adoc-docs-layout">
          {/* Left nav */}
          <aside className="adoc-docs-nav">
            <div className="adoc-docs-nav-title">Documents</div>
            {sections.map(([section, items]) => (
              <div className="adoc-docs-group" key={section}>
                <div className="adoc-docs-section-label">{section}</div>
                {items.map((doc) => (
                  <button
                    key={doc.id}
                    className={`adoc-docs-link ${selectedDoc === doc.id ? 'active' : ''}`}
                    onClick={() => setSelectedDoc(doc.id)}
                  >
                    <span className="adoc-docs-link-title">{doc.title}</span>
                    <span className="adoc-docs-link-sub">{doc.summary}</span>
                  </button>
                ))}
              </div>
            ))}
          </aside>

          {/* Content */}
          <main className="adoc-docs-content" ref={contentRef}>
            {loading ? (
              <div className="adoc-loading">
                <span className="spinner" /> Loading document…
              </div>
            ) : (
              <article className="adoc-prose">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    h1: ({ children, ...props }) => {
                      const id = String(children).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
                      return <h1 id={id} {...props}>{children}</h1>;
                    },
                    h2: ({ children, ...props }) => {
                      const id = String(children).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
                      return <h2 id={id} {...props}>{children}</h2>;
                    },
                    h3: ({ children, ...props }) => {
                      const id = String(children).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
                      return <h3 id={id} {...props}>{children}</h3>;
                    },
                  }}
                >{content}</ReactMarkdown>
              </article>
            )}
          </main>

          {/* Right TOC */}
          <aside className="adoc-toc">
            <div className="adoc-toc-title">On This Page</div>
            {toc.map((item, i) => (
              <button
                key={i}
                className="adoc-toc-link"
                style={{ paddingLeft: `${(item.level - 1) * 0.75 + 0.5}rem` }}
                onClick={() => scrollToHeading(item.id)}
              >
                {item.text}
              </button>
            ))}
          </aside>
        </div>
      )}
    </div>
  );
};

/* ── Stage descriptions ──────────────────────────── */
const stageDescriptions: Record<string, string> = {
  INIT: 'Create workspace directory structure, validate dependencies, and initialize build artifacts dictionary.',
  SPEC: 'Generate a detailed architecture specification from the natural-language prompt, including module interfaces, FSM descriptions, and clock/reset requirements.',
  RTL_GEN: 'Generate synthesizable RTL (SystemVerilog or Verilog-2005) from the architecture spec using multi-agent collaboration. Falls back to template library when available.',
  RTL_FIX: 'Run static analysis, pre-synthesis semantic checks, and iterative auto-repair. Supports strategy pivot (SV → Verilog-2005) when fixes stall.',
  VERIFICATION: 'Generate self-checking testbenches, compile, run simulation, and check for passing results. Includes static contract checking and fingerprint deduplication.',
  FORMAL_VERIFY: 'Generate formal assertions, run formal property checking. Includes clock-domain analysis.',
  COVERAGE_CHECK: 'Run coverage analysis against profile-based thresholds (line, branch, toggle, functional). Anti-regression guard restores best testbench on coverage drop.',
  REGRESSION: 'Generate and run multiple directed test scenarios (corner cases, reset stress, rapid fire) to verify robustness beyond basic functional verification.',
  SDC_GEN: 'Generate SDC timing constraints from the RTL module interface. Auto-detects clock ports and applies constraint post-processing.',
  FLOORPLAN: 'Floorplan estimation based on gate count, wire routing complexity, and PDK parameters. Produces die area and utilization targets.',
  HARDENING: 'Generate physical design configuration and run the full RTL-to-GDSII flow. Collects metrics for convergence analysis.',
  CONVERGENCE_REVIEW: 'Analyze PPA metrics (WNS, TNS, area, power, congestion) across iteration history. Determines whether to accept, resize die, or pivot strategy.',
  ECO_PATCH: 'Apply engineering change orders — gate-level patch first, RTL micro-patch fallback. Re-enters hardening loop after successful application.',
  SIGNOFF: 'Multi-dimensional check: DRC/LVS compliance, STA timing closure, power/IR-drop analysis, equivalence checking, and coverage re-validation.',
  SUCCESS: 'Build completed — all quality gates passed. GDSII, metrics, and documentation artifacts are finalized.',
  FAIL: 'Build terminated — one or more quality gates failed after exhausting retry budgets.',
};
