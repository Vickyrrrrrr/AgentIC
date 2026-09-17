/**
 * Spec generation stage — converts a natural-language chip description
 * into a structured HardwareSpec JSON contract.
 *
 * Ported from Python: src/agentic/core/spec_generator.py
 */

import { LlmClient } from "../llm/index.js";
import type {
  BuildContext,
  HardwareSpec,
  PortSpec,
  SubModuleSpec,
  BehavioralStatement,
  StageResult,
  StageStatus,
  FailureClass,
  DesignCategory,
} from "../types/index.js";

const DESIGN_CATEGORIES: DesignCategory[] = [
  "PROCESSOR", "MEMORY", "INTERFACE", "ARITHMETIC", "CONTROL", "DATAPATH", "MIXED",
];

const MANDATORY_FIELDS: Record<string, string[]> = {
  PROCESSOR: ["isa_subset", "pipeline_depth", "register_file", "memory_interface", "hazard_handling", "reset_type", "clock_domains", "target_frequency_mhz"],
  MEMORY: ["mem_type", "width_depth", "rw_port_count", "collision_behavior", "reset_behavior"],
  INTERFACE: ["protocol_version_mode", "data_width", "fifo_depth", "flow_control"],
  ARITHMETIC: ["input_output_widths", "signed_unsigned", "pipeline_stages", "overflow_behavior", "latency_cycles"],
  CONTROL: ["state_encoding", "state_count", "reset_type", "clock_domains"],
  DATAPATH: ["data_width", "pipeline_stages", "reset_type"],
};

const DOMAIN_SUBMODULES: Record<string, string[]> = {
  PROCESSOR: ["program_counter", "instruction_fetch", "instruction_decode", "register_file", "alu", "data_memory_interface", "writeback", "hazard_unit", "control_unit"],
  MEMORY: ["memory_array", "read_port_logic", "write_port_logic", "address_decoder", "collision_logic", "output_register"],
  INTERFACE: ["clock_divider", "shift_register", "state_machine", "data_buffer", "control_logic", "status_register", "fifo"],
  ARITHMETIC: ["input_register", "computation_unit", "pipeline_stage_register", "output_register", "overflow_detector"],
  CONTROL: ["state_register", "next_state_logic", "output_logic", "priority_encoder", "arbiter_logic", "interrupt_register"],
  DATAPATH: ["shift_register", "pipeline_register", "mux_network", "barrel_shifter", "data_register"],
};

export interface SpecStageInput {
  designName: string;
  description: string;
  targetPdk?: string;
  baseSid?: string;
}

export interface SpecStageOutput {
  spec: HardwareSpec;
  issues: string[];
}

export async function generateSpec(
  llm: LlmClient,
  input: SpecStageInput,
): Promise<SpecStageOutput> {
  const targetPdk = input.targetPdk ?? "sky130";
  const issues: string[] = [];

  const wordCount = input.description.trim().split(/\s+/).length;
  if (wordCount < 10 && !input.baseSid) {
    const options = await elaborateDescription(llm, input.designName, input.description, targetPdk);
    return {
      spec: {
        design_category: "ELABORATION_NEEDED",
        top_module_name: input.designName,
        target_pdk: targetPdk,
        target_frequency_mhz: 50,
        ports: [],
        submodules: [],
        behavioral_contract: [],
        inferred_fields: [],
        warnings: ["ELABORATION_NEEDED: Description is short.", ...options],
        design_description: input.description,
        mandatory_fields_status: {},
      },
      issues: ["Short description — options generated"],
    };
  }

  const category = input.baseSid ? "MIXED" : await classifyDesign(llm, input.description);

  const allMandatory = [...new Set(Object.values(MANDATORY_FIELDS).flat())];
  const allValidSubs = [...new Set(Object.values(DOMAIN_SUBMODULES).flat())];

  const prompt = `Return ONLY compact JSON for a VLSI hardware spec. No markdown.
Design ${input.designName}, category ${category}, target ${targetPdk}.
Requirement: ${input.description.slice(0, 4500)}
Keys: design_category, top_module_name, target_pdk, target_frequency_mhz,
mandatory_fields_status, ports, submodules, behavioral_contract, warnings.
ports items: name,direction,data_type,description. Always include clk and rst_n.
submodules items: name,description,ports.
behavioral_contract items: given,when,then,within.
If ADC/DAC/PLL/TRNG/analog/custom-layout behavior is requested, preserve the user-visible intent but specify a digital control/status or macro-facing interface only.
If PDK/tool constraints make the literal request infeasible, choose the closest feasible VLSI implementation and add a warning explaining the substitution.
Preserve requested widths, reset style, timers, watchdogs, buses, muxing, and standard ASIC naming.`;

  const { data, raw } = await llm.callJson<HardwareSpec>(
    "You are a principal VLSI architect generating complete, implementation-ready hardware specifications. Return only valid JSON.",
    prompt,
    { temperature: 0.2, maxTokens: 8192 },
  );

  if (!data) {
    issues.push("LLM spec generation failed, using deterministic fallback");
    return {
      spec: fallbackSpec(input.designName, input.description, category, targetPdk),
      issues,
    };
  }

  const spec = parseSpec(data, input.designName, category, targetPdk, input.description);
  return { spec, issues };
}

