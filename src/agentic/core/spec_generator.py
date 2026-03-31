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
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from crewai import Agent, Task, Crew, LLM

logger = logging.getLogger(__name__)


# ─── Design Categories ───────────────────────────────────────────────

DESIGN_CATEGORIES = [
    "PROCESSOR",   # CPU, microcontroller, DSP core, RISC-V, ARM-like
    "MEMORY",      # FIFO, SRAM, ROM, cache, register file
    "INTERFACE",   # UART, SPI, I2C, APB, AXI, Wishbone, USB
    "ARITHMETIC",  # ALU, multiplier, divider, FPU, MAC
    "CONTROL",     # State machine, arbiter, scheduler, interrupt controller
    "DATAPATH",    # Pipeline stage, shift register, barrel shifter
    "MIXED",       # Contains two or more of the above
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
        "program_counter", "instruction_memory_interface",
        "instruction_fetch", "instruction_decode", "register_file",
        "alu", "data_memory_interface", "writeback", "hazard_unit",
        "branch_predictor", "pipeline_register", "control_unit",
    ],
    "MEMORY": [
        "memory_array", "read_port_logic", "write_port_logic",
        "address_decoder", "collision_logic", "output_register",
    ],
    "INTERFACE": [
        "clock_divider", "shift_register", "state_machine",
        "data_buffer", "control_logic", "status_register", "fifo",
    ],
    "ARITHMETIC": [
        "input_register", "computation_unit", "pipeline_stage_register",
        "output_register", "overflow_detector",
    ],
    "CONTROL": [
        "state_register", "next_state_logic", "output_logic",
        "priority_encoder", "arbiter_logic", "interrupt_register",
    ],
    "DATAPATH": [
        "shift_register", "pipeline_register", "mux_network",
        "barrel_shifter", "data_register",
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
    direction: str           # "input" | "output" | "inout"
    data_type: str           # "logic" | "logic [N:0]"
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
        ports = [PortSpec(**p) for p in data.pop("ports", [])]
        subs = [SubModuleSpec(
            name=s["name"],
            description=s.get("description", ""),
            ports=[PortSpec(**p) for p in s.get("ports", [])],
        ) for s in data.pop("submodules", [])]
        contracts = [BehavioralStatement(**b) for b in data.pop("behavioral_contract", [])]
        inferred = [InferredField(**f) for f in data.pop("inferred_fields", [])]
        return cls(
            ports=ports, submodules=subs,
            behavioral_contract=contracts, inferred_fields=inferred,
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

Perform ALL of the following steps and return a single JSON object:

STEP 1 — COMPLETENESS CHECK
For this {category} design, check these mandatory fields:
{mandatory_fields}

For each field:
- If present in description → set status to "present" with the value
- If safely inferable from standard practice → set status to "inferred" with value and reasoning
- If missing and no safe default → set status to "missing"

Safe defaults you may use:
- Reset: synchronous active-low (standard for Sky130)
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
    {{"name": "<name>", "direction": "input|output|inout", "data_type": "logic|logic [N:0]", "description": "<purpose>"}}
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

    def generate(
        self,
        design_name: str,
        description: str,
        target_pdk: str = "sky130",
    ) -> Tuple[HardwareSpec, List[str]]:
        """
        Main entry point: generate a complete hardware specification.

        Args:
            design_name: Verilog-safe design name
            description: Natural language hardware description
            target_pdk: Target PDK (sky130, gf180)

        Returns:
            (HardwareSpec, issues) — spec and any issues/missing fields
        """
        issues: List[str] = []

        # ── Gate: short descriptions get LLM elaboration, not rejection ──
        word_count = len(description.strip().split())
        if word_count < 10:
            logger.info(f"[SpecGen] Description is short ({word_count} words) — elaborating via LLM")
            options = self._elaborate_description(design_name, description)
            # Return a special spec that signals the orchestrator to present options
            spec = HardwareSpec(
                design_category="ELABORATION_NEEDED",
                top_module_name=design_name,
                design_description=description,
                warnings=[f"ELABORATION_NEEDED: Description is short ({word_count} words). "
                           "Please select one of the options below."] + options,
            )
            return spec, [f"Short description ({word_count} words) — 3 design options generated"]

        # ── Stage 1: Classify ──
        logger.info(f"[SpecGen] Stage 1: Classifying '{design_name}'")
        category, classify_issues = self._classify(description)
        issues.extend(classify_issues)

        if category is None:
            return self._rejected_spec(
                design_name,
                "Could not classify the design. Description is too ambiguous."
            ), issues

        logger.info(f"[SpecGen] Classified as: {category}")

        # ── Stages 2-5: Generate full spec via LLM ──
        logger.info(f"[SpecGen] Stages 2-5: Generating full spec for '{design_name}' ({category})")
        spec, gen_issues = self._generate_full_spec(
            design_name, description, category, target_pdk
        )
        issues.extend(gen_issues)

        return spec, issues

    def _elaborate_description(
        self, design_name: str, description: str
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

Return ONLY this JSON (no markdown, no commentary):
{{
  "options": [
    {{
      "id": 1,
      "title": "<short title, max 8 words>",
      "description": "<2-3 sentence detailed technical description including: bit-widths, port count, reset style, key functionality, and typical target clock frequency on Sky130>",
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
            raw = str(Crew(agents=[agent], tasks=[task]).kickoff())
            data = self._extract_json(raw)

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
            raw = str(Crew(agents=[agent], tasks=[task]).kickoff())
            data = self._extract_json(raw)

            if data is None:
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
            "PROCESSOR": ["cpu", "processor", "risc", "riscv", "rv32", "rv64", "microcontroller",
                          "instruction", "isa", "pipeline", "fetch", "decode", "execute"],
            "MEMORY": ["fifo", "sram", "ram", "rom", "cache", "register file", "memory",
                       "stack", "queue", "buffer", "depth"],
            "INTERFACE": ["uart", "spi", "i2c", "apb", "axi", "wishbone", "usb", "serial",
                          "baud", "mosi", "miso", "sclk", "scl", "sda"],
            "ARITHMETIC": ["alu", "multiplier", "divider", "adder", "mac", "fpu",
                           "floating point", "multiply", "accumulate"],
            "CONTROL": ["state machine", "fsm", "arbiter", "scheduler", "interrupt",
                        "controller", "priority"],
            "DATAPATH": ["shift register", "barrel shifter", "pipeline stage",
                         "datapath", "mux", "demux"],
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
        category: str,
        target_pdk: str,
    ) -> Tuple[HardwareSpec, List[str]]:
        """Stages 2-5: Completeness, decomposition, interface, and contract."""
        issues: List[str] = []

        # Resolve mandatory fields for category
        if category == "MIXED":
            mandatory = []
            for cat in MANDATORY_FIELDS:
                mandatory.extend(MANDATORY_FIELDS[cat])
            mandatory = list(set(mandatory))
            valid_subs = []
            for cat in DOMAIN_SUBMODULES:
                valid_subs.extend(DOMAIN_SUBMODULES[cat])
            valid_subs = list(set(valid_subs))
        else:
            mandatory = MANDATORY_FIELDS.get(category, [])
            valid_subs = DOMAIN_SUBMODULES.get(category, [])

        prompt = SPEC_GENERATION_PROMPT.format(
            category=category,
            description=description[:6000],
            design_name=design_name,
            mandatory_fields=json.dumps(mandatory, indent=2),
            valid_submodules=json.dumps(valid_subs),
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
                raw = str(Crew(agents=[agent], tasks=[task]).kickoff())
                data = self._extract_json(raw)

                if data is None:
                    last_error = "Response was not valid JSON"
                    continue

                spec = self._parse_spec(data, design_name, category, target_pdk, description)
                validation_issues = self._validate_spec(spec, mandatory, valid_subs)

                if validation_issues:
                    last_error = "Validation issues:\n" + "\n".join(f"  - {i}" for i in validation_issues)
                    issues.extend(validation_issues)
                    # Accept with warnings on last attempt
                    if attempt == self.max_retries:
                        spec.warnings.extend(validation_issues)
                        logger.warning(f"[SpecGen] Accepting spec with {len(validation_issues)} warnings")
                        return spec, issues
                    continue

                logger.info(f"[SpecGen] Spec generated successfully: {len(spec.submodules)} submodules, "
                            f"{len(spec.behavioral_contract)} contract statements")
                return spec, issues

            except Exception as e:
                last_error = f"Error: {e}"
                logger.warning(f"[SpecGen] Attempt {attempt} failed: {e}")
                continue

        # Fallback: generate minimal spec
        logger.warning("[SpecGen] All attempts failed — generating minimal fallback spec")
        spec = self._fallback_spec(design_name, description, category, target_pdk)
        issues.append("Spec generation fell back to minimal template — manual review required")
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
            ports.append(PortSpec(
                name=p.get("name", ""),
                direction=p.get("direction", "input"),
                data_type=p.get("data_type", "logic"),
                description=p.get("description", ""),
            ))

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
            submodules.append(SubModuleSpec(
                name=s.get("name", ""),
                description=s.get("description", ""),
                ports=sub_ports,
            ))

        contracts = []
        for b in data.get("behavioral_contract", []):
            contracts.append(BehavioralStatement(
                given=b.get("given", ""),
                when=b.get("when", ""),
                then=b.get("then", ""),
                within=b.get("within", "1 cycle"),
            ))

        # Parse inferred fields from mandatory_fields_status
        inferred_fields = []
        mfs = data.get("mandatory_fields_status", {})
        for fname, fdata in mfs.items():
            if isinstance(fdata, dict) and fdata.get("status") == "inferred":
                inferred_fields.append(InferredField(
                    field_name=fname,
                    inferred_value=str(fdata.get("value", "")),
                    reasoning=fdata.get("reasoning", ""),
                ))

        warnings = data.get("warnings", [])
        if not warnings:
            warnings = ["No warnings were generated — spec should be reviewed for implicit assumptions"]

        return HardwareSpec(
            design_category=category,
            top_module_name=data.get("top_module_name", design_name),
            target_pdk=target_pdk,
            target_frequency_mhz=int(data.get("target_frequency_mhz", 50)),
            ports=ports,
            submodules=submodules,
            behavioral_contract=contracts,
            inferred_fields=inferred_fields,
            warnings=warnings,
            design_description=description,
            mandatory_fields_status={
                k: v if isinstance(v, dict) else {"status": "present", "value": str(v)}
                for k, v in mfs.items()
            },
        )

    def _validate_spec(
        self,
        spec: HardwareSpec,
        mandatory_fields: List[str],
        valid_submodules: List[str],
    ) -> List[str]:
        """Validate the generated spec for completeness and correctness."""
        issues = []

        # Check top module name
        if not spec.top_module_name:
            issues.append("top_module_name is empty")
        elif not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', spec.top_module_name):
            issues.append(f"top_module_name '{spec.top_module_name}' is not a valid Verilog identifier")

        # Check ports
        if len(spec.ports) < 2:
            issues.append("Fewer than 2 ports defined (need at minimum clk and rst_n)")
        port_names = {p.name for p in spec.ports}
        if "clk" not in port_names:
            issues.append("Missing clk port")
        if "rst_n" not in port_names:
            issues.append("Missing rst_n port")

        # Check for floating ports (output with no description)
        for p in spec.ports:
            if not p.description:
                issues.append(f"Port '{p.name}' has no description — may be floating")

        # Check submodules
        if not spec.submodules:
            issues.append("No submodules defined")
        elif len(spec.submodules) > 8:
            issues.append(f"Too many submodules ({len(spec.submodules)}) — maximum is 8")

        # Domain validation of submodule names
        if valid_submodules and spec.submodules:
            for sm in spec.submodules:
                # Fuzzy match: check if any valid name is a substring or vice versa
                name_lower = sm.name.lower().replace("-", "_")
                matched = any(
                    valid.lower() in name_lower or name_lower in valid.lower()
                    for valid in valid_submodules
                )
                if not matched:
                    issues.append(
                        f"Submodule '{sm.name}' does not match any standard component "
                        f"for {spec.design_category}: {valid_submodules}"
                    )

        # Check behavioral contract
        if not spec.behavioral_contract:
            issues.append("No behavioral contract statements defined")
        else:
            has_reset = any("reset" in b.given.lower() or "rst" in b.given.lower()
                           for b in spec.behavioral_contract)
            if not has_reset:
                issues.append("Behavioral contract missing a reset behavior statement")

        # Check mandatory fields
        missing_fields = []
        for mf in mandatory_fields:
            status = spec.mandatory_fields_status.get(mf, {})
            if isinstance(status, dict) and status.get("status") == "missing":
                missing_fields.append(mf)
        if missing_fields:
            issues.append(f"Missing mandatory fields: {', '.join(missing_fields)}")

        return issues

    def _fallback_spec(
        self,
        design_name: str,
        description: str,
        category: str,
        target_pdk: str,
    ) -> HardwareSpec:
        """Generate a minimal fallback spec when LLM generation fails."""
        return HardwareSpec(
            design_category=category,
            top_module_name=design_name,
            target_pdk=target_pdk,
            target_frequency_mhz=50,
            ports=[
                PortSpec("clk", "input", "logic", "System clock"),
                PortSpec("rst_n", "input", "logic", "Active-low synchronous reset"),
            ],
            submodules=[
                SubModuleSpec(
                    name=design_name,
                    description=description[:500],
                    ports=[
                        PortSpec("clk", "input", "logic", "System clock"),
                        PortSpec("rst_n", "input", "logic", "Active-low synchronous reset"),
                    ],
                ),
            ],
            behavioral_contract=[
                BehavioralStatement(
                    given="rst_n is asserted low",
                    when="the next rising clock edge occurs",
                    then="all outputs must be driven to their reset values",
                    within="1 cycle",
                ),
            ],
            inferred_fields=[],
            warnings=[
                "Fallback spec generated — LLM decomposition failed",
                "Manual review required before RTL generation",
                "Only minimal ports (clk, rst_n) are defined",
            ],
            design_description=description,
        )

    def _rejected_spec(self, design_name: str, reason: str) -> HardwareSpec:
        """Create a spec that signals rejection."""
        return HardwareSpec(
            design_category="REJECTED",
            top_module_name=design_name,
            warnings=[f"SPEC_REJECTED: {reason}"],
            design_description=reason,
        )

    def _extract_json(self, raw: str) -> Optional[Dict[str, Any]]:
        """Extract a JSON object from LLM response text."""
        text = raw.strip()

        # Strip markdown fences
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
        if json_match:
            text = json_match.group(1).strip()

        # Find outermost JSON object
        brace_start = text.find('{')
        brace_end = text.rfind('}')
        if brace_start >= 0 and brace_end > brace_start:
            try:
                return json.loads(text[brace_start:brace_end + 1])
            except json.JSONDecodeError:
                pass

        # Try parsing the whole thing
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    def to_sid_enrichment(self, spec: HardwareSpec) -> Dict[str, Any]:
        """
        Convert the HardwareSpec into enrichment data that can augment the
        ArchitectModule's StructuredSpecDict (SID).
        
        This bridges the spec generator output → existing SID pipeline.
        """
        enrichment = {
            "design_category": spec.design_category,
            "target_frequency_mhz": spec.target_frequency_mhz,
            "behavioral_contract": [b.to_dict() for b in spec.behavioral_contract],
            "inferred_fields": [f.to_dict() for f in spec.inferred_fields],
            "spec_warnings": spec.warnings,
            "mandatory_fields_status": spec.mandatory_fields_status,
            "spec_validated": spec.design_category != "REJECTED",
        }

        # Add verification hints derived from behavioral contract
        verification_hints = []
        for b in spec.behavioral_contract:
            verification_hints.append(
                f"Assert: GIVEN {b.given} WHEN {b.when} THEN {b.then} WITHIN {b.within}"
            )
        enrichment["verification_hints_from_spec"] = verification_hints

        return enrichment
