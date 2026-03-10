import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agentic.tools import vlsi_tools


class TempPathRewriteTests(unittest.TestCase):
    def test_rewrite_temp_paths_replaces_absolute_and_basename_forms(self):
        original = "/workspace/designs/counter8/src/counter8.v"
        staged = "/tmp/tmpabc/counter8.v"
        staged_map = {original: staged}

        rewritten_abs = vlsi_tools._rewrite_temp_paths(
            f"%Error: {staged}:12:3: syntax error",
            staged_map,
        )
        rewritten_base = vlsi_tools._rewrite_temp_paths(
            "%Error: counter8.v:12:3: syntax error",
            staged_map,
        )

        self.assertIn(original, rewritten_abs)
        self.assertIn(original, rewritten_base)
        self.assertNotIn("/tmp/tmpabc", rewritten_abs)
        self.assertNotIn("/tmp/tmpabc", rewritten_base)

    def test_rewrite_result_paths_sanitizes_all_text_fields(self):
        original = "/workspace/designs/counter8/src/counter8.v"
        staged = "/tmp/tmpxyz/counter8.v"
        staged_map = {original: staged}
        payload = {
            "stdout": f"warning in {staged}:4:1",
            "stderr": "counter8.v:7:2: syntax error",
            "diagnostics": [
                f"%Error: {staged}:9:9: bad token",
                "counter8.v:10:11: width warning",
            ],
        }

        sanitized = vlsi_tools._rewrite_result_paths(payload, staged_map)

        for text in [sanitized["stdout"], sanitized["stderr"], *sanitized["diagnostics"]]:
            self.assertIn(original, text)
            self.assertNotIn("/tmp/tmpxyz", text)

    def test_run_syntax_check_returns_original_path_in_legacy_message(self):
        with tempfile.TemporaryDirectory() as src_dir:
            rtl_path = os.path.join(src_dir, "counter8.v")
            with open(rtl_path, "w", encoding="utf-8") as f:
                f.write("module counter8; endmodule\n")

            seen = {}

            def fake_run(cmd, capture_output, text, timeout, cwd=None):
                seen["cwd"] = cwd
                return subprocess.CompletedProcess(
                    cmd,
                    1,
                    stdout="",
                    stderr=f"%Error: {cwd}/counter8.v:12:3: syntax error\n",
                )

            with mock.patch("agentic.tools.vlsi_tools.subprocess.run", side_effect=fake_run):
                ok, message = vlsi_tools.run_syntax_check(rtl_path)

        self.assertFalse(ok)
        self.assertIn(rtl_path, message)
        self.assertNotIn(seen["cwd"], message)


if __name__ == "__main__":
    unittest.main()
