"""
Coder & Waveform Expert Module — VerilogCoder Logic
=====================================================

Based on: VerilogCoder (AST-based Waveform Tracing)

When an Icarus Verilog simulation fails, this module:
1. Parses the generated RTL into an AST using Pyverilog.
2. Parses the VCD waveform to find the failing signal/time.
3. Back-traces the failing signal's RVALUE in the AST to identify
   exactly which line of code caused the mismatch.
4. Produces a structured diagnosis for the LLM to fix.

Tools used: Pyverilog (AST), Icarus Verilog (simulation), VCD parsing.
"""

import os
import re
import json
import logging
import subprocess
import tempfile
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ─── VCD Waveform Parser (pure Python, no external deps) ─────────────

@dataclass
class VCDSignalChange:
    """A single value change in a VCD trace."""
    time: int
    signal_id: str
    signal_name: str
    value: str


@dataclass 
class VCDSignal:
    """Metadata for a VCD signal."""
    id: str
    name: str
    width: int
    scope: str
    changes: List[VCDSignalChange] = field(default_factory=list)


class VCDParser:
    """
    Lightweight VCD parser — extracts signal transitions from .vcd files.
    No external dependencies required.
    """

    def __init__(self):
        self.signals: Dict[str, VCDSignal] = {}  # id → VCDSignal
        self.name_map: Dict[str, str] = {}       # full_name → id
        self.timescale: str = ""
        self.current_time: int = 0

    def parse(self, vcd_path: str) -> Dict[str, VCDSignal]:
        """Parse a VCD file and return signal map."""
        if not os.path.exists(vcd_path):
            logger.warning(f"VCD file not found: {vcd_path}")
            return {}

        self.signals.clear()
        self.name_map.clear()
        scope_stack: List[str] = []

        try:
            with open(vcd_path, "r", errors="replace") as f:
                in_defs = True
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    if in_defs:
                        if line.startswith("$timescale"):
                            self.timescale = line.replace("$timescale", "").replace("$end", "").strip()
                        elif line.startswith("$scope"):
                            parts = line.split()
                            if len(parts) >= 3:
                                scope_stack.append(parts[2])
                        elif line.startswith("$upscope"):
                            if scope_stack:
                                scope_stack.pop()
                        elif line.startswith("$var"):
                            self._parse_var(line, scope_stack)
                        elif line.startswith("$enddefinitions"):
                            in_defs = False
                    else:
                        # Value change section
                        if line.startswith("#"):
                            try:
                                self.current_time = int(line[1:])
                            except ValueError:
                                pass
                        elif line.startswith("b") or line.startswith("B"):
                            # Multi-bit: bVALUE ID
                            parts = line.split()
                            if len(parts) >= 2:
                                val = parts[0][1:]  # strip 'b'
                                sig_id = parts[1]
                                self._record_change(sig_id, val)
                        elif len(line) >= 2 and line[0] in "01xXzZ":
                            # Single-bit: VALUE_ID (e.g., "1!")
                            val = line[0]
                            sig_id = line[1:]
                            self._record_change(sig_id, val)

        except Exception as e:
            logger.error(f"VCD parse error: {e}")

        return self.signals

    def _parse_var(self, line: str, scope_stack: List[str]):
        """Parse a $var line."""
        # $var wire 8 ! data [7:0] $end
        parts = line.split()
        if len(parts) < 5:
            return
        var_type = parts[1]
        try:
            width = int(parts[2])
        except ValueError:
            width = 1
        sig_id = parts[3]
        name = parts[4]

        full_scope = ".".join(scope_stack)
        full_name = f"{full_scope}.{name}" if full_scope else name

        sig = VCDSignal(id=sig_id, name=name, width=width, scope=full_scope)
        self.signals[sig_id] = sig
        self.name_map[full_name] = sig_id
        self.name_map[name] = sig_id  # Short name lookup

    def _record_change(self, sig_id: str, value: str):
        if sig_id in self.signals:
            self.signals[sig_id].changes.append(
                VCDSignalChange(
                    time=self.current_time,
                    signal_id=sig_id,
                    signal_name=self.signals[sig_id].name,
                    value=value,
                )
            )

    def get_signal_value_at(self, signal_name: str, time: int) -> Optional[str]:
        """Get the value of a signal at a specific time."""
        sig_id = self.name_map.get(signal_name)
        if not sig_id or sig_id not in self.signals:
            return None
        sig = self.signals[sig_id]
        last_val = None
        for ch in sig.changes:
            if ch.time <= time:
                last_val = ch.value
            else:
                break
        return last_val

    def find_first_mismatch(self, signal_name: str, expected_values: List[Tuple[int, str]]
                            ) -> Optional[Tuple[int, str, str]]:
        """Compare signal against expected values; return first mismatch (time, expected, actual)."""
        for time, expected in expected_values:
            actual = self.get_signal_value_at(signal_name, time)
            if actual is None:
                return (time, expected, "UNDEFINED")
            # Normalize for comparison
            if actual.replace("0", "").replace("1", "") == "" and expected.replace("0", "").replace("1", "") == "":
                if int(actual, 2) != int(expected, 2):
                    return (time, expected, actual)
            elif actual != expected:
                return (time, expected, actual)
        return None


