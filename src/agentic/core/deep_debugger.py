"""
Deep Debugger Module — FVDebug Logic
=====================================

Based on: FVDebug (Formal Verification Debugging with Balanced Analysis)

When code fails formal checks (SymbiYosys), this module:
1. Runs SymbiYosys and parses the failure trace / counterexample.
2. Builds a Causal Graph of signals involved in the failure.
3. For EVERY suspicious signal, the agent MUST produce:
     - 2 arguments FOR it being the bug root cause
     - 2 arguments AGAINST it being the bug root cause
   This is MANDATORY to prevent confirmation bias (the "For-and-Against" protocol).
4. Only after balanced analysis, the debugger decides on the fix.

Tools used: SymbiYosys (sby), Yosys (synthesis), Icarus Verilog (sim).
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


# ─── Data Structures ─────────────────────────────────────────────────

@dataclass
class ForAgainstArgument:
    """A single argument for or against a signal being the root cause."""
    stance: str        # "FOR" | "AGAINST"
    reasoning: str     # The argument text
    evidence: str = "" # Supporting evidence (line ref, VCD data, etc.)


@dataclass
class SuspiciousSignal:
    """A signal flagged as potentially responsible for the failure."""
    name: str
    module: str
    line: int
    expression: str = ""
    for_arguments: List[ForAgainstArgument] = field(default_factory=list)
    against_arguments: List[ForAgainstArgument] = field(default_factory=list)
    verdict: str = ""          # "BUG" | "NOT_BUG" | "UNCERTAIN"
    confidence: float = 0.0   # 0.0 to 1.0


@dataclass
class CausalGraphNode:
    """A node in the failure causal graph."""
    signal: str
    driver_type: str         # "always_ff", "always_comb", "assign"
    source_line: int
    dependencies: List[str]  # Signals feeding into this node
    value_at_failure: str = ""


@dataclass
class CausalGraph:
    """Directed graph of signal dependencies involved in the failure."""
    nodes: Dict[str, CausalGraphNode] = field(default_factory=dict)
    root_signal: str = ""                  # The assertion/property that failed
    failure_time: int = 0
    counterexample: str = ""

    def get_cone_of_influence(self, signal: str, max_depth: int = 8) -> List[str]:
        """Get all signals in the backward cone of influence."""
        visited = set()
        self._coi_walk(signal, visited, 0, max_depth)
        return list(visited)

    def _coi_walk(self, sig: str, visited: set, depth: int, max_depth: int):
        if depth > max_depth or sig in visited:
            return
        visited.add(sig)
        node = self.nodes.get(sig)
        if node:
            for dep in node.dependencies:
                self._coi_walk(dep, visited, depth + 1, max_depth)

    def to_mermaid(self) -> str:
        """Export the causal graph as a Mermaid diagram."""
        lines = ["graph TD"]
        for sig, node in self.nodes.items():
            safe_sig = sig.replace("[", "_").replace("]", "_").replace(".", "_")
            for dep in node.dependencies:
                safe_dep = dep.replace("[", "_").replace("]", "_").replace(".", "_")
                lines.append(f"    {safe_dep} --> {safe_sig}")
        return "\n".join(lines)


@dataclass
class FormalFailure:
    """Parsed result from a SymbiYosys formal verification run."""
    property_name: str
    property_type: str        # "assert", "cover", "assume"
    status: str               # "FAIL", "PASS", "UNKNOWN", "ERROR"
    counterexample_trace: str  # Raw CEX trace text
    failing_step: int = 0
    signals_in_cex: List[str] = field(default_factory=list)
    error_message: str = ""


@dataclass
class DebugVerdict:
    """Final verdict from the Deep Debugger."""
    root_cause_signal: str
    root_cause_line: int
    root_cause_file: str
    fix_description: str
    confidence: float
    causal_graph: CausalGraph
    suspicious_signals: List[SuspiciousSignal]
    balanced_analysis_log: str   # Full for-against reasoning for audit


# ─── SymbiYosys Interface ────────────────────────────────────────────

class SymbiYosysRunner:
    """
    Runs SymbiYosys formal verification and parses results.
    """

    def __init__(self, sby_bin: str = "sby", yosys_bin: str = "yosys"):
        self.sby_bin = sby_bin
        self.yosys_bin = yosys_bin

    def run_formal(self, sby_config_path: str, work_dir: str = "") -> FormalFailure:
        """
        Run SymbiYosys and return parsed failure info.
        
        Args:
            sby_config_path: Path to the .sby config file
            work_dir: Working directory for sby output
            
        Returns:
            FormalFailure with status, CEX trace, and signal list.
        """
        if not work_dir:
            work_dir = os.path.dirname(sby_config_path)

        # Clean previous run directory
        sby_name = os.path.splitext(os.path.basename(sby_config_path))[0]
        sby_work = os.path.join(work_dir, sby_name)
        if os.path.isdir(sby_work):
            import shutil
            shutil.rmtree(sby_work, ignore_errors=True)

        try:
            result = subprocess.run(
                [self.sby_bin, "-f", sby_config_path],
                capture_output=True,
                text=True,
                cwd=work_dir,
                timeout=300,
            )
            output = result.stdout + "\n" + result.stderr
            return self._parse_sby_output(output, sby_work, sby_name)

        except subprocess.TimeoutExpired:
            return FormalFailure(
                property_name="timeout",
                property_type="assert",
                status="ERROR",
                counterexample_trace="",
                error_message="SymbiYosys timed out after 300s",
            )
        except FileNotFoundError:
            return FormalFailure(
                property_name="missing_tool",
                property_type="assert",
                status="ERROR",
                counterexample_trace="",
                error_message=f"SymbiYosys binary not found at '{self.sby_bin}'",
            )
        except Exception as e:
            return FormalFailure(
                property_name="exception",
                property_type="assert",
                status="ERROR",
                counterexample_trace="",
                error_message=str(e),
            )

    def _parse_sby_output(self, output: str, work_dir: str, name: str) -> FormalFailure:
        """Parse SymbiYosys stdout/stderr into a FormalFailure."""
        status = "UNKNOWN"
        prop_name = ""
        prop_type = "assert"
        cex_trace = ""
        failing_step = 0
        signals = []

        # Determine overall status
        if "DONE (PASS" in output:
            status = "PASS"
        elif "DONE (FAIL" in output:
            status = "FAIL"
        elif "DONE (ERROR" in output or "ERROR" in output:
            status = "ERROR"

        # Extract failing property
        m = re.search(r'Assert failed in .+?: (.+)', output)
        if m:
            prop_name = m.group(1).strip()

        # Extract failing step
        m = re.search(r'BMC failed at step\s+(\d+)', output)
        if not m:
            m = re.search(r'Induction failed at step\s+(\d+)', output)
        if m:
            failing_step = int(m.group(1))

        # Try to read the VCD counterexample
        cex_vcd = os.path.join(work_dir, "engine_0", "trace.vcd")
        if not os.path.exists(cex_vcd):
            cex_vcd = os.path.join(work_dir, "engine_0", "trace0.vcd")
        if os.path.exists(cex_vcd):
            try:
                with open(cex_vcd, "r", errors="replace") as f:
                    cex_trace = f.read()[:10000]  # Truncate for context
                # Extract signal names from VCD
                signals = re.findall(r'\$var\s+\w+\s+\d+\s+\S+\s+(\w+)', cex_trace)
            except Exception:
                pass

        # Try text-based counterexample
        if not cex_trace:
            cex_txt = os.path.join(work_dir, "engine_0", "trace.txt")
            if os.path.exists(cex_txt):
                try:
                    with open(cex_txt, "r") as f:
                        cex_trace = f.read()[:10000]
                except Exception:
                    pass

        return FormalFailure(
            property_name=prop_name,
            property_type=prop_type,
            status=status,
            counterexample_trace=cex_trace,
            failing_step=failing_step,
            signals_in_cex=signals,
            error_message="" if status != "ERROR" else output[-500:],
        )

    def generate_sby_config(
        self,
        design_name: str,
        rtl_files: List[str],
        properties_file: str = "",
        mode: str = "bmc",
        depth: int = 20,
        engine: str = "smtbmc",
    ) -> str:
        """
        Generate a .sby configuration file content.
        
        Args:
            design_name:     Top module name
            rtl_files:       List of Verilog source files
            properties_file: Optional SVA properties file
            mode:            "bmc" | "prove" | "cover"
            depth:           BMC depth
            engine:          "smtbmc" | "aiger" | "abc"
        """
        files_section = "\n".join(rtl_files)
        if properties_file:
            files_section += f"\n{properties_file}"

        return f"""[tasks]
{mode}

