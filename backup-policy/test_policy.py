"""CPU-only checks of tool lifetime, overrides and the actual submission method."""
import ast
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import patch

from deferred_backup import BackupGate, StateBusy, ToolState, active_tool, registry, REAP_INTERVAL


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = str(Path(self.directory.name) / "tools.bin")
        self.now = 10.0
        self.gate = BackupGate(self.path, max_delay=1, max_bytes=100,
                               clock=lambda: self.now)
        self.addCleanup(self.gate.close)

    def test_overlapping_tools_and_resume(self):
        with active_tool(self.path):
            with active_tool(self.path):
                with registry(self.path) as records:
                    self.assertEqual(len(records), 2)
                self.assertFalse(self.gate.decide(50))
            self.assertFalse(self.gate.decide(50))
        self.assertTrue(self.gate.decide(50))

    def test_idle_control_submits_while_actual_tool_runs(self):
        log = str(Path(self.directory.name) / "idle-log.jsonl")
        idle_gate = BackupGate(self.path + "-always-idle", log_path=log)
        self.addCleanup(idle_gate.close)
        with active_tool(self.path):
            self.assertTrue(idle_gate.decide(50))
        event = json.loads(Path(log).read_text())
        self.assertEqual(event["event"], "submit")
        self.assertEqual(event["reason"], "tools-idle")
        self.assertEqual(event["active_tools"], 0)

    def test_deadline_and_limits(self):
        with active_tool(self.path):
            self.assertFalse(self.gate.decide(50))
            self.now += 1.01
            self.assertTrue(self.gate.decide(50))
            self.assertTrue(self.gate.decide(100))
            self.assertTrue(self.gate.decide(50, force=True))

    def test_uncapped_deferral_waits_for_tool_end_but_preserves_forced_progress(self):
        self.gate.max_delay = self.gate.max_bytes = 0
        with active_tool(self.path):
            self.assertFalse(self.gate.decide(1))
            self.now += 1000
            self.assertFalse(self.gate.decide(100 * 1024**3, enqueue=True))
            self.assertTrue(self.gate.decide(100 * 1024**3, force=True))
            self.assertFalse(self.gate.decide(1))
        self.assertTrue(self.gate.decide(1))

    def test_limits_can_be_disabled_independently(self):
        with active_tool(self.path):
            self.gate.max_delay = 0
            self.assertFalse(self.gate.decide(50))
            self.now += 1000
            self.assertFalse(self.gate.decide(50))
            self.assertTrue(self.gate.decide(100))
            self.gate.max_delay, self.gate.max_bytes = 1, 0
            self.assertFalse(self.gate.decide(10000))
            self.now += 1.01
            self.assertTrue(self.gate.decide(10000))

    def test_zero_limit_constructor_and_invalid_limits(self):
        gate = BackupGate(self.path, max_delay=0, max_bytes=0)
        self.addCleanup(gate.close)
        for delay, size in ((-1, 0), (0, -1), (float('inf'), 0), (float('nan'), 0)):
            with self.assertRaises(ValueError):
                BackupGate(self.path, max_delay=delay, max_bytes=size)

    def test_crashed_tool_removed(self):
        code = "from deferred_backup import active_tool; import os; c=active_tool(%r); c.__enter__(); os._exit(0)" % self.path
        subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parent, check=True)
        with registry(self.path) as records:
            self.assertEqual(records, {})
        self.assertTrue(self.gate.decide(50))

    def test_exception_clears_signal_and_corruption_forces_progress(self):
        with self.assertRaises(RuntimeError):
            with active_tool(self.path):
                raise RuntimeError("tool failed")
        self.assertTrue(self.gate.decide(50))

        with open(self.path, "r+b") as file:
            file.write(b"BADMAGIC")
        self.assertTrue(self.gate.decide(50))

    def test_unchanged_pending_queue_does_no_registry_io(self):
        with active_tool(self.path):
            self.assertFalse(self.gate.decide(50, enqueue=True))
            with patch("deferred_backup.fcntl.flock", side_effect=AssertionError("lock")), \
                 patch("deferred_backup.os.open", side_effect=AssertionError("open")), \
                 patch("deferred_backup.Path.read_text", side_effect=AssertionError("proc read")), \
                 patch("deferred_backup.json.loads", side_effect=AssertionError("JSON")), \
                 patch.object(self.gate.state, "publish", side_effect=AssertionError("write")), \
                 patch.object(self.gate, "emit", side_effect=AssertionError("log")):
                for _ in range(100):
                    self.assertFalse(self.gate.decide(50))
        self.assertTrue(self.gate.decide(50))  # Tool end alone triggers progress.

    def test_mandatory_progress_does_not_open_tool_state(self):
        with patch("deferred_backup.ToolState", side_effect=AssertionError("state access")):
            self.assertTrue(self.gate.decide(100, enqueue=True))
            self.assertTrue(self.gate.decide(50, force=True))
        with active_tool(self.path):
            self.assertFalse(self.gate.decide(50))
            self.now += 1.01
            with patch.object(self.gate.state, "snapshot", side_effect=AssertionError("state access")):
                self.assertTrue(self.gate.decide(50))

    def test_crashed_tool_reaped_periodically_without_inspection(self):
        self.gate.max_delay = 100
        code = "from deferred_backup import active_tool; import os; c=active_tool(%r); c.__enter__(); os._exit(0)" % self.path
        subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parent, check=True)
        self.assertFalse(self.gate.decide(50))
        self.now += REAP_INTERVAL + 0.01
        self.assertTrue(self.gate.decide(50))

    def test_interrupted_publication_recovers_and_deadline_still_applies(self):
        self.gate.max_delay = 100
        with active_tool(self.path):
            self.assertFalse(self.gate.decide(50))
            state = self.gate.state
            sequence = state.snapshot()[0]
            struct.pack_into("<Q", state.mapping, 8, sequence + 1)
            self.assertFalse(self.gate.decide(50))
            self.now += REAP_INTERVAL + 0.01
            self.assertFalse(self.gate.decide(50))
            self.assertEqual(state.snapshot()[1], 1)
        self.assertTrue(self.gate.decide(50))

    def test_concurrent_process_tool_lifetimes(self):
        code = "from deferred_backup import active_tool; import sys;\nwith active_tool(%r):\n print('ready', flush=True); sys.stdin.readline()" % self.path
        children = []
        try:
            for _ in range(4):
                child = subprocess.Popen([sys.executable, "-c", code], cwd=Path(__file__).parent,
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
                children.append(child)
            for child in children:
                self.assertEqual(child.stdout.readline().strip(), "ready")
            self.assertFalse(self.gate.decide(50))
            self.assertEqual(self.gate.state.snapshot()[1], 4)
            for index, child in enumerate(children):
                child.communicate("done\n", timeout=10)
                self.assertEqual(child.returncode, 0)
                self.assertEqual(self.gate.state.snapshot()[1], 3 - index)
            self.assertTrue(self.gate.decide(50))
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.communicate()

    def test_reader_during_repeated_cross_process_publication(self):
        state = ToolState(self.path)
        self.addCleanup(state.close)
        code = "from deferred_backup import active_tool;\nfor _ in range(200):\n with active_tool(%r): pass" % self.path
        child = subprocess.Popen([sys.executable, "-c", code], cwd=Path(__file__).parent)
        try:
            while child.poll() is None:
                try:
                    self.assertIn(state.snapshot()[1], (0, 1))
                except StateBusy:
                    pass
            self.assertEqual(child.returncode, 0)
            self.assertEqual(state.snapshot()[1], 0)
        finally:
            if child.poll() is None:
                child.kill()
            child.wait()

    def controller_methods(self, relative, class_name, names):
        source = Path(__file__).resolve().parents[1] / "sglang/python/sglang/srt" / relative
        tree = ast.parse(source.read_text())
        cls = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == class_name)
        methods = [x for x in cls.body if isinstance(x, ast.FunctionDef) and x.name in names]
        for method in methods:
            method.returns = None
            method.decorator_list = []
            for arg in method.args.args:
                arg.annotation = None
        def operation(host, device, node, priority, **kwargs):
            return SimpleNamespace(host_indices=host, device_indices=device, node_ids=[node])
        def merge(queue):
            return SimpleNamespace(device_indices=[i for op in queue for i in op.device_indices],
                                   node_ids=[i for op in queue for i in op.node_ids])
        operation.merge_ops = merge
        namespace = {"CacheOperation": operation, "HiCacheAck": lambda **values: values}
        exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), "exec"), namespace)
        return namespace

    def test_byte_counter_base_and_hybrid_enqueue_poll_flush_reset(self):
        base = self.controller_methods("managers/cache_controller.py", "HiCacheController", {"write", "start_writing", "reset"})
        hybrid = self.controller_methods("mem_cache/hybrid_cache/hybrid_cache_controller.py", "HybridCacheController", {"write"})
        for write in (base["write"], hybrid["write"]):
            calls = []
            completion = SimpleNamespace(start_event="start", finish_event="end", timing_enabled=True)
            controller = SimpleNamespace(write_queue=[], write_queue_bytes=0, backup_gate=self.gate,
                ack_write_queue=[], load_queue=[], ack_load_queue=[], enable_storage=False,
                mem_pool_host=SimpleNamespace(alloc=lambda n: list(range(n))),
                allocate_host_transfers=lambda indices, extra: (list(range(len(indices))), []),
                _transfer_num_bytes=lambda op: calls.append(len(op.device_indices)) or len(op.device_indices)*25,
                _move_write_operation=lambda op: (None, None, None), _l2_transfers=lambda *args: args,
                _num_tokens_by_pool=lambda op: {}, _stop_storage_threads=lambda: None,
                storage_stop_event=SimpleNamespace(clear=lambda: None),
                l2_transfer_engine=SimpleNamespace(submit_device_to_host=lambda data: completion))
            controller.start_writing = MethodType(base["start_writing"], controller)
            with active_tool(self.path):
                write(controller, [1, 2], node_id=7)
                self.assertEqual(controller.write_queue_bytes, 50)
                for _ in range(20):
                    controller.start_writing()
                self.assertEqual(calls, [2])  # No byte rescans during scheduler polls.
                write(controller, [3, 4], node_id=8)
                self.assertEqual(controller.write_queue_bytes, 0)  # Enqueue hits limit.
                self.assertEqual(len(controller.ack_write_queue), 1)
                write(controller, [5], node_id=9)
                self.assertEqual(controller.write_queue_bytes, 25)
                base["reset"](controller)
                self.assertEqual(controller.write_queue_bytes, 0)
                self.assertEqual(controller.write_queue, [])
                self.assertIsNone(self.gate.pending_since)
                controller.mem_pool_host.alloc = lambda n: None
                controller.allocate_host_transfers = lambda *args: None
                self.assertIsNone(write(controller, [1, 2]))
                self.assertEqual(controller.write_queue_bytes, 0)

    def test_hybrid_delayed_batching_keeps_counter_until_explicit_flush(self):
        hybrid = self.controller_methods("mem_cache/hybrid_cache/hybrid_cache_controller.py", "HybridCacheController", {"write"})
        flushes = []
        controller = SimpleNamespace(write_queue=[], write_queue_bytes=0,
            allocate_host_transfers=lambda *args: ([1, 2], []),
            _transfer_num_bytes=lambda op: len(op.device_indices)*25,
            start_writing=lambda **kwargs: flushes.append(kwargs))
        hybrid["write"](controller, [1, 2], flush=False)
        self.assertEqual(controller.write_queue_bytes, 50)
        self.assertEqual(flushes, [])
        hybrid["write"](controller, [3], flush=True)
        self.assertEqual(controller.write_queue_bytes, 75)
        self.assertEqual(flushes, [{"enqueue": True}])
    def test_controller_keeps_queue_and_ack_until_submission(self):
        # Execute the patched production method without loading Torch/CUDA.
        source = Path(__file__).resolve().parents[1] / "sglang/python/sglang/srt/managers/cache_controller.py"
        tree = ast.parse(source.read_text())
        cls = next(item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == "HiCacheController")
        method = next(item for item in cls.body if isinstance(item, ast.FunctionDef) and item.name == "start_writing")
        calls = []
        operation = SimpleNamespace(device_indices=[1, 2], node_ids=[7])
        completion = SimpleNamespace(start_event="start", finish_event="finish", timing_enabled=True)
        namespace = {"CacheOperation": SimpleNamespace(merge_ops=lambda queue: operation),
                     "HiCacheAck": lambda **values: values}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), namespace)
        controller = SimpleNamespace(
            write_queue=[operation], write_queue_bytes=50, ack_write_queue=[], backup_gate=self.gate,
            _transfer_num_bytes=lambda op: 50,
            _move_write_operation=lambda op: ("host", "device", []),
            _l2_transfers=lambda *args: args, _num_tokens_by_pool=lambda op: {"kv": 2},
            l2_transfer_engine=SimpleNamespace(submit_device_to_host=lambda data: calls.append(data) or completion))
        with active_tool(self.path):
            namespace["start_writing"](controller)
            self.assertEqual(controller.write_queue, [operation])
            self.assertEqual(controller.ack_write_queue, [])
            self.assertEqual(calls, [])
            namespace["start_writing"](controller, force=True)
        self.assertEqual(controller.write_queue, [])
        self.assertEqual(controller.write_queue_bytes, 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(controller.ack_write_queue[0]["node_ids"], [7])

    def eviction_harness(self, first_reclaimed, requested=10, targets=None,
                         initial_available=0, enabled=True):
        source = Path(__file__).resolve().parents[1] / "sglang/python/sglang/srt/mem_cache/unified_radix_cache.py"
        tree = ast.parse(source.read_text())
        method = next(item for item in ast.walk(tree) if isinstance(item, ast.FunctionDef) and item.name == "_evict")
        method.decorator_list = []
        method.returns = None
        for argument in method.args.args:
            argument.annotation = None
        order = []
        available = [initial_available]
        controller = SimpleNamespace(backup_gate=self.gate if enabled else None, write_policy="write_through")

        def reclaim(request, tracker, **kw):
            # Model LRU reclaiming eligible rows, then the rows unpinned by drain.
            before = tracker["full"]
            order.append(("evict", before))
            freed = first_reclaimed if len(order) == 1 else max(0, requested - before)
            tracker["full"] += freed
            available[0] += freed

        cache = SimpleNamespace(disable=False, cache_controller=controller,
                                ongoing_write_through={7: "protected"}, tree_components=["full"],
                                _evict_request_by_type=lambda params: {"full": requested},
                                _component_available_size=lambda ct: available[0],
                                writing_check=lambda **kw: order.append(("finish", kw)),
                                _evict_components=reclaim,
                                update_eviction_metrics=lambda *args: None)
        namespace = {"time": __import__("time"), "EvictResult": lambda **kw: kw,
                     "BASE_COMPONENT_TYPE": "full", "ComponentType": SimpleNamespace(SWA="swa", MAMBA="mamba")}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), namespace)
        result = namespace["_evict"](cache, object(), targets)
        return order, result

    def test_eviction_does_not_drain_when_old_rows_suffice(self):
        order, result = self.eviction_harness(first_reclaimed=10)
        self.assertEqual(order, [("evict", 0)])
        self.assertEqual(result["num_tokens_evicted"], 10)

    def test_eviction_drains_only_after_shortfall_and_keeps_partial_reclaim(self):
        order, result = self.eviction_harness(first_reclaimed=4)
        self.assertEqual(order, [("evict", 0), ("finish", {"write_back": True}), ("evict", 4)])
        self.assertEqual(result["num_tokens_evicted"], 10)

    def test_allocation_target_satisfied_without_full_eviction_quota(self):
        order, result = self.eviction_harness(first_reclaimed=4, initial_available=20,
                                              targets={"full": ("full", 24)})
        self.assertEqual(order, [("evict", 0)])
        self.assertEqual(result["num_tokens_evicted"], 4)

    def test_allocation_target_shortfall_retries_after_drain(self):
        order, result = self.eviction_harness(first_reclaimed=4, initial_available=20,
                                              targets={"full": ("full", 30)})
        self.assertEqual(order, [("evict", 0), ("finish", {"write_back": True}), ("evict", 4)])
        self.assertEqual(result["num_tokens_evicted"], 10)

    def test_default_policy_does_not_gain_a_blocking_drain(self):
        order, result = self.eviction_harness(first_reclaimed=4, enabled=False)
        self.assertEqual(order, [("evict", 0)])
        self.assertEqual(result["num_tokens_evicted"], 4)

    def test_zero_eviction_request_does_not_force_backups(self):
        order, _ = self.eviction_harness(first_reclaimed=0, requested=0)
        self.assertEqual(order, [("evict", 0)])


if __name__ == "__main__":
    unittest.main()
