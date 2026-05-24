"""
Architect Module — Spec2RTL Decomposer Agent
=============================================

Based on: Spec2RTL-Agent (arXiv:2405.xxxxx)

Before writing any Verilog, this module reads the input specification (text/PDF)
and produces a Structured Information Dictionary (SID) in JSON format.

The SID explicitly defines:
  - Top-level module name, parameters, ports
  - Sub-module names, inputs, outputs, and functional logic
  - FSM state maps, datapath descriptions, timing constraints
  - Interface protocols and reset strategy

This JSON contract becomes the SINGLE SOURCE OF TRUTH for all downstream agents
(Coder, Verifier, Debugger) — eliminating ambiguity and hallucination.
"""

import json
import re
import logging
import os
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple
from crewai import Agent, Task, Crew, LLM

logger = logging.getLogger(__name__)


# ─── Structured Information Dictionary Schema ────────────────────────

@dataclass
class PortDef:
    """Single port definition."""
    name: str
    direction: str          # "input" | "output" | "inout"
    width: str              # e.g. "8", "DATA_WIDTH", "1"
    description: str = ""
    reset_value: str = ""   # Only for output registers


@dataclass
class ParameterDef:
    """Parameterisation slot."""
    name: str
    default: str
    description: str = ""


@dataclass
class FSMStateDef:
    """Single FSM state."""
    name: str
    encoding: str = ""
    description: str = ""
    transitions: List[Dict[str, str]] = field(default_factory=list)
    outputs: Dict[str, str] = field(default_factory=dict)


@dataclass
class SubModuleDef:
    """One sub-module (including the top-level module itself)."""
    name: str
    description: str = ""
    parameters: List[ParameterDef] = field(default_factory=list)
    ports: List[PortDef] = field(default_factory=list)
    functional_logic: str = ""                # Natural language description
    rtl_skeleton: str = ""                    # Verilog skeleton (optional)
    fsm_states: List[FSMStateDef] = field(default_factory=list)
    internal_signals: List[Dict[str, str]] = field(default_factory=list)
    instantiates: List[str] = field(default_factory=list)  # Sub-module names


@dataclass
class StructuredSpecDict:
    """
    Complete Structured Information Dictionary for a chip design.
    This is the JSON contract between the Architect → Coder → Verifier pipeline.
    """
    design_name: str
    chip_family: str            # e.g. "counter", "FIFO", "UART", "AES", "RISC-V"
    description: str
    top_module: str
    reset_style: str = "sync"   # "sync" | "async"
    clock_name: str = "clk"
    reset_name: str = "rst_n"
    reset_polarity: str = "active_low"
    parameters: List[ParameterDef] = field(default_factory=list)
    sub_modules: List[SubModuleDef] = field(default_factory=list)
    interface_protocol: str = ""     # "AXI4-Stream" | "APB" | "wishbone" | "custom"
    timing_notes: str = ""
    verification_hints: List[str] = field(default_factory=list)  # Hints for TB agent

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> "StructuredSpecDict":
        data = json.loads(json_str)
        # Reconstruct nested dataclasses
        params = [ParameterDef(**p) for p in data.pop("parameters", [])]
        subs = []
        for sm in data.pop("sub_modules", []):
            sm_params = [ParameterDef(**p) for p in sm.pop("parameters", [])]
            sm_ports = [PortDef(**p) for p in sm.pop("ports", [])]
            sm_fsm = [FSMStateDef(**s) for s in sm.pop("fsm_states", [])]
            subs.append(SubModuleDef(parameters=sm_params, ports=sm_ports,
                                     fsm_states=sm_fsm, **sm))
        return cls(parameters=params, sub_modules=subs, **data)

    def validate(self) -> Tuple[bool, List[str]]:
        """Validate the SID for completeness and consistency."""
        errors: List[str] = []
        if not self.design_name:
            errors.append("design_name is empty")
        if not self.top_module:
            errors.append("top_module is empty")
        if not self.sub_modules:
            errors.append("No sub_modules defined")
        for sm in self.sub_modules:
            if not sm.name:
                errors.append("Sub-module has empty name")
            if not sm.ports:
                errors.append(f"Sub-module '{sm.name}' has no ports")
            if not sm.functional_logic:
                errors.append(f"Sub-module '{sm.name}' has no functional_logic")
            # Check clk/rst on sequential modules
            port_names = {p.name for p in sm.ports}
            if sm.fsm_states and self.clock_name not in port_names:
                errors.append(f"Sub-module '{sm.name}' has FSM but no '{self.clock_name}' port")
        return len(errors) == 0, errors

    def top_submodule(self) -> Optional[SubModuleDef]:
        for sm in self.sub_modules:
            if sm.name == self.top_module or sm.name == self.design_name:
                return sm
        return self.sub_modules[0] if self.sub_modules else None

    def all_port_names(self) -> set:
        names = set()
        for sm in self.sub_modules:
            names.update(p.name for p in sm.ports if p.name)
        return names