[options]
{mode}:
mode {mode}
depth {depth}

[engines]
{mode}:
{engine}

[script]
read -formal {' '.join(os.path.basename(f) for f in rtl_files)}
{f'read -formal {os.path.basename(properties_file)}' if properties_file else ''}
prep -top {design_name}

[files]
{files_section}
"""


# ─── Causal Graph Builder ────────────────────────────────────────────

class CausalGraphBuilder:
    """
    Builds a causal graph from RTL + formal failure trace.
    
    The causal graph connects the failing assertion to the cone of
    signals that contributed to the failure, enabling systematic
    root-cause isolation.
    """

    def __init__(self):
        self._assignments: List[Dict[str, Any]] = []

    def build(
        self,
        rtl_path: str,
        failure: FormalFailure,
    ) -> CausalGraph:
        """Build a causal graph from RTL and formal failure."""
        graph = CausalGraph(
            root_signal=failure.property_name,
            failure_time=failure.failing_step,
            counterexample=failure.counterexample_trace[:2000],
        )

        # Parse RTL assignments
        self._parse_rtl(rtl_path)

        # Build graph nodes from assignments
        for asgn in self._assignments:
            sig = asgn["signal"]
            graph.nodes[sig] = CausalGraphNode(
                signal=sig,
                driver_type=asgn["type"],
                source_line=asgn["line"],
                dependencies=asgn["deps"],
            )

        # If we have CEX signals, annotate values
        if failure.signals_in_cex:
            for sig_name in failure.signals_in_cex:
                if sig_name in graph.nodes:
                    graph.nodes[sig_name].value_at_failure = "in_cex"

        return graph

    def _parse_rtl(self, rtl_path: str):
        """Parse RTL to extract signal assignments (regex-based)."""
        self._assignments.clear()
        if not os.path.exists(rtl_path):
            return

        try:
            with open(rtl_path, "r") as f:
                lines = f.readlines()
        except Exception:
            return

        in_ff = False
        in_comb = False

        for i, line in enumerate(lines, 1):
            s = line.strip()

            if re.search(r'always_ff\b|always\s*@\s*\(\s*posedge', s):
                in_ff = True
                in_comb = False
            elif re.search(r'always_comb\b|always\s*@\s*\(\*\)', s):
                in_comb = True
                in_ff = False
            elif s.startswith("end") and (in_ff or in_comb):
                in_ff = False
                in_comb = False

            # Continuous assign
            m = re.match(r'\s*assign\s+(\w+)\s*=\s*(.+?)\s*;', s)
            if m:
                sig, rval = m.groups()
                deps = re.findall(r'\b([a-zA-Z_]\w*)\b', rval)
                self._assignments.append({
                    "signal": sig, "rvalue": rval, "type": "assign",
                    "line": i, "deps": deps,
                })
                continue

            # Non-blocking
            m = re.match(r'\s*(\w+)\s*<=\s*(.+?)\s*;', s)
            if m:
                sig, rval = m.groups()
                deps = re.findall(r'\b([a-zA-Z_]\w*)\b', rval)
                self._assignments.append({
                    "signal": sig, "rvalue": rval,
                    "type": "always_ff" if in_ff else "always_comb",
                    "line": i, "deps": deps,
                })
                continue

            # Blocking in always
            if in_comb or in_ff:
                m = re.match(r'\s*(\w+)\s*=\s*(.+?)\s*;', s)
                if m:
                    sig, rval = m.groups()
                    deps = re.findall(r'\b([a-zA-Z_]\w*)\b', rval)
                    self._assignments.append({
                        "signal": sig, "rvalue": rval,
                        "type": "always_comb" if in_comb else "always_ff",
                        "line": i, "deps": deps,
                    })


# ─── Balanced Analysis Engine ────────────────────────────────────────

FOR_AGAINST_PROMPT = """\
You are performing root-cause analysis on a formal verification failure.

