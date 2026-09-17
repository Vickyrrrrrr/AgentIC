/**
 * SDC (Synopsys Design Constraints) generation stage.
 *
 * Ported from Python: agents/sdc_agent.py + orchestrator SDC_GEN stage
 */

import { LlmClient } from "../llm/index.js";
import { getSdcRole } from "../agents/index.js";
import { writeVerilog } from "../eda/index.js";
import { StageStatus, FailureClass } from "../types/index.js";
import type { BuildContext, HardwareSpec, StageResult } from "../types/index.js";
import { join } from "node:path";

export interface SdcGenInput {
  spec: HardwareSpec;
  rtlCode: string;
  designName: string;
  clockPeriodNs?: string;
  workspaceRoot: string;
}

export interface SdcGenOutput {
  sdcCode: string;
  sdcPath: string;
  issues: string[];
}

export async function generateSdc(
  llm: LlmClient,
  input: SdcGenInput,
): Promise<SdcGenOutput> {
  const role = getSdcRole();
  const clockPeriod = input.clockPeriodNs ?? "10.0";

  const portList = input.spec.ports
    .map((p) => `${p.direction} ${p.data_type} ${p.name}`)
    .join("\n");

  const prompt = `Generate an SDC (Synopsys Design Constraints) file for the following design.

DESIGN NAME: ${input.designName}
TARGET PDK: ${input.spec.target_pdk}
CLOCK PERIOD: ${clockPeriod} ns

TOP-LEVEL PORTS:
${portList}

RTL:
${input.rtlCode.slice(0, 8000)}

Generate standard SDC:
- create_clock -name clk -period ${clockPeriod} [get_ports clk]
- set_input_delay (20% of clock period)
- set_output_delay (20% of clock period)
- set_driving_cell
- set_load

Output ONLY valid SDC commands, no markdown, no explanations.`;

  const sdcCode = await llm.callText(role.systemPrompt, prompt, {
    temperature: 0.1,
    maxTokens: 4096,
  });

  const sdcPath = join(input.workspaceRoot, "designs", input.designName, "src", `${input.designName}.sdc`);
  await writeVerilog(sdcPath, sdcCode);

  return { sdcCode, sdcPath, issues: [] };
}

export async function executeSdcGenStage(ctx: BuildContext): Promise<StageResult> {
  const llm = new LlmClient(ctx.llm_config);
  const spec = ctx.artifacts["hw_spec"] as HardwareSpec;
  const rtlCode = ctx.artifacts["rtl_code"] as string;

  if (!spec || !rtlCode) {
    return {
      stage: "SDC_GEN",
      status: StageStatus.ERROR,
      producer: "sdc_generator",
      failure_class: FailureClass.ORCHESTRATOR_ROUTING_ERROR,
      consumable_payload: {},
      diagnostics: ["Missing artifacts for SDC generation"],
      artifacts_written: [],
      next_action: "fail",
    };
  }

  const { sdcCode, sdcPath, issues } = await generateSdc(llm, {
    spec,
    rtlCode,
    designName: ctx.name,
    clockPeriodNs: ctx.pdk_profile.default_clock_period,
    workspaceRoot: ctx.workspace_root,
  });

  ctx.artifacts["sdc_code"] = sdcCode;
  ctx.artifacts["sdc_path"] = sdcPath;

  return {
    stage: "SDC_GEN",
    status: StageStatus.PASS,
    producer: "sdc_generator",
    failure_class: FailureClass.UNKNOWN,
    consumable_payload: { sdc_path: sdcPath },
    diagnostics: issues,
    artifacts_written: ["sdc_code", "sdc_path"],
    next_action: "synthesis",
  };
}
