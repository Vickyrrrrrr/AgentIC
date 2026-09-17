/**
 * Static Timing Analysis (STA) stage — runs OpenSTA on gate-level netlists.
 *
 * Ported from Python: agents/sta_agent.py + orchestrator TIMING_ANALYSIS stage
 */

import { writeFile, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { runCommand, EDA_BINARIES } from "../eda/index.js";
import { StageStatus, FailureClass } from "../types/index.js";
import type { BuildContext, StageResult } from "../types/index.js";

// ── Interfaces ───────────────────────────────────────────────────────

export interface STAInput {
  netlist: string; sdc: string; libFiles: string[]; outputDir: string;
  corner?: string; mode?: string; topModule?: string; pdk?: string;
  reportPaths?: number; minPeriodNs?: number; timeout?: number;
}

export interface STAReport {
  ok: boolean; corner: string; mode: string;
  wnsSetup: number; wnsHold: number; tnsSetup: number; tnsHold: number;
  maxFreqMhz: number; reportPath: string;
  criticalPaths: Array<{ slackNs?: number; delayNs?: number; from?: string; to?: string }>;
  warnings: string[]; errors: string[];
}

export interface MultiCornerSTAResult {
  designName: string; corners: Record<string, STAReport>;
  worstWnsSetup: number; worstWnsHold: number;
  worstTnsSetup: number; worstTnsHold: number;
  maxFreqMhz: number; allCornersPass: boolean; summaryPath: string;
}

// ── TCL Script Builder ───────────────────────────────────────────────

export function buildOpenStaTcl(
  libFiles: string[], netlist: string, topModule: string,
  sdcPath: string, reportPaths: number,
): string[] {
  const lines: string[] = [
    ...libFiles.map((lib) => `read_liberty ${lib}`),
    `read_verilog ${netlist}`,
    `link_design ${topModule}`,
    `read_sdc ${sdcPath}`,
    "",
    `report_checks -path_delay max -format full_clock_expanded \\`,
    `  -fields {slew cap input_pins nets fanout} \\`,
    `  -no_line_splits -group_count ${reportPaths}`,
    "",
    `report_checks -path_delay min -format full_clock_expanded \\`,
    `  -fields {slew cap input_pins nets fanout} \\`,
    `  -no_line_splits -group_count ${reportPaths}`,
    "",
    "report_worst_slack -max",
    "report_worst_slack -min",
    `report_timing -max_paths ${reportPaths}`,
    "report_unconstrained",
    "report_clock_tree",
    "report_power",
  ];
  return lines;
}

// ── Parsers ──────────────────────────────────────────────────────────

export function parseWns(output: string, mode: string): number {
  const patterns = mode === "hold"
    ? [/worst\s+slack\s+\(min\)\s*[:\s]*([-\d.]+)/i,
       /wns\s*\(hold\)\s*[:\s]*([-\d.]+)/i,
       /worst\s+hold\s+slack\s*[:\s]*([-\d.]+)/i]
    : [/worst\s+slack\s+\(max\)\s*[:\s]*([-\d.]+)/i,
       /wns\s*\(setup\)\s*[:\s]*([-\d.]+)/i,
       /worst\s+setup\s+slack\s*[:\s]*([-\d.]+)/i];
  for (const pat of patterns) {
    const m = output.match(pat);
    if (m) return parseFloat(m[1]);
  }
  const fallback = output.match(/slack\s*[:\s]*([-\d.]+)/i);
  return fallback ? parseFloat(fallback[1]) : 0;
}

export function parseTns(output: string): number {
  for (const pat of [/tns\s*[:\s]*([-\d.]+)/i, /total\s+negative\s+slack\s*[:\s]*([-\d.]+)/i]) {
    const m = output.match(pat);
    if (m) return parseFloat(m[1]);
  }
  return 0;
}

export function parseCriticalPaths(output: string, limit: number): STAReport["criticalPaths"] {
  const paths: STAReport["criticalPaths"] = [];
  const sections = output.split(/Path\s+\d+:/i);
  for (let i = 1; i < sections.length && paths.length < limit; i++) {
    const s = sections[i];
    paths.push({
      slackNs: s.match(/slack\s*[:\s]*([-\d.]+)/i) ? parseFloat(RegExp.$1) : undefined,
      delayNs: s.match(/delay\s*[:\s]*([-\d.]+)/i) ? parseFloat(RegExp.$1) : undefined,
      from: s.match(/Startpoint:\s*(\S+)/i)?.[1],
      to: s.match(/Endpoint:\s*(\S+)/i)?.[1],
    });
  }
  return paths;
}

export function parseCtsReport(output: string): Record<string, number> {
  const result: Record<string, number> = {};
  const skewM = output.match(/clock\s+skew\s*[:\s]*([-\d.]+)/i);
  if (skewM) result["clock_skew"] = parseFloat(skewM[1]);
  const insertM = output.match(/insertion\s+delay\s*[:\s]*([-\d.]+)/i);
  if (insertM) result["insertion_delay"] = parseFloat(insertM[1]);
  return result;
}

export function freqFromWns(wns: number, periodNs: number): number {
  const effective = periodNs + wns;
  if (effective <= 0) return 0;
  return 1000 / effective;
}

// ── Core STA Runner ──────────────────────────────────────────────────

export async function runSta(input: STAInput): Promise<STAReport> {
  const corner = input.corner ?? "tt";
  const mode = input.mode ?? "func";
  const reportPaths = input.reportPaths ?? 10;
  const topModule = input.topModule ?? "top";
  const periodNs = input.minPeriodNs ?? 10;
  const timeout = input.timeout ?? 120_000;

  await mkdir(input.outputDir, { recursive: true });

  const tclLines = buildOpenStaTcl(input.libFiles, input.netlist, topModule, input.sdc, reportPaths);

  const tclPath = join(input.outputDir, `sta_${corner}_${mode}.tcl`);
  const rptPath = join(input.outputDir, `sta_${corner}_${mode}.rpt`);
  await writeFile(tclPath, tclLines.join("\n"), "utf-8");

  const result = await runCommand(EDA_BINARIES.sta(), ["-exit", tclPath], {
    cwd: input.outputDir,
    timeout,
  });

  const combined = result.stdout + "\n" + result.stderr;
  await writeFile(rptPath, combined, "utf-8");

  const wnsSetup = parseWns(combined, "setup");
  const wnsHold = parseWns(combined, "hold");
  const tnsSetup = parseTns(combined);
  const criticalPaths = parseCriticalPaths(combined, reportPaths);
  const maxFreqMhz = freqFromWns(wnsSetup, periodNs);
  const warnings: string[] = [];
  const errors: string[] = [];
  for (const line of combined.split("\n")) {
    if (/^Warning:/i.test(line)) warnings.push(line.trim());
    if (/^Error:/i.test(line)) errors.push(line.trim());
  }
  const ok = result.ok && wnsSetup >= -0.05 && wnsHold >= -0.05;

  return {
    ok, corner, mode, wnsSetup, wnsHold, tnsSetup, tnsHold: 0,
    maxFreqMhz, reportPath: rptPath, criticalPaths, warnings, errors,
  };
}

// ── Multi-Corner STA ─────────────────────────────────────────────────

export async function runMultiCornerSta(
  input: STAInput & { corners?: string[]; modes?: string[] },
): Promise<MultiCornerSTAResult> {
  const corners = input.corners ?? ["tt"];
  const modes = input.modes ?? ["func"];
  const designName = input.topModule ?? "top";
  const results: Record<string, STAReport> = {};

  for (const corner of corners) {
    for (const mode of modes) {
      const key = `${corner}_${mode}`;
      const cornerDir = join(input.outputDir, key);
      const report = await runSta({
        ...input,
        corner,
        mode,
        outputDir: cornerDir,
      });
      results[key] = report;
    }
  }

  const reports = Object.values(results);
  const worstWnsSetup = Math.min(...reports.map((r) => r.wnsSetup));
  const worstWnsHold = Math.min(...reports.map((r) => r.wnsHold));
  const worstTnsSetup = Math.min(...reports.map((r) => r.tnsSetup));
  const worstTnsHold = Math.min(...reports.map((r) => r.tnsHold));
  const maxFreqMhz = Math.min(...reports.map((r) => r.maxFreqMhz));
  const allCornersPass = reports.every((r) => r.ok);

  const summaryPath = join(input.outputDir, "sta_summary.json");
  const summary = {
    designName, worstWnsSetup, worstWnsHold, worstTnsSetup, worstTnsHold,
    maxFreqMhz, allCornersPass,
    corners: Object.fromEntries(Object.entries(results).map(([k, v]) => [
      k, { ok: v.ok, wnsSetup: v.wnsSetup, wnsHold: v.wnsHold, maxFreqMhz: v.maxFreqMhz },
    ])),
  };
  await writeFile(summaryPath, JSON.stringify(summary, null, 2), "utf-8");

  return {
    designName, corners: results, worstWnsSetup, worstWnsHold,
    worstTnsSetup, worstTnsHold, maxFreqMhz, allCornersPass, summaryPath,
  };
}

// ── Pipeline Stage Entry Point ───────────────────────────────────────

export async function executeStaStage(
  ctx: BuildContext,
): Promise<StageResult> {
  const netlist = ctx.artifacts["gate_netlist_path"] as string | undefined;
  const sdcPath = ctx.artifacts["sdc_path"] as string | undefined;
  const libFiles = (ctx.artifacts["timing_libs"] as string[] | undefined) ??
    ctx.pdk_tool_config.timing_libs;

  if (!netlist || !sdcPath || !libFiles?.length) {
    return {
      stage: "TIMING_ANALYSIS",
      status: "ERROR" as StageStatus,
      producer: "sta_runner",
      failure_class: "ORCHESTRATOR_ROUTING_ERROR" as FailureClass,
      consumable_payload: {},
      diagnostics: ["Missing artifacts: netlist, SDC, or liberty files"],
      artifacts_written: [],
      next_action: "fail",
    };
  }

  const outputDir = join(ctx.workspace_root, "designs", ctx.name, "sta");
  const periodNs = parseFloat(ctx.pdk_profile.default_clock_period || "10");

  try {
    const report = await runSta({
      netlist,
      sdc: sdcPath,
      libFiles,
      outputDir,
      corner: "tt",
      mode: "func",
      topModule: ctx.name,
      pdk: ctx.pdk_profile.pdk,
      minPeriodNs: periodNs,
    });

    ctx.artifacts["sta_report"] = report;
    ctx.artifacts["sta_report_path"] = report.reportPath;
    ctx.artifacts["max_freq_mhz"] = report.maxFreqMhz;

    if (!report.ok) {
      return {
        stage: "TIMING_ANALYSIS",
        status: "FAIL" as StageStatus,
        producer: "sta_runner",
        failure_class: "EDA_TOOL_ERROR" as FailureClass,
        consumable_payload: {
          wns_setup: report.wnsSetup,
          wns_hold: report.wnsHold,
          max_freq_mhz: report.maxFreqMhz,
          critical_paths: report.criticalPaths,
        },
        diagnostics: [
          `WNS setup: ${report.wnsSetup} ns`,
          `WNS hold: ${report.wnsHold} ns`,
          ...report.errors,
        ],
        artifacts_written: ["sta_report", "sta_report_path"],
        next_action: "fix_timing",
      };
    }

    return {
      stage: "TIMING_ANALYSIS",
      status: "PASS" as StageStatus,
      producer: "sta_runner",
      failure_class: "UNKNOWN" as FailureClass,
      consumable_payload: {
        wns_setup: report.wnsSetup,
        wns_hold: report.wnsHold,
        max_freq_mhz: report.maxFreqMhz,
        report_path: report.reportPath,
      },
      diagnostics: report.warnings,
      artifacts_written: ["sta_report", "sta_report_path", "max_freq_mhz"],
      next_action: "convergence_review",
    };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return {
      stage: "TIMING_ANALYSIS",
      status: "ERROR" as StageStatus,
      producer: "sta_runner",
      failure_class: "INFRASTRUCTURE_ERROR" as FailureClass,
      consumable_payload: {},
      diagnostics: [`STA execution failed: ${message}`],
      artifacts_written: [],
      next_action: "fail",
    };
  }
}
