/**
 * EDA tool invocation wrappers — shell out to open-source EDA binaries.
 *
 * Each function wraps a specific EDA tool (Yosys, Verilator, Icarus, etc.)
 * and returns a structured result. An AI agent can call these directly
 * instead of going through a CLI.
 */

import { execFile } from "node:child_process";
import { writeFile, mkdir, readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join, dirname } from "node:path";
import type { EdaToolResult, StructuredError } from "../types/index.js";

// ── Tool Binary Resolution ────────────────────────────────────────────

function resolveBinary(name: string, envVar?: string): string {
  if (envVar && process.env[envVar]) {
    return process.env[envVar]!;
  }
  const ossHome = process.env.OSS_CAD_SUITE_HOME ?? "";
  if (ossHome) {
    const candidate = join(ossHome, "bin", name);
    if (existsSync(candidate)) return candidate;
  }
  return name;
}

export const EDA_BINARIES = {
  yosys: () => resolveBinary("yosys", "YOSYS_BIN"),
  verilator: () => resolveBinary("verilator", "VERILATOR_BIN"),
  iverilog: () => resolveBinary("iverilog", "IVERILOG_BIN"),
  vvp: () => resolveBinary("vvp", "VVP_BIN"),
  sby: () => resolveBinary("sby", "SBY_BIN"),
  sta: () => resolveBinary("sta", "OPENSTA_BIN"),
  magic: () => resolveBinary("magic", "MAGIC_BIN"),
  netgen: () => resolveBinary("netgen", "NETGEN_BIN"),
  ngspice: () => resolveBinary("ngspice", "NGSPICE_BIN"),
  sv2v: () => resolveBinary("sv2v", "SV2V_BIN"),
  eqy: () => resolveBinary("eqy", "EQY_BIN"),
} as const;

// ── Execution Helper ──────────────────────────────────────────────────

export function runCommand(
  binary: string,
  args: string[],
  opts?: { cwd?: string; timeout?: number; stdin?: string },
): Promise<EdaToolResult> {
  return new Promise((resolve) => {
    const timeout = opts?.timeout ?? 120_000;
    const child = execFile(
      binary,
      args,
      {
        cwd: opts?.cwd ?? process.cwd(),
        timeout,
        maxBuffer: 10 * 1024 * 1024,
        env: { ...process.env, LANG: "C", LC_ALL: "C" },
      },
      (error, stdout, stderr) => {
        const exitCode = error ? (error as NodeJS.ErrnoException).code ?? 1 : 0;
        resolve({
          ok: !error,
          stdout: stdout ?? "",
          stderr: stderr ?? "",
          exit_code: typeof exitCode === "number" ? exitCode : 1,
        });
      },
    );
    if (opts?.stdin && child.stdin) {
      child.stdin.write(opts.stdin);
      child.stdin.end();
    }
  });
}

// ── File Helpers ──────────────────────────────────────────────────────

export async function writeVerilog(path: string, code: string): Promise<void> {
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, code, "utf-8");
}

export async function readVerilog(path: string): Promise<string> {
  return readFile(path, "utf-8");
}

// ── Syntax Check ──────────────────────────────────────────────────────

export async function runSyntaxCheck(rtlPath: string): Promise<EdaToolResult> {
  const result = await runCommand(EDA_BINARIES.verilator(), [
    "--lint-only",
    "--Wall",
    "--Wno-fatal",
    rtlPath,
  ]);
  return {
    ...result,
    structured_errors: parseVerilatorErrors(result.stderr),
  };
}

// ── Lint Check ────────────────────────────────────────────────────────

export async function runLintCheck(rtlPath: string): Promise<EdaToolResult> {
  return runSyntaxCheck(rtlPath);
}

// ── Simulation ────────────────────────────────────────────────────────

export async function runSimulation(
  rtlPath: string,
  tbPath: string,
  opts?: { backend?: "verilator" | "iverilog"; timeout?: number },
): Promise<EdaToolResult> {
  const backend = opts?.backend ?? "iverilog";
  if (backend === "verilator") {
    const args = ["--binary", "--timing", "--trace", "-o", "/tmp/agentic_sim", rtlPath, tbPath];
    const compileResult = await runCommand(EDA_BINARIES.verilator(), args, { timeout: opts?.timeout });
    if (!compileResult.ok) return compileResult;
    return runCommand("/tmp/agentic_sim", [], { timeout: opts?.timeout });
  }
  const args = ["-g2012", "-o", "/tmp/agentic_sim.vvp", rtlPath, tbPath];
  const compileResult = await runCommand(EDA_BINARIES.iverilog(), args, { timeout: opts?.timeout });
  if (!compileResult.ok) return compileResult;
  return runCommand(EDA_BINARIES.vvp(), ["/tmp/agentic_sim.vvp"], { timeout: opts?.timeout });
}

// ── Formal Verification ───────────────────────────────────────────────

