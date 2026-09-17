/**
 * Testbench generation and verification stage.
 *
 * Ported from Python: agents/testbench_designer.py + orchestrator verification stages
 */

import { LlmClient } from "../llm/index.js";
import { getTestbenchRole, getVerifierRole } from "../agents/index.js";
import {
  sanitizeVerilogArtifact,
  isValidVerilog,
  writeVerilog,
  runSimulation,
  runSyntaxCheck,
} from "../eda/index.js";
import { StageStatus, FailureClass, BuildStrategy } from "../types/index.js";
import type { BuildContext, HardwareSpec, StageResult } from "../types/index.js";
import { join } from "node:path";

export interface TbGenInput {
  spec: HardwareSpec;
  rtlCode: string;
  rtlPath: string;
  designName: string;
  strategy?: BuildStrategy;
  workspaceRoot: string;
  previousError?: string;
}

export interface TbGenOutput {
  tbCode: string;
  tbPath: string;
  isValid: boolean;
  issues: string[];
}

export async function generateTestbench(
  llm: LlmClient,
  input: TbGenInput,
): Promise<TbGenOutput> {
  const strategy = input.strategy ?? BuildStrategy.SV_MODULAR;
  const role = getTestbenchRole(strategy);
  const spec = input.spec;

  const portList = spec.ports
    .map((p) => `  ${p.direction} ${p.data_type} ${p.name}`)
    .join("\n");

  const errorContext = input.previousError
    ? `\n\nPREVIOUS TB ERROR — fix this:\n${input.previousError}\n`
    : "";

  const prompt = `Generate a self-checking Verilog testbench for the following design.

DESIGN NAME: ${input.designName}
TB MODULE NAME: ${input.designName}_tb

RTL CODE:
${input.rtlCode.slice(0, 12000)}

TOP-LEVEL PORTS:
${portList}

BEHAVIORAL CONTRACT:
${spec.behavioral_contract.map((b) => `GIVEN ${b.given} WHEN ${b.when} THEN ${b.then} WITHIN ${b.within}`).join("\n")}

RULES:
- TB module MUST be named: ${input.designName}_tb
- DUT output ports → wire in TB. DUT input ports → reg in TB.
- Print "TEST PASSED" on success, "TEST FAILED" on failure
- Hold reset for at least 4 clock cycles before stimulus
- Wait 2 cycles after driving inputs before sampling
- Add timeout watchdog: initial begin #100000; $display("TEST FAILED: Timeout"); $finish; end
- Add $dumpfile("${input.designName}_wave.vcd") and $dumpvars
- Output ONLY Verilog code${errorContext}`;

  const raw = await llm.callText(role.systemPrompt, prompt, {
    temperature: 0.1,
    maxTokens: 16384,
  });

  const tbCode = sanitizeVerilogArtifact(raw);
  const issues: string[] = [];

  if (!isValidVerilog(tbCode)) {
    issues.push("TB candidate is not valid Verilog.");
    return { tbCode, tbPath: "", isValid: false, issues };
  }

  if (!tbCode.includes(`${input.designName}_tb`)) {
    issues.push(`TB module name must be '${input.designName}_tb'.`);
  }
  if (!tbCode.includes("TEST PASSED") || !tbCode.includes("TEST FAILED")) {
    issues.push("TB must include TEST PASSED and TEST FAILED markers.");
  }

  const tbPath = join(input.workspaceRoot, "designs", input.designName, "src", `${input.designName}_tb.v`);
  await writeVerilog(tbPath, tbCode);

  return { tbCode, tbPath, isValid: issues.length === 0, issues };
}

export interface SimulationResult {
  passed: boolean;
  output: string;
  issues: string[];
}

export async function runTestbenchSimulation(
  rtlPath: string,
  tbPath: string,
  opts?: { backend?: "verilator" | "iverilog"; timeout?: number },
): Promise<SimulationResult> {
  const result = await runSimulation(rtlPath, tbPath, opts);
  const output = result.stdout + (result.stderr ? `\n${result.stderr}` : "");
  const passed = output.includes("TEST PASSED") && !output.includes("TEST FAILED");
  const issues: string[] = [];

  if (!result.ok && !passed) {
    issues.push(`Simulation failed: ${result.stderr.slice(0, 2000)}`);
  }
  if (!output.includes("TEST PASSED") && !output.includes("TEST FAILED")) {
    issues.push("No TEST PASSED/FAILED marker found in output.");
  }

  return { passed, output, issues };
}

