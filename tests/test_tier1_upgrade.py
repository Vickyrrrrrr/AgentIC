import os
import re
import sys
import shutil
import tempfile
import textwrap
import unittest
from unittest.mock import patch

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


class CoverageAdapterTests(unittest.TestCase):
    def test_detect_tb_style(self):
        self.assertEqual("sv_class_based", vlsi_tools.detect_tb_style("class Driver; endclass"))
        self.assertEqual("classic_verilog", vlsi_tools.detect_tb_style("module tb; initial begin end endmodule"))

    def test_coverage_never_returns_empty_dict_on_missing_files(self):
        tmp = tempfile.mkdtemp(prefix="tier1_cov_missing_")
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        original = vlsi_tools.OPENLANE_ROOT
        vlsi_tools.OPENLANE_ROOT = tmp
        self.addCleanup(lambda: setattr(vlsi_tools, "OPENLANE_ROOT", original))

        passed, output, cov = vlsi_tools.run_simulation_with_coverage("chip_missing", backend="auto")
        self.assertFalse(passed)
        self.assertIsInstance(cov, dict)
        self.assertNotEqual({}, cov)
        self.assertTrue(cov.get("infra_failure"))

    def test_iverilog_backend_rejects_class_sv_tb(self):
        tmp = tempfile.mkdtemp(prefix="tier1_cov_ivl_")
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        original = vlsi_tools.OPENLANE_ROOT
        vlsi_tools.OPENLANE_ROOT = tmp
        self.addCleanup(lambda: setattr(vlsi_tools, "OPENLANE_ROOT", original))

        src = os.path.join(tmp, "designs", "chip", "src")
        os.makedirs(src, exist_ok=True)
        rtl = os.path.join(src, "chip.v")
        tb = os.path.join(src, "chip_tb.v")
        with open(rtl, "w") as f:
            f.write("module chip(input logic clk, output logic y); assign y = clk; endmodule\n")
        with open(tb, "w") as f:
            f.write("interface chip_if; logic clk; endinterface\nclass Driver; virtual chip_if vif; endclass\nmodule chip_tb; endmodule\n")

        class CompileFail:
            returncode = 1
            stdout = ""
            stderr = "syntax error: unsupported class item"

        with patch("agentic.tools.vlsi_tools.subprocess.run", return_value=CompileFail()):
            passed, _, cov = vlsi_tools.run_simulation_with_coverage(
                "chip",
                backend="iverilog",
                fallback_policy="fail_closed",
                profile="balanced",
            )

        self.assertFalse(passed)
        self.assertTrue(cov.get("infra_failure"))
        self.assertEqual("unsupported_tb_style", cov.get("error_kind"))
        self.assertEqual("iverilog", cov.get("backend"))

    def test_auto_backend_fallback_oss_to_iverilog(self):
        tmp = tempfile.mkdtemp(prefix="tier1_cov_fallback_")
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        original = vlsi_tools.OPENLANE_ROOT
        vlsi_tools.OPENLANE_ROOT = tmp
        self.addCleanup(lambda: setattr(vlsi_tools, "OPENLANE_ROOT", original))

        src = os.path.join(tmp, "designs", "chip", "src")
        os.makedirs(src, exist_ok=True)
        with open(os.path.join(src, "chip.v"), "w") as f:
            f.write("module chip(input logic clk, output logic y); assign y = clk; endmodule\n")
        with open(os.path.join(src, "chip_tb.v"), "w") as f:
            f.write("class Driver; endclass\nmodule chip_tb; initial $display(\"TEST PASSED\"); endmodule\n")

        primary_fail = (
            False,
            "primary fail",
            {
                "ok": False,
                "backend": "verilator",
                "coverage_mode": "full_oss",
                "infra_failure": True,
                "error_kind": "compile_error",
                "diagnostics": ["compile fail"],
                "line_pct": 0.0,
                "branch_pct": 0.0,
                "toggle_pct": 0.0,
                "functional_pct": 0.0,
                "assertion_pct": 0.0,
                "signals_toggled": 0,
                "total_signals": 0,
                "report_path": "",
                "raw_diag_path": "",
            },
        )
        fallback_ok = (
            True,
            "fallback ok",
            {
                "ok": True,
                "backend": "iverilog",
                "coverage_mode": "fallback_oss",
                "infra_failure": False,
                "error_kind": "",
                "diagnostics": [],
                "line_pct": 86.0,
                "branch_pct": 81.0,
                "toggle_pct": 76.0,
                "functional_pct": 82.0,
                "assertion_pct": 100.0,
                "signals_toggled": 4,
                "total_signals": 5,
                "report_path": "diag",
                "raw_diag_path": "diag",
            },
        )

        with patch("agentic.tools.vlsi_tools.run_verilator_coverage", return_value=primary_fail) as ver_mock, patch(
            "agentic.tools.vlsi_tools.run_iverilog_coverage", return_value=fallback_ok
        ) as ivl_mock:
            passed, _, cov = vlsi_tools.run_simulation_with_coverage(
                "chip", backend="auto", fallback_policy="fallback_oss", profile="balanced"
            )

        self.assertTrue(passed)
        self.assertEqual("iverilog", cov.get("backend"))
        self.assertEqual("fallback_oss", cov.get("coverage_mode"))
        self.assertFalse(cov.get("infra_failure"))
        self.assertTrue(ver_mock.called)
        self.assertTrue(ivl_mock.called)