MANDATORY PROTOCOL: For the suspicious signal below, you MUST write:
  - EXACTLY 2 arguments FOR it being the root cause of the bug
  - EXACTLY 2 arguments AGAINST it being the root cause of the bug

Then give your VERDICT: BUG | NOT_BUG | UNCERTAIN (with confidence 0.0-1.0)

This balanced analysis is REQUIRED to prevent confirmation bias.

SIGNAL UNDER ANALYSIS:
  Name: {signal_name}
  Module: {module}
  Line: {line}
  Expression: {expression}
  Driver type: {driver_type}
  Dependencies: {dependencies}

FAILURE CONTEXT:
  Property: {property_name}
  Failure step: {failure_step}
  Counterexample signals: {cex_signals}

FULL RTL CONTEXT:
```verilog
{rtl_context}
```

Respond in this EXACT format:
FOR_1: <argument>
FOR_2: <argument>
AGAINST_1: <argument>
AGAINST_2: <argument>
VERDICT: <BUG|NOT_BUG|UNCERTAIN>
CONFIDENCE: <0.0 to 1.0>
REASONING: <one-line summary>
"""


class BalancedAnalyzer:
    """
    Implements the mandatory "For-and-Against" protocol for every suspicious signal.
    
    For every signal in the failure's cone of influence:
    1. The LLM MUST produce 2 FOR arguments (why it could be the bug)
    2. The LLM MUST produce 2 AGAINST arguments (why it might NOT be the bug)
    3. Only then: verdict + confidence score
    
    Signals are ranked by confidence and the highest-confidence BUG verdict
    identifies the root cause.
    """

    def __init__(self, llm, verbose: bool = False):
        from crewai import Agent, Task, Crew
        self.llm = llm
        self.verbose = verbose
        self._Agent = Agent
        self._Task = Task
        self._Crew = Crew

    def analyze_signal(
        self,
        signal_name: str,
        graph: CausalGraph,
        failure: FormalFailure,
        rtl_code: str,
    ) -> SuspiciousSignal:
        """
        Run balanced for-and-against analysis on a single signal.
        """
        node = graph.nodes.get(signal_name)
        if not node:
            return SuspiciousSignal(
                name=signal_name, module="", line=0,
                verdict="UNCERTAIN", confidence=0.0,
            )

        # Build context: extract surrounding lines from RTL
        rtl_lines = rtl_code.split("\n")
        ctx_start = max(0, node.source_line - 6)
        ctx_end = min(len(rtl_lines), node.source_line + 5)
        rtl_context = "\n".join(rtl_lines[ctx_start:ctx_end])

        prompt = FOR_AGAINST_PROMPT.format(
            signal_name=signal_name,
            module="top",
            line=node.source_line,
            expression=f"{signal_name} driven by {node.driver_type}",
            driver_type=node.driver_type,
            dependencies=", ".join(node.dependencies),
            property_name=failure.property_name,
            failure_step=failure.failing_step,
            cex_signals=", ".join(failure.signals_in_cex[:20]),
            rtl_context=rtl_context,
        )

        agent = self._Agent(
            role="Formal Verification Debugger",
            goal=f"Analyze signal '{signal_name}' for root-cause determination",
            backstory=(
                "You are a senior formal verification engineer. You ALWAYS perform "
                "balanced analysis: 2 FOR + 2 AGAINST arguments before any verdict. "
                "This prevents confirmation bias in root-cause identification."
            ),
            llm=self.llm,
            verbose=self.verbose,
        )

        task = self._Task(
            description=prompt,
            expected_output="FOR_1, FOR_2, AGAINST_1, AGAINST_2, VERDICT, CONFIDENCE, REASONING",
            agent=agent,
        )

        try:
            raw = str(self._Crew(agents=[agent], tasks=[task]).kickoff())
            return self._parse_analysis(raw, signal_name, node)
        except Exception as e:
            logger.warning(f"[BalancedAnalyzer] Analysis failed for {signal_name}: {e}")
            return SuspiciousSignal(
                name=signal_name, module="", line=node.source_line,
                expression=f"{node.driver_type} at line {node.source_line}",
                verdict="UNCERTAIN", confidence=0.0,
            )

    def _parse_analysis(
        self, raw: str, signal_name: str, node: CausalGraphNode
    ) -> SuspiciousSignal:
        """Parse the LLM's balanced analysis response."""
        result = SuspiciousSignal(
            name=signal_name,
            module="top",
            line=node.source_line,
            expression=f"{node.driver_type} at line {node.source_line}",
        )

        # Extract FOR arguments
        for i in (1, 2):
            m = re.search(rf'FOR_{i}\s*:\s*(.+?)(?:\n|$)', raw, re.IGNORECASE)
            if m:
                result.for_arguments.append(
                    ForAgainstArgument(stance="FOR", reasoning=m.group(1).strip())
                )

        # Extract AGAINST arguments
        for i in (1, 2):
            m = re.search(rf'AGAINST_{i}\s*:\s*(.+?)(?:\n|$)', raw, re.IGNORECASE)
            if m:
                result.against_arguments.append(
                    ForAgainstArgument(stance="AGAINST", reasoning=m.group(1).strip())
                )

        # Extract verdict
        m = re.search(r'VERDICT\s*:\s*(BUG|NOT_BUG|UNCERTAIN)', raw, re.IGNORECASE)
        if m:
            result.verdict = m.group(1).upper()
        else:
            result.verdict = "UNCERTAIN"

        # Extract confidence
        m = re.search(r'CONFIDENCE\s*:\s*([\d.]+)', raw, re.IGNORECASE)
        if m:
            try:
                result.confidence = float(m.group(1))
            except ValueError:
                result.confidence = 0.5
        else:
            result.confidence = 0.5

        # Validate: enforce mandatory 2+2 rule
        if len(result.for_arguments) < 2:
            logger.warning(
                f"[BalancedAnalyzer] Signal '{signal_name}': only {len(result.for_arguments)} "
                "FOR arguments (2 required) — reducing confidence"
            )
            result.confidence *= 0.5
        if len(result.against_arguments) < 2:
            logger.warning(
                f"[BalancedAnalyzer] Signal '{signal_name}': only {len(result.against_arguments)} "
                "AGAINST arguments (2 required) — reducing confidence"
            )
            result.confidence *= 0.5

        return result


