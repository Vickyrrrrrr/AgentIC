"""
Hardware Specification Generator — Rigorous 6-Stage Spec Pipeline
=================================================================

Takes a user's plain English hardware description and produces a complete,
unambiguous, implementation-ready hardware specification (JSON contract).

This is the first and most critical stage in the autonomous chip design pipeline.
Every mistake here gets amplified by every stage after.

Stages:
  1. CLASSIFY   — Categorise the design (PROCESSOR / MEMORY / INTERFACE / etc.)
  2. COMPLETE   — Completeness check against mandatory fields per category
  3. DECOMPOSE  — Module decomposition with domain validation
  4. INTERFACE  — Top-level interface specification
  5. CONTRACT   — Behavioral contract (GIVEN/WHEN/THEN assertions)
  6. OUTPUT     — Structured JSON output with warnings
"""

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from crewai import Agent, Task, Crew, LLM

from ..contracts import robust_json_extract

logger = logging.getLogger(__name__)


# ─── Design Categories ───────────────────────────────────────────────

DESIGN_CATEGORIES = [
    "PROCESSOR",  # CPU, microcontroller, DSP core, RISC-V, ARM-like
    "MEMORY",  # FIFO, SRAM, ROM, cache, register file
    "INTERFACE",  # UART, SPI, I2C, APB, AXI, Wishbone, USB
    "ARITHMETIC",  # ALU, multiplier, divider, FPU, MAC
    "CONTROL",  # State machine, arbiter, scheduler, interrupt controller
    "DATAPATH",  # Pipeline stage, shift register, barrel shifter
    "MIXED",  # Contains two or more of the above
]

# ─── Mandatory Fields Per Category ───────────────────────────────────

MANDATORY_FIELDS = {
    "PROCESSOR": [
        "isa_subset",
        "pipeline_depth",
        "register_file",
        "memory_interface",
        "hazard_handling",
        "reset_type",
        "clock_domains",
        "target_frequency_mhz",
    ],
    "MEMORY": [
        "mem_type",
        "width_depth",
        "rw_port_count",
        "collision_behavior",
        "reset_behavior",
    ],
    "INTERFACE": [
        "protocol_version_mode",
        "data_width",
        "fifo_depth",
        "flow_control",
    ],
    "ARITHMETIC": [
        "input_output_widths",
        "signed_unsigned",
        "pipeline_stages",
        "overflow_behavior",
        "latency_cycles",
    ],
    "CONTROL": [
        "state_encoding",
        "state_count",
        "reset_type",
        "clock_domains",
    ],
    "DATAPATH": [
        "data_width",
        "pipeline_stages",
        "reset_type",
    ],
}

# ─── Domain-Valid Submodule Names ─────────────────────────────────────

DOMAIN_SUBMODULES = {
    "PROCESSOR": [
        "program_counter",
        "instruction_memory_interface",
        "instruction_fetch",
        "instruction_decode",
        "register_file",
        "alu",
        "data_memory_interface",
        "writeback",
        "hazard_unit",
        "branch_predictor",
        "pipeline_register",
        "control_unit",
    ],
    "MEMORY": [
        "memory_array",
        "read_port_logic",
        "write_port_logic",
        "address_decoder",
        "collision_logic",
        "output_register",
    ],
    "INTERFACE": [
        "clock_divider",
        "shift_register",
        "state_machine",
        "data_buffer",
        "control_logic",
        "status_register",
        "fifo",
    ],
    "ARITHMETIC": [
        "input_register",
        "computation_unit",
        "pipeline_stage_register",
        "output_register",
        "overflow_detector",
    ],
    "CONTROL": [
        "state_register",
        "next_state_logic",
        "output_logic",
        "priority_encoder",
        "arbiter_logic",
        "interrupt_register",
    ],
    "DATAPATH": [
        "shift_register",
        "pipeline_register",
        "mux_network",
        "barrel_shifter",
        "data_register",
    ],
}


# ─── Safe Defaults (Convention-Based Inference) ──────────────────────

SAFE_DEFAULTS = {
    "reset_type": {
        "value": "synchronous active-low",
        "reasoning": "Active-low synchronous reset is standard for Sky130 PDK and ASIC flows",
    },
    "clock_domains": {
        "value": "single",
        "reasoning": "Single clock domain is the default unless explicitly specified",
    },
    "reset_behavior": {
        "value": "all_zeros",
        "reasoning": "Resetting all registers to zero is standard practice for deterministic startup",
    },
    "state_encoding": {
        "value": "binary",
        "reasoning": "Binary encoding is the default for small FSMs; one-hot selected automatically by synthesis tools for larger FSMs",
    },
    "flow_control": {
        "value": "none",
        "reasoning": "No flow control by default unless buffering or handshaking is specified",
    },
    "collision_behavior": {
        "value": "write_first",
        "reasoning": "Write-first is the most common RAM collision policy in FPGA/ASIC memory compilers",
    },
    "signed_unsigned": {
        "value": "unsigned",
        "reasoning": "Default to unsigned arithmetic unless explicitly stated otherwise",
    },
}


# ─── Output Dataclass ────────────────────────────────────────────────


@dataclass
class PortSpec:
    name: str
    direction: str  # "input" | "output" | "inout"
    data_type: str  # "logic" | "logic [N:0]"
    description: str = ""

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass
class SubModuleSpec:
    name: str
    description: str = ""
    ports: List[PortSpec] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class BehavioralStatement:
    given: str
    when: str
    then: str
    within: str  # e.g. "1 cycle"

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)

    def __str__(self) -> str:
        return f"GIVEN {self.given} WHEN {self.when} THEN {self.then} WITHIN {self.within}"