export async function runFormalVerification(
  sbyConfigPath: string,
  opts?: { timeout?: number },
): Promise<EdaToolResult> {
  return runCommand(EDA_BINARIES.sby(), ["--dump", sbyConfigPath], {
    timeout: opts?.timeout ?? 300_000,
  });
}

export async function writeSbyConfig(
  path: string,
  config: { rtlPath: string; svaPath?: string; mode: string; depth: number },
): Promise<void> {
  const content = [
    `[tasks]`,
    `[options]`,
    `mode ${config.mode}`,
    `depth ${config.depth}`,
    `[script]`,
    `read_verilog -sv ${config.rtlPath}`,
    config.svaPath ? `read_verilog -sv ${config.svaPath}` : "",
    `prep -top top`,
    `chformal -remove -reset`,
    `[files]`,
    config.rtlPath,
    config.svaPath ?? "",
  ]
    .filter(Boolean)
    .join("\n");
  await writeFile(path, content, "utf-8");
}

// ── Synthesis ─────────────────────────────────────────────────────────

export async function runYosysSynthesis(
  rtlPath: string,
  topModule: string,
  opts?: { outputDir?: string; sdcPath?: string; timeout?: number },
): Promise<EdaToolResult> {
  const outputDir = opts?.outputDir ?? "/tmp/agentic_synth";
  await mkdir(outputDir, { recursive: true });
  const scriptLines = [
    `read_verilog ${rtlPath}`,
    `synth -top ${topModule}`,
    `opt -full`,
    `abc`,
    `opt_clean`,
    `write_verilog ${join(outputDir, "gate_level_netlist.v")}`,
    `stat`,
    `write_json ${join(outputDir, "synth_stats.json")}`,
  ];
  if (opts?.sdcPath) {
    scriptLines.unshift(`read_sdc ${opts.sdcPath}`);
  }
  const scriptPath = join(outputDir, "synth.ys");
  await writeFile(scriptPath, scriptLines.join("\n"), "utf-8");
  return runCommand(EDA_BINARIES.yosys(), ["-s", scriptPath], {
    cwd: outputDir,
    timeout: opts?.timeout ?? 300_000,
  });
}

// ── STA (Static Timing Analysis) ─────────────────────────────────────

export async function runOpenSta(
  netlistPath: string,
  sdcPath: string,
  libFiles: string[],
  opts?: { timeout?: number },
): Promise<EdaToolResult> {
  const scriptLines = [
    ...libFiles.map((lib) => `read_liberty ${lib}`),
    `read_verilog ${netlistPath}`,
    `link_design [top]`,
    `read_sdc ${sdcPath}`,
    `report_checks -path_delay min`,
    `report_checks -path_delay max`,
    `report_wns`,
    `report_tns`,
  ];
  const scriptPath = "/tmp/agentic_sta.tcl";
  await writeFile(scriptPath, scriptLines.join("\n"), "utf-8");
  return runCommand(EDA_BINARIES.sta(), [scriptPath], {
    timeout: opts?.timeout ?? 120_000,
  });
}

// ── DRC (Design Rule Check) ───────────────────────────────────────────

export async function runMagicDrc(
  gdsPath: string,
  drcRules: string,
  opts?: { timeout?: number },
): Promise<EdaToolResult> {
  const script = [
    `gds read ${gdsPath}`,
    `load [cellname root]`,
    `drc check`,
    `drc catchall`,
    `drc report ${drcRules}`,
    `quit`,
  ].join("\n");
  return runCommand(EDA_BINARIES.magic(), ["-dnull", "-noconsole"], {
    timeout: opts?.timeout ?? 300_000,
    stdin: script,
  });
}

// ── LVS (Layout vs Schematic) ────────────────────────────────────────

export async function runNetgenLvs(
  schematicPath: string,
  layoutPath: string,
  lvsRules: string,
  opts?: { timeout?: number },
): Promise<EdaToolResult> {
  const script = [
    `readnet ${schematicPath} schematic`,
    `readnet ${layoutPath} layout`,
    `lvs schematic layout ${lvsRules}`,
    `quit`,
  ].join("\n");
  return runCommand(EDA_BINARIES.netgen(), [], {
    timeout: opts?.timeout ?? 300_000,
    stdin: script,
  });
}

// ── OpenLane Hardening ────────────────────────────────────────────────

