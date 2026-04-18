"""
Incremental Fix Engine
====================

Intelligent error fixing that analyzes impact before making changes.
Features:
- Error classification and root cause analysis
- Impact analysis (what could break)
- Reasoning-first approach (think before fixing)
- Surgical changes that preserve functionality

The key philosophy:
1. UNDERSTAND the error completely
2. ANALYZE what could break if fixed
3. PROPOSE safest fix that preserves existing behavior
4. VALIDATE the fix doesn't break other things

Usage:
    fixer = IncrementalFixEngine()

    # Analyze the error
    analysis = fixer.analyze_error(error, rtl)

    # Build a reasoning prompt
    prompt = fixer.build_fix_prompt(error, analysis, rtl)

    # Get reasoned fix from LLM
    fix = call_llm(prompt)

    # Validate before applying
    if fixer.validate_fix(original_rtl, fix, analysis):
        apply_fix(fix)
"""

import re
import logging
from typing import Dict, List, Optional, Tuple, Any, Set
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict

logger = logging.getLogger(__name__)


class ErrorType(Enum):
    """Classification of RTL errors."""

    SYNTAX = "syntax"
    WIDTH_MISMATCH = "width_mismatch"
    UNDECLARED_SIGNAL = "undeclared_signal"
    TYPE_MISMATCH = "type_mismatch"
    TIMING_VIOLATION = "timing_violation"
    DRIVER_CONFLICT = "driver_conflict"
    INFERRED_LATCH = "inferred_latch"
    UNREACHABLE_LOGIC = "unreachable_logic"
    COMBINATIONAL_LOOP = "combinational_loop"
    PORT_CONNECTION = "port_connection"
    PARAMETER_MISMATCH = "parameter_mismatch"
    SYNTHESIS_ERROR = "synthesis"
    TIMING_ERROR = "timing"
    POWER_ERROR = "power"
    DRC_ERROR = "drc"
    LVS_ERROR = "lvs"
    FLOORPLAN_ERROR = "floorplan"
    UNKNOWN_TOOL_ERROR = "unknown_tool"
    UNKNOWN = "unknown"


@dataclass
class ErrorAnalysis:
    """Complete analysis of an error."""

    error_type: ErrorType
    raw_error: str
    error_location: str
    line_number: Optional[int] = None
    file_name: Optional[str] = None

    # What the error involves
    signals_mentioned: List[str] = field(default_factory=list)
    modules_mentioned: List[str] = field(default_factory=list)

    # Impact analysis
    modules_affected: List[str] = field(default_factory=list)
    signals_affected: List[str] = field(default_factory=list)

    # Risk assessment
    side_effect_risks: List[str] = field(default_factory=list)
    fix_confidence: float = 0.5
    is_surgical: bool = False

    # Error context
    error_line: str = ""
    surrounding_lines: str = ""
    relevant_module: str = ""

    # Recommended approach
    recommended_strategy: str = "standard"
    avoid_changes: List[str] = field(default_factory=list)


@dataclass
class FixResult:
    """Result of attempting to fix an error."""

    success: bool
    fixed_code: str
    original_code: str
    changes_made: List[str] = field(default_factory=list)
    validation_passed: bool = False
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None