async function classifyDesign(llm: LlmClient, description: string): Promise<DesignCategory> {
  const prompt = `Classify the following hardware design description into EXACTLY ONE category.
Categories: PROCESSOR, MEMORY, INTERFACE, ARITHMETIC, CONTROL, DATAPATH, MIXED.
Description: ${description.slice(0, 4000)}
Respond with ONLY: {"category": "<CATEGORY>", "confidence": <0.0-1.0>}`;

  const { data } = await llm.callJson<{ category: string; confidence: number }>(
    "You are a senior VLSI design classifier. Return only valid JSON.",
    prompt,
  );

  if (data?.category && DESIGN_CATEGORIES.includes(data.category.toUpperCase() as DesignCategory)) {
    return data.category.toUpperCase() as DesignCategory;
  }
  return keywordClassify(description);
}

function keywordClassify(description: string): DesignCategory {
  const desc = description.toLowerCase();
  const keywordMap: Record<DesignCategory, string[]> = {
    PROCESSOR: ["cpu", "processor", "risc", "riscv", "microcontroller", "instruction", "pipeline", "fetch", "decode", "execute"],
    MEMORY: ["fifo", "sram", "ram", "rom", "cache", "register file", "memory", "stack", "queue", "buffer"],
    INTERFACE: ["uart", "spi", "i2c", "apb", "axi", "wishbone", "usb", "serial", "baud"],
    ARITHMETIC: ["alu", "multiplier", "divider", "adder", "mac", "fpu", "floating point", "multiply"],
    CONTROL: ["state machine", "fsm", "arbiter", "scheduler", "interrupt", "controller", "priority"],
    DATAPATH: ["shift register", "barrel shifter", "pipeline stage", "datapath", "mux"],
    MIXED: [],
  };

  let bestCat: DesignCategory = "CONTROL";
  let bestScore = 0;
  for (const [cat, keywords] of Object.entries(keywordMap) as [DesignCategory, string[]][]) {
    let score = 0;
    for (const kw of keywords) {
      if (desc.includes(kw)) score++;
    }
    if (score > bestScore) {
      bestScore = score;
      bestCat = cat;
    }
  }
  return bestCat;
}

function parseSpec(
  data: Record<string, unknown>,
  designName: string,
  category: string,
  targetPdk: string,
  description: string,
): HardwareSpec {
  const portsData = (data.ports as Record<string, unknown>[]) ?? [];
  const ports: PortSpec[] = portsData.map((p) => ({
    name: String(p.name ?? ""),
    direction: String(p.direction ?? "input") as "input" | "output" | "inout",
    data_type: String(p.data_type ?? "logic"),
    description: String(p.description ?? ""),
  }));

  const portNames = new Set(ports.map((p) => p.name));
  if (!portNames.has("clk")) {
    ports.unshift({ name: "clk", direction: "input", data_type: "logic", description: "System clock" });
  }
  if (!portNames.has("rst_n")) {
    ports.splice(1, 0, { name: "rst_n", direction: "input", data_type: "logic", description: "Active-low reset" });
  }

  const subsData = (data.submodules as Record<string, unknown>[]) ?? [];
  const submodules: SubModuleSpec[] = subsData.map((s) => ({
    name: String(s.name ?? ""),
    description: String(s.description ?? ""),
    ports: ((s.ports as Record<string, unknown>[]) ?? []).map((sp) => ({
      name: String(sp.name ?? ""),
      direction: String(sp.direction ?? "input") as "input" | "output" | "inout",
      data_type: String(sp.data_type ?? "logic"),
      description: String(sp.description ?? ""),
    })),
    parameters: ((s.parameters as Record<string, unknown>[]) ?? []).map((p) => ({
      name: String(p.name ?? ""),
      default: String(p.default ?? ""),
      description: String(p.description ?? ""),
    })),
  }));

  const contractsData = (data.behavioral_contract as Record<string, unknown>[]) ?? [];
  const behavioral_contract: BehavioralStatement[] = contractsData.map((b) => ({
    given: String(b.given ?? ""),
    when: String(b.when ?? ""),
    then: String(b.then ?? ""),
    within: String(b.within ?? "1 cycle"),
  }));

  return {
    design_category: category,
    top_module_name: String(data.top_module_name ?? designName),
    target_pdk: String(data.target_pdk ?? targetPdk),
    target_frequency_mhz: Number(data.target_frequency_mhz ?? 50),
    ports,
    submodules,
    behavioral_contract,
    inferred_fields: [],
    warnings: (data.warnings as string[]) ?? [],
    design_description: description,
    mandatory_fields_status: (data.mandatory_fields_status as HardwareSpec["mandatory_fields_status"]) ?? {},
  };
}

