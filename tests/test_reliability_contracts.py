import sys
import contextlib
import unittest
from unittest import mock
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agentic.contracts import FailureClass, extract_json_object, infer_failure_class, validate_agent_payload
from agentic.orchestrator import BuildOrchestrator, BuildState


class ReliabilityContractsTest(unittest.TestCase):
    def _make_orchestrator(self) -> BuildOrchestrator:
        orch = BuildOrchestrator(
            name="demo",
            desc="demo design",
            llm=object(),
            verbose=False,
        )
        orch.logger = mock.Mock()
        orch.state = BuildState.VERIFICATION
        orch.artifacts["rtl_code"] = (
            "module demo(input logic clk, input logic rst_n, output logic y);\n"
            "assign y = clk;\n"
            "endmodule\n"
        )
        orch.artifacts["rtl_path"] = "/tmp/demo.v"
        return orch

    def test_extract_json_object_from_fenced_text(self):
        raw = 'noise\n```json\n{"class":"B","root_cause":"bad"}\n```\n'
        parsed = extract_json_object(raw)
        self.assertEqual(parsed, {"class": "B", "root_cause": "bad"})

    def test_validate_agent_payload_missing_keys(self):
        errors = validate_agent_payload({"class": "A"}, ["class", "root_cause"])
        self.assertEqual(errors, ["Missing required key 'root_cause'."])

    def test_infer_failure_class(self):
        cls = infer_failure_class(
            producer="llm_fixer",
            raw_output="This is not valid json",
            diagnostics=["Missing required key 'root_cause'."],
        )
        self.assertEqual(cls, FailureClass.LLM_FORMAT_ERROR)

    def test_transition_resets_retry_count(self):
        orch = self._make_orchestrator()
        orch.retry_count = 3
        orch.transition(BuildState.FORMAL_VERIFY)
        self.assertEqual(orch.retry_count, 0)

    def test_required_artifact_handoff_raises(self):
        orch = self._make_orchestrator()
        with self.assertRaises(RuntimeError):
            orch._consume_handoff("tb_regen_context", consumer="VERIFICATION", required=True)

    def test_parse_structured_agent_json_rejects_prose(self):
        orch = self._make_orchestrator()
        result = orch._parse_structured_agent_json(
            agent_name="VerificationAnalyst",
            raw_output="The issue is likely in the RTL.",
            required_keys=["class", "root_cause"],
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_class, FailureClass.LLM_FORMAT_ERROR)

    def test_validate_rtl_candidate_rejects_hierarchy_rewrite(self):
        orch = self._make_orchestrator()
        previous = (
            "module demo(input logic clk, output logic y); assign y = clk; endmodule\n"
            "module demo_helper(input logic a, output logic b); assign b = a; endmodule\n"
        )
        candidate = "module demo(input logic clk, output logic y); assign y = clk; endmodule\n"
        issues = orch._validate_rtl_candidate(candidate, previous)
        self.assertTrue(any("module inventory" in issue for issue in issues))

    def test_validate_tb_candidate_requires_waveform_and_name(self):
        orch = self._make_orchestrator()
        issues = orch._validate_tb_candidate("module wrong_tb; initial begin $finish; end endmodule")
        self.assertTrue(any("TB module name" in issue for issue in issues))
        self.assertTrue(any("dumpfile" in issue for issue in issues))

    def test_tb_regen_context_bypasses_golden_tb(self):
        orch = self._make_orchestrator()
        orch.artifacts["golden_tb"] = "module counter_tb; endmodule"
        orch.artifacts["golden_template"] = "counter"
        orch._set_artifact(
            "tb_regen_context",
            '{"issue":"pin mismatch"}',
            producer="test",
            consumer="VERIFICATION",
            required=True,
            blocking=True,
        )
        generated_tb = """module demo_tb;
initial begin
    $dumpfile("demo_wave.vcd");
    $dumpvars(0, demo_tb);
    $display("TEST FAILED");
    $display("TEST PASSED");
    $finish;
end
endmodule
"""

        with mock.patch("agentic.orchestrator.get_testbench_agent", return_value=object()), \
             mock.patch("agentic.orchestrator.Task", side_effect=lambda **kwargs: kwargs), \
             mock.patch.object(orch, "_kickoff_with_timeout", return_value=generated_tb) as kickoff, \
             mock.patch("agentic.orchestrator.console.status", return_value=contextlib.nullcontext()), \
             mock.patch("agentic.orchestrator.write_verilog", return_value="/tmp/demo_tb.v"), \
             mock.patch("agentic.orchestrator.run_tb_static_contract_check", side_effect=RuntimeError("stop_after_generation")):
            with self.assertRaisesRegex(RuntimeError, "stop_after_generation"):
                orch.do_verification()
        self.assertTrue(kickoff.called)


if __name__ == "__main__":
    unittest.main()
