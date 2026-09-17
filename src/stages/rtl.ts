/**
 * RTL generation stage — generates synthesizable Verilog/SystemVerilog from a HardwareSpec.
 *
 * Ported from Python: src/agentic/orchestrator.py (RTL_GEN stage) + agents/designer.py
 */

import { LlmClient } from "../llm/index.js";
import { getDesignerRole } from "../agents/index.js";
import {
  sanitizeVerilogArtifact,
  isValidVerilog,
  writeVerilog,
  runSyntaxCheck,
} from "../eda/index.js";
import { StageStatus, FailureClass, BuildStrategy } from "../types/index.js";
import type { BuildContext, HardwareSpec, StageResult } from "../types/index.js";
import { join } from "node:path";

export interface RtlGenInput {
  spec: HardwareSpec;
  designName: string;
  strategy?: BuildStrategy;
  workspaceRoot: string;
  previousError?: string;
  existingRtl?: string;
}

export interface RtlGenOutput {
  rtlCode: string;
  rtlPath: string;
  isValid: boolean;
  issues: string[];
}

export async function generateRtl(
  llm: LlmClient,
  input: RtlGenInput,
): Promise<RtlGenOutput> {
  const strategy = input.strategy ?? BuildStrategy.SV_MODULAR;
  const role = getDesignerRole(strategy);
  const spec = input.spec;

  const portList = spec.ports
    .map((p) => `  ${p.direction} ${p.data_type} ${p.name} // ${p.description}`)
    .join("\n");

  const submoduleList = spec.submodules
    .map((s) => `  - ${s.name}: ${s.description}`)
    .join("\n");

  const contractList = spec.behavioral_contract
    .map((b) => `  GIVEN ${b.given} WHEN ${b.when} THEN ${b.then} WITHIN ${b.within}`)
    .join("\n");

  const errorContext = input.previousError
    ? `\n\nPREVIOUS ERROR — fix this:\n${input.previousError}\n`
    : "";

  const existingContext = input.existingRtl
    ? `\n\nEXISTING RTL (repair, do not rewrite from scratch):\n${input.existingRtl.slice(0, 8000)}\n`
    : "";

  const prompt = `Generate complete, synthesizable ${strategy === BuildStrategy.VERILOG_CLASSIC ? "Verilog-2005" : "SystemVerilog"} for the following hardware specification.

DESIGN NAME: ${input.designName}
TARGET PDK: ${spec.target_pdk}
CATEGORY: ${spec.design_category}
TARGET FREQUENCY: ${spec.target_frequency_mhz} MHz

TOP-LEVEL INTERFACE:
${portList}

SUBMODULES:
${submoduleList}

BEHAVIORAL CONTRACT:
${contractList}

RULES:
- Module name MUST be: ${input.designName}
- Include ALL submodules in the same file or generate separate files
- The top-level module MUST instantiate and wire together ALL sub-blocks
- Every output must be driven by exactly one source
- Output ONLY Verilog code, no explanations${errorContext}${existingContext}`;

  const raw = await llm.callText(role.systemPrompt, prompt, {
    temperature: 0.1,
    maxTokens: 16384,
  });

  const rtlCode = sanitizeVerilogArtifact(raw);
  const issues: string[] = [];

  if (!isValidVerilog(rtlCode)) {
    issues.push("RTL candidate is not valid Verilog/SystemVerilog code output.");
    return { rtlCode, rtlPath: "", isValid: false, issues };
  }

  const rtlPath = join(input.workspaceRoot, "designs", input.designName, "src", `${input.designName}.v`);
  await writeVerilog(rtlPath, rtlCode);

  const syntaxResult = await runSyntaxCheck(rtlPath);
  if (!syntaxResult.ok) {
    issues.push(`Syntax check failed: ${syntaxResult.stderr.slice(0, 2000)}`);
  }

  return {
    rtlCode,
    rtlPath,
    isValid: syntaxResult.ok,
    issues,
  };
}