class FormalConversionTests(unittest.TestCase):
    def test_sva_converter_removes_temporal_tokens_for_sby(self):
        sva = textwrap.dedent(
            """
            module my_chip_sva (
                input logic clk,
                input logic rst_n,
                input logic en,
                output logic [7:0] cnt_out
            );
                property p_reset_assert;
                    @(posedge clk) !rst_n |-> ##1 cnt_out == 8'd0;
                endproperty
                assert property (p_reset_assert);

                property p_increment;
                    @(posedge clk) disable iff (!rst_n) en |=> cnt_out == $past(cnt_out) + 1;
                endproperty
                assert property (p_increment);

                property p_toggle_seq;
                    @(posedge clk) !en ##1 en;
                endproperty
                assert property (p_toggle_seq);
            endmodule
            """
        )

        converted = vlsi_tools.convert_sva_to_yosys(sva, "my_chip")
        self.assertIsNotNone(converted)
        self.assertNotIn("|->", converted)
        self.assertNotIn("|=>", converted)
        self.assertIsNone(re.search(r"##\s*\d+", converted))
        self.assertIn("reg [7:0] past_cnt_out;", converted)

        ok, report = vlsi_tools.validate_yosys_sby_check(converted)
        self.assertTrue(ok, msg=str(report))

    def test_sby_preflight_rejects_residual_temporal_syntax(self):
        bad_code = textwrap.dedent(
            """
            module bad_sby(input logic clk, input logic a, input logic b);
                always @(posedge clk) begin
                    assert(a |-> ##1 b);
                end
            endmodule
            """
        )
        ok, report = vlsi_tools.validate_yosys_sby_check(bad_code)
        self.assertFalse(ok)
        issue_codes = {issue.get("issue_code") for issue in report.get("issues", [])}
        self.assertIn("residual_temporal_implication", issue_codes)
        self.assertIn("residual_temporal_delay", issue_codes)


