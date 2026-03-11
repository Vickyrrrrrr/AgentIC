"""
VerificationPlanner — Deterministic Verification Plan Generator
================================================================
Produces a complete, structured verification plan from the hardware spec
BEFORE any testbench code is written. Ensures every P0 test is
implemented by downstream testbench generation.

Pipeline stage: VERIFICATION_PLAN (between CDC_ANALYZE and RTL_GEN)

Five steps:
    1. Extract testable behaviors from behavioral_contract
    2. Add mandatory tests by design category
    3. Generate SVA properties for all P0 tests
    4. Create coverage plan (port bins, FSM, FIFO boundaries)
    5. Output structured VerificationPlan dataclass
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


# ─── Constants ────────────────────────────────────────────────────────

class Priority:
    P0 = "P0"  # Must pass — testbench generator must implement
    P1 = "P1"  # Should pass
    P2 = "P2"  # Nice to have


class TestCategory:
    RESET = "RESET"
    FUNCTIONAL = "FUNCTIONAL"
    EDGE_CASE = "EDGE_CASE"
    PROTOCOL = "PROTOCOL"
    TIMING = "TIMING"
    STRESS = "STRESS"


class Complexity:
    TRIVIAL = "TRIVIAL"      # ~5 lines
    MODERATE = "MODERATE"    # ~20 lines
    COMPLEX = "COMPLEX"      # 50+ lines


# ─── Output Dataclasses ──────────────────────────────────────────────

@dataclass
class SVAProperty:
    """A SystemVerilog Assertion derived from a P0 test case."""
    property_name: str
    description: str
    sva_code: str
    related_test_id: str

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass
class CoveragePoint:
    """A single coverage collection point."""
    name: str
    cover_type: str          # "port_bins" | "fsm_state" | "fsm_transition" | "fifo_boundary" | "toggle"
    target_signal: str
    bins: List[str] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TestCase:
    """A single verification test case."""
    test_id: str
    title: str
    category: str            # TestCategory value
    priority: str            # Priority value
    complexity: str          # Complexity value
    description: str
    stimulus: str            # What to drive
    expected: str            # What to check
    sva_property: Optional[SVAProperty] = None
    source: str = "behavioral_contract"  # "behavioral_contract" | "mandatory"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class VerificationPlan:
    """Complete verification plan output."""
    top_module_name: str
    design_category: str
    total_tests: int = 0
    p0_count: int = 0
    p1_count: int = 0
    p2_count: int = 0
    test_cases: List[TestCase] = field(default_factory=list)
    sva_properties: List[SVAProperty] = field(default_factory=list)
    coverage_points: List[CoveragePoint] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "top_module_name": self.top_module_name,
            "design_category": self.design_category,
            "total_tests": self.total_tests,
            "p0_count": self.p0_count,
            "p1_count": self.p1_count,
            "p2_count": self.p2_count,
            "test_cases": [t.to_dict() for t in self.test_cases],
            "sva_properties": [s.to_dict() for s in self.sva_properties],
            "coverage_points": [c.to_dict() for c in self.coverage_points],
            "warnings": self.warnings,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


# ─── Mandatory Test Templates ────────────────────────────────────────

_RESET_TESTS = [
    {
        "test_id": "TEST_RST_001",
        "title": "Assert reset clears all outputs",
        "category": TestCategory.RESET,
        "priority": Priority.P0,
        "complexity": Complexity.TRIVIAL,
        "description": "Apply active-low reset and verify all outputs go to default values",
        "stimulus": "Assert rst_n=0 for 2 cycles, then release",
        "expected": "All output registers go to 0 / default",
    },
    {
        "test_id": "TEST_RST_002",
        "title": "Reset during active operation",
        "category": TestCategory.RESET,
        "priority": Priority.P0,
        "complexity": Complexity.MODERATE,
        "description": "Apply reset while module is in a non-idle state",
        "stimulus": "Drive module to active state, assert rst_n=0 mid-operation",
        "expected": "Module returns to idle/default state within 2 cycles of reset",
    },
    {
        "test_id": "TEST_RST_003",
        "title": "Multiple reset assertion cycles",
        "category": TestCategory.STRESS,
        "priority": Priority.P1,
        "complexity": Complexity.MODERATE,
        "description": "Rapidly toggle reset to verify no metastability or latch-up",
        "stimulus": "Toggle rst_n every 3 cycles for 20 cycles",
        "expected": "No X/Z propagation, outputs remain deterministic",
    },
]

_PROCESSOR_TESTS = [
    {
        "test_id": "TEST_PROC_001",
        "title": "Instruction fetch and decode",
        "category": TestCategory.FUNCTIONAL,
        "priority": Priority.P0,
        "complexity": Complexity.COMPLEX,
        "description": "Verify basic instruction fetch-decode pipeline stage",
        "stimulus": "Load instruction memory with NOP/ADD/SUB, drive clk",
        "expected": "Correct opcode decoded within pipeline latency",
    },
    {
        "test_id": "TEST_PROC_002",
        "title": "Register file read/write",
        "category": TestCategory.FUNCTIONAL,
        "priority": Priority.P0,
        "complexity": Complexity.MODERATE,
        "description": "Write to register file and read back",
        "stimulus": "Write known values to registers, then read each",
        "expected": "Read data matches written data for all addressed registers",
    },
    {
        "test_id": "TEST_PROC_003",
        "title": "Pipeline stall/flush",
        "category": TestCategory.EDGE_CASE,
        "priority": Priority.P0,
        "complexity": Complexity.COMPLEX,
        "description": "Verify pipeline handles data hazards correctly",
        "stimulus": "Issue back-to-back dependent instructions",
        "expected": "Stall inserts or forwarding produces correct result",
    },
    {
        "test_id": "TEST_PROC_004",
        "title": "Branch taken / not-taken",
        "category": TestCategory.FUNCTIONAL,
        "priority": Priority.P1,
        "complexity": Complexity.COMPLEX,
        "description": "Verify branch resolution and PC update",
        "stimulus": "Issue conditional branch with condition true then false",
        "expected": "PC updates correctly for both taken and not-taken paths",
    },
    {
        "test_id": "TEST_PROC_005",
        "title": "Interrupt handling",
        "category": TestCategory.PROTOCOL,
        "priority": Priority.P1,
        "complexity": Complexity.COMPLEX,
        "description": "Assert interrupt during active execution",
        "stimulus": "Drive interrupt line during instruction execution",
        "expected": "PC jumps to ISR, context is saved",
    },
    {
        "test_id": "TEST_PROC_006",
        "title": "Memory read/write latency",
        "category": TestCategory.TIMING,
        "priority": Priority.P1,
        "complexity": Complexity.MODERATE,
        "description": "Verify data memory access completes within spec'd latency",
        "stimulus": "Issue load/store to data memory",
        "expected": "Data available within declared latency cycles",
    },
]

_MEMORY_TESTS = [
    {
        "test_id": "TEST_MEM_001",
        "title": "Write then read all addresses",
        "category": TestCategory.FUNCTIONAL,
        "priority": Priority.P0,
        "complexity": Complexity.MODERATE,
        "description": "Exhaustive write-read of all memory addresses",
        "stimulus": "Write unique pattern to each address, read back",
        "expected": "Read data matches written data for every address",
    },
    {
        "test_id": "TEST_MEM_002",
        "title": "Simultaneous read-write (dual port)",
        "category": TestCategory.EDGE_CASE,
        "priority": Priority.P0,
        "complexity": Complexity.MODERATE,
        "description": "If dual-port, read and write same address simultaneously",
        "stimulus": "Write addr=N while reading addr=N in same cycle",
        "expected": "Defined behavior per spec (read-before-write or write-first)",
    },
    {
        "test_id": "TEST_MEM_003",
        "title": "Boundary address access",
        "category": TestCategory.EDGE_CASE,
        "priority": Priority.P0,
        "complexity": Complexity.TRIVIAL,
        "description": "Access first and last valid addresses",
        "stimulus": "Write/read address 0 and address MAX",
        "expected": "No out-of-bounds errors, correct data returned",
    },
]

_INTERFACE_TESTS = [
    {
        "test_id": "TEST_IF_001",
        "title": "Protocol handshake sequence",
        "category": TestCategory.PROTOCOL,
        "priority": Priority.P0,
        "complexity": Complexity.MODERATE,
        "description": "Verify valid/ready handshake completes correctly",
        "stimulus": "Assert valid, wait for ready, transfer data",
        "expected": "Data transferred on (valid && ready) cycle",
    },
    {
        "test_id": "TEST_IF_002",
        "title": "Back-pressure handling",
        "category": TestCategory.PROTOCOL,
        "priority": Priority.P0,
        "complexity": Complexity.MODERATE,
        "description": "Hold ready low to create back-pressure",
        "stimulus": "Assert valid, keep ready=0 for N cycles, then assert ready",
        "expected": "Data held stable until handshake completes, no data loss",
    },
    {
        "test_id": "TEST_IF_003",
        "title": "Burst transfer",
        "category": TestCategory.FUNCTIONAL,
        "priority": Priority.P1,
        "complexity": Complexity.COMPLEX,
        "description": "Send burst of consecutive transfers",
        "stimulus": "Drive valid continuously with incrementing data",
        "expected": "All data items received in order without loss",
    },
    {
        "test_id": "TEST_IF_004",
        "title": "Idle / inactive state",
        "category": TestCategory.EDGE_CASE,
        "priority": Priority.P1,
        "complexity": Complexity.TRIVIAL,
        "description": "Verify interface outputs in idle when no transaction",
        "stimulus": "Deassert valid and all enables for 10 cycles",
        "expected": "No spurious ready/valid assertions, outputs stable",
    },
]

_ARITHMETIC_TESTS = [
    {
        "test_id": "TEST_ARITH_001",
        "title": "Zero operand",
        "category": TestCategory.EDGE_CASE,
        "priority": Priority.P0,
        "complexity": Complexity.TRIVIAL,
        "description": "Verify operation with one or both operands zero",
        "stimulus": "Drive a=0, b=non-zero; then a=non-zero, b=0; then both=0",
        "expected": "Correct arithmetic result for all zero combinations",
    },
    {
        "test_id": "TEST_ARITH_002",
        "title": "Maximum value (overflow boundary)",
        "category": TestCategory.EDGE_CASE,
        "priority": Priority.P0,
        "complexity": Complexity.MODERATE,
        "description": "Drive maximum representable values to check overflow",
        "stimulus": "Drive a=MAX, b=MAX for addition/multiply",
        "expected": "Overflow flag set or result wraps correctly per spec",
    },
    {
        "test_id": "TEST_ARITH_003",
        "title": "Signed vs unsigned interpretation",
        "category": TestCategory.FUNCTIONAL,
        "priority": Priority.P1,
        "complexity": Complexity.MODERATE,
        "description": "Verify signed arithmetic produces correct sign extension",
        "stimulus": "Drive negative values (MSB=1) through signed operations",
        "expected": "Result matches expected signed arithmetic",
    },
    {
        "test_id": "TEST_ARITH_004",
        "title": "Pipeline throughput",
        "category": TestCategory.TIMING,
        "priority": Priority.P1,
        "complexity": Complexity.MODERATE,
        "description": "Feed consecutive operations to measure pipeline throughput",
        "stimulus": "Drive new operands every cycle for N cycles",
        "expected": "Results appear after pipeline latency, one per cycle",
    },
]

_CATEGORY_TESTS = {
    "PROCESSOR": _PROCESSOR_TESTS,
    "MEMORY": _MEMORY_TESTS,
    "INTERFACE": _INTERFACE_TESTS,
    "ARITHMETIC": _ARITHMETIC_TESTS,
}


# ─── VerificationPlanner ─────────────────────────────────────────────

class VerificationPlanner:
    """
    Deterministic verification plan generator.

    Usage::

        planner = VerificationPlanner()
        plan = planner.plan(hardware_spec)
    """

    def plan(
        self,
        hardware_spec: Any,
        cdc_result: Any = None,
        hierarchy_result: Any = None,
    ) -> VerificationPlan:
        """
        Generate a complete verification plan from the hardware spec.

        Parameters
        ----------
        hardware_spec : HardwareSpec
            The validated hardware specification.
        cdc_result : CDCResult, optional
            CDC analysis result (for multi-clock coverage).
        hierarchy_result : HierarchyResult, optional
            Hierarchy expansion result (for submodule coverage).

        Returns
        -------
        VerificationPlan
        """
        top_name = getattr(hardware_spec, "top_module_name", "unknown")
        category = getattr(hardware_spec, "design_category", "MIXED")

        plan = VerificationPlan(
            top_module_name=top_name,
            design_category=category,
        )

        # Step 1 — extract testable behaviors from behavioral_contract
        self._extract_testable_behaviors(hardware_spec, plan)

        # Step 2 — add mandatory tests by design category
        self._add_mandatory_tests(category, plan)

        # Step 3 — generate SVA properties for P0 tests
        self._generate_sva_properties(plan, hardware_spec)

        # Step 4 — create coverage plan
        self._generate_coverage_plan(plan, hardware_spec, cdc_result)

        # Step 5 — finalize counts
        self._finalize(plan)

        return plan

    # ── Step 1: Extract Testable Behaviors ────────────────────────────

    def _extract_testable_behaviors(
        self,
        spec: Any,
        plan: VerificationPlan,
    ) -> None:
        """Parse behavioral_contract statements into test cases."""
        contracts = getattr(spec, "behavioral_contract", []) or []
        if not contracts:
            plan.warnings.append("No behavioral_contract found — only mandatory tests will be generated")
            return

        for idx, stmt in enumerate(contracts, start=1):
            given = getattr(stmt, "given", str(stmt)) if not isinstance(stmt, str) else stmt
            when = getattr(stmt, "when", "") if not isinstance(stmt, str) else ""
            then = getattr(stmt, "then", "") if not isinstance(stmt, str) else ""
            within = getattr(stmt, "within", "1 cycle") if not isinstance(stmt, str) else "1 cycle"

            test_id = f"TEST_BEH_{idx:03d}"

            # Determine category from statement content
            category = self._categorize_behavior(given, when, then)

            # Determine priority from timing constraint
            priority = Priority.P0 if within and within != "N/A" else Priority.P1

            # Determine complexity from description length
            total_len = len(given) + len(when) + len(then)
            if total_len < 60:
                complexity = Complexity.TRIVIAL
            elif total_len < 150:
                complexity = Complexity.MODERATE
            else:
                complexity = Complexity.COMPLEX

            title = self._shorten(f"GIVEN {given} WHEN {when} THEN {then}", 80)

            tc = TestCase(
                test_id=test_id,
                title=title,
                category=category,
                priority=priority,
                complexity=complexity,
                description=f"GIVEN {given} WHEN {when} THEN {then} WITHIN {within}",
                stimulus=f"Set up: {given}. Drive: {when}",
                expected=f"Check: {then} within {within}",
                source="behavioral_contract",
            )
            plan.test_cases.append(tc)

    def _categorize_behavior(self, given: str, when: str, then: str) -> str:
        """Infer test category from behavioral statement text."""
        combined = f"{given} {when} {then}".lower()
        if any(kw in combined for kw in ("reset", "rst", "rst_n", "power-on")):
            return TestCategory.RESET
        if any(kw in combined for kw in ("edge", "boundary", "overflow", "underflow", "max", "min", "full", "empty")):
            return TestCategory.EDGE_CASE
        if any(kw in combined for kw in ("valid", "ready", "handshake", "ack", "req", "protocol")):
            return TestCategory.PROTOCOL
        if any(kw in combined for kw in ("latency", "cycle", "timing", "clock", "delay", "frequency")):
            return TestCategory.TIMING
        if any(kw in combined for kw in ("stress", "continuous", "rapid", "burst", "saturation")):
            return TestCategory.STRESS
        return TestCategory.FUNCTIONAL

    @staticmethod
    def _shorten(text: str, max_len: int) -> str:
        if len(text) <= max_len:
            return text
        return text[: max_len - 3] + "..."

    # ── Step 2: Add Mandatory Tests ───────────────────────────────────

    def _add_mandatory_tests(self, category: str, plan: VerificationPlan) -> None:
        """Add reset tests (for all designs) + category-specific mandatory tests."""
        # Reset tests are mandatory for all designs
        for tmpl in _RESET_TESTS:
            plan.test_cases.append(TestCase(**tmpl, source="mandatory"))

        # Category-specific tests
        cat_tests = _CATEGORY_TESTS.get(category, [])
        for tmpl in cat_tests:
            plan.test_cases.append(TestCase(**tmpl, source="mandatory"))

    # ── Step 3: Generate SVA Properties ───────────────────────────────

    def _generate_sva_properties(
        self,
        plan: VerificationPlan,
        spec: Any,
    ) -> None:
        """Generate SystemVerilog Assertions for all P0 tests."""
        p0_tests = [t for t in plan.test_cases if t.priority == Priority.P0]

        for tc in p0_tests:
            sva = self._test_to_sva(tc, spec)
            if sva:
                tc.sva_property = sva
                plan.sva_properties.append(sva)

    def _test_to_sva(self, tc: TestCase, spec: Any) -> Optional[SVAProperty]:
        """Convert a P0 test case into an SVA property."""
        # Derive a clean property name
        prop_name = re.sub(r"[^a-zA-Z0-9_]", "_", tc.test_id).lower()

        if tc.category == TestCategory.RESET:
            return self._sva_reset(tc, prop_name, spec)
        elif tc.source == "behavioral_contract":
            return self._sva_from_contract(tc, prop_name, spec)
        elif tc.category == TestCategory.FUNCTIONAL:
            return self._sva_functional(tc, prop_name, spec)
        elif tc.category == TestCategory.EDGE_CASE:
            return self._sva_edge_case(tc, prop_name, spec)
        elif tc.category == TestCategory.PROTOCOL:
            return self._sva_protocol(tc, prop_name, spec)
        elif tc.category == TestCategory.TIMING:
            return self._sva_timing(tc, prop_name, spec)
        else:
            return self._sva_generic(tc, prop_name)

    def _get_output_ports(self, spec: Any) -> List[str]:
        """Extract output port names from spec."""
        ports = getattr(spec, "ports", []) or []
        return [
            getattr(p, "name", str(p))
            for p in ports
            if getattr(p, "direction", "").lower() == "output"
        ]

    def _get_clk_rst(self, spec: Any) -> tuple:
        """Identify clock and reset signal names from spec ports."""
        ports = getattr(spec, "ports", []) or []
        clk_name = "clk"
        rst_name = "rst_n"
        for p in ports:
            name = getattr(p, "name", "").lower()
            if name in ("clk", "clock", "i_clk", "sys_clk"):
                clk_name = getattr(p, "name", "clk")
            if name in ("rst_n", "reset_n", "rstn", "i_rst_n", "rst", "reset"):
                rst_name = getattr(p, "name", "rst_n")
        return clk_name, rst_name

    def _sva_reset(self, tc: TestCase, prop_name: str, spec: Any) -> SVAProperty:
        """Generate reset assertion: all outputs go to 0 after reset."""
        clk, rst = self._get_clk_rst(spec)
        outputs = self._get_output_ports(spec)

        if outputs:
            checks = " && ".join(f"({o} == '0)" for o in outputs[:8])  # Cap at 8
            sva_code = (
                f"property {prop_name};\n"
                f"    @(posedge {clk}) !{rst} |-> ##2 ({checks});\n"
                f"endproperty\n"
                f"assert property ({prop_name}) else $error(\"{tc.test_id}: reset check failed\");"
            )
        else:
            sva_code = (
                f"// {prop_name}: No output ports found — manual SVA required\n"
                f"// property {prop_name};\n"
                f"//     @(posedge clk) !rst_n |-> ##2 (outputs == '0);\n"
                f"// endproperty"
            )

        return SVAProperty(
            property_name=prop_name,
            description=tc.description,
            sva_code=sva_code,
            related_test_id=tc.test_id,
        )

    def _sva_from_contract(self, tc: TestCase, prop_name: str, spec: Any) -> SVAProperty:
        """Generate SVA from a behavioral contract statement."""
        clk, rst = self._get_clk_rst(spec)

        # Parse WITHIN for cycle count
        cycles = self._parse_within_cycles(tc.description)

        # Build antecedent from stimulus, consequent from expected
        antecedent = self._to_sva_expr(tc.stimulus)
        consequent = self._to_sva_expr(tc.expected)

        if cycles and cycles > 0:
            delay = f"##[1:{cycles}]" if cycles > 1 else "##1"
            sva_code = (
                f"property {prop_name};\n"
                f"    @(posedge {clk}) disable iff (!{rst})\n"
                f"        ({antecedent}) |-> {delay} ({consequent});\n"
                f"endproperty\n"
                f"assert property ({prop_name}) else $error(\"{tc.test_id}: behavioral contract violated\");"
            )
        else:
            sva_code = (
                f"property {prop_name};\n"
                f"    @(posedge {clk}) disable iff (!{rst})\n"
                f"        ({antecedent}) |=> ({consequent});\n"
                f"endproperty\n"
                f"assert property ({prop_name}) else $error(\"{tc.test_id}: behavioral contract violated\");"
            )

        return SVAProperty(
            property_name=prop_name,
            description=tc.description,
            sva_code=sva_code,
            related_test_id=tc.test_id,
        )

    def _sva_functional(self, tc: TestCase, prop_name: str, spec: Any) -> SVAProperty:
        """Generic functional SVA with overlapping implication."""
        clk, rst = self._get_clk_rst(spec)
        sva_code = (
            f"property {prop_name};\n"
            f"    @(posedge {clk}) disable iff (!{rst})\n"
            f"        /* Functional: {self._shorten(tc.title, 60)} */\n"
            f"        1 |-> ##1 1; // TODO: replace with design-specific check\n"
            f"endproperty\n"
            f"assert property ({prop_name}) else $error(\"{tc.test_id}: {tc.title}\");"
        )
        return SVAProperty(
            property_name=prop_name,
            description=tc.description,
            sva_code=sva_code,
            related_test_id=tc.test_id,
        )

    def _sva_edge_case(self, tc: TestCase, prop_name: str, spec: Any) -> SVAProperty:
        """Edge case SVA — check stability after edge condition."""
        clk, rst = self._get_clk_rst(spec)
        sva_code = (
            f"property {prop_name};\n"
            f"    @(posedge {clk}) disable iff (!{rst})\n"
            f"        /* Edge case: {self._shorten(tc.title, 60)} */\n"
            f"        1 |-> ##1 $stable(1'b1); // TODO: replace with edge-case signal\n"
            f"endproperty\n"
            f"assert property ({prop_name}) else $error(\"{tc.test_id}: {tc.title}\");"
        )
        return SVAProperty(
            property_name=prop_name,
            description=tc.description,
            sva_code=sva_code,
            related_test_id=tc.test_id,
        )

    def _sva_protocol(self, tc: TestCase, prop_name: str, spec: Any) -> SVAProperty:
        """Protocol SVA using $rose/$fell for handshake signals."""
        clk, rst = self._get_clk_rst(spec)
        sva_code = (
            f"property {prop_name};\n"
            f"    @(posedge {clk}) disable iff (!{rst})\n"
            f"        /* Protocol: {self._shorten(tc.title, 60)} */\n"
            f"        $rose(valid) |-> ##[1:4] $rose(ready);\n"
            f"endproperty\n"
            f"assert property ({prop_name}) else $error(\"{tc.test_id}: {tc.title}\");"
        )
        return SVAProperty(
            property_name=prop_name,
            description=tc.description,
            sva_code=sva_code,
            related_test_id=tc.test_id,
        )

    def _sva_timing(self, tc: TestCase, prop_name: str, spec: Any) -> SVAProperty:
        """Timing SVA — latency bound check."""
        clk, rst = self._get_clk_rst(spec)
        cycles = self._parse_within_cycles(tc.description) or 4
        sva_code = (
            f"property {prop_name};\n"
            f"    @(posedge {clk}) disable iff (!{rst})\n"
            f"        /* Timing: {self._shorten(tc.title, 60)} */\n"
            f"        $rose(start) |-> ##[1:{cycles}] $rose(done);\n"
            f"endproperty\n"
            f"assert property ({prop_name}) else $error(\"{tc.test_id}: timing violated\");"
        )
        return SVAProperty(
            property_name=prop_name,
            description=tc.description,
            sva_code=sva_code,
            related_test_id=tc.test_id,
        )

    def _sva_generic(self, tc: TestCase, prop_name: str) -> SVAProperty:
        """Fallback generic SVA placeholder."""
        sva_code = (
            f"// {prop_name}: {tc.title}\n"
            f"// TODO: Implement SVA for test {tc.test_id}\n"
            f"// Category: {tc.category}, Priority: {tc.priority}"
        )
        return SVAProperty(
            property_name=prop_name,
            description=tc.description,
            sva_code=sva_code,
            related_test_id=tc.test_id,
        )

    @staticmethod
    def _parse_within_cycles(text: str) -> Optional[int]:
        """Extract cycle count from 'WITHIN N cycle(s)' patterns."""
        m = re.search(r"(\d+)\s*cycle", text, re.IGNORECASE)
        if m:
            return int(m.group(1))
        return None

    @staticmethod
    def _to_sva_expr(text: str) -> str:
        """Convert stimulus/expected text to an SVA-like expression.
        Returns a sanitized placeholder for manual refinement.
        """
        if not text:
            return "1'b1"
        # Strip common prefixes
        cleaned = re.sub(r"^(Set up:|Drive:|Check:)\s*", "", text).strip()
        # Sanitize for SVA: keep alphanumeric, underscores, spaces, ops
        cleaned = re.sub(r"[^\w\s=!<>&|().,\[\]:]", "", cleaned)
        if not cleaned:
            return "1'b1"
        # Truncate for readability
        if len(cleaned) > 80:
            cleaned = cleaned[:77] + "..."
        return cleaned

    # ── Step 4: Generate Coverage Plan ────────────────────────────────

    def _generate_coverage_plan(
        self,
        plan: VerificationPlan,
        spec: Any,
        cdc_result: Any = None,
    ) -> None:
        """Generate coverage points: port bins, FSM, FIFO boundaries."""
        self._port_coverage(plan, spec)
        self._fsm_coverage(plan, spec)
        self._fifo_coverage(plan, spec, cdc_result)

    def _port_coverage(self, plan: VerificationPlan, spec: Any) -> None:
        """Generate port-width-based bin coverage for input ports."""
        ports = getattr(spec, "ports", []) or []

        for p in ports:
            direction = getattr(p, "direction", "").lower()
            if direction != "input":
                continue
            name = getattr(p, "name", "")
            dtype = getattr(p, "data_type", "logic")

            # Skip clock/reset
            if name.lower() in ("clk", "clock", "rst_n", "reset_n", "rstn", "rst", "i_clk", "i_rst_n"):
                continue

            width = self._extract_width(dtype)
            bins = self._generate_bins(width, name)

            plan.coverage_points.append(CoveragePoint(
                name=f"cov_{name}",
                cover_type="port_bins",
                target_signal=name,
                bins=bins,
                description=f"Input port '{name}' coverage ({width}-bit)",
            ))

    def _fsm_coverage(self, plan: VerificationPlan, spec: Any) -> None:
        """Generate FSM state and transition coverage from spec hints."""
        contracts = getattr(spec, "behavioral_contract", []) or []

        # Look for state-like keywords in behavioral contracts
        state_names = set()
        for stmt in contracts:
            text = str(stmt).lower()
            for keyword in ("idle", "active", "ready", "busy", "wait", "done",
                            "fetch", "decode", "execute", "writeback",
                            "read", "write", "init", "error"):
                if keyword in text:
                    state_names.add(keyword.upper())

        if not state_names:
            # Check design category for default FSM states
            category = plan.design_category
            if category == "PROCESSOR":
                state_names = {"FETCH", "DECODE", "EXECUTE", "WRITEBACK", "IDLE"}
            elif category == "INTERFACE":
                state_names = {"IDLE", "ACTIVE", "WAIT", "DONE"}
            elif category == "MEMORY":
                state_names = {"IDLE", "READ", "WRITE", "DONE"}
            else:
                return  # No FSM detected

        states_list = sorted(state_names)

        # State coverage
        plan.coverage_points.append(CoveragePoint(
            name="cov_fsm_state",
            cover_type="fsm_state",
            target_signal="state",
            bins=states_list,
            description=f"FSM state coverage — {len(states_list)} states",
        ))

        # Transition coverage (all pairs from detected states)
        transitions = []
        for s1 in states_list:
            for s2 in states_list:
                if s1 != s2:
                    transitions.append(f"{s1} => {s2}")

        if transitions:
            plan.coverage_points.append(CoveragePoint(
                name="cov_fsm_transition",
                cover_type="fsm_transition",
                target_signal="state",
                bins=transitions[:32],  # Cap at 32 transitions
                description=f"FSM transition coverage — {min(len(transitions), 32)} transitions",
            ))

    def _fifo_coverage(
        self,
        plan: VerificationPlan,
        spec: Any,
        cdc_result: Any = None,
    ) -> None:
        """Generate FIFO boundary condition coverage."""
        has_fifo = False

        # Check submodules for FIFO references
        submodules = getattr(spec, "submodules", []) or []
        for sm in submodules:
            sm_name = getattr(sm, "name", "").lower()
            sm_desc = getattr(sm, "description", "").lower()
            if "fifo" in sm_name or "fifo" in sm_desc:
                has_fifo = True
                break

        # Check CDC result for async FIFO submodules
        if not has_fifo and cdc_result:
            cdc_subs = getattr(cdc_result, "cdc_submodules_added", []) or []
            for sub in cdc_subs:
                if isinstance(sub, dict):
                    if "fifo" in sub.get("name", "").lower():
                        has_fifo = True
                        break
                else:
                    if "fifo" in getattr(sub, "name", "").lower():
                        has_fifo = True
                        break

        if not has_fifo:
            return

        plan.coverage_points.append(CoveragePoint(
            name="cov_fifo_empty",
            cover_type="fifo_boundary",
            target_signal="fifo_empty",
            bins=["empty_asserted", "empty_deasserted"],
            description="FIFO empty flag boundary condition",
        ))
        plan.coverage_points.append(CoveragePoint(
            name="cov_fifo_full",
            cover_type="fifo_boundary",
            target_signal="fifo_full",
            bins=["full_asserted", "full_deasserted"],
            description="FIFO full flag boundary condition",
        ))
        plan.coverage_points.append(CoveragePoint(
            name="cov_fifo_level",
            cover_type="fifo_boundary",
            target_signal="fifo_count",
            bins=["level_0", "level_1", "level_half", "level_max_minus_1", "level_max"],
            description="FIFO fill-level boundary conditions",
        ))

    @staticmethod
    def _extract_width(data_type: str) -> int:
        """Extract bit width from data type string like 'logic [7:0]'."""
        m = re.search(r"\[(\d+):(\d+)\]", data_type)
        if m:
            return abs(int(m.group(1)) - int(m.group(2))) + 1
        return 1

    @staticmethod
    def _generate_bins(width: int, signal_name: str) -> List[str]:
        """Generate appropriate bins for a given signal width."""
        if width == 1:
            return ["0", "1"]
        max_val = (1 << width) - 1
        if width <= 4:
            return [str(v) for v in range(max_val + 1)]
        # For wider signals, use boundary + range bins
        bins = [
            f"{signal_name}_zero = 0",
            f"{signal_name}_one = 1",
            f"{signal_name}_mid = [{max_val // 4}:{3 * max_val // 4}]",
            f"{signal_name}_max_m1 = {max_val - 1}",
            f"{signal_name}_max = {max_val}",
        ]
        return bins

    # ── Step 5: Finalize ──────────────────────────────────────────────

    def _finalize(self, plan: VerificationPlan) -> None:
        """Compute summary counts and validate the plan."""
        plan.total_tests = len(plan.test_cases)
        plan.p0_count = sum(1 for t in plan.test_cases if t.priority == Priority.P0)
        plan.p1_count = sum(1 for t in plan.test_cases if t.priority == Priority.P1)
        plan.p2_count = sum(1 for t in plan.test_cases if t.priority == Priority.P2)

        # Validate: every P0 test must have an SVA property
        p0_no_sva = [
            t.test_id for t in plan.test_cases
            if t.priority == Priority.P0 and t.sva_property is None
        ]
        if p0_no_sva:
            plan.warnings.append(
                f"P0 tests without SVA: {', '.join(p0_no_sva)}"
            )

        if plan.total_tests == 0:
            plan.warnings.append("Empty verification plan — no test cases generated")