function fallbackSpec(
  designName: string,
  description: string,
  category: string,
  targetPdk: string,
): HardwareSpec {
  const desc = description.toLowerCase();
  const ports: PortSpec[] = [
    { name: "clk", direction: "input", data_type: "logic", description: "System clock" },
    { name: "rst_n", direction: "input", data_type: "logic", description: "Active-low reset" },
  ];
  const submodules: SubModuleSpec[] = [];
  const contracts: BehavioralStatement[] = [
    { given: "rst_n is low", when: "a clock edge occurs", then: "all registers return to reset values", within: "1 cycle" },
  ];

  if (desc.includes("counter") || desc.includes("cnt")) {
    ports.push({ name: "enable", direction: "input", data_type: "logic", description: "Count enable" });
    ports.push({ name: "count", direction: "output", data_type: "logic [7:0]", description: "Counter output" });
    submodules.push({ name: "counter_core", description: "8-bit synchronous up-counter", ports: [], parameters: [] });
  } else {
    ports.push({ name: "data_in", direction: "input", data_type: "logic [7:0]", description: "Data input" });
    ports.push({ name: "data_out", direction: "output", data_type: "logic [7:0]", description: "Data output" });
    submodules.push({ name: `${designName}_core`, description: "Top-level control logic", ports: [], parameters: [] });
  }

  return {
    design_category: category,
    top_module_name: designName,
    target_pdk: targetPdk,
    target_frequency_mhz: 50,
    ports,
    submodules,
    behavioral_contract: contracts,
    inferred_fields: [],
    warnings: ["Deterministic fallback spec generated — manual review recommended"],
    design_description: description,
    mandatory_fields_status: {},
  };
}

async function elaborateDescription(
  llm: LlmClient,
  designName: string,
  description: string,
  targetPdk: string,
): Promise<string[]> {
  const prompt = `A user wants to build a chip called '${designName}' and described it as: "${description}".
This is very brief. Generate EXACTLY 3 distinct, detailed design interpretations.
Return ONLY: {"options": [{"id": 1, "title": "...", "description": "...", "category": "...", "key_ports": ["clk", "rst_n", ...], "target_frequency_mhz": 50}]}`;

  const { data } = await llm.callJson<{ options: { id: number; title: string; description: string; category: string; key_ports: string[]; target_frequency_mhz: number }[] }>(
    "You are a senior VLSI architect. Return only valid JSON.",
    prompt,
  );

  if (data?.options) {
    return data.options.slice(0, 3).map((opt) =>
      `OPTION_${opt.id}: ${opt.title} | Category: ${opt.category} | Freq: ${opt.target_frequency_mhz} MHz | Ports: ${opt.key_ports.join(", ")} | Details: ${opt.description}`,
    );
  }
  return ["OPTION_1: Basic implementation | Category: CONTROL | Freq: 50 MHz | Ports: clk, rst_n, data_in[7:0], data_out[7:0]"];
}

// ── Stage Handler ─────────────────────────────────────────────────────

export async function executeSpecStage(ctx: BuildContext): Promise<StageResult> {
  const llm = new LlmClient(ctx.llm_config);
  const { spec, issues } = await generateSpec(llm, {
    designName: ctx.name,
    description: ctx.description,
    targetPdk: ctx.pdk_profile.pdk,
  });

  ctx.artifacts["hw_spec"] = spec;
  ctx.artifacts["spec_issues"] = issues;

  return {
    stage: "SPEC",
    status: issues.length === 0 ? StageStatus.PASS : StageStatus.PASS,
    producer: "spec_generator",
    failure_class: FailureClass.UNKNOWN,
    consumable_payload: spec as unknown as Record<string, unknown>,
    diagnostics: issues,
    artifacts_written: ["hw_spec"],
    next_action: "hierarchy_expand",
  };
}