export async function runOpenlane(
  designName: string,
  rtlDir: string,
  opts?: {
    pdk?: string;
    clockPeriod?: string;
    dieArea?: string;
    coreUtil?: number;
    skip?: boolean;
    timeout?: number;
  },
): Promise<EdaToolResult> {
  if (opts?.skip) {
    return {
      ok: true,
      stdout: "OpenLane skipped",
      stderr: "",
      exit_code: 0,
    };
  }
  const pdk = opts?.pdk ?? "sky130A";
  const clockPeriod = opts?.clockPeriod ?? "10.0";
  const config = [
    `set ::env(DESIGN_NAME) ${designName}`,
    `set ::env(VERILOG_FILES) ${join(rtlDir, "*.v")}`,
    `set ::env(CLOCK_PERIOD) ${clockPeriod}`,
    `set ::env(CLOCK_PORT) clk`,
    `set ::env(PDK) ${pdk}`,
  ];
  if (opts?.dieArea) config.push(`set ::env(DIE_AREA) ${opts.dieArea}`);
  if (opts?.coreUtil) config.push(`set ::env(PL_TARGET_DENSITY) ${opts.coreUtil}`);

  const configPath = join(rtlDir, "config.tcl");
  await writeFile(configPath, config.join("\n"), "utf-8");

  return runCommand("docker", [
    "run",
    "--rm",
    "-v",
    `${rtlDir}:/work`,
    "-v",
    `${configPath}:/work/config.tcl`,
    "ghcr.io/the-openroad-project/openlane:latest",
    "bash",
    "-c",
    "cd /work && openroad -exit /work/config.tcl",
  ], {
    timeout: opts?.timeout ?? 1_800_000,
  });
}

// ── Error Parsing ─────────────────────────────────────────────────────

export function parseVerilatorErrors(stderr: string): StructuredError[] {
  const errors: StructuredError[] = [];
  const lines = stderr.split("\n");
  for (const line of lines) {
    const match = line.match(/^(.+?):(\d+):(?:\d+:)?\s*(error|warning|Error|Warning):\s*(.+)$/);
    if (match) {
      errors.push({
        type: match[3].toLowerCase(),
        message: match[4],
        file: match[1],
        line: parseInt(match[2], 10),
        severity: match[3].toLowerCase() === "error" ? "error" : "warning",
      });
    }
  }
  return errors;
}

export function parseYosysErrors(stderr: string): StructuredError[] {
  const errors: StructuredError[] = [];
  const lines = stderr.split("\n");
  for (const line of lines) {
    if (line.match(/^ERROR:\s*(.+)$/)) {
      errors.push({
        type: "yosys_error",
        message: line.replace(/^ERROR:\s*/, ""),
        severity: "error",
      });
    } else if (line.match(/^WARNING:\s*(.+)$/)) {
      errors.push({
        type: "yosys_warning",
        message: line.replace(/^WARNING:\s*/, ""),
        severity: "warning",
      });
    }
  }
  return errors;
}

// ── Verilog Code Validation ───────────────────────────────────────────

const PROSE_START_WORDS =
  /^(the|this|here|i |in |a |an |to |we |my |it |as |for |note|sure|below|above|let|thank|sorry|great|of course)/i;

export function extractVerilogSafely(rawLlmText: string): string {
  if (!rawLlmText?.trim()) return "";
  let text = rawLlmText;
  text = text.replace(/<think>[\s\S]*?<\/think>/g, "");

  const fences = [
    /```(?:verilog|systemverilog|sv)\s*\n([\s\S]*?)\n```/gi,
    /```(?:verilog|systemverilog|sv)\s*([\s\S]*?)```/gi,
    /```v\s*\n([\s\S]*?)\n```/gi,
    /```v\s*([\s\S]*?)```/gi,
    /```\s*\n([\s\S]*?)\n```/g,
    /```\s*([\s\S]*?)```/g,
  ];

  for (const pattern of fences) {
    const m = pattern.exec(text);
    if (m && m[1].includes("module") && m[1].includes("endmodule")) {
      return m[1].trim();
    }
  }

  const moduleMatch = text.match(/\bmodule\s+[a-zA-Z_]\w*\s*(?:#|\(|;)[\s\S]*?\bendmodule\b/);
  if (moduleMatch) return moduleMatch[0].trim();

  return text.trim();
}

export function sanitizeVerilogArtifact(rawLlmText: string): string {
  const code = extractVerilogSafely(rawLlmText);
  const cleanedLines: string[] = [];
  for (const line of (code || "").split("\n")) {
    const stripped = line.trim().toLowerCase();
    if (["```", "```verilog", "```systemverilog", "```sv", "```v"].includes(stripped)) {
      continue;
    }
    cleanedLines.push(line);
  }
  return cleanedLines.join("\n").trim();
}

export function isValidVerilog(raw: string): boolean {
  const extracted = sanitizeVerilogArtifact(raw);
  if (!extracted) return false;
  if (!extracted.includes("module") || !extracted.includes("endmodule")) return false;
  if (PROSE_START_WORDS.test(extracted)) return false;
  return true;
}

// ── Coverage ──────────────────────────────────────────────────────────

export async function runSimulationWithCoverage(
  rtlPath: string,
  tbPath: string,
  opts?: { timeout?: number },
): Promise<EdaToolResult & { coverage_pct?: number }> {
  const result = await runSimulation(rtlPath, tbPath, opts);
  const coverageMatch = result.stdout.match(/coverage:\s*([\d.]+)%/i);
  return {
    ...result,
    coverage_pct: coverageMatch ? parseFloat(coverageMatch[1]) : undefined,
  };
}