@dataclass
class InferredField:
    field_name: str
    inferred_value: str
    reasoning: str

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass
class HardwareSpec:
    """Complete hardware specification — output of the 6-stage pipeline."""

    design_category: str
    top_module_name: str
    target_pdk: str = "sky130"
    target_frequency_mhz: int = 50
    ports: List[PortSpec] = field(default_factory=list)
    submodules: List[SubModuleSpec] = field(default_factory=list)
    behavioral_contract: List[BehavioralStatement] = field(default_factory=list)
    inferred_fields: List[InferredField] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    # Extra metadata for downstream pipeline
    design_description: str = ""
    mandatory_fields_status: Dict[str, str] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "design_category": self.design_category,
            "top_module_name": self.top_module_name,
            "target_pdk": self.target_pdk,
            "target_frequency_mhz": self.target_frequency_mhz,
            "ports": [p.to_dict() for p in self.ports],
            "submodules": [s.to_dict() for s in self.submodules],
            "behavioral_contract": [b.to_dict() for b in self.behavioral_contract],
            "inferred_fields": [f.to_dict() for f in self.inferred_fields],
            "warnings": self.warnings,
            "design_description": self.design_description,
            "mandatory_fields_status": self.mandatory_fields_status,
        }

    @classmethod
    def from_json(cls, json_str: str) -> "HardwareSpec":
        data = json.loads(json_str)

        # Helper to get keys even if they have extra quotes (LLM glitch)
        def get_val(d, key, default=None):
            if key in d: return d[key]
            # Try with literal quotes
            quoted = f'"{key}"'
            if quoted in d: return d[quoted]
            return default

        ports_data = get_val(data, "ports", [])
        ports = [PortSpec(**p) for p in ports_data]
        
        subs_data = get_val(data, "submodules", [])
        subs = []
        for s in subs_data:
            s_name = get_val(s, "name", "unknown_sub")
            s_desc = get_val(s, "description", "")
            s_ports_data = get_val(s, "ports", [])
            s_ports = [PortSpec(**p) for p in s_ports_data]
            subs.append(SubModuleSpec(name=s_name, description=s_desc, ports=s_ports))

        contracts_data = get_val(data, "behavioral_contract", [])
        contracts = [BehavioralStatement(**b) for b in contracts_data]
        
        inferred_data = get_val(data, "inferred_fields", [])
        inferred = [InferredField(**f) for f in inferred_data]

        # Cleanup data for remaining kwargs
        for k in ["ports", "submodules", "behavioral_contract", "inferred_fields", '"ports"', '"submodules"', '"behavioral_contract"', '"inferred_fields"']:
            data.pop(k, None)

        return cls(
            ports=ports,
            submodules=subs,
            behavioral_contract=contracts,
            inferred_fields=inferred,
            **data,
        )


# ─── Classification Prompt ───────────────────────────────────────────

CLASSIFY_PROMPT = """\
You are a senior VLSI architect. Classify the following hardware design description
into EXACTLY ONE category. If the design spans multiple categories, use MIXED and
list which categories it combines.

Categories:
- PROCESSOR: CPU, microcontroller, DSP core, RISC-V, ARM-like
- MEMORY: FIFO, SRAM, ROM, cache, register file
- INTERFACE: UART, SPI, I2C, APB, AXI, Wishbone, USB
- ARITHMETIC: ALU, multiplier, divider, FPU, MAC
- CONTROL: state machine, arbiter, scheduler, interrupt controller
- DATAPATH: pipeline stage, shift register, barrel shifter
- MIXED: contains two or more of the above

Design description:
{description}

Respond with ONLY a JSON object:
{{"category": "<CATEGORY>", "sub_categories": ["<if MIXED>"], "confidence": <0.0-1.0>, "reasoning": "<one sentence>"}}
"""


# ─── Completeness + Decomposition + Contract Prompt ──────────────────

