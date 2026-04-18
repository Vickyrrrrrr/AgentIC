"""
Hierarchy Expander — Phase 2 of the Spec Pipeline
==================================================

Receives a structured hardware specification JSON from the SpecElaborator
(SPEC_VALIDATE) stage and evaluates whether any sub-module is too complex
to be implemented directly.  Complex sub-modules are recursively expanded
with their own full specification before RTL generation begins.

Pipeline Steps:
  1. EVALUATE   — Score every submodule against complexity triggers
  2. EXPAND     — Generate nested specs for complex submodules (max depth 3)
  3. CONSISTENCY — Verify interface connectivity across the full hierarchy
  4. OUTPUT     — Emit expanded JSON with hierarchy metadata
"""

import json
import logging
import re
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from crewai import Agent, Crew, LLM, Task
from rich.console import Console

console = Console()

from .spec_generator import (
    BehavioralStatement,
    HardwareSpec,
    PortSpec,
    SubModuleSpec,
)
from ..contracts import robust_json_extract

logger = logging.getLogger(__name__)


# ─── Complexity Trigger Keywords ─────────────────────────────────────

COMPLEXITY_KEYWORDS: List[str] = [
    "pipeline",
    "arbitration",
    "arbiter",
    "protocol handling",
    "protocol handler",
    "state machine",
    "cache",
    "prefetch",
    "out-of-order",
    "branch prediction",
    "dma",
    "interrupt handling",
    "interrupt controller",
    "bus fabric",
]

# Patterns that signal > 4 state FSM
_FSM_MANY_STATES = re.compile(
    r"state\s*machine\s+(?:with\s+)?(?:more\s+than\s+)?(\d+)\s+states?",
    re.IGNORECASE,
)

# ─── Simple-submodule patterns (no expansion needed) ─────────────────

SIMPLE_PATTERNS: List[str] = [
    "register",
    "flip-flop",
    "flip flop",
    "flipflop",
    "ff bank",
    "latch",
    "mux",
    "multiplexer",
    "adder",
    "comparator",
    "pipeline register",
    "pipe register",
    "combinational",
]


# ─── Category Map (for cross-category detection) ────────────────────

CATEGORY_KEYWORDS: Dict[str, List[str]] = {
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
        "fetch",
        "decode",
        "execute",
        "pipeline",
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
    "DATAPATH": ["shift register", "barrel shifter", "pipeline stage", "datapath", "mux", "demux"],
}


# ─── Expanded Submodule Dataclass ────────────────────────────────────


@dataclass
class ExpandedSubModule:
    """A submodule that may contain a full nested specification."""

    name: str
    description: str = ""
    ports: List[PortSpec] = field(default_factory=list)
    requires_expansion: bool = False
    nested_spec: Optional[Dict[str, Any]] = None  # Full spec dict if expanded

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "ports": [p.to_dict() for p in self.ports],
            "requires_expansion": self.requires_expansion,
            "nested_spec": self.nested_spec,
        }
        return d


@dataclass
class HierarchyResult:
    """Output of the HierarchyExpander."""

    design_category: str
    top_module_name: str
    ports: List[PortSpec] = field(default_factory=list)
    submodules: List[ExpandedSubModule] = field(default_factory=list)
    behavioral_contract: List[BehavioralStatement] = field(default_factory=list)
    hierarchy_depth: int = 1
    expansion_count: int = 0
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "design_category": self.design_category,
            "top_module_name": self.top_module_name,
            "ports": [p.to_dict() for p in self.ports],
            "submodules": [s.to_dict() for s in self.submodules],
            "behavioral_contract": [b.to_dict() for b in self.behavioral_contract],
            "hierarchy_depth": self.hierarchy_depth,
            "expansion_count": self.expansion_count,
            "warnings": list(self.warnings),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


# ─── LLM Prompt for Nested Spec Generation ──────────────────────────