# ─── AST Back-Tracer (Pyverilog-based) ───────────────────────────────

@dataclass
class ASTTraceResult:
    """Result of back-tracing a signal through the AST."""
    signal_name: str
    source_file: str
    source_line: int
    assignment_type: str      # "always_ff", "always_comb", "assign", "unknown"
    rvalue_expression: str    # The RHS expression driving this signal
    driving_signals: List[str]  # Signals on the RHS (dependencies)
    context_lines: str        # Surrounding code context
    fsm_state: str = ""       # If signal is state-dependent, which state


class ASTBackTracer:
    """
    Uses Pyverilog to parse RTL and trace signal assignments.
    
    When a simulation mismatch is detected on a signal, this tracer
    finds the exact Verilog line(s) that drive it and extracts the
    RVALUE expression for root-cause analysis.
    """

    def __init__(self):
        self._ast = None
        self._source_lines: Dict[str, List[str]] = {}  # file → lines
        self._assignments: List[Dict[str, Any]] = []

    def parse_rtl(self, rtl_path: str) -> bool:
        """Parse an RTL file and build the assignment database."""
        if not os.path.exists(rtl_path):
            logger.error(f"RTL file not found: {rtl_path}")
            return False

        # Load source for line references
        try:
            with open(rtl_path, "r") as f:
                self._source_lines[rtl_path] = f.readlines()
        except Exception as e:
            logger.error(f"Failed to read RTL: {e}")
            return False

        # Try Pyverilog AST parse
        try:
            from pyverilog.vparser.parser import parse as pyverilog_parse
            ast, _ = pyverilog_parse([rtl_path])
            self._ast = ast
            self._extract_assignments_from_ast(ast, rtl_path)
            logger.info(f"[AST] Parsed {rtl_path}: {len(self._assignments)} assignments found")
            return True
        except ImportError:
            logger.warning("Pyverilog not available — falling back to regex-based tracing")
            self._extract_assignments_regex(rtl_path)
            return True
        except Exception as e:
            logger.warning(f"Pyverilog parse failed ({e}) — falling back to regex")
            self._extract_assignments_regex(rtl_path)
            return True

    def _extract_assignments_from_ast(self, ast, source_file: str):
        """Walk the Pyverilog AST and extract all assignments."""
        try:
            from pyverilog.vparser.ast import (
                Assign, Always, IfStatement, CaseStatement,
                NonblockingSubstitution, BlockingSubstitution,
                Lvalue, Rvalue, Identifier
            )

            def _get_identifiers(node) -> List[str]:
                """Recursively extract all Identifier names from an AST node."""
                ids = []
                if isinstance(node, Identifier):
                    ids.append(node.name)
                if hasattr(node, 'children'):
                    for child in node.children():
                        ids.extend(_get_identifiers(child))
                return ids

            def _node_to_str(node) -> str:
                """Best-effort conversion of AST node to string."""
                if hasattr(node, 'name'):
                    return node.name
                try:
                    return str(node)
                except Exception:
                    return repr(node)

            def _walk(node, context: str = "unknown"):
                if node is None:
                    return

                if isinstance(node, Assign):
                    lv = _node_to_str(node.left) if node.left else "?"
                    rv = _node_to_str(node.right) if node.right else "?"
                    deps = _get_identifiers(node.right) if node.right else []
                    lineno = getattr(node, 'lineno', 0)
                    self._assignments.append({
                        "signal": lv,
                        "rvalue": rv,
                        "type": "assign",
                        "line": lineno,
                        "file": source_file,
                        "deps": deps,
                    })

                elif isinstance(node, (NonblockingSubstitution, BlockingSubstitution)):
                    atype = "always_ff" if isinstance(node, NonblockingSubstitution) else "always_comb"
                    lv = _node_to_str(node.left) if node.left else "?"
                    rv = _node_to_str(node.right) if node.right else "?"
                    deps = _get_identifiers(node.right) if node.right else []
                    lineno = getattr(node, 'lineno', 0)
                    self._assignments.append({
                        "signal": lv,
                        "rvalue": rv,
                        "type": context if context != "unknown" else atype,
                        "line": lineno,
                        "file": source_file,
                        "deps": deps,
                    })

                if hasattr(node, 'children'):
                    new_ctx = context
                    if isinstance(node, Always):
                        # Detect always_ff vs always_comb from sensitivity
                        sens = _node_to_str(node.sens_list) if hasattr(node, 'sens_list') and node.sens_list else ""
                        if "posedge" in sens or "negedge" in sens:
                            new_ctx = "always_ff"
                        else:
                            new_ctx = "always_comb"
                    for child in node.children():
                        _walk(child, new_ctx)

            _walk(ast)

        except Exception as e:
            logger.warning(f"AST walk failed: {e}")
            self._extract_assignments_regex(source_file)

    def _extract_assignments_regex(self, rtl_path: str):
        """Fallback: regex-based assignment extraction."""
        lines = self._source_lines.get(rtl_path, [])
        in_always_ff = False
        in_always_comb = False

        for i, line in enumerate(lines, 1):
            stripped = line.strip()

            # Track always block context
            if re.search(r'always_ff\b|always\s*@\s*\(\s*posedge', stripped):
                in_always_ff = True
                in_always_comb = False
            elif re.search(r'always_comb\b|always\s*@\s*\(\*\)', stripped):
                in_always_comb = True
                in_always_ff = False
            elif stripped.startswith("end") and (in_always_ff or in_always_comb):
                in_always_ff = False
                in_always_comb = False

            # Continuous assign
            m = re.match(r'\s*assign\s+(\w+)\s*=\s*(.+?)\s*;', stripped)
            if m:
                sig, rval = m.groups()
                deps = re.findall(r'\b([a-zA-Z_]\w*)\b', rval)
                self._assignments.append({
                    "signal": sig, "rvalue": rval, "type": "assign",
                    "line": i, "file": rtl_path, "deps": deps,
                })
                continue

            # Non-blocking (<=) 
            m = re.match(r'\s*(\w+)\s*<=\s*(.+?)\s*;', stripped)
            if m:
                sig, rval = m.groups()
                deps = re.findall(r'\b([a-zA-Z_]\w*)\b', rval)
                self._assignments.append({
                    "signal": sig, "rvalue": rval,
                    "type": "always_ff" if in_always_ff else "always_comb",
                    "line": i, "file": rtl_path, "deps": deps,
                })
                continue

            # Blocking (=) inside always
            if in_always_comb or in_always_ff:
                m = re.match(r'\s*(\w+)\s*=\s*(.+?)\s*;', stripped)
                if m:
                    sig, rval = m.groups()
                    deps = re.findall(r'\b([a-zA-Z_]\w*)\b', rval)
                    self._assignments.append({
                        "signal": sig, "rvalue": rval,
                        "type": "always_comb" if in_always_comb else "always_ff",
                        "line": i, "file": rtl_path, "deps": deps,
                    })

    def trace_signal(self, signal_name: str, max_depth: int = 5) -> List[ASTTraceResult]:
        """
        Back-trace a signal through the assignment graph.
        
        Returns all assignments that drive `signal_name`, plus recursive
        traces of the driving signals (up to max_depth).
        """
        results: List[ASTTraceResult] = []
        visited: set = set()
        self._trace_recursive(signal_name, results, visited, 0, max_depth)
        return results

    def _trace_recursive(self, sig: str, results: List[ASTTraceResult],
                         visited: set, depth: int, max_depth: int):
        if depth > max_depth or sig in visited:
            return
        visited.add(sig)

        for asgn in self._assignments:
            if asgn["signal"] == sig:
                # Get context lines
                src_lines = self._source_lines.get(asgn["file"], [])
                line_num = asgn["line"]
                start = max(0, line_num - 4)
                end = min(len(src_lines), line_num + 3)
                context = "".join(src_lines[start:end])

                results.append(ASTTraceResult(
                    signal_name=sig,
                    source_file=asgn["file"],
                    source_line=line_num,
                    assignment_type=asgn["type"],
                    rvalue_expression=asgn["rvalue"],
                    driving_signals=asgn["deps"],
                    context_lines=context,
                ))

                # Recurse into dependencies
                for dep in asgn["deps"]:
                    self._trace_recursive(dep, results, visited, depth + 1, max_depth)

    def get_all_signals(self) -> List[str]:
        """Return all signal names found in the AST."""
        return list(set(a["signal"] for a in self._assignments))