export async function executeRtlGenStage(ctx: BuildContext): Promise<StageResult> {
  const llm = new LlmClient(ctx.llm_config);
  const spec = ctx.artifacts["hw_spec"] as HardwareSpec;

  if (!spec) {
    return {
      stage: "RTL_GEN",
      status: StageStatus.ERROR,
      producer: "rtl_generator",
      failure_class: FailureClass.ORCHESTRATOR_ROUTING_ERROR,
      consumable_payload: {},
      diagnostics: ["Missing hw_spec artifact"],
      artifacts_written: [],
      next_action: "fail",
    };
  }

  const { rtlCode, rtlPath, isValid, issues } = await generateRtl(llm, {
    spec,
    designName: ctx.name,
    strategy: ctx.strategy,
    workspaceRoot: ctx.workspace_root,
  });

  ctx.artifacts["rtl_code"] = rtlCode;
  ctx.artifacts["rtl_path"] = rtlPath;

  return {
    stage: "RTL_GEN",
    status: isValid ? StageStatus.PASS : StageStatus.RETRY,
    producer: "rtl_generator",
    failure_class: issues.length > 0 ? FailureClass.EDA_TOOL_ERROR : FailureClass.UNKNOWN,
    consumable_payload: { rtl_path: rtlPath, rtl_length: rtlCode.length },
    diagnostics: issues,
    artifacts_written: ["rtl_code", "rtl_path"],
    next_action: isValid ? "verification" : "rtl_fix",
  };
}

// ── RTL Fix Stage ─────────────────────────────────────────────────────

export async function fixRtl(
  llm: LlmClient,
  input: {
    rtlCode: string;
    rtlPath: string;
    errorText: string;
    designName: string;
    spec: HardwareSpec;
    strategy?: BuildStrategy;
  },
): Promise<RtlGenOutput> {
  const strategy = input.strategy ?? BuildStrategy.SV_MODULAR;
  const role = getDesignerRole(strategy);

  const prompt = `Fix the following Verilog code that failed compilation/simulation.

DESIGN NAME: ${input.designName}

ERROR:
${input.errorText.slice(0, 6000)}

CURRENT RTL:
${input.rtlCode.slice(0, 12000)}

SPEC REMINDER:
- Module name: ${input.designName}
- Ports: ${input.spec.ports.map((p) => `${p.direction} ${p.data_type} ${p.name}`).join(", ")}

Fix the error and output the COMPLETE corrected Verilog module. Do not output partial snippets.`;

  const raw = await llm.callText(role.systemPrompt, prompt, {
    temperature: 0.1,
    maxTokens: 16384,
  });

  const rtlCode = sanitizeVerilogArtifact(raw);
  const issues: string[] = [];

  if (!isValidVerilog(rtlCode)) {
    issues.push("RTL fix candidate is not valid Verilog.");
    return { rtlCode, rtlPath: input.rtlPath, isValid: false, issues };
  }

  await writeVerilog(input.rtlPath, rtlCode);

  const syntaxResult = await runSyntaxCheck(input.rtlPath);
  if (!syntaxResult.ok) {
    issues.push(`Syntax check after fix failed: ${syntaxResult.stderr.slice(0, 2000)}`);
  }

  return {
    rtlCode,
    rtlPath: input.rtlPath,
    isValid: syntaxResult.ok,
    issues,
  };
}

export async function executeRtlFixStage(ctx: BuildContext): Promise<StageResult> {
  const llm = new LlmClient(ctx.llm_config);
  const spec = ctx.artifacts["hw_spec"] as HardwareSpec;
  const rtlCode = ctx.artifacts["rtl_code"] as string;
  const rtlPath = ctx.artifacts["rtl_path"] as string;
  const lastError = ctx.artifacts["last_error"] as string;

  if (!spec || !rtlCode) {
    return {
      stage: "RTL_FIX",
      status: StageStatus.ERROR,
      producer: "rtl_fixer",
      failure_class: FailureClass.ORCHESTRATOR_ROUTING_ERROR,
      consumable_payload: {},
      diagnostics: ["Missing artifacts for RTL fix"],
      artifacts_written: [],
      next_action: "fail",
    };
  }

  const { rtlCode: fixedCode, isValid, issues } = await fixRtl(llm, {
    rtlCode,
    rtlPath,
    errorText: lastError ?? "",
    designName: ctx.name,
    spec,
    strategy: ctx.strategy,
  });

  ctx.artifacts["rtl_code"] = fixedCode;

  return {
    stage: "RTL_FIX",
    status: isValid ? StageStatus.PASS : StageStatus.RETRY,
    producer: "rtl_fixer",
    failure_class: issues.length > 0 ? FailureClass.EDA_TOOL_ERROR : FailureClass.UNKNOWN,
    consumable_payload: { rtl_length: fixedCode.length },
    diagnostics: issues,
    artifacts_written: ["rtl_code"],
    next_action: isValid ? "verification" : "rtl_fix",
  };
}