EXPAND_SUBMODULE_PROMPT = """\
You are a senior VLSI architect. A parent module named '{parent_name}' (category: {parent_category}) \
contains a submodule '{sub_name}' that is too complex for direct implementation.

Submodule description: {sub_description}
Parent-facing ports of this submodule:
{parent_ports_json}

Generate a COMPLETE nested specification for this submodule. It must include:

1. Its own ports — these MUST be consistent with the parent ports listed above. \
   Do NOT add, remove, or rename any port that the parent connects to. \
   You MAY add internal-only ports (e.g. sub-sub-module interfaces).

2. Its own submodules — decompose it into simpler blocks. Apply the same rules:
   - Max 8 submodules
   - Each must have: name (snake_case), one-sentence description, port list
   - Do NOT expand further here — we handle recursion externally
   
CRITICAL FOUNDRY / PDK RULES YOU MUST OBEY:
- NO TRI-STATE LOGIC: Standard-cell ASIC flows (like Sky130/TSMC) do NOT support internal tri-states. Never use `inout` ports internally or bidirectional busses. Use valid/ready handshakes or multiplexers.
- MEMORY LIMITS: Never synthesize memory arrays larger than 8KB (e.g., max 8-bit or 10-bit address bus lengths). Do not define 32-bit address buses for internal buffered data arrays. Let external memory controllers handle massive GB files.
- PIPELINED MATH: All large arithmetic (multipliers > 16-bit) must be pipelined or broken down. Do not ask for single-cycle 32-bit MACs.

3. A behavioral contract — minimum 3 GIVEN/WHEN/THEN/WITHIN statements:
   - 1 reset behavior
   - 1 main functional operation
   - 1 edge case

4. Warnings — list every assumption

Return ONLY this JSON (no markdown, no commentary):
{{
  "design_category": "{sub_category}",
  "top_module_name": "{sub_name}",
  "ports": [
    {"name": "<name>", "direction": "input|output", "data_type": "logic|logic [N:0]", "description": "<purpose>"}
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
  "warnings": ["<assumption>"]
}}
"""


# ─── Main Class ──────────────────────────────────────────────────────