# ─── Deep Debugger Module ────────────────────────────────────────────

class DeepDebuggerModule:
    """
    FVDebug: Formal Verification Deep Debugger with Balanced Analysis.
    
    Pipeline:
        1. Run SymbiYosys formal verification
        2. Parse failure → build Causal Graph
        3. Identify suspicious signals (cone of influence)
        4. For each suspicious signal: mandatory For-and-Against analysis
        5. Rank signals by confidence → identify root cause
        6. Generate precise fix prompt
    """

    def __init__(self, llm, sby_bin: str = "sby", yosys_bin: str = "yosys",
                 verbose: bool = False, max_signals_to_analyze: int = 5):
        self.llm = llm
        self.verbose = verbose
        self.max_signals = max_signals_to_analyze
        self.sby_runner = SymbiYosysRunner(sby_bin, yosys_bin)
        self.graph_builder = CausalGraphBuilder()
        self.analyzer = BalancedAnalyzer(llm, verbose)

    def debug_formal_failure(
        self,
        rtl_path: str,
        sby_config_path: str,
        design_name: str,
        rtl_code: str = "",
    ) -> Optional[DebugVerdict]:
        """
        Full FVDebug pipeline: formal check → causal graph → balanced analysis → verdict.
        
        Args:
            rtl_path:        Path to the RTL source file
            sby_config_path: Path to the .sby configuration
            design_name:     Top module name
            rtl_code:        RTL source code (read from file if empty)
        
        Returns:
            DebugVerdict with root cause, fix, and full balanced analysis log.
        """
        logger.info(f"[DeepDebugger] Starting FVDebug pipeline for {design_name}")

        # Load RTL if not provided
        if not rtl_code and os.path.exists(rtl_path):
            with open(rtl_path, "r") as f:
                rtl_code = f.read()

        # Step 1: Run formal verification
        logger.info("[DeepDebugger] Step 1: Running SymbiYosys formal checks")
        failure = self.sby_runner.run_formal(sby_config_path)
        
        if failure.status == "PASS":
            logger.info("[DeepDebugger] All formal properties passed!")
            return None  # No debugging needed
        
        if failure.status == "ERROR":
            logger.error(f"[DeepDebugger] SymbiYosys error: {failure.error_message}")
            return None

        # Step 2: Build causal graph
        logger.info("[DeepDebugger] Step 2: Building causal graph")
        graph = self.graph_builder.build(rtl_path, failure)

        # Step 3: Identify suspicious signals (cone of influence)
        logger.info("[DeepDebugger] Step 3: Identifying suspicious signals")
        if failure.property_name and failure.property_name in graph.nodes:
            coi = graph.get_cone_of_influence(failure.property_name)
        elif failure.signals_in_cex:
            # Use CEX signals as starting points
            coi = set()
            for sig in failure.signals_in_cex[:5]:
                if sig in graph.nodes:
                    coi.update(graph.get_cone_of_influence(sig))
            coi = list(coi)
        else:
            # Fallback: analyze all signals
            coi = list(graph.nodes.keys())

        # Filter to most relevant signals
        coi = [s for s in coi if s not in ("clk", "rst_n", "reset")]
        coi = coi[:self.max_signals]

        # Step 4: Balanced For-and-Against analysis for each signal
        logger.info(f"[DeepDebugger] Step 4: Balanced analysis on {len(coi)} signals")
        suspicious: List[SuspiciousSignal] = []
        analysis_log_parts: List[str] = []

        for sig_name in coi:
            logger.info(f"[DeepDebugger] Analyzing signal: {sig_name}")
            ss = self.analyzer.analyze_signal(sig_name, graph, failure, rtl_code)
            suspicious.append(ss)
            
            # Build audit log
            analysis_log_parts.append(f"\n--- Signal: {sig_name} (line {ss.line}) ---")
            for fa in ss.for_arguments:
                analysis_log_parts.append(f"  FOR:     {fa.reasoning}")
            for fa in ss.against_arguments:
                analysis_log_parts.append(f"  AGAINST: {fa.reasoning}")
            analysis_log_parts.append(f"  VERDICT: {ss.verdict} (confidence: {ss.confidence:.2f})")

        # Step 5: Rank and select root cause
        bugs = [s for s in suspicious if s.verdict == "BUG"]
        bugs.sort(key=lambda s: s.confidence, reverse=True)

        if bugs:
            root = bugs[0]
            fix_desc = (
                f"Signal '{root.name}' at line {root.line} is the most likely root cause "
                f"(confidence: {root.confidence:.2f}). "
                f"FOR: {'; '.join(a.reasoning for a in root.for_arguments)}. "
                f"Fix the {root.expression}."
            )
        else:
            # No confident BUG verdict — use highest confidence UNCERTAIN
            suspicious.sort(key=lambda s: s.confidence, reverse=True)
            root = suspicious[0] if suspicious else SuspiciousSignal(
                name="unknown", module="", line=0, verdict="UNCERTAIN"
            )
            fix_desc = (
                f"No clear root cause identified. Most suspicious: '{root.name}' "
                f"at line {root.line} (confidence: {root.confidence:.2f}). "
                "Manual review recommended."
            )

        return DebugVerdict(
            root_cause_signal=root.name,
            root_cause_line=root.line,
            root_cause_file=rtl_path,
            fix_description=fix_desc,
            confidence=root.confidence,
            causal_graph=graph,
            suspicious_signals=suspicious,
            balanced_analysis_log="\n".join(analysis_log_parts),
        )

    def generate_fix_prompt(self, verdict: DebugVerdict, rtl_code: str) -> str:
        """
        Generate a precise LLM fix prompt from the debug verdict.
        
        Unlike generic "fix the formal error" prompts, this includes:
        - The exact root-cause signal and line
        - The balanced analysis reasoning
        - The causal dependency chain
        """
        parts = [
            "# FORMAL VERIFICATION DEBUG FIX REQUEST",
            "",
            "## Root Cause (from FVDebug balanced analysis)",
            f"Signal: {verdict.root_cause_signal}",
            f"File: {verdict.root_cause_file}:{verdict.root_cause_line}",
            f"Confidence: {verdict.confidence:.2f}",
            f"Description: {verdict.fix_description}",
            "",
            "## Balanced Analysis Log",
            verdict.balanced_analysis_log,
            "",
            "## Causal Graph (Mermaid)",
            "```mermaid",
            verdict.causal_graph.to_mermaid(),
            "```",
            "",
            "## Instructions",
            f"1. Fix signal '{verdict.root_cause_signal}' at line {verdict.root_cause_line}.",
            "2. Ensure the fix satisfies ALL formal properties.",
            "3. Do NOT break existing passing properties.",
            "4. Return ONLY corrected Verilog inside ```verilog fences.",
            "",
            "## Current RTL",
            "```verilog",
            rtl_code,
            "```",
        ]
        return "\n".join(parts)

    def debug_from_existing_failure(
        self,
        rtl_path: str,
        failure: FormalFailure,
        rtl_code: str = "",
    ) -> Optional[DebugVerdict]:
        """
        Run balanced analysis on an already-parsed formal failure.
        
        Use this when SymbiYosys has already been run and you have the
        FormalFailure data — skips re-running sby.
        """
        if not rtl_code and os.path.exists(rtl_path):
            with open(rtl_path, "r") as f:
                rtl_code = f.read()

        graph = self.graph_builder.build(rtl_path, failure)
        
        coi = list(graph.nodes.keys())
        coi = [s for s in coi if s not in ("clk", "rst_n", "reset")]
        coi = coi[:self.max_signals]

        suspicious: List[SuspiciousSignal] = []
        log_parts: List[str] = []

        for sig in coi:
            ss = self.analyzer.analyze_signal(sig, graph, failure, rtl_code)
            suspicious.append(ss)
            log_parts.append(f"Signal {sig}: {ss.verdict} ({ss.confidence:.2f})")

        bugs = sorted([s for s in suspicious if s.verdict == "BUG"],
                       key=lambda s: s.confidence, reverse=True)
        root = bugs[0] if bugs else (suspicious[0] if suspicious else
                                      SuspiciousSignal(name="unknown", module="", line=0))

        return DebugVerdict(
            root_cause_signal=root.name,
            root_cause_line=root.line,
            root_cause_file=rtl_path,
            fix_description=f"Root cause: {root.name} at line {root.line}",
            confidence=root.confidence,
            causal_graph=graph,
            suspicious_signals=suspicious,
            balanced_analysis_log="\n".join(log_parts),
        )