SPEC_GENERATION_PROMPT = """\
You are a senior VLSI architect generating a complete hardware specification.
The design has been classified as: {category}

Design description: {description}
Design name: {design_name}
Target PDK: {target_pdk}

Perform ALL of the following steps and return a single JSON object:

STEP 1 — COMPLETENESS CHECK
For this {category} design, check these mandatory fields:
{mandatory_fields}

For each field:
- If present in description → set status to "present" with the value
- If safely inferable from standard practice → set status to "inferred" with value and reasoning
- If missing and no safe default → set status to "missing"

Safe defaults you may use:
- Reset: synchronous active-low (standard for {target_pdk})
- Clock: single domain unless explicitly specified
- Memory reset: all zeros
- FSM encoding: binary for small FSMs
- Arithmetic: unsigned unless stated otherwise

STEP 2 — MODULE DECOMPOSITION
Decompose into sub-modules. Rules:
- Maximum 8 sub-modules
- Each must have: name (snake_case), one-sentence description, complete port list
- Valid sub-module names for {category}: {valid_submodules}
- Every sub-module must correspond to a standard hardware component
- No overlapping responsibilities between sub-modules

STEP 3 — TOP-LEVEL INTERFACE
Define all top-level ports:
- Always include clk (input) and rst_n (input)
- Every port: name, direction (input/output/inout), data type (logic/logic[N:0])
- No floating ports — every port must have a defined purpose
- Justify every bus width

STEP 4 — BEHAVIORAL CONTRACT
Write precise English statements a testbench engineer can use for assertions.
Format: GIVEN/WHEN/THEN/WITHIN
Minimum requirements:
- 1 reset behavior statement
- 1 statement per major operation type
- 1 statement per edge case (overflow, empty, hazard, timeout)

STEP 5 — WARNINGS
List every assumption that could affect correctness. If you have zero warnings,
you are being overconfident — look again.

CRITICAL JSON RULES: 
- NEVER use unescaped double-quotes inside string values (escape them like \\\"). 
- DO NOT leave trailing commas at the end of objects or arrays.
- Double check that every brace and bracket has a matching closing pair.

Return ONLY this JSON (no markdown fences, no commentary):
{{
  "design_category": "{category}",
  "top_module_name": "<snake_case>",
  "target_pdk": "sky130",
  "target_frequency_mhz": <integer>,
  "mandatory_fields_status": {{
    "<field_name>": {{"status": "present|inferred|missing", "value": "<value>", "reasoning": "<if inferred>"}}
  }},
  "ports": [
    {{"name": "<name>", "direction": "input|output", "data_type": "logic|logic [N:0]", "description": "<purpose>"}}
  ],
  "submodules": [
    {{
      "name": "<snake_case>",
      "description": "<one sentence>",
      "ports": [
        {{"name": "<name>", "direction": "input|output", "data_type": "logic|logic [N:0]", "description": "<purpose>"}}
      ]
    }}
  ],
  "behavioral_contract": [
    {{"given": "<precondition>", "when": "<trigger>", "then": "<expected>", "within": "<latency>"}}
  ],
  "warnings": ["<assumption that could affect correctness>"]
}}
"""


# ─── The Spec Generator ─────────────────────────────────────────────


