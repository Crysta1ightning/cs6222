"""Measurement accounting and result-separation tests, using synthetic data."""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

import run as runner

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("compare", HERE / "compare.py")
compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare)


class MeasurementTests(unittest.TestCase):
    def test_legacy_memory_only_runner_retains_defaults_and_separates_warmup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / "fake-stream"
            output = ("Array size = 100\nCopy: 1 0.1 0.1 0.1\nScale: 1 0.1 0.1 0.1\n"
                      "Add: 1 0.1 0.1 0.1\nTriad: 1 0.1 0.1 0.1\nSolution Validates\n")
            binary.write_text("#!/usr/bin/env python3\nprint(" + repr(output) + ")\n")
            binary.chmod(0o700)
            argv = ["run.py", "--mode", "memory-only", "--threads", "1", "--trials", "1",
                    "--binary", str(binary), "--output-dir", str(root / "results")]
            with patch.object(sys, "argv", argv), patch.object(runner, "command_output", return_value={}), \
                 patch.object(runner, "source_version", return_value={}):
                runner.main()
            data = json.loads(next((root / "results").glob("*.json")).read_text())
            self.assertEqual(data["controller_mode"], "normal")
            self.assertFalse(data["controller_enabled"])
            self.assertEqual(set(data["trials"][0]["windows"]), {"tool", "post_tool", "whole"})
            self.assertIsNone(data["trials"][0]["backup_payload_GB_per_second"])
            self.assertTrue(data["stream_processes"][0]["warmup"])
            self.assertLessEqual(data["stream_processes"][0]["end_ns"], data["trials"][0]["start_ns"])

    def test_missing_metrics_remain_unknown(self):
        server = runner.Server.__new__(runner.Server)
        server.get = lambda path: SimpleNamespace(text=f'{runner.COUNTERS[0]}{{rank="0"}} 123\n')
        result = server.metrics()
        self.assertEqual(result[runner.COUNTERS[0]], 123)
        self.assertIsNone(result[runner.COUNTERS[1]])
        delta = runner.counter_delta(result, result)
        self.assertEqual(delta[runner.COUNTERS[0]], 0)
        self.assertIsNone(delta[runner.COUNTERS[1]])

    def test_reset_counter_invalidates_measurement(self):
        with self.assertRaises(RuntimeError):
            runner.counter_delta(dict.fromkeys(runner.COUNTERS, 10), dict.fromkeys(runner.COUNTERS, 1))

    def test_post_tool_work_is_counted_in_whole(self):
        before = dict.fromkeys(runner.COUNTERS, 0)
        tool_end = dict.fromkeys(runner.COUNTERS, 10)
        drained = dict.fromkeys(runner.COUNTERS, 100)
        requests = [dict(start_ns=0, end_ns=900_000_000),
                    dict(start_ns=1_100_000_000, end_ns=1_900_000_000)]
        tool = runner.measurement_window(0, 1_000_000_000, before, tool_end, requests)
        post = runner.measurement_window(1_000_000_000, 2_000_000_000, tool_end, drained, requests)
        whole = runner.measurement_window(0, 2_000_000_000, before, drained, requests)
        self.assertEqual(tool["transfer_counter_deltas"][runner.COUNTERS[0]], 10)
        self.assertEqual(post["transfer_counter_deltas"][runner.COUNTERS[0]], 90)
        self.assertEqual(whole["transfer_counter_deltas"][runner.COUNTERS[0]], 100)
        self.assertEqual(whole["fully_contained_requests"], 2)
        self.assertEqual(whole["process_seconds"], 2)

    def test_plot_selects_window_and_preserves_missing(self):
        row = dict(backup_payload_GB_per_second=10, requests_per_second=2,
                   windows={"post_tool": dict(backup_payload_GB_per_second=30, requests_per_second=3)})
        run = dict(data=dict(mode="hicache", trials=[row]), window="post_tool")
        self.assertEqual(compare.values(run, "backup"), [30])
        self.assertEqual(compare.values(run, "Triad"), [None])
        run["window"] = "whole"
        self.assertEqual(compare.values(run, "backup"), [None])

    def test_summary_keeps_controller_modes_and_windows_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for mode in ("normal", "defer", "skip"):
                row = dict(trial=0, process_seconds=1, warnings=[], requests_per_second=2,
                           fully_contained_requests=2, backup_payload_GB_per_second=None,
                           windows={"post_tool": dict(process_seconds=2, requests_per_second=3,
                                      fully_contained_requests=6, backup_payload_GB_per_second=5)})
                data = dict(mode="hicache", workload="stream", hostname="test", threads=1,
                            cpu_affinity=[0], binary_sha256="hash", write_policy="write_through",
                            controller_enabled=True, controller_mode=mode, trials=[row])
                path = Path(tmp) / f"{mode}.json"
                path.write_text(json.dumps(data)); paths.append(str(path))
            result = subprocess.run([sys.executable, str(HERE / "summarize.py"), *paths],
                                    capture_output=True, text=True, check=True)
            for mode in ("normal", "defer", "skip"):
                self.assertIn(f"{mode} | tool | 1 | N/A | N/A", result.stdout)
                self.assertIn(f"{mode} | post_tool | 1 | N/A | 5.000", result.stdout)


if __name__ == "__main__":
    unittest.main()