# ─── Waveform Expert Module ──────────────────────────────────────────

@dataclass
class WaveformDiagnosis:
    """Structured diagnosis from waveform + AST analysis."""
    failing_signal: str
    mismatch_time: int
    expected_value: str
    actual_value: str
    root_cause_traces: List[ASTTraceResult]
    suggested_fix_area: str     # Human-readable location
    diagnosis_summary: str      # Natural language summary for LLM


class WaveformExpertModule:
    """
    VerilogCoder-style AST-based Waveform Tracing Tool.
    
    Combines VCD waveform analysis with Pyverilog AST back-tracing
    to produce precise, line-level root-cause diagnosis when
    Icarus Verilog simulations fail.
    
    Pipeline:
        1. Parse VCD → find failing signal + mismatch time
        2. Parse RTL AST → build assignment dependency graph
        3. Back-trace failing signal's RVALUE through the graph
        4. Produce structured WaveformDiagnosis for the fixer agent
    """

    def __init__(self):
        self.vcd_parser = VCDParser()
        self.ast_tracer = ASTBackTracer()

    def analyze_failure(
        self,
        rtl_path: str,
        vcd_path: str,
        sim_log: str,
        design_name: str,
    ) -> Optional[WaveformDiagnosis]:
        """
        Full waveform + AST analysis pipeline.
        
        Args:
            rtl_path:    Path to the RTL .v file
            vcd_path:    Path to the simulation .vcd file
            sim_log:     Text output from iverilog/vvp simulation
            design_name: Module name
            
        Returns:
            WaveformDiagnosis with traces, or None if analysis not possible.
        """
        logger.info(f"[WaveformExpert] Analyzing failure for {design_name}")

        # Step 1: Parse VCD
        signals = self.vcd_parser.parse(vcd_path)
        if not signals:
            logger.warning("[WaveformExpert] No signals found in VCD")
            return self._fallback_from_log(sim_log, rtl_path)

        # Step 2: Parse RTL AST
        self.ast_tracer.parse_rtl(rtl_path)

        # Step 3: Identify failing signal from simulation log
        failing_sig, mismatch_time, expected, actual = self._extract_failure_from_log(sim_log, signals)
        if not failing_sig:
            logger.warning("[WaveformExpert] Could not identify failing signal from log")
            return self._fallback_from_log(sim_log, rtl_path)

        # Step 4: Back-trace through AST
        traces = self.ast_tracer.trace_signal(failing_sig)

        # Step 5: Build diagnosis
        if traces:
            primary = traces[0]
            fix_area = f"{primary.source_file}:{primary.source_line} ({primary.assignment_type})"
        else:
            fix_area = "Could not trace — check module ports and combinational logic"

        summary = self._build_diagnosis_summary(
            failing_sig, mismatch_time, expected, actual, traces
        )

        return WaveformDiagnosis(
            failing_signal=failing_sig,
            mismatch_time=mismatch_time,
            expected_value=expected,
            actual_value=actual,
            root_cause_traces=traces,
            suggested_fix_area=fix_area,
            diagnosis_summary=summary,
        )

    def _extract_failure_from_log(
        self, sim_log: str, signals: Dict[str, VCDSignal]
    ) -> Tuple[str, int, str, str]:
        """
        Extract the failing signal, time, expected, and actual values from sim output.
        
        Handles common testbench output patterns:
            - "ERROR: signal_name expected X got Y at time T"
            - "MISMATCH at T: expected=X actual=Y"
            - "$display output with expected/got"
        """
        if not sim_log:
            return "", 0, "", ""

        # Pattern 1: ERROR: <signal> expected <exp> got <act> at time <t>
        m = re.search(
            r'(?:ERROR|FAIL|MISMATCH)[:\s]+(\w+)\s+expected\s+(\S+)\s+got\s+(\S+)\s+(?:at\s+)?(?:time\s+)?(\d+)',
            sim_log, re.IGNORECASE
        )
        if m:
            return m.group(1), int(m.group(4)), m.group(2), m.group(3)

        # Pattern 2: "expected <exp> but got <act>" with signal context
        m = re.search(
            r'(\w+)\s*:\s*expected\s+(\S+)\s+(?:but\s+)?got\s+(\S+)',
            sim_log, re.IGNORECASE
        )
        if m:
            return m.group(1), 0, m.group(2), m.group(3)

        # Pattern 3: "MISMATCH at <time>: expected=<exp> actual=<act>"
        m = re.search(
            r'MISMATCH\s+at\s+(\d+).*expected[=:\s]+(\S+).*actual[=:\s]+(\S+)',
            sim_log, re.IGNORECASE
        )
        if m:
            return "", int(m.group(1)), m.group(2), m.group(3)

        # Pattern 4: "TEST FAILED" — pick the first non-clk signal with x/z
        for sig_id, sig in signals.items():
            if sig.name in ("clk", "rst_n", "reset"):
                continue
            for ch in sig.changes:
                if 'x' in ch.value.lower() or 'z' in ch.value.lower():
                    return sig.name, ch.time, "defined", ch.value

        return "", 0, "", ""

    def _fallback_from_log(self, sim_log: str, rtl_path: str) -> Optional[WaveformDiagnosis]:
        """Fallback diagnosis when VCD isn't available or parseable."""
        if not sim_log:
            return None

        # Try to at least identify error lines
        error_lines = [l for l in sim_log.split("\n")
                       if re.search(r'error|fail|mismatch', l, re.IGNORECASE)]

        if not error_lines:
            return None

        return WaveformDiagnosis(
            failing_signal="unknown",
            mismatch_time=0,
            expected_value="unknown",
            actual_value="unknown",
            root_cause_traces=[],
            suggested_fix_area="See simulation log",
            diagnosis_summary=(
                "VCD/AST analysis unavailable. Raw errors from simulation:\n"
                + "\n".join(error_lines[:10])
            ),
        )

    def _build_diagnosis_summary(
        self,
        sig: str,
        time: int,
        expected: str,
        actual: str,
        traces: List[ASTTraceResult],
    ) -> str:
        """Build a human-readable diagnosis for the LLM fixer agent."""
        parts = []
        parts.append(f"SIGNAL MISMATCH: '{sig}' at time {time}ns")
        parts.append(f"  Expected: {expected}")
        parts.append(f"  Actual:   {actual}")
        parts.append("")

        if traces:
            parts.append("AST BACK-TRACE (root cause chain):")
            for i, tr in enumerate(traces):
                parts.append(
                    f"  [{i+1}] {tr.signal_name} ← {tr.rvalue_expression}"
                )
                parts.append(
                    f"      Type: {tr.assignment_type} | "
                    f"File: {tr.source_file}:{tr.source_line}"
                )
                parts.append(f"      Depends on: {', '.join(tr.driving_signals)}")
                if tr.context_lines:
                    parts.append(f"      Context:\n{tr.context_lines}")
                parts.append("")

            parts.append("SUGGESTED FIX STRATEGY:")
            primary = traces[0]
            parts.append(
                f"  Check the {primary.assignment_type} block at line {primary.source_line} "
                f"of {primary.source_file}."
            )
            parts.append(
                f"  The RHS expression '{primary.rvalue_expression}' produces "
                f"'{actual}' but should produce '{expected}'."
            )
            if len(traces) > 1:
                parts.append(
                    f"  The dependency chain involves {len(traces)} signals — "
                    "check upstream logic too."
                )
        else:
            parts.append("No AST traces found — signal may be a port or undeclared.")

        return "\n".join(parts)

    def generate_fix_prompt(self, diagnosis: WaveformDiagnosis, rtl_code: str) -> str:
        """
        Generate a precise LLM prompt from the diagnosis.
        
        This replaces vague "fix the simulation error" prompts with exact,
        line-level instructions based on AST + VCD evidence.
        """
        prompt_parts = [
            "# WAVEFORM-GUIDED RTL FIX REQUEST",
            "",
            "## Diagnosis (from AST + VCD analysis)",
            diagnosis.diagnosis_summary,
            "",
            "## Fix Location",
            f"Primary: {diagnosis.suggested_fix_area}",
            "",
            "## Instructions",
            f"1. The signal '{diagnosis.failing_signal}' produces '{diagnosis.actual_value}' "
            f"but should be '{diagnosis.expected_value}' at time {diagnosis.mismatch_time}ns.",
        ]

        if diagnosis.root_cause_traces:
            tr = diagnosis.root_cause_traces[0]
            prompt_parts.append(
                f"2. Fix the expression: {tr.signal_name} <= {tr.rvalue_expression} "
                f"(line {tr.source_line})"
            )
            if tr.driving_signals:
                prompt_parts.append(
                    f"3. Check these upstream signals: {', '.join(tr.driving_signals)}"
                )

        prompt_parts.extend([
            "",
            "## Current RTL (fix in-place)",
            "```verilog",
            rtl_code,
            "```",
            "",
            "Return ONLY the corrected Verilog inside ```verilog fences.",
        ])

        return "\n".join(prompt_parts)