class HardwareSpecGenerator:
    """
    6-stage hardware specification generator.

    Takes a plain English hardware description and produces a complete,
    unambiguous HardwareSpec that can be consumed by the Architect SID
    decomposer for RTL generation.
    """

    def __init__(self, llm: LLM, verbose: bool = False, max_retries: int = 3):
        self.llm = llm
        self.verbose = verbose
        self.max_retries = max_retries

    @staticmethod
    def _strip_provider_prefix(model: str) -> str:
        if "/" in model and model.split("/", 1)[0] in {
            "openai",
            "infinity",
            "generic",
            "openrouter",
            "together_ai",
            "deepseek",
            "ollama",
        }:
            return model.split("/", 1)[1]
        return model

    @staticmethod
    def _extra_body_from_env() -> Dict[str, Any]:
        raw = os.environ.get("LLM_EXTRA_BODY_JSON", "").strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            logger.warning("[SpecGen] Ignoring invalid LLM_EXTRA_BODY_JSON")
            return {}

    def _direct_llm_call(self, prompt: str, system: str) -> Optional[str]:
        """Call an OpenAI-compatible endpoint directly for spec JSON generation.

        CrewAI is still used elsewhere, but the spec stage must be transparent and
        fail fast. This path uses the already-configured LLM/base_url/api_key and
        avoids silently falling into deterministic fallback because of CrewAI
        provider adapter behavior.
        """
        if os.environ.get("AGENTIC_SPECGEN_DIRECT_LLM", "1").strip().lower() in {
            "0",
            "false",
            "no",
            "off",
        }:
            return None

        model = getattr(self.llm, "model", "") if self.llm else ""
        base_url = getattr(self.llm, "base_url", "") if self.llm else ""
        api_key = getattr(self.llm, "api_key", "") if self.llm else ""
        model = model or os.environ.get("LLM_MODEL", "")
        base_url = base_url or os.environ.get("LLM_BASE_URL", "")
        api_key = api_key or os.environ.get("LLM_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
        if not model or not base_url or not api_key:
            return None

        try:
            from openai import OpenAI

            timeout_s = float(os.environ.get("AGENTIC_SPECGEN_LLM_TIMEOUT", "90"))
            max_tokens = int(
                os.environ.get(
                    "AGENTIC_SPECGEN_MAX_TOKENS",
                    str(max(4096, int(getattr(self.llm, "max_tokens", 4096) or 4096))),
                )
            )
            client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout_s)
            response = client.chat.completions.create(
                model=self._strip_provider_prefix(model),
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=float(getattr(self.llm, "temperature", 0.2) or 0.2),
                max_tokens=max_tokens,
                extra_body=self._extra_body_from_env(),
            )
            message = response.choices[0].message
            content = (message.content or "").strip()
            if content:
                return content
            reasoning = getattr(message, "reasoning_content", "") or ""
            if reasoning.strip():
                logger.warning(
                    "[SpecGen] Direct LLM returned reasoning but empty final content; "
                    "increase AGENTIC_SPECGEN_MAX_TOKENS or disable thinking at provider."
                )
            return None
        except Exception as exc:
            logger.warning(f"[SpecGen] Direct LLM call failed: {exc}")
            return None

    def _crew_llm_call(self, agent: Agent, task: Task) -> str:
        return str(Crew(agents=[agent], tasks=[task]).kickoff())

    def _llm_call(self, prompt: str, system: str, agent: Agent, task: Task) -> str:
        direct = self._direct_llm_call(prompt, system)
        if direct:
            return direct
        return self._crew_llm_call(agent, task)

    def _compact_spec_prompt(
        self,
        design_name: str,
        description: str,
        target_pdk: str,
        category: str,
        base_sid: Optional[str] = None,
    ) -> str:
        sid_note = ""
        if base_sid:
            sid_note = (
                " Preserve/refine the existing SID architecture; do not collapse to a minimal template."
            )
        return f"""\
Return ONLY compact JSON for a VLSI hardware spec. No markdown.
Design {design_name}, category {category}, target {target_pdk}.
Requirement: {description[:4500]}{sid_note}
Keys: design_category, top_module_name, target_pdk, target_frequency_mhz,
mandatory_fields_status, ports, submodules, behavioral_contract, warnings.
ports items: name,direction,data_type,description. Always include clk and rst_n.
submodules items: name,description,ports.
behavioral_contract items: given,when,then,within.
If ADC/DAC/PLL/analog is requested, specify digital control/status or macro-facing interface only.
Preserve requested widths, reset style, timers, watchdogs, buses, muxing, and standard ASIC naming.
"""

    def generate(
        self,
        design_name: str,
        description: str,
        target_pdk: str = "sky130",
        base_sid: Optional[str] = None,
    ) -> Tuple[HardwareSpec, List[str]]:
        """
        Main entry point: generate a complete hardware specification.

        Args:
            design_name: Verilog-safe design name
            description: Natural language hardware description
            target_pdk: Target PDK (sky130, gf180)
            base_sid:   (Optional) Existing architectural plan to preserve
        """
        issues: List[str] = []

        # ── Gate: short descriptions get LLM elaboration ──
        word_count = len(description.strip().split())
        if word_count < 10 and not base_sid:
            logger.info(
                f"[SpecGen] Description is short ({word_count} words) — elaborating via LLM"
            )
            options = self._elaborate_description(design_name, description, target_pdk=target_pdk)
            spec = HardwareSpec(
                design_category="ELABORATION_NEEDED",
                top_module_name=design_name,
                design_description=description,
                warnings=[f"ELABORATION_NEEDED: Description is short."] + options,
            )
            return spec, [f"Short description — options generated"]

        # ── Stage 1-5: Updated full spec via LLM ──
        logger.info(f"[SpecGen] Generating full spec for '{design_name}' (PDK={target_pdk})")
        spec, gen_issues = self._generate_full_spec(design_name, description, target_pdk, base_sid)
        issues.extend(gen_issues)

        return spec, issues

    def to_sid_enrichment(self, spec: HardwareSpec) -> Dict[str, Any]:
        """
        Convert a HardwareSpec into enrichment data for the Architect SID.
        
        This adds behavioral hints and port justifications that help the
        recursive RTL generator produce better code.
        """
        hints = []
        for stmt in spec.behavioral_contract:
            hints.append(str(stmt))
            
        return {
            "category": spec.design_category,
            "verification_hints_from_spec": hints,
            "inferred_parameters": {
                f.field_name: f.inferred_value for f in spec.inferred_fields
            },
            "pdk_constraints": {
                "target_frequency_mhz": spec.target_frequency_mhz,
                "pdk": spec.target_pdk
            }
        }

    def _elaborate_description(
        self, design_name: str, description: str, target_pdk: str = "sky130"
    ) -> List[str]:
        """
        When the user's description is short or vague, use LLM VLSI knowledge to
        generate 3 concrete, expert-level design options and return them as a list
        of strings (one per option) suitable for the orchestrator to present.
        """
        prompt = f"""\
You are a senior VLSI architect. A user wants to build a chip called '{design_name}' and described it as:

    "{description}"

This is very brief. Using your expertise, generate EXACTLY 3 distinct, detailed design interpretations
for this chip. Each option should specify the architectural variant, key features, I/O ports, and 
typical use cases. Make each option meaningfully different from the others.

CRITICAL HARDWARE CONSTRAINT:
The target PDK is {target_pdk.upper()}. 
Ensure that the `target_frequency_mhz` for every option is physically realizable on {target_pdk.upper()}.
For Sky130, standard digital logic rarely exceeds 50-100 MHz reliably without extreme pipelining.
DO NOT suggest frequencies above the safe upper limits of the {target_pdk.upper()} PDK.

Return ONLY this JSON (no markdown, no commentary):
{{
  "options": [
    {{
      "id": 1,
      "title": "<short title, max 8 words>",
      "description": "<2-3 sentence detailed technical description including: bit-widths, port count, reset style, key functionality, and typical realizable target clock frequency on {target_pdk}>",
      "category": "<PROCESSOR|MEMORY|INTERFACE|ARITHMETIC|CONTROL|DATAPATH>",
      "key_ports": ["clk", "rst_n", "<port1>", "<port2>"],
      "target_frequency_mhz": <number>
    }},
    {{
      "id": 2,
      "title": "<short title>",
      "description": "<detailed description>",
      "category": "<category>",
      "key_ports": ["clk", "rst_n", "<port1>"],
      "target_frequency_mhz": <number>
    }},
    {{
      "id": 3,
      "title": "<short title>",
      "description": "<detailed description>",
      "category": "<category>",
      "key_ports": ["clk", "rst_n", "<port1>"],
      "target_frequency_mhz": <number>
    }}
  ]
}}
"""
        try:
            agent = Agent(
                role="VLSI Design Advisor",
                goal=f"Generate 3 detailed design options for '{design_name}'",
                backstory=(
                    "You are a principal VLSI architect with 25 years of experience designing "
                    "chips for Sky130 and GF180. You excel at interpreting vague hardware requirements "
                    "and proposing concrete, implementable architectures with precise specifications."
                ),
                llm=self.llm,
                verbose=self.verbose,
            )
            task = Task(
                description=prompt,
                expected_output="JSON with 3 design options",
                agent=agent,
            )
            raw = self._llm_call(
                prompt=prompt,
                system="You are a senior VLSI architect. Return only valid JSON.",
                agent=agent,
                task=task,
            )
            data, _ = robust_json_extract(
                raw, context="spec_elaboration", required_keys=["options"]
            )

            if data and isinstance(data.get("options"), list):
                result = []
                for opt in data["options"][:3]:
                    opt_id = opt.get("id", "?")
                    title = opt.get("title", "Option")
                    desc = opt.get("description", "")
                    category = opt.get("category", "")
                    ports = ", ".join(opt.get("key_ports", [])[:6])
                    freq = opt.get("target_frequency_mhz", 50)
                    result.append(
                        f"OPTION_{opt_id}: {title} | "
                        f"Category: {category} | "
                        f"Freq: {freq} MHz | "
                        f"Ports: {ports} | "
                        f"Details: {desc}"
                    )
                return result

        except Exception as e:
            logger.warning(f"[SpecGen] Elaboration LLM failed: {e}")

        # Fallback: rule-based options based on common design patterns
        name_lower = design_name.lower()
        if any(kw in name_lower for kw in ["counter", "cnt"]):
            return [
                f"OPTION_1: Simple Up-Counter | Category: CONTROL | Freq: 50 MHz | "
                f"Ports: clk, rst_n, enable, count[7:0] | "
                f"Details: 8-bit synchronous up-counter with active-low reset and clock enable. "
                f"Counts 0-255, wraps around. Single clock domain. Target 50 MHz on Sky130.",
                f"OPTION_2: Up-Down Counter with Load | Category: CONTROL | Freq: 50 MHz | "
                f"Ports: clk, rst_n, enable, dir, load, d[7:0], count[7:0] | "
                f"Details: 8-bit bidirectional counter with parallel load and direction control. "
                f"Supports up/down counting and preload of arbitrary values.",
                f"OPTION_3: Programmable Counter with Terminal Count | Category: CONTROL | Freq: 100 MHz | "
                f"Ports: clk, rst_n, enable, load, d[7:0], count[7:0], tc | "
                f"Details: 8-bit counter with programmable terminal count compare and TC flag output. "
                f"Auto-reloads on terminal count. Suitable for PWM and timer applications.",
            ]
        else:
            return [
                f"OPTION_1: Basic {design_name} (minimal) | Category: CONTROL | Freq: 50 MHz | "
                f"Ports: clk, rst_n, data_in[7:0], data_out[7:0], valid | "
                f"Details: Minimal synchronous implementation with 8-bit data path, active-low reset, "
                f"and valid handshake. Single clock domain, 50 MHz target.",
                f"OPTION_2: Pipelined {design_name} | Category: DATAPATH | Freq: 100 MHz | "
                f"Ports: clk, rst_n, data_in[15:0], data_out[15:0], valid_in, valid_out | "
                f"Details: 2-stage pipelined 16-bit datapath implementation. Back-to-back throughput "
                f"of 1 result/cycle after 2-cycle latency. 100 MHz target on Sky130.",
                f"OPTION_3: {design_name} with AXI-Lite interface | Category: INTERFACE | Freq: 50 MHz | "
                f"Ports: clk, rst_n, awaddr, awvalid, awready, wdata, wvalid, wready, bresp, bvalid, bready | "
                f"Details: Full AXI4-Lite slave wrapper around the core logic for register-mapped "
                f"configuration from a host processor. 32-bit address/data.",
            ]

    def _classify(self, description: str) -> Tuple[Optional[str], List[str]]:
        """Stage 1: Classify the design into a category."""
        issues = []

        prompt = CLASSIFY_PROMPT.format(description=description[:4000])

        agent = Agent(
            role="VLSI Design Classifier",
            goal="Classify a hardware design into exactly one category",
            backstory="Senior VLSI architect who classifies designs for the spec pipeline.",
            llm=self.llm,
            verbose=self.verbose,
        )
        task = Task(
            description=prompt,
            expected_output="JSON object with category, confidence, and reasoning",
            agent=agent,
        )

        try:
            raw = self._llm_call(
                prompt=prompt,
                system="You are a senior VLSI design classifier. Return only valid JSON.",
                agent=agent,
                task=task,
            )
            data, success = robust_json_extract(
                raw, context="spec_classify", required_keys=["category"]
            )

            if not success or data is None:
                issues.append("Classification LLM output was not valid JSON")
                # Attempt keyword-based fallback
                return self._keyword_classify(description), issues

            category = data.get("category", "").upper()
            confidence = float(data.get("confidence", 0.0))

            if category not in DESIGN_CATEGORIES:
                issues.append(f"LLM returned unknown category '{category}', using keyword fallback")
                return self._keyword_classify(description), issues

            if confidence < 0.5:
                issues.append(
                    f"Low classification confidence ({confidence:.2f}) for category {category}"
                )

            return category, issues

        except Exception as e:
            issues.append(f"Classification failed: {e}")
            return self._keyword_classify(description), issues

    def _keyword_classify(self, description: str) -> Optional[str]:
        """Deterministic keyword-based classification fallback."""
        desc_lower = description.lower()

        keyword_map = {
            "PROCESSOR": [
                "cpu",
                "processor",
                "risc",
                "riscv",
                "rv32",
                "rv64",
                "microcontroller",
                "instruction",
                "isa",
                "pipeline",
                "fetch",
                "decode",
                "execute",
            ],
            "MEMORY": [
                "fifo",
                "sram",
                "ram",
                "rom",
                "cache",
                "register file",
                "memory",
                "stack",
                "queue",
                "buffer",
                "depth",
            ],
            "INTERFACE": [
                "uart",
                "spi",
                "i2c",
                "apb",
                "axi",
                "wishbone",
                "usb",
                "serial",
                "baud",
                "mosi",
                "miso",
                "sclk",
                "scl",
                "sda",
            ],
            "ARITHMETIC": [
                "alu",
                "multiplier",
                "divider",
                "adder",
                "mac",
                "fpu",
                "floating point",
                "multiply",
                "accumulate",
            ],
            "CONTROL": [
                "state machine",
                "fsm",
                "arbiter",
                "scheduler",
                "interrupt",
                "controller",
                "priority",
            ],
            "DATAPATH": [
                "shift register",
                "barrel shifter",
                "pipeline stage",
                "datapath",
                "mux",
                "demux",
            ],
        }

        scores: Dict[str, int] = {cat: 0 for cat in keyword_map}
        for cat, keywords in keyword_map.items():
            for kw in keywords:
                if kw in desc_lower:
                    scores[cat] += 1

        best_cat = max(scores, key=scores.get)
        if scores[best_cat] == 0:
            return "CONTROL"  # Safe default: treat as generic state machine/controller

        # Check for MIXED
        active = [cat for cat, score in scores.items() if score > 0]
        if len(active) >= 2 and scores[active[1]] >= 2:
            return "MIXED"

        return best_cat

    def _generate_full_spec(
        self,
        design_name: str,
        description: str,
        target_pdk: str,
        base_sid: Optional[str] = None,
    ) -> Tuple[HardwareSpec, List[str]]:
        """Stages 2-5: Completeness, decomposition, interface, and contract."""
        issues: List[str] = []

        # Classification fallback to MIXED if complex base_sid provided
        category = "MIXED"
        if not base_sid:
            category, classify_issues = self._classify(description)
            issues.extend(classify_issues)
            if not category:
                category = "CONTROL"

        # Resolve mandatory fields for category
        mandatory = []
        for cat in MANDATORY_FIELDS:
            mandatory.extend(MANDATORY_FIELDS[cat])
        mandatory = list(set(mandatory))

        valid_subs = []
        for cat in DOMAIN_SUBMODULES:
            valid_subs.extend(DOMAIN_SUBMODULES[cat])
        valid_subs = list(set(valid_subs))

        sid_context = (
            f"\nCONSIDER PREVIOUS ARCHITECTURE PLAN (DO NOT FALLBACK TO MINIMAL):\n{base_sid}\n"
            if base_sid
            else ""
        )

        prompt = (
            SPEC_GENERATION_PROMPT.format(
                category=category,
                description=description[:6000],
                design_name=design_name,
                target_pdk=target_pdk,
                mandatory_fields=json.dumps(mandatory, indent=2),
                valid_submodules=json.dumps(valid_subs),
            )
            + sid_context
        )

        compact_prompt = self._compact_spec_prompt(
            design_name=design_name,
            description=description,
            target_pdk=target_pdk,
            category=category,
            base_sid=base_sid,
        )

        last_error = ""
        for attempt in range(1, self.max_retries + 1):
            logger.info(f"[SpecGen] Full spec attempt {attempt}/{self.max_retries}")

            retry_context = ""
            if last_error:
                retry_context = (
                    f"\n\nPREVIOUS ATTEMPT FAILED:\n{last_error}\n"
                    "Fix the issues and return a corrected JSON."
                )

            agent = Agent(
                role="Hardware Specification Architect",
                goal=f"Generate a complete, unambiguous hardware specification for {design_name}",
                backstory=(
                    "You are a principal VLSI architect with expertise in RTL specification. "
                    "You produce implementation-ready specs that leave no room for ambiguity. "
                    "Every field you fill in must be justified. Every assumption is a warning."
                ),
                llm=self.llm,
                verbose=self.verbose,
            )
            task = Task(
                description=prompt + retry_context,
                expected_output="Complete hardware specification JSON",
                agent=agent,
            )

            try:
                raw = self._llm_call(
                    prompt=compact_prompt + retry_context,
                    system=(
                        "You are a principal VLSI architect generating complete, "
                        "implementation-ready hardware specifications. Return only valid JSON."
                    ),
                    agent=agent,
                    task=task,
                )
                data, success = robust_json_extract(
                    raw, context="spec_generate", required_keys=["ports"]
                )

                if not success or data is None:
                    last_error = "Response was not valid JSON"
                    continue

                spec = self._parse_spec(data, design_name, category, target_pdk, description)
                validation_issues = self._validate_spec(spec, mandatory, valid_subs)

                if validation_issues:
                    last_error = "Validation issues:\n" + "\n".join(
                        f"  - {i}" for i in validation_issues
                    )
                    if attempt == self.max_retries:
                        issues = list(dict.fromkeys(validation_issues))  # Deduplicated
                        spec.warnings.extend(issues)
                        logger.warning(f"[SpecGen] Accepting spec with {len(issues)} warnings")
                        return spec, issues
                    continue

                logger.info(
                    f"[SpecGen] Spec generated successfully: {len(spec.submodules)} submodules, "
                    f"{len(spec.behavioral_contract)} contract statements"
                )
                return spec, []  # Return empty issues if generation succeeds

            except Exception as e:
                last_error = f"Error: {e}"
                logger.warning(f"[SpecGen] Attempt {attempt} failed: {e}")
                continue

        # Fallback: generate minimal spec (even if base_sid is provided)
        # We previously raised RuntimeError in high-reliability mode, but we need
        # to ensure it always falls back to avoid complete pipeline failure.
        logger.warning(
            f"[SpecGen] All attempts failed (base_sid present: {bool(base_sid)}) — "
            "generating deterministic keyword fallback spec"
        )
        require_llm = os.environ.get("AGENTIC_REQUIRE_LLM_SPEC", "0").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if require_llm:
            raise RuntimeError(
                "LLM spec generation failed and AGENTIC_REQUIRE_LLM_SPEC=1. "
                f"Last error: {last_error or 'unknown'}"
            )
        spec = self._fallback_spec(design_name, description, category, target_pdk)
        if last_error:
            issues.append(f"LLM spec generation failed after retries: {last_error}")
        issues.append("Spec generation used deterministic fallback — manual review recommended")
        return spec, issues

    def _parse_spec(
        self,
        data: Dict[str, Any],
        design_name: str,
        category: str,
        target_pdk: str,
        description: str,
    ) -> HardwareSpec:
        """Parse LLM JSON output into a HardwareSpec."""
        ports = []
        for p in data.get("ports", []):
            ports.append(
                PortSpec(
                    name=p.get("name", ""),
                    direction=p.get("direction", "input"),
                    data_type=p.get("data_type", "logic"),
                    description=p.get("description", ""),
                )
            )

        # Ensure clk and rst_n are present
        port_names = {p.name for p in ports}
        if "clk" not in port_names:
            ports.insert(0, PortSpec("clk", "input", "logic", "System clock"))
        if "rst_n" not in port_names:
            ports.insert(1, PortSpec("rst_n", "input", "logic", "Active-low synchronous reset"))

        submodules = []
        for s in data.get("submodules", []):
            sub_ports = [
                PortSpec(
                    name=sp.get("name", ""),
                    direction=sp.get("direction", "input"),
                    data_type=sp.get("data_type", "logic"),
                    description=sp.get("description", ""),
                )
                for sp in s.get("ports", [])
            ]
            submodules.append(
                SubModuleSpec(
                    name=s.get("name", ""),
                    description=s.get("description", ""),
                    ports=sub_ports,
                )
            )

        contracts = []
        for b in data.get("behavioral_contract", []):
            contracts.append(
                BehavioralStatement(
                    given=b.get("given", ""),
                    when=b.get("when", ""),
                    then=b.get("then", ""),
                    within=b.get("within", "1 cycle"),
                )
            )

        return HardwareSpec(
            design_category=category,
            top_module_name=data.get("top_module_name", design_name),
            target_pdk=target_pdk,
            target_frequency_mhz=data.get("target_frequency_mhz", 50),
            ports=ports,
            submodules=submodules,
            behavioral_contract=contracts,
            warnings=data.get("warnings", []),
            design_description=description,
            mandatory_fields_status=data.get("mandatory_fields_status", {}),
        )

    def _validate_spec(self, spec, mandatory, valid_subs) -> List[str]:
        """Validate the spec for sanity."""
        v_issues = []
        meaningful_ports = [
            p for p in spec.ports if p.name not in {"clk", "rst_n"} and p.name.strip()
        ]
        if not spec.ports or not meaningful_ports:
            v_issues.append("No ports defined")
        if not spec.behavioral_contract:
            v_issues.append("No behavioral contract defined")
        v_issues.extend(self._validate_against_description(spec, spec.design_description))
        return v_issues

    @staticmethod
    def _normalized_port_names(spec: HardwareSpec) -> set:
        names = set()
        for port in spec.ports:
            name = (port.name or "").strip().lower()
            if not name:
                continue
            names.add(name)
            names.add(re.sub(r"\[[^\]]+\]", "", name).strip())
        return names

    @staticmethod
    def _port_declares_width(port: PortSpec, width: int) -> bool:
        text = f"{port.name} {port.data_type}".lower()
        hi = width - 1
        return f"[{hi}:0]" in text or f"[0:{hi}]" in text

    def _has_bus_or_indexed_ports(self, spec: HardwareSpec, base: str, width: int) -> bool:
        ports = self._normalized_port_names(spec)
        if any(
            re.sub(r"\[[^\]]+\]", "", (p.name or "").lower()).strip() == base
            and self._port_declares_width(p, width)
            for p in spec.ports
        ):
            return True
        indexed = {
            f"{base}_{i}" for i in range(width)
        } | {
            f"{base}{i}" for i in range(width)
        }
        return indexed.issubset(ports)

    def _validate_against_description(self, spec: HardwareSpec, description: str) -> List[str]:
        """Reject LLM specs that drift away from explicit user-facing requirements."""
        desc = (description or "").lower()
        issues: List[str] = []
        ports = self._normalized_port_names(spec)
        searchable = " ".join(
            [spec.design_category, spec.top_module_name]
            + [p.name for p in spec.ports]
            + [s.name for s in spec.submodules]
            + [s.description for s in spec.submodules]
            + [str(b) for b in spec.behavioral_contract]
        ).lower()

        required_terms = {
            "gpio": ["gpio"],
            "pwm": ["pwm"],
            "uart": ["uart"],
            "timer": ["timer"],
            "watchdog": ["watchdog", "wdt"],
            "converter": ["converter", "adc", "sample"],
        }
        for feature, aliases in required_terms.items():
            if any(alias in desc for alias in aliases) and not any(alias in searchable for alias in aliases):
                issues.append(f"Spec missing requested feature semantics: {feature}")

        explicit_ports = ["uart_rx", "uart_tx", "reset_io_n"]
        for port in explicit_ports:
            if port in desc and port not in ports:
                issues.append(f"Spec missing explicit requested top-level port: {port}")

        gpio_match = re.search(r"gpio\s*\[\s*(\d+)\s*:\s*0\s*\]", desc)
        if gpio_match:
            width = int(gpio_match.group(1)) + 1
            if not self._has_bus_or_indexed_ports(spec, "gpio", width):
                issues.append(f"Spec must preserve requested gpio[{width - 1}:0] width")

        pwm_match = re.search(r"pwm_out\s*\[\s*(\d+)\s*:\s*0\s*\]", desc)
        if pwm_match:
            width = int(pwm_match.group(1)) + 1
            if not self._has_bus_or_indexed_ports(spec, "pwm_out", width) and not self._has_bus_or_indexed_ports(spec, "pwm", width):
                issues.append(f"Spec must preserve requested pwm_out[{width - 1}:0] width")

        if "20 external pads" in desc or "20-pin" in desc or "20 pin" in desc:
            if "vccd" in ports or "vssd" in ports or "vcca" in ports:
                issues.append(
                    "Spec introduced process-specific supply port names not requested by the package intent"
                )

        return issues

    def _fallback_spec(self, design_name, description, category, target_pdk) -> HardwareSpec:
        desc = description.lower()
        ports = [
            PortSpec("clk", "input", "logic", "System clock"),
            PortSpec("rst_n", "input", "logic", "Active-low reset"),
        ]
        submodules: List[SubModuleSpec] = []
        contracts = [
            BehavioralStatement(
                given="rst_n is low",
                when="a clock edge occurs or reset is asserted",
                then="all control/status registers return to documented reset values",
                within="1 cycle",
            )
        ]

        def add_port(name: str, direction: str, data_type: str, purpose: str) -> None:
            if name not in {p.name for p in ports}:
                ports.append(PortSpec(name, direction, data_type, purpose))

        def add_submodule(name: str, summary: str) -> None:
            if name not in {s.name for s in submodules}:
                submodules.append(SubModuleSpec(name=name, description=summary, ports=[]))

        if "gpio" in desc:
            add_port("gpio", "inout", "logic [7:0]", "Eight multiplexed GPIO pins")
            add_submodule("gpio_controller", "GPIO direction, output, input, and mux control")
            contracts.append(
                BehavioralStatement(
                    given="a GPIO pin is configured as output",
                    when="software writes the GPIO output register",
                    then="the corresponding pad drives the programmed value unless muxed to a peripheral",
                    within="1 cycle",
                )
            )
        if "pwm" in desc:
            add_port("pwm_out", "output", "logic [2:0]", "Three PWM channel outputs")
            add_submodule("pwm_controller", "Programmable period/duty PWM generation")
            contracts.append(
                BehavioralStatement(
                    given="a PWM channel is enabled",
                    when="its counter is below the configured duty value",
                    then="the PWM output is high, otherwise it is low",
                    within="1 cycle",
                )
            )
        if "uart" in desc:
            add_port("uart_rx", "input", "logic", "UART receive pin")
            add_port("uart_tx", "output", "logic", "UART transmit pin")
            add_submodule("uart_controller", "UART RX/TX control and status datapath")
            contracts.append(
                BehavioralStatement(
                    given="UART transmit data is written while TX is idle",
                    when="the baud divider advances",
                    then="uart_tx emits start, data, and stop bits in order",
                    within="one UART frame",
                )
            )
        if "watchdog" in desc:
            add_port("watchdog_reset_req", "output", "logic", "Watchdog timeout reset request")
            add_submodule("watchdog_timer", "Configurable watchdog counter with kick and timeout")
            contracts.append(
                BehavioralStatement(
                    given="watchdog is enabled and not kicked",
                    when="the watchdog counter reaches its timeout value",
                    then="watchdog_reset_req asserts",
                    within="1 cycle",
                )
            )
        if "timer" in desc or "500 microsecond" in desc or "500 microseconds" in desc:
            add_port("timer_tick_500us", "output", "logic", "Internal 500 microsecond timer tick")
            add_submodule("timer_block", "Clock divider and 500 microsecond tick generation")
        if "reset" in desc:
            add_port("reset_io_n", "inout", "logic", "Reset pin that can be read after release")
        if "adc" in desc or "12-bit" in desc or "converter" in desc:
            add_port("conv_sample_valid", "input", "logic", "Sample valid from converter interface")
            add_port("conv_sample_data", "input", "logic [11:0]", "12-bit sampled-data converter result")
            add_submodule(
                "converter_controller",
                "Digital sampled-data converter control/status interface for a hard macro or off-chip converter",
            )

        return HardwareSpec(
            design_category=category,
            top_module_name=design_name,
            target_pdk=target_pdk,
            ports=ports,
            submodules=submodules or [
                SubModuleSpec(
                    name=f"{design_name}_core",
                    description="Top-level control logic inferred from the user description",
                    ports=[],
                )
            ],
            behavioral_contract=contracts,
            design_description=description,
            warnings=[
                "Deterministic fallback spec was generated because LLM structured spec output was invalid or incomplete."
            ],
        )
