"""CPU-only checks of burst signals and fixed inference work."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys
import time
import concurrent.futures

import experiment
from deferred_backup import registry


class BurstTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.state = str(self.root / "tools.bin")

    def test_signal_is_clear_during_gap_and_set_for_each_child(self):
        args = SimpleNamespace(stream_rounds=3, stream_gap=2)
        code = "import sys; sys.path.insert(0, %r); from deferred_backup import ToolState; s=ToolState(%r); assert s.snapshot()[1] == 1; s.close()" % (str(Path(__file__).resolve().parent), self.state)
        gaps = []

        def gap(seconds):
            with registry(self.state) as records:
                self.assertEqual(records, {})
            gaps.append(seconds)

        with patch.object(experiment, "tool_command", return_value=[sys.executable, "-c", code]), \
             patch.object(experiment, "metrics", return_value=dict.fromkeys(experiment.METRICS, 0)), \
             patch.object(experiment, "tool_summary", return_value={"validated": True}), \
             patch.object(experiment, "time", SimpleNamespace(monotonic_ns=time.monotonic_ns, sleep=gap)):
            windows = experiment.execute_tools(args, self.root, self.state, None)
        self.assertEqual(gaps, [2, 2])
        self.assertEqual(len(windows), 3)
        self.assertTrue(all(window["summary"]["validated"] for window in windows))
        with registry(self.state) as records:
            self.assertEqual(records, {})

    def test_failed_child_clears_tool_signal(self):
        args = SimpleNamespace(stream_rounds=2, stream_gap=2)
        with patch.object(experiment, "tool_command", return_value=[sys.executable, "-c", "raise SystemExit(1)"]), \
             patch.object(experiment, "metrics", return_value=dict.fromkeys(experiment.METRICS, 0)):
            with self.assertRaises(RuntimeError):
                experiment.execute_tools(args, self.root, self.state, None)
        with registry(self.state) as records:
            self.assertEqual(records, {})

    def test_generation_requests_fixed_128_output_tokens(self):
        import requests
        response = SimpleNamespace(raise_for_status=lambda: None,
                                   json=lambda: {"meta_info": {"completion_tokens": 128}})
        with patch.object(requests, "post", return_value=response) as post:
            record = experiment.generate([1] * 512, output_tokens=128)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["sampling_params"]["max_new_tokens"], 128)
        self.assertTrue(payload["sampling_params"]["ignore_eos"])
        self.assertEqual(record["prompt_tokens"], 512)

    def test_stream_off_skips_tools_and_completes_identical_inference_work(self):
        args = SimpleNamespace(requests=3, followup=0, output_tokens=16, tool="stream", nsys=False)
        def generated(ids, output_tokens):
            self.assertEqual(output_tokens, 16)
            now = time.monotonic_ns()
            return {"start_ns": now, "end_ns": now + 1, "prompt_tokens": len(ids)}
        with patch.object(experiment, "settle", return_value=dict.fromkeys(experiment.METRICS, 0)), \
             patch.object(experiment, "generate", side_effect=generated) as generate, \
             patch.object(experiment, "execute_tools") as tools:
            result = experiment.measure_condition(args, "deferred", self.root, [[1], [2], [3]],
                                                  self.state, self.root / "policy.jsonl", stream_enabled=False)
        tools.assert_not_called()
        self.assertEqual(generate.call_count, 3)
        self.assertFalse(result["stream_enabled"])
        self.assertEqual(result["tool_windows"], [])
        self.assertEqual(result["tool_seconds"], 0)
        self.assertEqual(result["completed_agent_b_requests"], 3)
        self.assertEqual(result["defer_events"], 0)

    def test_request_boundary_blocks_until_launch_and_rejects_overrunning_round(self):
        schedule = experiment.RequestSchedule([2, 4], timeout=2)
        schedule.before_request(1)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(schedule.before_request, 2)
            self.assertTrue(schedule.reached[0].wait(2))
            self.assertFalse(future.done())
            trigger = schedule.wait_for_request(0)
            self.assertEqual(trigger["completed_requests_at_trigger"], 1)
            schedule.started[0].set()
            future.result(timeout=2)
        with self.assertRaisesRegex(RuntimeError, "still running"):
            schedule.before_request(4)

    def test_abort_releases_both_sides_of_handshake(self):
        schedule = experiment.RequestSchedule([1], timeout=2)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            request = pool.submit(schedule.before_request, 1)
            self.assertTrue(schedule.reached[0].wait(2))
            schedule.abort(ValueError("failed child"))
            with self.assertRaisesRegex(RuntimeError, "aborted"):
                request.result(timeout=2)
            with self.assertRaisesRegex(RuntimeError, "aborted"):
                schedule.wait_for_request(0)

    def test_request_aligned_measurement_launches_after_exact_completed_count(self):
        args = SimpleNamespace(requests=5, followup=0, output_tokens=16, tool="stream", nsys=False,
                               tool_schedule="requests", stream_at_requests=[3], stream_rounds=1,
                               stream_gap=0)
        completed = []
        original_popen = experiment.subprocess.Popen

        def generated(ids, output_tokens):
            now = time.monotonic_ns()
            completed.append(ids)
            return {"start_ns": now, "end_ns": now + 1, "prompt_tokens": len(ids)}

        def launch(*args, **kwargs):
            self.assertEqual(len(completed), 2)
            return original_popen(*args, **kwargs)

        with patch.object(experiment, "settle", return_value=dict.fromkeys(experiment.METRICS, 0)), \
             patch.object(experiment, "generate", side_effect=generated), \
             patch.object(experiment, "tool_command", return_value=[sys.executable, "-c", "pass"]), \
             patch.object(experiment.subprocess, "Popen", side_effect=launch), \
             patch.object(experiment, "metrics", return_value=dict.fromkeys(experiment.METRICS, 0)), \
             patch.object(experiment, "tool_summary", return_value={"validated": True,
                 "kernels": {"Triad": {"average_GB_per_second": 1}}}):
            result = experiment.measure_condition(args, "default", self.root,
                [[i] for i in range(5)], self.state, self.root / "policy.jsonl")
        window = result["tool_windows"][0]
        self.assertEqual(window["trigger_request"], 3)
        self.assertGreaterEqual(window["trigger_request_start_ns"], window["process_launched_ns"])
        self.assertEqual(result["completed_agent_b_requests"], 5)

    def test_schedule_validates_targets_and_disallows_wall_clock_gaps(self):
        args = SimpleNamespace(tool_schedule="requests", stream_at_requests=None,
                               stream_rounds=3, requests=12, stream_gap=0)
        experiment.configure_schedule(args)
        self.assertEqual(args.stream_at_requests, [1, 5, 9])
        for targets in ("1,1,9", "1,5,13", "0,5,9", "1,5"):
            args.stream_at_requests = targets
            with self.assertRaises(ValueError):
                experiment.configure_schedule(args)
        args.stream_at_requests = None
        args.stream_gap = 2
        with self.assertRaisesRegex(ValueError, "omit --stream-gap"):
            experiment.configure_schedule(args)


if __name__ == "__main__":
    unittest.main()