export async function executeVerificationStage(ctx: BuildContext): Promise<StageResult> {
  const llm = new LlmClient(ctx.llm_config);
  const spec = ctx.artifacts["hw_spec"] as HardwareSpec;
  const rtlCode = ctx.artifacts["rtl_code"] as string;
  const rtlPath = ctx.artifacts["rtl_path"] as string;

  if (!spec || !rtlCode) {
    return {
      stage: "VERIFICATION",
      status: StageStatus.ERROR,
      producer: "testbench_generator",
      failure_class: FailureClass.ORCHESTRATOR_ROUTING_ERROR,
      consumable_payload: {},
      diagnostics: ["Missing artifacts for verification"],
      artifacts_written: [],
      next_action: "fail",
    };
  }

  const { tbCode, tbPath, isValid, issues } = await generateTestbench(llm, {
    spec,
    rtlCode,
    rtlPath,
    designName: ctx.name,
    strategy: ctx.strategy,
    workspaceRoot: ctx.workspace_root,
  });

  ctx.artifacts["tb_code"] = tbCode;
  ctx.artifacts["tb_path"] = tbPath;

  if (!isValid) {
    return {
      stage: "VERIFICATION",
      status: StageStatus.RETRY,
      producer: "testbench_generator",
      failure_class: FailureClass.LLM_FORMAT_ERROR,
      consumable_payload: { tb_path: tbPath },
      diagnostics: issues,
      artifacts_written: ["tb_code", "tb_path"],
      next_action: "regenerate_tb",
    };
  }

  const simResult = await runTestbenchSimulation(rtlPath, tbPath);

  ctx.artifacts["sim_output"] = simResult.output;
  ctx.artifacts["sim_passed"] = simResult.passed;

  return {
    stage: "VERIFICATION",
    status: simResult.passed ? StageStatus.PASS : StageStatus.RETRY,
    producer: "testbench_generator",
    failure_class: simResult.passed ? FailureClass.UNKNOWN : FailureClass.EDA_TOOL_ERROR,
    consumable_payload: { sim_passed: simResult.passed, output_length: simResult.output.length },
    diagnostics: simResult.issues,
    artifacts_written: ["tb_code", "tb_path", "sim_output", "sim_passed"],
    next_action: simResult.passed ? "sdc_gen" : "diagnose",
  };
}

// ── Formal Verification ───────────────────────────────────────────────

export async function executeFormalVerifyStage(ctx: BuildContext): Promise<StageResult> {
  const rtlPath = ctx.artifacts["rtl_path"] as string;
  if (!rtlPath) {
    return {
      stage: "FORMAL_VERIFY",
      status: StageStatus.SKIP,
      producer: "formal_verifier",
      failure_class: FailureClass.UNKNOWN,
      consumable_payload: {},
      diagnostics: ["No RTL for formal verification"],
      artifacts_written: [],
      next_action: "skip",
    };
  }

  const role = getVerifierRole();
  const llm = new LlmClient(ctx.llm_config);
  const spec = ctx.artifacts["hw_spec"] as HardwareSpec;

  const prompt = `Generate Verilator-compatible SVA assertions for the following design.
DESIGN: ${ctx.name}
RTL:
${(ctx.artifacts["rtl_code"] as string).slice(0, 10000)}

CONTRACT:
${spec?.behavioral_contract?.map((b) => `GIVEN ${b.given} WHEN ${b.when} THEN ${b.then}`).join("\n") ?? ""}

Output a module named ${ctx.name}_sva with inline assertions. Verilator-compatible only.`;

  const raw = await llm.callText(role.systemPrompt, prompt, {
    temperature: 0.1,
    maxTokens: 8192,
  });

  const svaCode = sanitizeVerilogArtifact(raw);
  ctx.artifacts["sva_code"] = svaCode;

  return {
    stage: "FORMAL_VERIFY",
    status: StageStatus.PASS,
    producer: "formal_verifier",
    failure_class: FailureClass.UNKNOWN,
    consumable_payload: { sva_length: svaCode.length },
    diagnostics: [],
    artifacts_written: ["sva_code"],
    next_action: "sdc_gen",
  };
}