class IncrementalFixEngine:
    """
    Intelligent incremental RTL fixer.

    Analyzes errors deeply and reasons about fixes before applying them.
    Key principle: "Think twice, fix once."
    """

    # Patterns for error classification
    ERROR_PATTERNS = {
        ErrorType.WIDTH_MISMATCH: [
            r"width.*mismatch",
            r"operator.*width",
            r"result.*width",
            r"bits.*selected",
            r"signal.*width",
            r"cannot match operand",
        ],
        ErrorType.UNDECLARED_SIGNAL: [
            r"not found in declaration",
            r"undeclared",
            r"identifier.*undefined",
            r"signal.*does.*not exist",
            r"missing definition of signal",
            r"signal.*lacks.*driver",
        ],
        ErrorType.SYNTAX: [
            r"syntax error",
            r"unexpected token",
            r"invalid token",
            r"parse error",
            r"expected.*but found",
        ],
        ErrorType.TYPE_MISMATCH: [
            r"type.*mismatch",
            r"incompatible types",
            r"cannot assign",
            r"sign mismatch",
        ],
        ErrorType.DRIVER_CONFLICT: [
            r"multiple.*driver",
            r"driver.*conflict",
            r"multiple.*assignment",
        ],
        ErrorType.INFERRED_LATCH: [
            r"inferred latch",
            r"latch.*found",
            r"combinational.*process",
        ],
        ErrorType.UNREACHABLE_LOGIC: [
            r"unreachable",
            r"dead code",
            r"never.*used",
        ],
        ErrorType.PORT_CONNECTION: [
            r"port.*connection",
            r"wrong.*number.*port",
            r"port.*not.*found",
            r"port.*direction.*mismatch",
            r"argument.*count.*mismatch",
            r"instantiation",
        ],
        ErrorType.TIMING_VIOLATION: [
            r"timing.*violation",
            r"slack.*negative",
            r"setup.*hold",
        ],
        ErrorType.SYNTHESIS_ERROR: [
            r"error.*module.*not.*found",
            r"error.*syntax.*in.*module",
            r"unknown.*cell.*type",
            r"unsupported.*syntax.*in.*context",
            r"yosys.*error",
            r"synthesis.*failed",
            r"failed.*elaborat",
        ],
        ErrorType.TIMING_ERROR: [
            r"setup.*violation",
            r"hold.*violation",
            r"negative slack",
            r"slack.*negative",
            r"timing.*error",
            r"critical.*path.*violated",
            r"wns.*-\d",
            r"tns.*-\d",
        ],
        ErrorType.POWER_ERROR: [
            r"power.*analysis.*failed",
            r"ir.*drop",
            r"electromigration",
            r"em.*warning",
            r"missing.*spef",
            r"missing.*vcd",
            r"activity.*file.*not.*found",
        ],
        ErrorType.DRC_ERROR: [
            r"drc.*violation",
            r"design.*rule.*check",
            r"min.*spacing",
            r"min.*width",
            r"short.*detected",
            r"overlap.*detected",
        ],
        ErrorType.LVS_ERROR: [
            r"lvs.*mismatch",
            r"net.*mismatch",
            r"pin.*mismatch",
            r"connectivity.*error",
            r"not.*equivalent",
        ],
        ErrorType.FLOORPLAN_ERROR: [
            r"floorplan.*failed",
            r"placement.*failed",
            r"route.*failed",
            r"congestion",
            r"die.*area",
            r"utilization",
        ],
        ErrorType.UNKNOWN_TOOL_ERROR: [
            r"binary.*not.*found",
            r"command.*not.*found",
            r"timed out",
            r"return code",
            r"segmentation fault",
            r"internal error",
        ],
    }

    # High confidence fix patterns
    SURGICAL_PATTERNS = {
        ErrorType.SYNTAX: True,
        ErrorType.UNDECLARED_SIGNAL: False,  # Need to understand usage
        ErrorType.WIDTH_MISMATCH: True,  # Usually straightforward
        ErrorType.PORT_CONNECTION: True,
        ErrorType.SYNTHESIS_ERROR: False,
        ErrorType.TIMING_ERROR: False,
        ErrorType.DRC_ERROR: False,
        ErrorType.LVS_ERROR: False,
    }

    def __init__(self, enable_deep_analysis: bool = True):
        self.enable_deep_analysis = enable_deep_analysis
        self.analysis_cache: Dict[str, ErrorAnalysis] = {}

    def analyze_error(self, error: str = "", rtl: str = "", **kwargs: Any) -> ErrorAnalysis:
        """
        Perform deep analysis of an error.

        Args:
            error: The error message
            rtl: The RTL code (optional but helps analysis)

        Returns:
            ErrorAnalysis with detailed findings
        """
        error = error or kwargs.get("error_text", "") or kwargs.get("error_log", "")
        rtl = rtl or kwargs.get("rtl_code", "") or kwargs.get("code", "")

        analysis = ErrorAnalysis(
            error_type=self._classify_error(error),
            raw_error=error,
            error_location=self._extract_location(error),
            error_line=self._extract_error_line(error),
        )

        # Extract line number
        line_match = re.search(r":(\d+):", error)
        if line_match:
            analysis.line_number = int(line_match.group(1))

        # Extract mentioned signals
        analysis.signals_mentioned = self._extract_signals(error)

        # Extract mentioned modules
        analysis.modules_mentioned = self._extract_modules(error)

        if rtl:
            # Find the error line in RTL
            analysis.surrounding_lines = self._get_surrounding_lines(rtl, analysis.line_number)

            # Find the module containing the error
            analysis.relevant_module = self._find_module_for_line(rtl, analysis.line_number)

            # Analyze impact
            if self.enable_deep_analysis:
                self._analyze_impact(analysis, rtl)

        # Assess confidence
        analysis.fix_confidence = self._assess_fix_confidence(analysis)
        analysis.is_surgical = self._is_surgical_fix(analysis)

        return analysis

    def _classify_error(self, error: str) -> ErrorType:
        """Classify error type from message."""
        error_lower = error.lower()

        for error_type, patterns in self.ERROR_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, error_lower):
                    return error_type

        return ErrorType.UNKNOWN

    def _extract_location(self, error: str) -> str:
        """Extract file:line:col from error."""
        match = re.search(r"([\w./]+\.v?):(\d+):(\d+)?", error)
        if match:
            return f"{match.group(1)}:{match.group(2)}"
        match = re.search(r"([\w./]+\.v?):(\d+)", error)
        if match:
            return f"{match.group(1)}:{match.group(2)}"
        return "unknown location"

    def _extract_error_line(self, error: str) -> str:
        """Extract the problematic line from error."""
        match = re.search(r"Error: (.+)", error)
        if match:
            return match.group(1)

        lines = error.split("\n")
        for line in lines:
            if any(kw in line.lower() for kw in ["error", "warning", "line"]):
                return line.strip()

        return error[:200]

    def _extract_signals(self, text: str) -> List[str]:
        """Extract signal names from text."""
        # Common Verilog signal patterns
        patterns = [
            r"\b(\w+)\s*(?:\[|\s*=|\s*<=|\s*\|)",  # signal[ or signal =
            r"\b(\w+)\s*\[.*?\]\s*(?:=|<=)",  # signal[7:0] =
            r"in (\w+)",  # in signal_name
        ]

        signals = set()
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                sig = match.group(1)
                # Filter out keywords and common words
                if not any(
                    kw in sig.lower()
                    for kw in [
                        "if",
                        "else",
                        "case",
                        "for",
                        "begin",
                        "end",
                        "module",
                        "input",
                        "output",
                        "wire",
                        "reg",
                    ]
                ):
                    signals.add(sig)

        return list(signals)

    def _extract_modules(self, text: str) -> List[str]:
        """Extract module names from text."""
        modules = set()

        # Pattern for "module_name" or 'module_name'
        for match in re.finditer(r"['\"](\w+)['\"]", text):
            modules.add(match.group(1))

        # Pattern for "in module X"
        for match in re.finditer(r"in (?:module\s+)?(\w+)", text, re.IGNORECASE):
            modules.add(match.group(1))

        return list(modules)

    def _get_surrounding_lines(self, rtl: str, line_num: Optional[int], context: int = 20) -> str:
        """Get lines around the error for context."""
        if not line_num:
            return rtl[:2000]

        lines = rtl.split("\n")

        if line_num > len(lines):
            return rtl[:2000]

        start = max(0, line_num - context - 1)
        end = min(len(lines), line_num + context)

        return "\n".join(f"{i + 1}: {line}" for i, line in enumerate(lines[start:end], start))

    def _find_module_for_line(self, rtl: str, line_num: Optional[int]) -> str:
        """Find which module contains the error line."""
        if not line_num:
            return "unknown"

        lines = rtl.split("\n")
        current_module = None
        current_line = 0

        for i, line in enumerate(lines):
            if re.match(r"^\s*module\s+(\w+)", line):
                current_module = re.match(r"^\s*module\s+(\w+)", line).group(1)
                current_line = i

            if i == line_num - 1:
                return current_module or "unknown"

        return current_module or "unknown"

    def _analyze_impact(self, analysis: ErrorAnalysis, rtl: str) -> None:
        """Deep analysis of what could break if fixed."""

        modules = self._parse_modules(rtl)

        # Find affected modules
        affected_modules = set()
        affected_signals = set()
        risks = []

        for sig in analysis.signals_mentioned:
            for mod_name, (_, _, lines) in modules.items():
                mod_code = " ".join(lines)
                if sig in mod_code:
                    affected_modules.add(mod_name)

                    # Check if signal is declared in this module
                    for line in lines:
                        if re.search(
                            rf"(?:input|output|wire|reg|logic)\s+.*{re.escape(sig)}", line
                        ):
                            affected_signals.add(sig)
                            break

        analysis.modules_affected = list(affected_modules)
        analysis.signals_affected = list(affected_signals)

        # Analyze specific risks based on error type
        if analysis.error_type == ErrorType.WIDTH_MISMATCH:
            risks.extend(
                [
                    "Arithmetic operations may produce different results",
                    "Comparison operations may change truth table",
                    "Bit selection indices may need adjustment",
                    "Zero/one extension behavior will change",
                ]
            )
            analysis.fix_confidence = 0.85

        elif analysis.error_type == ErrorType.UNDECLARED_SIGNAL:
            risks.extend(
                [
                    "Signal may need declaration in parent module",
                    "Signal may be a typo of another signal",
                    "Signal may need to be port of parent module",
                    "Hierarchy path may be incorrect",
                ]
            )
            analysis.fix_confidence = 0.70

        elif analysis.error_type == ErrorType.DRIVER_CONFLICT:
            risks.extend(
                [
                    "Multiple processes driving same signal",
                    "May need tri-state buffer logic",
                    "May need mux for different drivers",
                ]
            )
            analysis.fix_confidence = 0.60

        elif analysis.error_type == ErrorType.INFERRED_LATCH:
            risks.extend(
                [
                    "Logic may change behavior with enable",
                    "State retention behavior will change",
                    "May affect timing paths",
                ]
            )
            analysis.fix_confidence = 0.75

        elif analysis.error_type == ErrorType.SYNTHESIS_ERROR:
            risks.extend(
                [
                    "Synthesis-only errors may indicate unsupported RTL constructs",
                    "Fix may require preserving simulation behavior while simplifying RTL",
                    "Hierarchy or module binding changes can affect downstream netlists",
                ]
            )
            analysis.fix_confidence = 0.68

        elif analysis.error_type in (ErrorType.TIMING_ERROR, ErrorType.TIMING_VIOLATION):
            risks.extend(
                [
                    "Timing fixes can change latency or throughput",
                    "Pipelining may require matching testbench and formal expectations",
                    "Constraint relaxation may hide a real architecture issue",
                ]
            )
            analysis.fix_confidence = 0.58

        elif analysis.error_type == ErrorType.POWER_ERROR:
            risks.extend(
                [
                    "Power fixes may trade off timing or area",
                    "Clock gating must preserve reset and enable behavior",
                    "Activity assumptions may be incomplete",
                ]
            )
            analysis.fix_confidence = 0.52

        elif analysis.error_type in (ErrorType.DRC_ERROR, ErrorType.LVS_ERROR):
            risks.extend(
                [
                    "Physical signoff errors may require floorplan or PDK rule changes",
                    "Layout fixes can affect timing, power, or extracted netlists",
                    "RTL regeneration is only appropriate for logic-connectivity causes",
                ]
            )
            analysis.fix_confidence = 0.45

        elif analysis.error_type == ErrorType.FLOORPLAN_ERROR:
            risks.extend(
                [
                    "Floorplan changes can affect congestion, timing, and power",
                    "Area/utilization changes may mask RTL complexity problems",
                ]
            )
            analysis.fix_confidence = 0.50

        elif analysis.error_type == ErrorType.UNKNOWN_TOOL_ERROR:
            risks.extend(
                [
                    "Tool or environment errors are usually not fixable by RTL edits",
                    "Retrying without changing setup can repeat the same failure",
                ]
            )
            analysis.fix_confidence = 0.25

        analysis.side_effect_risks = risks

        # Determine recommended strategy
        if analysis.fix_confidence > 0.8:
            analysis.recommended_strategy = "surgical"
        elif analysis.fix_confidence > 0.6:
            analysis.recommended_strategy = "reasoned"
        else:
            analysis.recommended_strategy = "conservative"

    def _assess_fix_confidence(self, analysis: ErrorAnalysis) -> float:
        """Assess confidence in being able to fix this error."""
        confidence = 0.5  # Base

        # Error type contributes
        if analysis.error_type == ErrorType.SYNTAX:
            confidence += 0.3
        elif analysis.error_type == ErrorType.WIDTH_MISMATCH:
            confidence += 0.25
        elif analysis.error_type == ErrorType.UNDECLARED_SIGNAL:
            confidence += 0.15
        elif analysis.error_type in (ErrorType.SYNTHESIS_ERROR, ErrorType.PORT_CONNECTION):
            confidence += 0.18
        elif analysis.error_type in (ErrorType.TIMING_ERROR, ErrorType.TIMING_VIOLATION):
            confidence += 0.08
        elif analysis.error_type in (ErrorType.POWER_ERROR, ErrorType.FLOORPLAN_ERROR):
            confidence += 0.02
        elif analysis.error_type in (ErrorType.DRC_ERROR, ErrorType.LVS_ERROR):
            confidence -= 0.05
        elif analysis.error_type == ErrorType.UNKNOWN_TOOL_ERROR:
            confidence -= 0.25

        # Have context
        if analysis.surrounding_lines:
            confidence += 0.1

        # Can identify affected modules
        if len(analysis.modules_affected) <= 2:
            confidence += 0.1

        return min(confidence, 0.95)

    def _is_surgical_fix(self, analysis: ErrorAnalysis) -> bool:
        """Determine if this is a surgical (safe) fix."""
        if analysis.error_type in self.SURGICAL_PATTERNS:
            return self.SURGICAL_PATTERNS[analysis.error_type]

        return len(analysis.modules_affected) <= 1 and len(analysis.signals_affected) <= 2

    def _parse_modules(self, rtl: str) -> Dict[str, Tuple[int, int, List[str]]]:
        """Parse RTL to find module boundaries."""
        lines = rtl.split("\n")
        modules = {}

        current_module = None
        module_start = 0
        module_lines = []

        for i, line in enumerate(lines):
            stripped = line.strip()

            module_match = re.match(r"^\s*module\s+(\w+)", stripped)
            if module_match:
                if current_module:
                    modules[current_module] = (module_start, i, module_lines)
                current_module = module_match.group(1)
                module_start = i
                module_lines = [line]
            elif current_module:
                module_lines.append(line)
                if stripped == "endmodule" or re.match(r"^\s*endmodule\b", stripped):
                    modules[current_module] = (module_start, i + 1, module_lines)
                    current_module = None

        if current_module and module_lines:
            modules[current_module] = (module_start, len(lines), module_lines)

        return modules

    def build_fix_prompt(
        self,
        analysis: ErrorAnalysis,
        original_rtl: str,
        spec: str = "",
    ) -> str:
        """
        Build a prompt that emphasizes reasoning before fixing.

        The key instruction: "Think about consequences before making changes."
        """

        template = """You are fixing a Verilog/SystemVerilog error.

## ERROR TO FIX
```
{error}
```
Location: {location}
Error Type: {error_type}
Confidence: {confidence}%

## ANALYSIS
### Signals mentioned in error:
{signals}

### Modules using these signals:
{modules}

### Potential side effects if fixed incorrectly:
{risks}

### Relevant code (error location highlighted):
```verilog
{surrounding}
```

{spec_section}

## INSTRUCTIONS

1. **FIRST, REASON about the error:**
   - What is the root cause?
   - What modules use the affected signals?
   - What could break if I fix it this way vs that way?

2. **THEN, propose a fix that:**
   - Fixes the immediate error
   - Does NOT break the {affected_count} modules that use these signals
   - Preserves existing functionality
   - Is the MINIMAL change necessary

3. **OUTPUT FORMAT:**
   Think step-by-step in your reasoning, then output ONLY the fixed code.

## REASONING (think before fixing):
1. Root cause: ...

2. Impact analysis: ...

3. Options considered: ...

4. Chosen approach and why: ...

## FIXED CODE:
```verilog
[your fixed code here]
```
"""

        spec_section = ""
        if spec:
            spec_section = f"\n## SPECIFICATION CONTEXT\n```\n{spec[:2000]}\n```"

        return template.format(
            error=analysis.raw_error,
            location=analysis.error_location,
            error_type=analysis.error_type.value,
            confidence=int(analysis.fix_confidence * 100),
            signals=", ".join(analysis.signals_mentioned[:10]) or "None identified",
            modules=", ".join(analysis.modules_affected[:5]) or "None identified",
            risks="\n".join(f"- {r}" for r in analysis.side_effect_risks[:5])
            or "- None identified",
            surrounding=analysis.surrounding_lines[:1500],
            affected_count=len(analysis.modules_affected),
            spec_section=spec_section,
        )

    def validate_fix(
        self,
        original: str,
        fixed: str,
        analysis: ErrorAnalysis,
    ) -> Tuple[bool, List[str]]:
        """
        Validate that a fix is safe to apply.

        Returns:
            Tuple of (is_valid, list_of_warnings)
        """
        warnings = []

        # Check 1: Significant changes indicator
        if not materially_changed(original, fixed):
            warnings.append("Code barely changed - fix may not address error")

        # Check 2: Signal preservation
        original_signals = set(self._extract_signals(original))
        fixed_signals = set(self._extract_signals(fixed))

        lost_signals = original_signals - fixed_signals
        if lost_signals and analysis.error_type != ErrorType.UNDECLARED_SIGNAL:
            warnings.append(f"Lost signals: {', '.join(lost_signals)}")

        # Check 3: Module structure preservation
        original_modules = set(self._parse_modules(original).keys())
        fixed_modules = set(self._parse_modules(fixed).keys())

        lost_modules = original_modules - fixed_modules
        if lost_modules:
            warnings.append(f"Lost modules: {', '.join(lost_modules)}")

        # Check 4: Port preservation
        for mod_name in original_modules & fixed_modules:
            orig_ports = self._get_module_ports(original, mod_name)
            fixed_ports = self._get_module_ports(fixed, mod_name)

            if orig_ports != fixed_ports:
                warnings.append(f"Module '{mod_name}' port interface changed")

        # Check 5: Width changes are deliberate
        if analysis.error_type == ErrorType.WIDTH_MISMATCH:
            # Allow width changes for this error type
            pass
        else:
            # Check for unexpected width changes
            orig_widths = self._extract_widths(original)
            fixed_widths = self._extract_widths(fixed)

            changed_widths = set(orig_widths.keys()) & set(fixed_widths.keys())
            for sig in changed_widths:
                if orig_widths[sig] != fixed_widths[sig]:
                    if sig not in analysis.signals_affected:
                        warnings.append(f"Width of '{sig}' changed unexpectedly")

        is_valid = len(warnings) == 0 or all("may" in w.lower() for w in warnings)

        return is_valid, warnings

    def _get_module_ports(self, rtl: str, module_name: str) -> Set[str]:
        """Get port names for a module."""
        modules = self._parse_modules(rtl)

        if module_name not in modules:
            return set()

        _, _, lines = modules[module_name]

        ports = set()
        for line in lines:
            port_match = re.search(
                r"(?:input|output)\s+(?:reg\s+)?(?:logic\s+)?(?:wire\s+)?(?:\[\d+:\d+\]\s+)?(\w+)",
                line,
            )
            if port_match:
                ports.add(port_match.group(1))

        return ports

    def _extract_widths(self, rtl: str) -> Dict[str, Tuple[int, int]]:
        """Extract signal widths from RTL."""
        widths = {}

        for match in re.finditer(
            r"(?:wire|reg|logic)\s+(?:\[\s*(\d+)\s*:\s*(\d+)\]\s+)?(\w+)", rtl
        ):
            msb, lsb, name = match.groups()
            if name:
                if msb and lsb:
                    widths[name] = (int(msb), int(lsb))
                else:
                    widths[name] = (0, 0)

        return widths

    def apply_incremental_fix(
        self,
        original: str,
        fixed: str,
        analysis: ErrorAnalysis,
    ) -> FixResult:
        """
        Apply an incrementally reasoned fix.

        Validates the fix before applying.
        """
        # Validate first
        is_valid, warnings = self.validate_fix(original, fixed, analysis)

        if not is_valid:
            return FixResult(
                success=False,
                fixed_code=original,
                original_code=original,
                validation_passed=False,
                warnings=warnings,
                error="Fix validation failed: " + "; ".join(warnings),
            )

        # Calculate changes
        changes = self._diff_changes(original, fixed)

        return FixResult(
            success=True,
            fixed_code=fixed,
            original_code=original,
            changes_made=changes,
            validation_passed=True,
            warnings=warnings,
        )

    def _diff_changes(self, original: str, fixed: str) -> List[str]:
        """Generate human-readable change description."""
        changes = []

        orig_lines = original.split("\n")
        fixed_lines = fixed.split("\n")

        # Simple line-by-line diff
        for i, (o, f) in enumerate(zip(orig_lines, fixed_lines)):
            if o != f:
                changes.append(f"Line {i + 1}: {o.strip()[:50]} -> {f.strip()[:50]}")

        # Check for added/removed lines
        if len(orig_lines) < len(fixed_lines):
            changes.append(f"Added {len(fixed_lines) - len(orig_lines)} lines")
        elif len(orig_lines) > len(fixed_lines):
            changes.append(f"Removed {len(orig_lines) - len(fixed_lines)} lines")

        return changes[:20]  # Limit to 20 changes


def materially_changed(original: str, fixed: str, threshold: float = 0.3) -> bool:
    """Check if enough changed to be considered a real fix."""
    if not original or not fixed:
        return True

    orig_lines = [l for l in original.split("\n") if l.strip()]
    fixed_lines = [l for l in fixed.split("\n") if l.strip()]

    orig_set = set(orig_lines)
    fixed_set = set(fixed_lines)

    # Jaccard similarity
    intersection = len(orig_set & fixed_set)
    union = len(orig_set | fixed_set)

    similarity = intersection / union if union > 0 else 0

    return similarity < (1 - threshold)