class TestbenchGateTests(unittest.TestCase):
    def test_tb_static_gate_rejects_non_virtual_interface_usage(self):
        tb = textwrap.dedent(
            """
            interface dut_if;
              logic clk;
              logic rst_n;
              logic en;
              logic [7:0] q;
            endinterface

            class Transaction; endclass
            class Driver;
              dut_if vif;
              function new(dut_if vif);
                this.vif = vif;
              endfunction
            endclass
            class Monitor; endclass
            class Scoreboard; endclass

            module dut_tb;
              initial begin
                $display("TEST PASSED");
                $display("TEST FAILED");
              end
            endmodule
            """
        )
        ok, report = vlsi_tools.run_tb_static_contract_check(tb, "SV_MODULAR")
        self.assertFalse(ok)
        codes = set(report.get("issue_codes", []))
        self.assertIn("non_virtual_interface_handle", codes)
        self.assertIn("constructor_interface_type_error", codes)

    def test_tb_repair_patches_interface_and_covergroup_patterns(self):
        tb = textwrap.dedent(
            """
            interface dut_if;
              logic clk;
              logic rst_n;
              logic en;
              logic [7:0] q;
            endinterface

            class Transaction; endclass
            class Driver;
              dut_if vif;
              function new(dut_if vif);
                this.vif = vif;
              endfunction
            endclass

            covergroup cv_q;
              coverpoint q { bins all = {[0:255]}; }
            endgroup

            class Scoreboard;
              cv_q cov;
              function new();
                cov = new;
              endfunction
              function void sample();
                cov.sample();
              endfunction
            endclass
            """
        )
        repaired = vlsi_tools.repair_tb_for_verilator(tb, {"issue_categories": ["interface_typing_error", "covergroup_scope_error"]})
        self.assertIn("virtual dut_if vif;", repaired)
        self.assertIn("function new(virtual dut_if vif);", repaired)
        self.assertNotIn("covergroup cv_q", repaired)
        self.assertNotIn("cov.sample()", repaired)

    def test_tb_compile_gate_normalizes_diagnostics(self):
        tmpdir = tempfile.mkdtemp(prefix="tier1_tb_compile_")
        self.addCleanup(lambda: shutil.rmtree(tmpdir, ignore_errors=True))
        rtl_path = os.path.join(tmpdir, "dut.v")
        tb_path = os.path.join(tmpdir, "dut_tb.v")
        with open(rtl_path, "w") as f:
            f.write("module dut(input logic clk, output logic y); assign y = clk; endmodule\n")
        with open(tb_path, "w") as f:
            f.write("module dut_tb; dut_if vif; endmodule\n")

        fake_stderr = textwrap.dedent(
            """
            %Error: /tmp/dut_tb.v:34:5: syntax error, unexpected IDENTIFIER
            %Error: /tmp/dut_tb.v:37:30: syntax error, unexpected IDENTIFIER, expecting ')'
            %Error: Internal Error: parser confused in class Driver
            """
        )

        class DummyResult:
            returncode = 1
            stdout = ""
            stderr = fake_stderr

        with patch("agentic.tools.vlsi_tools.subprocess.run", return_value=DummyResult()):
            ok, report = vlsi_tools.run_tb_compile_gate("dut", tb_path, rtl_path)

        self.assertFalse(ok)
        cats = set(report.get("issue_categories", []))
        self.assertIn("syntax_error", cats)
        self.assertIn("interface_typing_error", cats)
        self.assertIn("parser_internal_state_error", cats)
        self.assertTrue(report.get("fingerprint"))

    def test_coverpoint_hierarchical_expression_not_flagged(self):
        tb = textwrap.dedent(
            """
            interface dut_if;
              logic clk;
              logic en;
            endinterface
            class Transaction; endclass
            class Driver; endclass
            class Monitor; endclass
            class Scoreboard; endclass
            covergroup cg;
              coverpoint vif.en { bins all[] = {0,1}; }
            endgroup
            module dut_tb;
              initial begin
                $display("TEST PASSED");
                $display("TEST FAILED");
              end
            endmodule
            """
        )
        ok, report = vlsi_tools.run_tb_static_contract_check(tb, "SV_MODULAR")
        codes = set(report.get("issue_codes", []))
        self.assertNotIn("covergroup_scope_error", codes)


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

    def test_extract_module_ports_ignores_comments(self):
        orch = BuildOrchestrator(name="ports_demo", desc="demo", llm=None)
        rtl = textwrap.dedent(
            """
            module ports_demo (
                input logic clk,
                input logic rst_n, // asynchronous reset
                output logic [7:0] count // output assignments are below
            );
            // External output assignments comment should not become a port name.
            assign count = 8'h00;
            endmodule
            """
        )
        ports = orch._extract_module_ports(rtl)
        names = [p["name"] for p in ports]
        self.assertEqual(["clk", "rst_n", "count"], names)
        self.assertNotIn("assignments", names)

    def test_coverage_infra_failure_fail_closed_no_tb_regen(self):
        orch = BuildOrchestrator(
            name="cov_fail_demo",
            desc="demo",
            llm=None,
            strict_gates=True,
            coverage_backend="auto",
            coverage_fallback_policy="fail_closed",
            coverage_profile="balanced",
        )
        orch.state = orch.state.COVERAGE_CHECK
        orch.artifacts["root"] = tempfile.mkdtemp(prefix="tier1_cov_fail_")
        self.addCleanup(lambda: shutil.rmtree(orch.artifacts["root"], ignore_errors=True))
        orch.setup_logger()
        orch.artifacts["rtl_code"] = "module cov_fail_demo(input logic clk, output logic y); assign y = clk; endmodule\n"
        tb_path = os.path.join(orch.artifacts["root"], "cov_fail_demo_tb.v")
        with open(tb_path, "w") as f:
            f.write("module cov_fail_demo_tb; initial $display(\"TEST PASSED\"); endmodule\n")
        orch.artifacts["tb_path"] = tb_path

        cov_result = {
            "ok": False,
            "backend": "verilator",
            "coverage_mode": "full_oss",
            "infra_failure": True,
            "error_kind": "tool_missing",
            "diagnostics": ["verilator missing"],
            "line_pct": 0.0,
            "branch_pct": 0.0,
            "toggle_pct": 0.0,
            "functional_pct": 0.0,
            "assertion_pct": 0.0,
            "signals_toggled": 0,
            "total_signals": 0,
            "report_path": "",
            "raw_diag_path": "",
        }

        with patch("agentic.orchestrator.run_simulation_with_coverage", return_value=(False, "infra fail", cov_result)):
            orch.do_coverage_check()

        self.assertEqual("FAIL", orch.state.name)
        self.assertEqual(0, orch.retry_count)
        self.assertEqual(1, orch.artifacts.get("coverage_attempt_count"))


if __name__ == "__main__":
    unittest.main()
