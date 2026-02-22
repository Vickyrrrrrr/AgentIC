import os
import re
import sys
import shutil
import tempfile
import textwrap
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC_ROOT = os.path.join(REPO_ROOT, "src")
if SRC_ROOT not in sys.path:
    sys.path.insert(0, SRC_ROOT)

from agentic.tools import vlsi_tools  # noqa: E402
from agentic.orchestrator import BuildOrchestrator  # noqa: E402


class SyntaxIntegrityTests(unittest.TestCase):
    def test_no_merge_conflict_markers(self):
        base = os.path.join(REPO_ROOT, "src", "agentic")
        bad = []
        for root, _, files in os.walk(base):
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                path = os.path.join(root, fname)
                with open(path, "r", errors="ignore") as f:
                    for idx, line in enumerate(f, start=1):
                        if line.startswith("<<<<<<<") or line.startswith(">>>>>>>"):
                            bad.append(f"{path}:{idx}")
        self.assertEqual([], bad, msg=f"Found conflict markers: {bad}")


class SemanticGateTests(unittest.TestCase):
    def _write_tmp(self, code: str) -> str:
        tmpdir = tempfile.mkdtemp(prefix="tier1_sem_")
        path = os.path.join(tmpdir, "dut.sv")
        with open(path, "w") as f:
            f.write(code)
        self.addCleanup(lambda: shutil.rmtree(tmpdir, ignore_errors=True))
        return path

    def test_port_shadowing_rejected(self):
        code = textwrap.dedent(
            """
            module dut(
                input logic clk,
                input logic a,
                output logic y
            );
                logic a;
                always_comb y = a;
            endmodule
            """
        )
        path = self._write_tmp(code)
        ok, report = vlsi_tools.run_semantic_rigor_check(path)
        self.assertFalse(ok)
        self.assertIn("a", report.get("port_shadowing", []))

    def test_clean_semantics_pass(self):
        code = textwrap.dedent(
            """
            module dut(
                input logic clk,
                input logic [3:0] a,
                output logic [3:0] y
            );
                always_comb y = a;
            endmodule
            """
        )
        path = self._write_tmp(code)
        ok, report = vlsi_tools.run_semantic_rigor_check(path)
        self.assertTrue(ok, msg=str(report))


class ParserTests(unittest.TestCase):
    def test_log_summary_stream_parser(self):
        tmpdir = tempfile.mkdtemp(prefix="tier1_log_")
        self.addCleanup(lambda: shutil.rmtree(tmpdir, ignore_errors=True))
        log = os.path.join(tmpdir, "routing.log")
        with open(log, "w") as f:
            for _ in range(5000):
                f.write("[INFO GRT] overflow on met2 congestion\n")
            for _ in range(200):
                f.write("[WARN] antenna violation\n")
        summary = vlsi_tools.parse_eda_log_summary(log, kind="routing", top_n=10)
        self.assertEqual(summary.get("total_lines"), 5200)
        self.assertTrue(summary.get("top_issues"))
        self.assertIn("routing_congestion", summary.get("counts", {}))

    def test_multi_corner_sta_parse(self):
        tmp = tempfile.mkdtemp(prefix="tier1_sta_")
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        original = vlsi_tools.OPENLANE_ROOT
        vlsi_tools.OPENLANE_ROOT = tmp
        self.addCleanup(lambda: setattr(vlsi_tools, "OPENLANE_ROOT", original))

        base = os.path.join(tmp, "designs", "chip", "runs", "run1", "reports", "signoff")
        for corner, setup, hold in [
            ("26-mca", "5.20", "0.11"),
            ("28-mca", "5.00", "0.09"),
            ("30-mca", "4.90", "0.08"),
        ]:
            os.makedirs(os.path.join(base, corner), exist_ok=True)
            path = os.path.join(base, corner, f"{corner}_sta.summary.rpt")
            with open(path, "w") as f:
                f.write(
                    textwrap.dedent(
                        f"""
                        report_wns
                        wns {setup}
                        report_worst_slack -max (Setup)
                        worst slack {setup}
                        report_worst_slack -min (Hold)
                        worst slack {hold}
                        """
                    )
                )
        sta = vlsi_tools.parse_sta_signoff("chip")
        self.assertFalse(sta.get("error"))
        self.assertEqual(3, len(sta.get("corners", [])))
        self.assertAlmostEqual(4.90, sta.get("worst_setup"), places=2)
        self.assertAlmostEqual(0.08, sta.get("worst_hold"), places=2)

    def test_congestion_parser(self):
        tmp = tempfile.mkdtemp(prefix="tier1_cong_")
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        original = vlsi_tools.OPENLANE_ROOT
        vlsi_tools.OPENLANE_ROOT = tmp
        self.addCleanup(lambda: setattr(vlsi_tools, "OPENLANE_ROOT", original))

        log_dir = os.path.join(tmp, "designs", "chip", "runs", "agentrun", "logs", "routing")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "19-global.log")
        with open(log_path, "w") as f:
            f.write("met1 8342 44 0.53% 0 / 0 / 0\n")
            f.write("met2 8036 1580 19.66% 5 / 2 / 7\n")
            f.write("Total 16378 1624 9.91% 5 / 2 / 7\n")
        data = vlsi_tools.parse_congestion_metrics("chip")
        self.assertAlmostEqual(9.91, data.get("total_usage_pct"), places=2)
        self.assertEqual(7, data.get("total_overflow"))