# ─── Decomposer Prompt Templates ─────────────────────────────────────

DECOMPOSE_SYSTEM_PROMPT = """\
You are a Principal VLSI Architect performing Spec-to-RTL decomposition.

TASK: Given a natural-language chip specification, produce a COMPLETE Structured 
Information Dictionary (SID) in **valid JSON format**.

The JSON MUST follow this EXACT schema:
{schema}

MANDATORY RULES:
1. Every module (including top-level) MUST appear in "sub_modules" with ALL fields populated.
2. Every sub-module MUST have at minimum: name, ports (with direction and width), functional_logic.
3. For sequential designs, the global clock and reset ports are MANDATORY for the top module and EVERY sub-module that contains logic (FSM, registers, etc.).
4. Strictly separate Control (FSM) and Datapath into separate sub-modules when modeling complex systems like CPUs, NPUs, or pipelined components like Multipliers.
5. If the design involves Arithmetic (e.g. Multiplier, ALU, MAC), you MUST explicitly mandate the internal pipeline stages inside "functional_logic" to avoid logic being optimized away into 0 logic gates (0 GE). Do NOT allow single-cycle massive arithmetic block unless specified.
6. Use "parameters" for configurable widths/depths — NEVER hardcode magic numbers.
7. "functional_logic" must be a deeply VLSI-aware specification of the microarchitecture (e.g., multiplier staging, adder trees, FSM encodings) under 100 words. DO NOT generate Verilog skeletons in this JSON.
8. CRITICAL JSON RULES: You are generating a massive JSON object. You MUST double check your syntax. NEVER use unescaped quotes inside strings. NEVER leave trailing commas before closing braces. Ensure all objects and arrays are properly closed.
9. IF THE DESIGN IS MASSIVE (e.g. CPUs, SoCs, Superscalar systems): You MUST OMIT the `fsm_states` and `internal_signals` arrays entirely to save tokens. The Designer module will independently infer those.
"""

DECOMPOSE_USER_PROMPT = """\
DESIGN NAME: {design_name}
SPECIFICATION: {spec_text}

Produce the complete Structured Information Dictionary (JSON) for this chip design.
MANDATORY: Decompose into sub-modules where architecturally appropriate (e.g., separate datapath,
controller, interface adapter, specialized arithmetic blocks). 
Do NOT generate a single top-level module unless the design is a basic primitive (like a simple gate or 1-bit mux). 
For designs described as 'SoC', 'Controller', or 'Engine', expect at least 3-5 sub-modules.
"""


# ─── The Architect Module ────────────────────────────────────────────

