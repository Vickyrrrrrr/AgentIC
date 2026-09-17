/**
 * Physical verification stage — Magic DRC, Netgen LVS, and antenna checks.
 *
 * Ported from Python physical verification tools for TypeScript AI agents.
 */
import { writeFile, mkdir, readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join, basename } from "node:path";
import { runCommand, EDA_BINARIES } from "../eda/index.js";
import type { BuildContext, StageResult } from "../types/index.js";
import { StageStatus, FailureClass } from "../types/index.js";

// ── DRC Types ────────────────────────────────────────────────────────
export interface DRCInput {
  gdsPath: string;
  techFile?: string;
  outputDir: string;
  pdk?: string;
  pdkRoot?: string;
  designName?: string;
  timeout?: number;
}
export interface DRCViolation {
  rule: string;
  layer: string;
  xUm: number;
  yUm: number;
  message: string;
  severity: string;
}
export interface DRCResult {
  ok: boolean;
  violations: DRCViolation[];
  violationCount: number;
  reportPath: string;
  runtimeSec: number;
  errors: string[];
}

// ── LVS Types ────────────────────────────────────────────────────────
export interface LVSInput {
  schematicNetlist: string;
  layoutGds: string;
  outputDir: string;
  techSetup?: string;
  pdk?: string;
  topModule?: string;
  designName?: string;
  timeout?: number;
}
export interface LVSResult {
  ok: boolean;
  equivalent: boolean;
  netMismatches: number;
  pinMismatches: number;
  unconnectedNets: number;
  reportPath: string;
  runtimeSec: number;
  errors: string[];
}

// ── Antenna Types ────────────────────────────────────────────────────
export interface AntennaInput {
  gdsPath: string;
  techFile: string;
  outputDir: string;
  antennaRatio?: number;
  designName?: string;
  timeout?: number;
}
export interface AntennaResult {
  ok: boolean;
  violations: number;
  maxRatio: number;
  reportPath: string;
  errors: string[];
}

// ── TCL Builders ─────────────────────────────────────────────────────
export function buildMagicDrcTcl(
  gdsPath: string,
  designName: string,
  reportPath: string,
  techFile?: string,
): string {
  const lines: string[] = [];
  if (techFile) lines.push(`tech load ${techFile}`);
  lines.push(
    `gds read ${gdsPath}`, `load ${designName}`, `select top cell`,
    `drc on`, `drc check`, `drc catchall`,
    `drc report ${reportPath}`, `quit`,
  );
  return lines.join("\n");
}

function buildLvsTcl(
  schematicNetlist: string,
  layoutNetlist: string,
  reportPath: string,
  techSetup?: string,
): string {
  const lines: string[] = [];
  if (techSetup) lines.push(`source ${techSetup}`);
  lines.push(
    `readnet spice ${schematicNetlist} schematic`,
    `readnet spice ${layoutNetlist} layout`,
    `lvs schematic layout ${reportPath} -json`, `quit`,
  );
  return lines.join("\n");
}

function buildAntennaTcl(
  gdsPath: string, designName: string, techFile: string,
  reportPath: string, antennaRatio: number,
): string {
  return [
    `tech load ${techFile}`, `gds read ${gdsPath}`,
    `load ${designName}`, `select top cell`,
    `antennacheck -ratio ${antennaRatio}`,
    `antennacheck report ${reportPath}`, `quit`,
  ].join("\n");
}

// ── Parsers ──────────────────────────────────────────────────────────

/** Parse Magic DRC output for violations with layer/coordinates. */
export function parseDrcOutput(raw: string): DRCViolation[] {
  const violations: DRCViolation[] = [];
  const rulePattern =
    /^\s*(?:Rule\s+(?:violated:\s*)?)?(\S+?)[\s:]+.*?\(\s*([-\d.]+)\s*um?\s*[,;\s]\s*([-\d.]+)\s*um?\s*\)(.*)$/gm;
  let match: RegExpExecArray | null;
  while ((match = rulePattern.exec(raw)) !== null) {
    const rule = match[1];
    const layerMatch = rule.match(/^([a-zA-Z][a-zA-Z0-9_]*)/);
    violations.push({
      rule, layer: layerMatch ? layerMatch[1] : "unknown",
      xUm: parseFloat(match[2]), yUm: parseFloat(match[3]),
      message: match[4]?.trim() || rule, severity: "error",
    });
  }
  if (violations.length === 0) {
    const linePattern = /^\s*(\S+)\s*:\s*(.+?)\s*$/gm;
    while ((match = linePattern.exec(raw)) !== null) {
      const line = match[0];
      if (/violation|error|spacing|width/i.test(line)) {
        violations.push({
          rule: match[1], layer: match[1].split(".")[0] || "unknown",
          xUm: 0, yUm: 0, message: match[2].trim(), severity: "error",
        });
      }
    }
  }
  return violations;
}

