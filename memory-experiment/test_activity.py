"""CPU-only protocol/runner tests; no GPU, inference server, or STREAM load."""

import importlib.util
import os
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock

from activity import ActivityClient, run_stream, connect_bounded

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parents[1] / "sglang/python/sglang/srt/mem_cache/stream_activity.py"
spec = importlib.util.spec_from_file_location("stream_activity", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
ActivityController = module.ActivityController


class BusyQueue(unittest.TestCase):
    def test_transient_full_queue_recovers_before_sending(self):
        channel = Mock()
        channel.connect.side_effect = [BlockingIOError(11, 'busy'), BlockingIOError(11, 'busy'), None]
        record = {}
        connect_bounded(channel, '/unused', 1, record)
        self.assertEqual(record['connect_retries'], 2)
        channel.sendall.assert_not_called()

    def test_full_queue_has_bounded_wait(self):
        channel = Mock()
        channel.connect.side_effect = BlockingIOError(11, 'busy')
        start = time.monotonic()
        with self.assertRaises(TimeoutError):
            connect_bounded(channel, '/unused', 0.02, {})
        self.assertLess(time.monotonic() - start, 0.5)
        channel.sendall.assert_not_called()


class Decisions(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.c = ActivityController("defer", clock=lambda: self.now, max_tokens=100, max_age=2, lease=5)
        self.addCleanup(self.c.close)

    def event(self, name, tool="A"):
        return self.c.handle(dict(event=name, tool_id=tool))

    def pending(self):
        self.c.observe_pending(20, 200, 1)

    def reason(self, **kwargs):
        return self.c.flush_reason(free_tokens=500, reserve_tokens=50, **kwargs)

    def test_normal_is_unchanged(self):
        self.c.mode = "normal"
        self.event("TOOL_START")
        self.pending()
        self.assertEqual(self.reason(), "normal")
        self.assertTrue(self.c.allow_optional(20))

    def test_defer_until_last_overlapping_tool_ends(self):
        self.event("TOOL_START")
        self.event("TOOL_START", "B")
        self.pending()
        self.assertIsNone(self.reason())
        self.event("TOOL_END")
        self.assertIsNone(self.reason())
        self.event("TOOL_END", "B")
        self.assertEqual(self.reason(), "normal")

    def test_skip_has_no_backlog(self):
        self.c.mode = "skip"
        self.event("TOOL_START")
        self.assertFalse(self.c.allow_optional(20))
        self.assertEqual(self.c.snapshot()["pending_ops"], 0)
        self.event("TOOL_END")
        self.assertTrue(self.c.allow_optional(20))

    def test_pending_cap_age_and_pressure_overrides(self):
        self.event("TOOL_START")
        self.pending()
        self.assertEqual(self.reason(force="allocation_critical"), "allocation_critical")
        self.assertEqual(self.c.flush_reason(free_tokens=1, reserve_tokens=50), "gpu_pressure")
        self.now = 2
        self.assertEqual(self.reason(), "maximum_age")
        self.c.observe_pending(100, 1000, 1)
        self.assertEqual(self.reason(), "pending_limit")

    def test_missing_end_expires_and_late_start_cannot_resurrect(self):
        self.event("TOOL_START")
        self.pending()
        self.now = 6
        self.assertEqual(self.reason(), "normal")
        self.assertEqual(self.c.snapshot()["active_tool_ids"], [])
        with self.assertRaises(ValueError):
            self.event("TOOL_START")

    def test_heartbeat_and_duplicate_events(self):
        self.event("TOOL_START")
        self.event("TOOL_START")
        self.assertEqual(len(self.c.active), 1)
        self.now = 4
        self.event("TOOL_HEARTBEAT")
        self.now = 6
        self.assertEqual(len(self.c.snapshot()["active_tool_ids"]), 1)
        self.event("TOOL_END")
        self.event("TOOL_END")
        self.assertEqual(self.c.counters["TOOL_END"], 1)

    def test_submission_and_completion_accounting(self):
        self.pending()
        self.c.submitted(200, 20, "normal")
        self.assertEqual(self.c.snapshot()["in_flight_batches"], 1)
        self.assertEqual(self.c.snapshot()["pending_ops"], 0)
        self.c.completed(SimpleNamespace(num_bytes=200, num_tokens=20))
        self.assertEqual(self.c.snapshot()["in_flight_batches"], 0)

    def test_required_copy_does_not_erase_optional_pending_state(self):
        self.event("TOOL_START")
        self.pending()
        self.c.required_override("required_retraction", 10, 100)
        self.c.submitted(100, 10, "required_retraction", clear_pending=False)
        self.assertEqual(self.c.snapshot()["pending_tokens"], 20)
        self.assertEqual(self.c.counters["pressure_overrides"], 1)

    def test_bounds_and_unknown_events(self):
        self.c.max_tools = 1
        self.event("TOOL_START")
        with self.assertRaises(ValueError):
            self.event("TOOL_START", "B")
        with self.assertRaises(ValueError):
            self.event("BANDWIDTH_ESTIMATE")


class Protocol(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="stream-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.c = ActivityController("defer", lease=0.3, log_path=self.root / "events.jsonl")
        self.addCleanup(self.c.close)
        self.c.serve(self.root / "control.sock")
        self.client = ActivityClient(self.root / "control.sock", "defer", timeout=0.5)

    def binary(self, body):
        path = self.root / "fake-stream"
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o700)
        return path

    def test_acknowledged_subprocess_lifetime(self):
        record = {}
        result = run_stream(self.binary("sleep 0.15; echo done"), os.environ,
                            client=self.client, record=record)
        self.assertEqual(result.returncode, 0)
        self.assertLessEqual(record["start_ack"]["ack_monotonic_ns"], record["start_ns"])
        self.assertLessEqual(record["end_ns"], record["end_ack"]["ack_monotonic_ns"])
        self.assertEqual(self.client.rpc("STATUS")["active_tool_ids"], [])
        self.assertTrue(any(e["request"]["event"] == "TOOL_HEARTBEAT" for e in self.client.events))

    def test_timeout_kills_process_group_and_ends_tool(self):
        record = {}
        with self.assertRaises(subprocess.TimeoutExpired):
            run_stream(self.binary("sleep 10"), os.environ, client=self.client, timeout=0.1, record=record)
        self.assertIsNotNone(record["returncode"])
        self.assertEqual(self.client.rpc("STATUS")["active_tool_ids"], [])

    def test_failed_exit_and_failed_spawn_end_tool(self):
        result = run_stream(self.binary("exit 7"), os.environ, client=self.client)
        self.assertEqual(result.returncode, 7)
        with self.assertRaises(FileNotFoundError):
            run_stream(self.root / "missing", os.environ, client=self.client)
        self.assertEqual(self.client.rpc("STATUS")["active_tool_ids"], [])

    def test_lost_start_ack_does_not_launch_process(self):
        original = self.client.rpc
        def lose_ack(event, **fields):
            reply = original(event, **fields)
            if event == "TOOL_START":
                raise TimeoutError("simulated lost acknowledgment")
            return reply
        with patch.object(self.client, "rpc", side_effect=lose_ack):
            with self.assertRaises(TimeoutError):
                run_stream(self.binary(f"touch {self.root / 'launched'}"), os.environ, client=self.client)
        self.assertFalse((self.root / "launched").exists())
        self.assertEqual(original("STATUS")["active_tool_ids"], [])

    def test_lost_heartbeat_aborts_and_cleans_up(self):
        original = self.client.rpc
        def fail_heartbeat(event, **fields):
            if event == "TOOL_HEARTBEAT":
                raise TimeoutError("heartbeat lost")
            return original(event, **fields)
        with patch.object(self.client, "rpc", side_effect=fail_heartbeat):
            with self.assertRaises(TimeoutError):
                run_stream(self.binary("sleep 10"), os.environ, client=self.client)
        self.assertEqual(original("STATUS")["active_tool_ids"], [])

    def test_client_crash_lease_recovery(self):
        self.client.rpc("TOOL_START", tool_id="crashed-client")
        time.sleep(0.4)
        self.assertEqual(self.client.rpc("STATUS")["active_tool_ids"], [])

    def test_malformed_frame_does_not_kill_listener(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(1)
            connection.connect(str(self.root / "control.sock"))
            connection.sendall(b"[]\n")
            self.assertIn(b'"ok": false', connection.recv(1024))
        self.assertTrue(self.client.rpc("STATUS")["ok"])


if __name__ == "__main__":
    unittest.main()