class ArchitectModule:
    """
    Spec2RTL Decomposer Agent.
    
    Reads a natural language specification and produces a StructuredSpecDict 
    (JSON) that defines every sub-module, port, parameter, and FSM state 
    BEFORE any Verilog is written.
    """

    # Minimal JSON schema description for the LLM prompt
    _SCHEMA_DESC = json.dumps({
        "design_name": "str",
        "chip_family": "str (counter|ALU|FIFO|FSM|UART|SPI|AXI|crypto|processor|SoC|...)",
        "description": "str",
        "top_module": "str (Verilog identifier)",
        "reset_style": "sync|async",
        "clock_name": "str",
        "reset_name": "str",
        "reset_polarity": "active_low|active_high",
        "parameters": [{"name": "str", "default": "str", "description": "str"}],
        "sub_modules": [{
            "name": "str (Verilog identifier)",
            "description": "str",
            "parameters": [{"name": "str", "default": "str", "description": "str"}],
            "ports": [{"name": "str", "direction": "input|output",
                        "width": "str", "description": "str", "reset_value": "str"}],
            "functional_logic": "CONCISE natural-language description of behavior (Max 100 words)",
            "fsm_states": [{"name": "str", "encoding": "str", "description": "str",
                            "transitions": [{"condition": "str", "next_state": "str"}],
                            "outputs": {"signal": "value"}}],
            "internal_signals": [{"name": "str", "width": "str", "purpose": "str"}],
            "instantiates": ["sub_module_name"]
        }],
        "interface_protocol": "str",
        "timing_notes": "str",
        "verification_hints": ["str"]
    }, indent=2)

    def __init__(self, llm: LLM, verbose: bool = False, max_retries: int = 3):
        self.llm = llm
        self.verbose = verbose
        self.max_retries = max_retries

    FEATURE_LIBRARY: Dict[str, Dict[str, Any]] = {
        "gpio": {
            "keywords": ["gpio"],
            "ports": {"gpio"},
            "terms": {"gpio"},
            "summary": "GPIO direction/output/input and multiplexing",
        },
        "pwm": {
            "keywords": ["pwm"],
            "ports": {"pwm_out"},
            "terms": {"pwm"},
            "summary": "PWM period/duty/enable generation",
        },
        "uart": {
            "keywords": ["uart"],
            "ports": {"uart_rx", "uart_tx"},
            "terms": {"uart"},
            "summary": "UART RX/TX control/status datapath",
        },
        "spi": {
            "keywords": ["spi"],
            "ports": {"spi_sclk", "spi_mosi", "spi_miso", "spi_cs_n"},
            "terms": {"spi"},
            "summary": "SPI serial clock, chip select, transmit, and receive datapath",
        },
        "i2c": {
            "keywords": ["i2c", "i²c"],
            "ports": {"i2c_scl", "i2c_sda"},
            "terms": {"i2c"},
            "summary": "I2C open-drain serial clock/data controller interface",
        },
        "apb": {
            "keywords": ["apb"],
            "ports": {"paddr", "psel", "penable", "pwrite", "pwdata", "prdata", "pready", "pslverr"},
            "terms": {"apb"},
            "summary": "AMBA APB register bus interface",
        },
        "axi_lite": {
            "keywords": ["axi-lite", "axi4-lite", "axi lite", "axil"],
            "ports": {
                "axi_awaddr",
                "axi_awvalid",
                "axi_awready",
                "axi_wdata",
                "axi_wvalid",
                "axi_wready",
                "axi_bvalid",
                "axi_bready",
                "axi_araddr",
                "axi_arvalid",
                "axi_arready",
                "axi_rdata",
                "axi_rvalid",
                "axi_rready",
            },
            "terms": {"axi"},
            "summary": "AXI4-Lite slave register interface",
        },
        "wishbone": {
            "keywords": ["wishbone"],
            "ports": {"wb_adr_i", "wb_dat_i", "wb_dat_o", "wb_we_i", "wb_stb_i", "wb_cyc_i", "wb_ack_o"},
            "terms": {"wishbone"},
            "summary": "Wishbone-compatible register interface",
        },
        "fifo": {
            "keywords": ["fifo"],
            "ports": {"data_in", "data_out", "wr_en", "rd_en", "full", "empty"},
            "terms": {"fifo"},
            "summary": "FIFO storage with write/read enables and full/empty status",
        },
        "memory": {
            "keywords": ["sram", "ram", "memory", "scratchpad"],
            "ports": {"mem_addr", "mem_wdata", "mem_rdata", "mem_we", "mem_en"},
            "terms": {"memory", "sram", "ram"},
            "summary": "Memory interface suitable for SRAM compiler or memory macro integration",
        },
        "processor": {
            "keywords": ["risc-v", "riscv", "rv32", "processor", "cpu"],
            "ports": {"instr_addr", "instr_rdata", "data_addr", "data_wdata", "data_rdata", "data_we", "irq"},
            "terms": {"processor", "cpu", "risc"},
            "summary": "Processor core instruction/data interface and interrupt input",
        },
        "alu": {
            "keywords": ["alu"],
            "ports": {"operand_a", "operand_b", "opcode", "result", "valid"},
            "terms": {"alu"},
            "summary": "Arithmetic logic datapath with opcode-controlled operation",
        },
        "mac": {
            "keywords": ["multiply accumulate", "multiply-accumulate"],
            "ports": {"operand_a", "operand_b", "accum_out", "valid"},
            "terms": {"mac", "multiply", "accumulate"},
            "summary": "Multiply-accumulate datapath with registered output",
        },
        "aes": {
            "keywords": ["aes"],
            "ports": {"key", "plaintext", "ciphertext", "start", "done"},
            "terms": {"aes"},
            "summary": "AES crypto datapath/control interface",
        },
        "dma": {
            "keywords": ["dma"],
            "ports": {"src_addr", "dst_addr", "length", "start", "done"},
            "terms": {"dma"},
            "summary": "DMA source/destination/length control interface",
        },
        "watchdog": {
            "keywords": ["watchdog"],
            "ports": {"watchdog_reset_req"},
            "terms": {"watchdog"},
            "summary": "Watchdog enable/kick/timeout reset request",
        },
        "timer": {
            "keywords": ["timer", "500 microsecond", "500 microseconds"],
            "ports": {"timer_tick_500us"},
            "terms": {"timer"},
            "summary": "Timer tick generation from a configurable divider",
        },
        "reset_io": {
            "keywords": ["reset i/o", "reset io", "reset pin"],
            "ports": {"reset_io_n"},
            "terms": {"reset"},
            "summary": "Reset pin with readable status after release",
        },
        "converter": {
            "keywords": ["adc", "converter", "sampled-data"],
            "ports": {"conv_sample_valid", "conv_sample_data"},
            "terms": {"converter", "sample"},
            "summary": "Digital sampled-data converter interface for hard macro or off-chip converter",
        },
    }

    PORT_WIDTHS: Dict[str, str] = {
        "gpio": "8",
        "pwm_out": "3",
        "conv_sample_data": "12",
        "paddr": "32",
        "pwdata": "32",
        "prdata": "32",
        "axi_awaddr": "32",
        "axi_wdata": "32",
        "axi_araddr": "32",
        "axi_rdata": "32",
        "wb_adr_i": "32",
        "wb_dat_i": "32",
        "wb_dat_o": "32",
        "data_in": "32",
        "data_out": "32",
        "mem_addr": "16",
        "mem_wdata": "32",
        "mem_rdata": "32",
        "instr_addr": "32",
        "instr_rdata": "32",
        "data_addr": "32",
        "data_wdata": "32",
        "data_rdata": "32",
        "irq": "8",
        "operand_a": "32",
        "operand_b": "32",
        "opcode": "4",
        "result": "32",
        "accum_out": "32",
        "key": "128",
        "plaintext": "128",
        "ciphertext": "128",
        "src_addr": "32",
        "dst_addr": "32",
        "length": "32",
    }

    INPUT_PORTS = {
        "uart_rx",
        "spi_miso",
        "conv_sample_valid",
        "conv_sample_data",
        "paddr",
        "psel",
        "penable",
        "pwrite",
        "pwdata",
        "axi_awaddr",
        "axi_awvalid",
        "axi_wdata",
        "axi_wvalid",
        "axi_bready",
        "axi_araddr",
        "axi_arvalid",
        "axi_rready",
        "wb_adr_i",
        "wb_dat_i",
        "wb_we_i",
        "wb_stb_i",
        "wb_cyc_i",
        "data_in",
        "wr_en",
        "rd_en",
        "mem_addr",
        "mem_wdata",
        "mem_we",
        "mem_en",
        "instr_rdata",
        "data_rdata",
        "irq",
        "operand_a",
        "operand_b",
        "opcode",
        "key",
        "plaintext",
        "src_addr",
        "dst_addr",
        "length",
        "start",
    }

    INOUT_PORTS = {"gpio", "reset_io_n", "i2c_scl", "i2c_sda"}

    @staticmethod
    def _keyword_present(desc: str, keyword: str) -> bool:
        if re.search(r"[^\w]", keyword):
            return keyword in desc
        return re.search(rf"\b{re.escape(keyword)}\b", desc) is not None

    @staticmethod
    def _feature_expectations(spec_text: str) -> Dict[str, Dict[str, Any]]:
        desc = spec_text.lower()
        expectations: Dict[str, Dict[str, Any]] = {}
        for feature, metadata in ArchitectModule.FEATURE_LIBRARY.items():
            if any(ArchitectModule._keyword_present(desc, keyword) for keyword in metadata["keywords"]):
                expectations[feature] = {
                    "ports": set(metadata["ports"]),
                    "terms": set(metadata["terms"]),
                    "summary": metadata["summary"],
                }
        return expectations

    @staticmethod
    def _port_width_for(name: str, spec_text: str) -> str:
        return ArchitectModule.PORT_WIDTHS.get(name, "1")

    @staticmethod
    def _port_direction_for(name: str) -> str:
        if name in ArchitectModule.INOUT_PORTS:
            return "inout"
        if name in ArchitectModule.INPUT_PORTS:
            return "input"
        return "output"

    @staticmethod
    def _infer_chip_family(expectations: Dict[str, Dict[str, Any]]) -> str:
        features = set(expectations)
        if "processor" in features:
            return "processor_soc"
        if "aes" in features:
            return "crypto_accelerator"
        if {"alu", "mac"} & features:
            return "datapath_accelerator"
        if "fifo" in features:
            return "fifo_peripheral"
        if "memory" in features:
            return "memory_subsystem"
        if features & {"gpio", "pwm", "uart", "spi", "i2c", "timer", "watchdog", "converter", "reset_io"}:
            return "peripheral_controller"
        return "custom_digital"

    @staticmethod
    def _interface_protocol_for(expectations: Dict[str, Dict[str, Any]]) -> str:
        if "axi_lite" in expectations:
            return "AXI4-Lite"
        if "apb" in expectations:
            return "APB"
        if "wishbone" in expectations:
            return "Wishbone"
        return "custom register interface"

    def validate_sid_against_spec(
        self, sid: StructuredSpecDict, spec_text: str
    ) -> Tuple[bool, List[str]]:
        """Semantic SID validation: ensure JSON matches the actual user request."""
        errors: List[str] = []
        desc = spec_text.lower()

        if sid.top_module != sid.design_name:
            errors.append(
                f"top_module '{sid.top_module}' does not match design_name '{sid.design_name}'"
            )
        if sid.chip_family.strip().lower() in {"", "unknown", "generic"} and len(spec_text.split()) > 8:
            errors.append("chip_family is unknown for a detailed chip request")

        top = sid.top_submodule()
        if top is None:
            errors.append("SID has no top-level submodule")
            return False, errors

        top_ports = {p.name for p in top.ports if p.name}
        all_ports = sid.all_port_names()
        if sid.clock_name not in all_ports or sid.reset_name not in all_ports:
            errors.append(f"SID must include clock '{sid.clock_name}' and reset '{sid.reset_name}' ports")
        if len(top_ports - {sid.clock_name, sid.reset_name}) < 2:
            errors.append("top module has too few meaningful ports for the requested chip")

        if "async" in desc and sid.reset_style != "async":
            errors.append("spec requests async reset but SID reset_style is not async")

        module_text = " ".join(
            [
                sid.chip_family,
                sid.description,
                sid.interface_protocol,
                " ".join(sm.name for sm in sid.sub_modules),
                " ".join(sm.description for sm in sid.sub_modules),
                " ".join(sm.functional_logic for sm in sid.sub_modules),
            ]
        ).lower()
        for feature, expectation in self._feature_expectations(spec_text).items():
            missing_ports = expectation["ports"] - all_ports
            if missing_ports:
                errors.append(
                    f"SID missing {feature} port(s): {', '.join(sorted(missing_ports))}"
                )
            if not any(term in module_text for term in expectation["terms"]):
                errors.append(f"SID does not describe required feature: {feature}")

        return len(errors) == 0, errors

    def decompose(self, design_name: str, spec_text: str,
                  save_path: Optional[str] = None) -> StructuredSpecDict:
        """
        Main entry point: decompose a natural-language spec into a StructuredSpecDict.
        
        Args:
            design_name: Verilog-safe design name.
            spec_text:   Natural language specification (or existing MAS).
            save_path:   Optional path to save the JSON artifact.
            
        Returns:
            Validated StructuredSpecDict.
        """
        logger.info(f"[Architect] Decomposing spec for '{design_name}'")

        system_prompt = DECOMPOSE_SYSTEM_PROMPT.format(schema=self._SCHEMA_DESC)
        user_prompt = DECOMPOSE_USER_PROMPT.format(
            design_name=design_name,
            spec_text=spec_text[:12000],  # Truncate to fit context
        )

        sid = None
        last_error = ""

        for attempt in range(1, self.max_retries + 1):
            logger.info(f"[Architect] Decompose attempt {attempt}/{self.max_retries}")

            # Build the CrewAI agent for this attempt
            retry_context = ""
            if last_error:
                retry_context = (
                    f"\n\nPREVIOUS ATTEMPT FAILED WITH:\n{last_error}\n"
                    "Fix the issues and return a corrected JSON. Ensure there are no trailing commas and double quotes are escaped."
                )

            agent = Agent(
                role="Spec2RTL Decomposer",
                goal=f"Produce a complete Structured Information Dictionary for {design_name}",
                backstory=(
                    "You are a world-class VLSI architect who converts natural-language "
                    "chip specifications into precise, machine-readable JSON contracts. "
                    "You never leave fields empty or use placeholders."
                ),
                llm=self.llm,
                verbose=self.verbose,
            )

            task = Task(
                description=system_prompt + "\n\n" + user_prompt + retry_context,
                expected_output="Valid JSON matching the Structured Information Dictionary schema",
                agent=agent,
            )

            try:
                raw = str(Crew(agents=[agent], tasks=[task]).kickoff())
                sid = self._parse_response(raw, design_name)
                
                # Validate structure and semantic coverage against the actual request.
                ok, errs = sid.validate()
                if not ok:
                    last_error = "Validation errors:\n" + "\n".join(f"  - {e}" for e in errs)
                    logger.warning(f"[Architect] Validation failed: {errs}")
                    sid = None
                    continue
                ok, errs = self.validate_sid_against_spec(sid, spec_text)
                if not ok:
                    last_error = "Semantic SID validation errors:\n" + "\n".join(
                        f"  - {e}" for e in errs
                    )
                    logger.warning(f"[Architect] Semantic validation failed: {errs}")
                    sid = None
                    continue

                logger.info(f"[Architect] Successfully decomposed into "
                            f"{len(sid.sub_modules)} sub-modules")
                break

            except Exception as e:
                last_error = f"Parse/execution error: {str(e)}"
                logger.warning(f"[Architect] Attempt {attempt} failed: {e}")
                continue

        if sid is None:
            # Fallback: create a minimal SID from the spec text
            logger.warning("[Architect] All attempts failed — generating fallback SID")
            require_llm_sid = os.environ.get("AGENTIC_REQUIRE_LLM_SID", "0").strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            require_llm_spec = os.environ.get("AGENTIC_REQUIRE_LLM_SPEC", "0").strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            if require_llm_sid or require_llm_spec:
                raise RuntimeError(
                    "LLM SID decomposition failed and fallback is disabled "
                    "(AGENTIC_REQUIRE_LLM_SID/AGENTIC_REQUIRE_LLM_SPEC)."
                )
            sid = self._fallback_sid(design_name, spec_text)

        # Persist artifact
        if save_path:
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            with open(save_path, "w") as f:
                f.write(sid.to_json())
            logger.info(f"[Architect] SID saved to {save_path}")

        return sid

    def _parse_response(self, raw: str, design_name: str) -> StructuredSpecDict:
        """Extract JSON from LLM response (may contain markdown fences)."""
        text = raw.strip()

        # Strip markdown fences
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
        if json_match:
            text = json_match.group(1).strip()

        # Try to find the outermost JSON object
        brace_start = text.find('{')
        brace_end = text.rfind('}')
        if brace_start >= 0 and brace_end > brace_start:
            text = text[brace_start:brace_end + 1]

        data = json.loads(text)

        # Ensure design_name is set
        if not data.get("design_name"):
            data["design_name"] = design_name
        if not data.get("top_module"):
            data["top_module"] = design_name

        return StructuredSpecDict.from_json(json.dumps(data))

    def _fallback_sid(self, design_name: str, spec_text: str) -> StructuredSpecDict:
        """Generate a deterministic SID when LLM decomposition fails or is inaccurate."""
        expectations = self._feature_expectations(spec_text)
        desc = spec_text.lower()
        top_ports = [
            PortDef(name="clk", direction="input", width="1", description="System clock"),
            PortDef(name="rst_n", direction="input", width="1", description="Active-low reset"),
        ]
        for feature in expectations.values():
            for port_name in sorted(feature["ports"]):
                top_ports.append(
                    PortDef(
                        name=port_name,
                        direction=self._port_direction_for(port_name),
                        width=self._port_width_for(port_name, spec_text),
                        description=feature["summary"],
                    )
                )

        submodules = [
            SubModuleDef(
                name=design_name,
                description=spec_text[:2000],
                ports=top_ports,
                functional_logic=(
                    "Top-level integration of requested digital interfaces, control/status registers, "
                    "datapath blocks, reset handling, and macro-facing ports."
                ),
                instantiates=[f"{name}_block" for name in expectations],
            )
        ]
        for name, feature in expectations.items():
            submodules.append(
                SubModuleDef(
                    name=f"{name}_block",
                    description=feature["summary"],
                    ports=[
                        PortDef(name="clk", direction="input", width="1", description="System clock"),
                        PortDef(
                            name="rst_n",
                            direction="input",
                            width="1",
                            description="Active-low reset",
                        ),
                    ],
                    functional_logic=feature["summary"],
                )
            )

        feature_names = ", ".join(sorted(expectations)) or "custom digital logic"
        chip_family = self._infer_chip_family(expectations)
        reset_style = "async" if "async" in desc else "sync"
        return StructuredSpecDict(
            design_name=design_name,
            chip_family=chip_family,
            description=spec_text[:2000],
            top_module=design_name,
            reset_style=reset_style,
            parameters=[],
            sub_modules=submodules,
            interface_protocol=self._interface_protocol_for(expectations),
            timing_notes="Deterministic SID fallback; verify final pad ring and macro collateral before fabrication.",
            verification_hints=[
                "SID generated by deterministic semantic fallback after LLM SID failed validation.",
                f"Verify requested feature behavior and interfaces: {feature_names}.",
            ],
        )

    def enrich_with_pdf(self, pdf_path: str) -> str:
        """
        Extract text from a PDF specification document.
        
        Uses basic text extraction (no heavy dependencies).
        Falls back to reading the file as plain text if PDF parsing unavailable.
        """
        try:
            import subprocess
            result = subprocess.run(
                ["pdftotext", "-layout", pdf_path, "-"],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Fallback: try reading as plain text
        try:
            with open(pdf_path, "r", errors="ignore") as f:
                return f.read()
        except Exception:
            return ""

    def sid_to_rtl_prompt(self, sid: StructuredSpecDict, target_pdk_profile: Optional[Dict[str, Any]] = None) -> str:
        """
        Convert a SID into a detailed RTL generation prompt.
        
        This is what gets fed to the Coder agent — it's a precise, 
        unambiguous specification derived from the JSON contract.
        """
        sections = []
        sections.append(f"# RTL Specification for {sid.top_module}")
        sections.append(f"Chip Family: {sid.chip_family}")
        
        if target_pdk_profile:
            pdk_name = target_pdk_profile.get("profile", "unknown")
            voltage = target_pdk_profile.get("voltage_vdd", "unknown")
            clock_ns = target_pdk_profile.get("default_clock_period", "unknown")
            
            sections.append(f"\n## Target PDK Constraints (CRITICAL for Pipelining)")
            sections.append(f"Target node: {pdk_name} ({voltage}V).")
            
            if "asap7" in pdk_name or "asap5" in pdk_name or "asap2" in pdk_name or "open28" in pdk_name:
                sections.append(f"Advanced FinFET/GAAFET Node Warning: Expect severe wire RC delays and routing congestion. Heavily pipeline your datapaths. Do not synthesize large RAMs to flip-flops.")
                if "asap7" in pdk_name:
                    sections.append("Flow rule: ASAP7 is a predictive research node. Prefer the OpenROAD-flow-scripts backend (`agentic setup-7nm`, then `agentic run-orfs --platform asap7`) instead of treating it as a Sky130/OpenLane tapeout target.")
            elif "gf180" in pdk_name or "osu" in pdk_name:
                sections.append(f"Legacy Node Warning: Logic delays dominate wire delays. Optimize logic depth.")
            
            sections.append(f"If the clock period is aggressive for {pdk_name}, ensure intermediate pipeline registers are utilized heavily.\n")

        sections.append(f"Description: {sid.description}")
        sections.append(f"Reset: {sid.reset_style} ({sid.reset_polarity})")
        sections.append(f"Interface: {sid.interface_protocol or 'custom'}")

        if sid.parameters:
            sections.append("\n## Global Parameters")
            for p in sid.parameters:
                sections.append(f"  parameter {p.name} = {p.default}  // {p.description}")

        for sm in sid.sub_modules:
            sections.append(f"\n## Module: {sm.name}")
            sections.append(f"  Description: {sm.description}")

            if sm.parameters:
                sections.append("  Parameters:")
                for p in sm.parameters:
                    sections.append(f"    parameter {p.name} = {p.default}  // {p.description}")

            sections.append("  Ports:")
            for p in sm.ports:
                rv = f" (reset: {p.reset_value})" if p.reset_value else ""
                sections.append(f"    {p.direction} [{p.width}] {p.name} — {p.description}{rv}")

            sections.append(f"  Functional Logic:\n    {sm.functional_logic}")

            if sm.fsm_states:
                sections.append("  FSM States:")
                for s in sm.fsm_states:
                    sections.append(f"    {s.name}: {s.description}")
                    for t in s.transitions:
                        sections.append(f"      → {t.get('next_state')} when {t.get('condition')}")

            if sm.instantiates:
                sections.append(f"  Instantiates: {', '.join(sm.instantiates)}")

        if sid.verification_hints:
            sections.append("\n## Verification Hints")
            for h in sid.verification_hints:
                sections.append(f"  - {h}")

        return "\n".join(sections)