/** Parse Netgen LVS output to extract equivalence results. */
export function parseNetgenOutput(stdout: string): {
  equivalent: boolean;
  netMismatches: number;
  pinMismatches: number;
  unconnectedNets: number;
} {
  const result = { equivalent: false, netMismatches: 0, pinMismatches: 0, unconnectedNets: 0 };
  if (/circuits\s+match\s+uniquely/i.test(stdout) || /netlists\s+match\s+uniquely/i.test(stdout)) {
    result.equivalent = true;
  }
  const netM = stdout.match(/Net\s+mismatches?\s*[:=]\s*(\d+)/i);
  if (netM) result.netMismatches = parseInt(netM[1], 10);
  const pinM = stdout.match(/Pin\s+mismatches?\s*[:=]\s*(\d+)/i);
  if (pinM) result.pinMismatches = parseInt(pinM[1], 10);
  const uncM = stdout.match(/(?:Unconnected|Disconnected)\s+nets?\s*[:=]\s*(\d+)/i);
  if (uncM) result.unconnectedNets = parseInt(uncM[1], 10);
  if (result.netMismatches > 0 || result.pinMismatches > 0) result.equivalent = false;
  return result;
}

// ── Helper: write TCL, run tool, read report ─────────────────────────

async function writeTclAndRun(
  tclContent: string, tclName: string, outputDir: string,
  binary: string, args: string[], timeout: number,
): Promise<{ stdout: string; stderr: string; ok: boolean; reportContent: string; reportPath: string }> {
  const tclPath = join(outputDir, tclName);
  await writeFile(tclPath, tclContent, "utf-8");
  const result = await runCommand(binary, [...args, tclPath], { cwd: outputDir, timeout });
  const combined = result.stdout + "\n" + result.stderr;
  return { stdout: result.stdout, stderr: result.stderr, ok: result.ok, reportContent: combined, reportPath: "" };
}

// ── Run Functions ────────────────────────────────────────────────────

export async function runDrc(input: DRCInput): Promise<DRCResult> {
  const startTime = Date.now();
  const errors: string[] = [];
  await mkdir(input.outputDir, { recursive: true });
  const designName = input.designName || basename(input.gdsPath, ".gds");
  const reportPath = join(input.outputDir, `${designName}_drc.rpt`);
  const tclScript = buildMagicDrcTcl(input.gdsPath, designName, reportPath, input.techFile);
  const tclPath = join(input.outputDir, "drc_run.tcl");
  await writeFile(tclPath, tclScript, "utf-8");

  const result = await runCommand(EDA_BINARIES.magic(), ["-dnull", "-noconsole", tclPath], {
    cwd: input.outputDir, timeout: input.timeout ?? 300_000,
  });
  if (!result.ok) errors.push(`Magic DRC exited with errors: ${result.stderr.slice(0, 2000)}`);

  let reportContent = result.stdout + "\n" + result.stderr;
  if (existsSync(reportPath)) reportContent = await readFile(reportPath, "utf-8");
  const violations = parseDrcOutput(reportContent);

  return {
    ok: result.ok && violations.length === 0, violations,
    violationCount: violations.length, reportPath,
    runtimeSec: (Date.now() - startTime) / 1000, errors,
  };
}

export async function runLvs(input: LVSInput): Promise<LVSResult> {
  const startTime = Date.now();
  const errors: string[] = [];
  await mkdir(input.outputDir, { recursive: true });
  const designName = input.designName || input.topModule || "design";
  const reportPath = join(input.outputDir, `${designName}_lvs.rpt`);
  const tclScript = buildLvsTcl(input.schematicNetlist, input.layoutGds, reportPath, input.techSetup);
  const tclPath = join(input.outputDir, "lvs_run.tcl");
  await writeFile(tclPath, tclScript, "utf-8");

  const result = await runCommand(EDA_BINARIES.netgen(), ["-batch", "source", tclPath], {
    cwd: input.outputDir, timeout: input.timeout ?? 300_000,
  });
  if (!result.ok) errors.push(`Netgen LVS exited with errors: ${result.stderr.slice(0, 2000)}`);

  let reportContent = result.stdout + "\n" + result.stderr;
  if (existsSync(reportPath)) reportContent = await readFile(reportPath, "utf-8");
  const parsed = parseNetgenOutput(reportContent);

  return {
    ok: result.ok && parsed.equivalent, equivalent: parsed.equivalent,
    netMismatches: parsed.netMismatches, pinMismatches: parsed.pinMismatches,
    unconnectedNets: parsed.unconnectedNets, reportPath,
    runtimeSec: (Date.now() - startTime) / 1000, errors,
  };
}