class OrchestratorSafetyTests(unittest.TestCase):
    def test_failure_fingerprint_repetition(self):
        orch = BuildOrchestrator(
            name="fingerprint_demo",
            desc="demo",
            llm=None,
            strict_gates=True,
        )
        first = orch._record_failure_fingerprint("same failure")
        second = orch._record_failure_fingerprint("same failure")
        self.assertFalse(first)
        self.assertTrue(second)

    def test_hierarchy_auto_threshold(self):
        orch = BuildOrchestrator(
            name="hier_demo",
            desc="demo",
            llm=None,
            hierarchical_mode="auto",
        )
        rtl = "\n".join([
            "module top(input logic clk, output logic y); assign y = 1'b0; endmodule",
            "module blk_a(input logic i, output logic o); assign o = i; endmodule",
            "module blk_b(input logic i, output logic o); assign o = i; endmodule",
        ] + ["// filler"] * 650)
        orch._evaluate_hierarchy(rtl)
        plan = orch.artifacts.get("hierarchy_plan", {})
        self.assertTrue(plan.get("enabled"), msg=str(plan))

    def test_benchmark_metrics_written_to_metircs(self):
        import agentic.orchestrator as orch_mod

        tmp = tempfile.mkdtemp(prefix="tier1_metircs_")
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        old_workspace = orch_mod.WORKSPACE_ROOT
        orch_mod.WORKSPACE_ROOT = tmp
        self.addCleanup(lambda: setattr(orch_mod, "WORKSPACE_ROOT", old_workspace))

        orch = BuildOrchestrator(name="metric_chip", desc="demo", llm=None)
        orch.state = orch.state.SUCCESS
        orch.artifacts["signoff_result"] = "PASS"
        orch.artifacts["metrics"] = {"chip_area_um2": 1234.5, "area": 321, "utilization": 42.0, "timing_tns": 0.0, "timing_wns": 0.1}
        orch.artifacts["sta_signoff"] = {"worst_setup": 0.1, "worst_hold": 0.05}
        orch.artifacts["power_signoff"] = {"total_power_w": 1e-3, "internal_power_w": 5e-4, "switching_power_w": 4e-4, "leakage_power_w": 1e-5, "irdrop_max_vpwr": 0.01, "irdrop_max_vgnd": 0.02}
        orch.artifacts["signoff"] = {"drc_violations": 0, "lvs_errors": 0, "antenna_violations": 0}
        orch.artifacts["coverage"] = {"line_pct": 90.0}
        orch.artifacts["formal_result"] = "PASS"
        orch.artifacts["lec_result"] = "PASS"
        orch._save_industry_benchmark_metrics()

        metircs_dir = os.path.join(tmp, "metircs", "metric_chip")
        self.assertTrue(os.path.isdir(metircs_dir))
        self.assertTrue(os.path.isfile(os.path.join(metircs_dir, "latest.json")))
        self.assertTrue(os.path.isfile(os.path.join(metircs_dir, "latest.md")))


if __name__ == "__main__":
    unittest.main()