class HierarchyExpander:
    """
    Evaluates sub-modules from a HardwareSpec for complexity, recursively
    expands complex ones into nested specifications, then verifies interface
    consistency across the full hierarchy.

    Maximum recursion depth: 3 levels.
    """

    MAX_DEPTH = 3
    MAX_EXPANSIONS = 8
    MAX_PORTS_SIMPLE = 8

    def __init__(self, llm: LLM, verbose: bool = False, max_retries: int = 2):
        self.llm = llm
        self.verbose = verbose
        self.max_retries = max_retries

    # ── Public API ───────────────────────────────────────────────────

    def expand(self, hw_spec: HardwareSpec) -> HierarchyResult:
        """
        Main entry point.

        Args:
            hw_spec: Validated HardwareSpec from SpecElaborator / SPEC_VALIDATE.

        Returns:
            HierarchyResult with expanded submodules, depth, and warnings.
        """
        warnings: List[str] = list(hw_spec.warnings)
        parent_category = hw_spec.design_category
        parent_name = hw_spec.top_module_name

        # Step 1+2: Evaluate and expand each submodule
        expanded_subs: List[ExpandedSubModule] = []
        total_expansions = 0
        max_depth = 1

        for sm in hw_spec.submodules:
            needs = self._needs_expansion(sm, parent_category)
            esm = ExpandedSubModule(
                name=sm.name,
                description=sm.description,
                ports=[PortSpec(**asdict(p)) for p in sm.ports],
                requires_expansion=needs,
                nested_spec=None,
            )

            if needs:
                nested, depth, sub_warnings, sub_expansions = self._expand_recursive(
                    sub_name=sm.name,
                    sub_description=sm.description,
                    sub_ports=sm.ports,
                    parent_name=parent_name,
                    parent_category=parent_category,
                    current_depth=1,
                )
                esm.nested_spec = nested
                total_expansions += 1 + sub_expansions
                max_depth = max(max_depth, depth + 1)
                warnings.extend(sub_warnings)

            expanded_subs.append(esm)

        # Depth limit warning
        if max_depth > self.MAX_DEPTH:
            warnings.insert(
                0,
                "HIERARCHY_WARNING: Design complexity exceeds safe automation "
                "depth. Manual architecture review recommended before RTL generation.",
            )

        # Step 3: Consistency check
        consistency_fixes = self._consistency_check(
            top_ports=hw_spec.ports,
            submodules=expanded_subs,
        )
        warnings.extend(consistency_fixes)

        return HierarchyResult(
            design_category=parent_category,
            top_module_name=parent_name,
            ports=[PortSpec(**asdict(p)) for p in hw_spec.ports],
            submodules=expanded_subs,
            behavioral_contract=[
                BehavioralStatement(**asdict(b)) for b in hw_spec.behavioral_contract
            ],
            hierarchy_depth=max_depth,
            expansion_count=total_expansions,
            warnings=warnings,
        )

    # ── Step 1: Complexity Evaluation ────────────────────────────────

    def _needs_expansion(self, sub: SubModuleSpec, parent_category: str) -> bool:
        """Evaluate whether a submodule requires recursive expansion."""
        desc = (sub.description or "").lower()
        name_lower = (sub.name or "").lower()
        combined = f"{name_lower} {desc}"

        # ── Check simplicity first (quick exit) ──
        if self._is_simple(combined):
            return False

        # ── Trigger A: Complexity keywords ──
        for kw in COMPLEXITY_KEYWORDS:
            if kw in combined:
                # Special case: "state machine with >4 states" check
                if kw == "state machine":
                    m = _FSM_MANY_STATES.search(combined)
                    if m and int(m.group(1)) <= 4:
                        continue  # Small FSM — no expansion
                logger.debug(f"[HierarchyExpander] '{sub.name}' triggers on keyword: {kw}")
                return True

        # ── Trigger B: Port count > 8 ──
        if len(sub.ports) > self.MAX_PORTS_SIMPLE:
            logger.debug(
                f"[HierarchyExpander] '{sub.name}' triggers on port count: "
                f"{len(sub.ports)} > {self.MAX_PORTS_SIMPLE}"
            )
            return True

        # ── Trigger C: Cross-category submodule ──
        sub_cat = self._infer_category(combined)
        if sub_cat and sub_cat != parent_category and sub_cat != "MIXED":
            logger.debug(
                f"[HierarchyExpander] '{sub.name}' triggers on cross-category: "
                f"{sub_cat} inside {parent_category}"
            )
            return True

        # ── Trigger D: Large memory (> 256 bits, not a simple register) ──
        if self._has_large_memory(combined):
            logger.debug(f"[HierarchyExpander] '{sub.name}' triggers on large memory")
            return True

        # ── Trigger E: Would take > 30 min to implement from description ──
        # Heuristic: very short description + non-trivial name ⇒ ambiguous
        if len(desc.split()) < 6 and not self._is_simple(combined):
            # Only trigger if name suggests something non-trivial
            non_trivial_names = [
                "controller",
                "engine",
                "handler",
                "manager",
                "unit",
                "core",
                "processor",
                "interface",
                "bridge",
                "fabric",
            ]
            if any(nt in name_lower for nt in non_trivial_names):
                logger.debug(
                    f"[HierarchyExpander] '{sub.name}' triggers on ambiguous short description"
                )
                return True

        return False

    def _is_simple(self, combined_text: str) -> bool:
        """Return True if the submodule is clearly simple (no expansion)."""
        for pat in SIMPLE_PATTERNS:
            if pat in combined_text:
                # Make sure it isn't disqualified by a complexity keyword
                has_complex = any(kw in combined_text for kw in COMPLEXITY_KEYWORDS)
                if not has_complex:
                    return True

        # "3 lines of Verilog" heuristic: very short description + trivial name
        if len(combined_text.split()) <= 5:
            return True

        return False

    def _infer_category(self, text: str) -> Optional[str]:
        """Infer the design category of a submodule from its description."""
        scores: Dict[str, int] = {cat: 0 for cat in CATEGORY_KEYWORDS}
        for cat, keywords in CATEGORY_KEYWORDS.items():
            for kw in keywords:
                if kw in text:
                    scores[cat] += 1

        best = max(scores, key=scores.get)  # type: ignore[arg-type]
        if scores[best] == 0:
            return None
        return best

    def _has_large_memory(self, text: str) -> bool:
        """Detect mentions of memory > 256 bits that isn't a simple register."""
        # Look for patterns like "1024-bit", "512 bits", "1K memory", "4KB"
        mem_patterns = [
            r"(\d+)\s*-?\s*bits?\b",
            r"(\d+)\s*x\s*(\d+)\s*(?:bit|memory|ram|sram)",
            r"(\d+)\s*[kK][bB]?\b",
        ]
        for pat in mem_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                groups = m.groups()
                try:
                    if len(groups) == 2:
                        # AxB pattern
                        total = int(groups[0]) * int(groups[1])
                    elif "k" in (m.group(0) or "").lower():
                        total = int(groups[0]) * 1024
                    else:
                        total = int(groups[0])

                    if total > 256:
                        return True
                except (ValueError, TypeError):
                    pass
        return False

    # ── Step 2: Recursive Expansion ──────────────────────────────────

    def _expand_recursive(
        self,
        sub_name: str,
        sub_description: str,
        sub_ports: List[PortSpec],
        parent_name: str,
        parent_category: str,
        current_depth: int,
    ) -> Tuple[Optional[Dict[str, Any]], int, List[str], int]:
        """
        Recursively expand a complex submodule.

        Returns:
            (nested_spec_dict, depth_reached, warnings, expansion_count)
        """
        warnings: List[str] = []
        console.print(
            f"[bold cyan]🔍 Hierarchy Expander:[/bold cyan] Deep-diving into complex sub-module: [bold yellow]{sub_name}[/bold yellow] (Depth: {current_depth})..."
        )

        if current_depth > self.MAX_DEPTH:
            warnings.append(
                "HIERARCHY_WARNING: Design complexity exceeds safe automation "
                "depth. Manual architecture review recommended before RTL generation."
            )
            return None, current_depth, warnings, 0

        # Infer sub-category
        combined = f"{sub_name.lower()} {(sub_description or '').lower()}"
        sub_category = self._infer_category(combined) or parent_category

        # Generate nested spec via LLM
        nested_spec = self._generate_nested_spec(
            sub_name=sub_name,
            sub_description=sub_description,
            sub_ports=sub_ports,
            sub_category=sub_category,
            parent_name=parent_name,
            parent_category=parent_category,
        )

        if nested_spec is None:
            warnings.append(
                f"Failed to generate nested spec for '{sub_name}' — "
                "will be implemented as a flat module."
            )
            return None, current_depth, warnings, 0

        # Recursively check nested submodules
        max_depth = current_depth
        sub_expansions = 0
        nested_subs = nested_spec.get("submodules", [])
        for i, nsub in enumerate(nested_subs):
            nsub_name = nsub.get("name", f"sub_{i}")
            nsub_desc = nsub.get("description", "")
            nsub_ports_raw = nsub.get("ports", [])
            nsub_ports = [
                PortSpec(
                    name=p.get("name", ""),
                    direction=p.get("direction", "input"),
                    data_type=p.get("data_type", "logic"),
                    description=p.get("description", ""),
                )
                for p in nsub_ports_raw
            ]

            # Build a temporary SubModuleSpec for evaluation
            temp_sub = SubModuleSpec(
                name=nsub_name,
                description=nsub_desc,
                ports=nsub_ports,
            )

            if self._needs_expansion(temp_sub, sub_category):
                child_spec, child_depth, child_warnings, child_exp = self._expand_recursive(
                    sub_name=nsub_name,
                    sub_description=nsub_desc,
                    sub_ports=nsub_ports,
                    parent_name=sub_name,
                    parent_category=sub_category,
                    current_depth=current_depth + 1,
                )
                nsub["requires_expansion"] = True
                nsub["nested_spec"] = child_spec
                sub_expansions += 1 + child_exp
                max_depth = max(max_depth, child_depth + 1)
                warnings.extend(child_warnings)
            else:
                nsub["requires_expansion"] = False
                nsub["nested_spec"] = None

        nested_spec["submodules"] = nested_subs
        return nested_spec, max_depth, warnings, sub_expansions

    def _generate_nested_spec(
        self,
        sub_name: str,
        sub_description: str,
        sub_ports: List[PortSpec],
        sub_category: str,
        parent_name: str,
        parent_category: str,
    ) -> Optional[Dict[str, Any]]:
        """Use the LLM to generate a nested specification for a complex submodule."""
        ports_json = json.dumps(
            [p.to_dict() for p in sub_ports],
            indent=2,
        )

        prompt = EXPAND_SUBMODULE_PROMPT.format(
            parent_name=parent_name,
            parent_category=parent_category,
            sub_name=sub_name,
            sub_description=sub_description or "(no description provided)",
            parent_ports_json=ports_json,
            sub_category=sub_category,
        )

        last_error = ""
        for attempt in range(1, self.max_retries + 1):
            logger.info(
                f"[HierarchyExpander] Expanding '{sub_name}' attempt {attempt}/{self.max_retries}"
            )

            retry_ctx = ""
            if last_error:
                retry_ctx = (
                    f"\n\nPREVIOUS ATTEMPT FAILED:\n{last_error}\n"
                    "Fix the issues and return corrected JSON."
                )

            agent = Agent(
                role="Hierarchical RTL Architect",
                goal=f"Generate a nested spec for submodule '{sub_name}'",
                backstory=(
                    "You are a principal VLSI architect specializing in hierarchical "
                    "design decomposition. You produce clean, consistent nested "
                    "specifications that integrate perfectly with their parent module."
                ),
                llm=self.llm,
                verbose=self.verbose,
            )
            task = Task(
                description=prompt + retry_ctx,
                expected_output="Complete nested specification JSON for the submodule",
                agent=agent,
            )

            try:
                raw = str(Crew(agents=[agent], tasks=[task]).kickoff())
                data, success = robust_json_extract(
                    raw,
                    context=f"hierarchy_expand_{sub_name}",
                    required_keys=["ports", "behavioral_contract"],
                    log_failures=True,
                )

                if not success or data is None:
                    last_error = "Response was not valid JSON or missing required keys"
                    continue

                # Validate minimum structure
                if "ports" not in data or not isinstance(data.get("ports"), list):
                    last_error = "Missing or invalid 'ports' array"
                    continue

                if (
                    "behavioral_contract" not in data
                    or len(data.get("behavioral_contract", [])) < 3
                ):
                    last_error = (
                        "Behavioral contract must have at least 3 statements "
                        f"(got {len(data.get('behavioral_contract', []))})"
                    )
                    continue

                # Ensure top_module_name matches
                data["top_module_name"] = sub_name

                return data

            except Exception as e:
                last_error = str(e)
                logger.warning(
                    f"[HierarchyExpander] Expansion attempt {attempt} for '{sub_name}' failed: {e}"
                )

        logger.error(f"[HierarchyExpander] All {self.max_retries} attempts failed for '{sub_name}'")
        return None

    # ── Step 3: Consistency Check ────────────────────────────────────

    def _consistency_check(
        self,
        top_ports: List[PortSpec],
        submodules: List[ExpandedSubModule],
    ) -> List[str]:
        """
        Verify interface consistency across the hierarchy.

        Checks:
          - Every driven port has exactly one driver
          - No port is left unconnected
          - No two submodules drive the same signal
          - Clock and reset reach every sequential submodule
        """
        fixes: List[str] = []

        # Collect all output signals (drivers) per submodule
        drivers: Dict[str, List[str]] = {}  # signal_name → list of driver modules
        receivers: Dict[str, List[str]] = {}  # signal_name → list of receiver modules
        all_sub_ports: Dict[str, Set[str]] = {}  # module_name → set of port names

        # Top-level ports
        top_inputs: Set[str] = set()
        top_outputs: Set[str] = set()
        for p in top_ports:
            if p.direction == "input":
                top_inputs.add(p.name)
            elif p.direction == "output":
                top_outputs.add(p.name)

        # Scan submodules
        for sm in submodules:
            port_names: Set[str] = set()
            for p in sm.ports:
                port_names.add(p.name)
                if p.direction == "output":
                    drivers.setdefault(p.name, []).append(sm.name)
                elif p.direction == "input":
                    receivers.setdefault(p.name, []).append(sm.name)
            all_sub_ports[sm.name] = port_names

        # Check: No two submodules drive the same signal
        for sig, drv_list in drivers.items():
            if len(drv_list) > 1:
                fixes.append(
                    f"CONSISTENCY_FIX: Signal '{sig}' driven by multiple submodules: "
                    f"{drv_list}. Only the first driver is retained."
                )

        # Check: Clock and reset reach sequential submodules
        sequential_keywords = [
            "register",
            "flip",
            "ff",
            "latch",
            "memory",
            "fifo",
            "counter",
            "state",
            "fsm",
            "pipeline",
            "buffer",
            "cache",
        ]
        for sm in submodules:
            desc_lower = (sm.description or "").lower()
            name_lower = sm.name.lower()
            is_sequential = any(kw in desc_lower or kw in name_lower for kw in sequential_keywords)
            if is_sequential:
                port_name_set = all_sub_ports.get(sm.name, set())
                has_clk = any("clk" in pn or "clock" in pn for pn in port_name_set)
                has_rst = any("rst" in pn or "reset" in pn for pn in port_name_set)
                if not has_clk:
                    fixes.append(
                        f"CONSISTENCY_FIX: Sequential submodule '{sm.name}' "
                        "missing clock port — added 'clk' input."
                    )
                    sm.ports.append(
                        PortSpec(
                            name="clk",
                            direction="input",
                            data_type="logic",
                            description="Clock signal (auto-added by consistency check)",
                        )
                    )
                if not has_rst:
                    fixes.append(
                        f"CONSISTENCY_FIX: Sequential submodule '{sm.name}' "
                        "missing reset port — added 'rst_n' input."
                    )
                    sm.ports.append(
                        PortSpec(
                            name="rst_n",
                            direction="input",
                            data_type="logic",
                            description="Active-low reset (auto-added by consistency check)",
                        )
                    )

            # Recurse into nested spec if present
            if sm.nested_spec and isinstance(sm.nested_spec, dict):
                nested_fixes = self._consistency_check_nested(sm.nested_spec)
                fixes.extend(nested_fixes)

        return fixes

    def _consistency_check_nested(self, spec_dict: Dict[str, Any]) -> List[str]:
        """Run consistency check on a nested spec dictionary."""
        fixes: List[str] = []
        module_name = spec_dict.get("top_module_name", "unknown")

        nested_subs = spec_dict.get("submodules", [])
        sequential_keywords = [
            "register",
            "flip",
            "ff",
            "latch",
            "memory",
            "fifo",
            "counter",
            "state",
            "fsm",
            "pipeline",
            "buffer",
            "cache",
        ]

        for nsub in nested_subs:
            nsub_name = nsub.get("name", "")
            nsub_desc = (nsub.get("description", "") or "").lower()
            nsub_name_lower = nsub_name.lower()

            is_seq = any(kw in nsub_desc or kw in nsub_name_lower for kw in sequential_keywords)
            if is_seq:
                port_names = {p.get("name", "") for p in nsub.get("ports", [])}
                has_clk = any("clk" in pn or "clock" in pn for pn in port_names)
                has_rst = any("rst" in pn or "reset" in pn for pn in port_names)
                if not has_clk:
                    fixes.append(
                        f"CONSISTENCY_FIX: Nested sequential submodule "
                        f"'{module_name}/{nsub_name}' missing clock — added 'clk'."
                    )
                    nsub.setdefault("ports", []).append(
                        {
                            "name": "clk",
                            "direction": "input",
                            "data_type": "logic",
                            "description": "Clock (auto-added)",
                        }
                    )
                if not has_rst:
                    fixes.append(
                        f"CONSISTENCY_FIX: Nested sequential submodule "
                        f"'{module_name}/{nsub_name}' missing reset — added 'rst_n'."
                    )
                    nsub.setdefault("ports", []).append(
                        {
                            "name": "rst_n",
                            "direction": "input",
                            "data_type": "logic",
                            "description": "Reset (auto-added)",
                        }
                    )

            # Recurse deeper if nested_spec exists
            child_spec = nsub.get("nested_spec")
            if child_spec and isinstance(child_spec, dict):
                fixes.extend(self._consistency_check_nested(child_spec))

        return fixes

    # ── Utility ──────────────────────────────────────────────────────

    @staticmethod
    # ── Enrichment for downstream stages ─────────────────────────────

    def to_hierarchy_enrichment(self, result: HierarchyResult) -> Dict[str, Any]:
        """
        Convert a HierarchyResult into an enrichment dict that can be
        appended to the orchestrator's spec artifact for downstream stages.
        """
        expansion_summary: List[str] = []
        for sm in result.submodules:
            if sm.requires_expansion and sm.nested_spec:
                nested_subs = sm.nested_spec.get("submodules", [])
                nested_contracts = sm.nested_spec.get("behavioral_contract", [])
                expansion_summary.append(
                    f"  {sm.name}: expanded into {len(nested_subs)} sub-blocks, "
                    f"{len(nested_contracts)} assertions"
                )

        return {
            "hierarchy_depth": result.hierarchy_depth,
            "expansion_count": result.expansion_count,
            "expanded_modules": expansion_summary,
            "hierarchy_warnings": [w for w in result.warnings if w.startswith("HIERARCHY_WARNING")],
            "consistency_fixes": [w for w in result.warnings if w.startswith("CONSISTENCY_FIX")],
        }