export async function runAntennaCheck(input: AntennaInput): Promise<AntennaResult> {
  const errors: string[] = [];
  const antennaRatio = input.antennaRatio ?? 4.0;
  await mkdir(input.outputDir, { recursive: true });
  const designName = input.designName || basename(input.gdsPath, ".gds");
  const reportPath = join(input.outputDir, `${designName}_antenna.rpt`);
  const tclScript = buildAntennaTcl(input.gdsPath, designName, input.techFile, reportPath, antennaRatio);
  const tclPath = join(input.outputDir, "antenna_run.tcl");
  await writeFile(tclPath, tclScript, "utf-8");

  const result = await runCommand(EDA_BINARIES.magic(), ["-dnull", "-noconsole", tclPath], {
    cwd: input.outputDir, timeout: input.timeout ?? 300_000,
  });
  if (!result.ok) errors.push(`Magic antenna check exited with errors: ${result.stderr.slice(0, 2000)}`);

  let reportContent = result.stdout + "\n" + result.stderr;
  if (existsSync(reportPath)) reportContent = await readFile(reportPath, "utf-8");

  let violationCount = 0;
  let maxRatio = 0;
  const ratioPattern = /ratio\s*[:=]\s*([\d.]+)/gi;
  let rm: RegExpExecArray | null;
  while ((rm = ratioPattern.exec(reportContent)) !== null) {
    const ratio = parseFloat(rm[1]);
    if (ratio > antennaRatio) violationCount++;
    if (ratio > maxRatio) maxRatio = ratio;
  }
  const violMatches = reportContent.match(/violation|antenna\s+error/gi);
  if (violMatches && violMatches.length > violationCount) violationCount = violMatches.length;

  return { ok: result.ok && violationCount === 0, violations: violationCount, maxRatio, reportPath, errors };
}

// ── Pipeline Stage Entry Point ───────────────────────────────────────

export async function executePhysicalVerifyStage(ctx: BuildContext): Promise<StageResult> {
  const diagnostics: string[] = [];
  const artifactsWritten: string[] = [];
  const gdsPath = ctx.artifacts["gds_path"] as string | undefined;
  const schematicNetlist = ctx.artifacts["schematic_netlist"] as string | undefined;

  if (!gdsPath) {
    return {
      stage: "PHYSICAL_VERIFY", status: StageStatus.ERROR, producer: "physical_verify",
      failure_class: FailureClass.ORCHESTRATOR_ROUTING_ERROR, consumable_payload: {},
      diagnostics: ["Missing gds_path artifact — cannot run DRC/LVS"],
      artifacts_written: [], next_action: "fail",
    };
  }

  const outputDir = join(ctx.workspace_root, "designs", ctx.name, "physical_verify");

  // ── DRC ──
  const drcResult = await runDrc({
    gdsPath, techFile: ctx.pdk_tool_config.drc_rules || undefined,
    outputDir, pdk: ctx.pdk_profile.pdk, designName: ctx.name,
  });
  ctx.artifacts["drc_result"] = drcResult;
  artifactsWritten.push("drc_result");
  diagnostics.push(`DRC: ${drcResult.violationCount} violation(s) in ${drcResult.runtimeSec.toFixed(1)}s`);
  if (drcResult.errors.length > 0) diagnostics.push(...drcResult.errors);

  // ── LVS (if schematic netlist available) ──
  let lvsResult: LVSResult | undefined;
  if (schematicNetlist) {
    lvsResult = await runLvs({
      schematicNetlist, layoutGds: gdsPath, outputDir,
      techSetup: ctx.pdk_tool_config.lvs_rules || undefined,
      pdk: ctx.pdk_profile.pdk, topModule: ctx.name, designName: ctx.name,
    });
    ctx.artifacts["lvs_result"] = lvsResult;
    artifactsWritten.push("lvs_result");
    diagnostics.push(
      `LVS: ${lvsResult.equivalent ? "MATCH" : "MISMATCH"} ` +
      `(nets: ${lvsResult.netMismatches}, pins: ${lvsResult.pinMismatches}) in ${lvsResult.runtimeSec.toFixed(1)}s`,
    );
    if (lvsResult.errors.length > 0) diagnostics.push(...lvsResult.errors);
  } else {
    diagnostics.push("LVS: skipped — no schematic_netlist artifact");
  }

  const drcOk = drcResult.ok;
  const lvsOk = lvsResult ? lvsResult.ok : true;
  const overallOk = drcOk && lvsOk;

  return {
    stage: "PHYSICAL_VERIFY",
    status: overallOk ? StageStatus.PASS : StageStatus.FAIL,
    producer: "physical_verify",
    failure_class: overallOk ? FailureClass.UNKNOWN : FailureClass.EDA_TOOL_ERROR,
    consumable_payload: {
      drc_violations: drcResult.violationCount, drc_ok: drcOk,
      lvs_equivalent: lvsResult?.equivalent ?? null, lvs_ok: lvsOk,
    },
    diagnostics, artifacts_written: artifactsWritten,
    next_action: overallOk ? "signoff" : "eco_patch",
  };
}
