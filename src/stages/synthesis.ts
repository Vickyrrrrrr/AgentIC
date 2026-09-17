/**
 * Synthesis stage — runs Yosys to synthesize RTL into a gate-level netlist.
 *
 * Ported from Python: src/agentic/orchestrator.py (SYNTHESIS stage) + yosys runner.
 */

import { writeFile, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { LlmClient } from "../llm/index.js";
import { runCommand, EDA_BINARIES } from "../eda/index.js";
import {
  StageStatus,
  FailureClass,
} from "../types/index.js";
import type {
  BuildContext,
  StageResult,
} from "../types/index.js";

// ── Interfaces ───────────────────────────────────────────────────────

export interface SynthesisInput {
  netlistOrRtlPaths: string[];
  topModule: string;
  outputDir: string;
  pdk: string;
  pdkRoot?: string;
  clkConstraintNs?: number;
  flattenHierarchy?: boolean;
  workspaceRoot: string;
}

export interface SynthesisMetrics {
  cellCount: number;
  gateCount: number;
  areaUm2: number;
  dffCount: number;
  lutCount: number;
  frequencyMhz: number;
}

export interface SynthesisOutput {
  ok: boolean;
  netlistPath: string;
  checkpointPath?: string;
  reportPath: string;
  metrics: SynthesisMetrics;
  warnings: string[];
  errors: string[];
}

// ── Yosys Script Builder ─────────────────────────────────────────────

export function buildYosysScript(input: SynthesisInput): string[] {
  const lines: string[] = [];
  const designName = input.topModule;
  const netlistPath = join(input.outputDir, `${designName}_synth.v`);
  const checkpointPath = join(input.outputDir, `${designName}_synth.ilang`);

  // Read each source file
  for (const rtlPath of input.netlistOrRtlPaths) {
    const lower = rtlPath.toLowerCase();
    if (lower.endsWith(".sv") || lower.endsWith(".svh")) {
      lines.push(`read_verilog -sv ${rtlPath}`);
    } else {
      lines.push(`read_verilog ${rtlPath}`);
    }
  }

  // Synthesize
  lines.push(`synth -top ${input.topModule}`);

  // Optimisation passes
  lines.push("opt_expr -fine");
  lines.push("opt_clean");

  // Mid-synthesis stats
  lines.push("stat");

  // Buffer insertion / purge pass
  lines.push("opt -purge");

  // Write outputs
  lines.push(`write_verilog ${netlistPath}`);
  lines.push(`write_ilang ${checkpointPath}`);

  // Final stats and design check
  lines.push("stat");
  lines.push("check");

  return lines;
}

// ── Stat Parsers (pure functions) ────────────────────────────────────

export function parseStatCellCount(stdout: string): number {
  const m =
    stdout.match(/Number of cells:\s*(\d+)/) ??
    stdout.match(/TECHMEM\s+(\d+)/);
  return m ? parseInt(m[1], 10) : 0;
}

export function parseStatDff(stdout: string): number {
  const m =
    stdout.match(/\$dff\s+(\d+)/) ??
    stdout.match(/FDRE\s+(\d+)/) ??
    stdout.match(/Number of flip flops:\s*(\d+)/);
  return m ? parseInt(m[1], 10) : 0;
}

export function parseStatLut(stdout: string): number {
  const m =
    stdout.match(/\$lut\s+(\d+)/) ??
    stdout.match(/LUT\d+\s+(\d+)/) ??
    stdout.match(/Number of LUTs:\s*(\d+)/);
  return m ? parseInt(m[1], 10) : 0;
}

export function parseStatArea(stdout: string): number {
  const m = stdout.match(/Chip area for module\s+\S+:\s*(\d+\.?\d*)/);
  return m ? parseFloat(m[1]) : 0;
}

export function parseGateEquiv(dff: number, lut: number, cells: number): number {
  return dff * 6 + lut * 4 + Math.max(0, cells - dff - lut);
}

// ── Run Synthesis ────────────────────────────────────────────────────

export async function runSynthesis(input: SynthesisInput): Promise<SynthesisOutput> {
  const designName = input.topModule;
  const netlistPath = join(input.outputDir, `${designName}_synth.v`);
  const checkpointPath = join(input.outputDir, `${designName}_synth.ilang`);
  const reportPath = join(input.outputDir, `${designName}_synth_report.txt`);
  const scriptsDir = join(input.outputDir, "scripts");
  const scriptPath = join(scriptsDir, `${designName}_synth.ys`);

  // Ensure directories exist
  await mkdir(input.outputDir, { recursive: true });
  await mkdir(scriptsDir, { recursive: true });

  // Build and write the Yosys script
  const scriptLines = buildYosysScript(input);
  await writeFile(scriptPath, scriptLines.join("\n"), "utf-8");

  // Execute Yosys
  const result = await runCommand(EDA_BINARIES.yosys(), ["-s", scriptPath], {
    cwd: input.outputDir,
    timeout: 300_000,
  });

  const stdout = result.stdout;
  const stderr = result.stderr;

  // Parse metrics from stdout
  const cellCount = parseStatCellCount(stdout);
  const dffCount = parseStatDff(stdout);
  const lutCount = parseStatLut(stdout);
  const areaUm2 = parseStatArea(stdout);
  const gateCount = parseGateEquiv(dffCount, lutCount, cellCount);
  const clkNs = input.clkConstraintNs ?? 10;
  const frequencyMhz = clkNs > 0 ? 1000 / clkNs : 0;

  // Extract warnings from stderr
  const warnings: string[] = [];
  const errors: string[] = [];
  for (const line of stderr.split("\n")) {
    if (/^WARNING:/i.test(line)) {
      warnings.push(line.replace(/^WARNING:\s*/i, "").trim());
    } else if (/^ERROR:/i.test(line)) {
      errors.push(line.replace(/^ERROR:\s*/i, "").trim());
    }
  }

  // Write report
  const reportContent = [
    `=== Synthesis Report: ${designName} ===`,
    `Cell count : ${cellCount}`,
    `Gate equiv : ${gateCount}`,
    `DFF count  : ${dffCount}`,
    `LUT count  : ${lutCount}`,
    `Area (um²) : ${areaUm2}`,
    `Freq (MHz) : ${frequencyMhz}`,
    `Warnings   : ${warnings.length}`,
    `Errors     : ${errors.length}`,
    "",
    "--- stdout ---",
    stdout.slice(0, 50_000),
    "",
    "--- stderr ---",
    stderr.slice(0, 10_000),
  ].join("\n");
  await writeFile(reportPath, reportContent, "utf-8");

  // Check if netlist was actually written
  const netlistExists = existsSync(netlistPath);
  const ok = result.ok && netlistExists && errors.length === 0;

  return {
    ok,
    netlistPath,
    checkpointPath: existsSync(checkpointPath) ? checkpointPath : undefined,
    reportPath,
    metrics: { cellCount, gateCount, areaUm2, dffCount, lutCount, frequencyMhz },
    warnings,
    errors: ok ? errors : [...errors, ...(netlistExists ? [] : ["Netlist file was not generated"])],
  };
}

// ── Stage Executor ───────────────────────────────────────────────────

export async function executeSynthesisStage(ctx: BuildContext): Promise<StageResult> {
  const rtlPath = ctx.artifacts["rtl_path"] as string | undefined;

  if (!rtlPath) {
    return {
      stage: "SYNTHESIS",
      status: StageStatus.ERROR,
      producer: "synthesis_runner",
      failure_class: FailureClass.ORCHESTRATOR_ROUTING_ERROR,
      consumable_payload: {},
      diagnostics: ["Missing rtl_path artifact"],
      artifacts_written: [],
      next_action: "fail",
    };
  }

  try {
    const output = await runSynthesis({
      netlistOrRtlPaths: [rtlPath],
      topModule: ctx.name,
      outputDir: join(ctx.workspace_root, "designs", ctx.name, "synth"),
      pdk: ctx.pdk_profile.pdk,
      workspaceRoot: ctx.workspace_root,
    });

    ctx.artifacts["synth_netlist_path"] = output.netlistPath;
    ctx.artifacts["synth_metrics"] = output.metrics;
    ctx.artifacts["synth_report_path"] = output.reportPath;
    if (output.checkpointPath) {
      ctx.artifacts["synth_checkpoint_path"] = output.checkpointPath;
    }

    return {
      stage: "SYNTHESIS",
      status: output.ok ? StageStatus.PASS : StageStatus.RETRY,
      producer: "synthesis_runner",
      failure_class: output.ok ? FailureClass.UNKNOWN : FailureClass.EDA_TOOL_ERROR,
      consumable_payload: {
        netlist_path: output.netlistPath,
        cell_count: output.metrics.cellCount,
        gate_count: output.metrics.gateCount,
        area_um2: output.metrics.areaUm2,
      },
      diagnostics: [...output.errors, ...output.warnings],
      artifacts_written: [
        "synth_netlist_path",
        "synth_metrics",
        "synth_report_path",
        ...(output.checkpointPath ? ["synth_checkpoint_path"] : []),
      ],
      next_action: output.ok ? "timing_analysis" : "rtl_fix",
    };
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    return {
      stage: "SYNTHESIS",
      status: StageStatus.ERROR,
      producer: "synthesis_runner",
      failure_class: FailureClass.INFRASTRUCTURE_ERROR,
      consumable_payload: {},
      diagnostics: [`Synthesis stage crashed: ${message}`],
      artifacts_written: [],
      next_action: "fail",
    };
  }
}

// ── LLM-compatible wrapper (signature for pipeline consistency) ──────

export async function generateSynthesis(
  _llm: LlmClient,
  input: SynthesisInput,
): Promise<SynthesisOutput> {
  // Synthesis is deterministic — no LLM needed.
  // The parameter is accepted for interface consistency with other stages.
  return runSynthesis(input);
}
