"""Real cache-method tests with CPU tensors and a mocked CUDA transfer engine."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import torch

from sglang.srt.managers.cache_controller import CacheOperation, HiCacheController
from sglang.srt.mem_cache.base_prefix_cache import EvictParams
from sglang.srt.mem_cache.stream_activity import ActivityController
from sglang.srt.mem_cache.unified_cache.cache_action import BackupKV
from sglang.srt.mem_cache.unified_cache.component_type import ComponentType
from sglang.srt.mem_cache.unified_radix_cache import UnifiedRadixCache


class CacheHooks(unittest.TestCase):
    def setup_cache(self, mode):
        activity = ActivityController(mode, max_tokens=100, max_age=5)
        self.addCleanup(activity.close)
        activity.handle(dict(event="TOOL_START", tool_id="test"))
        controller = HiCacheController.__new__(HiCacheController)
        controller.stream_activity = activity
        controller.activity_free_tokens = lambda: 1000
        controller.activity_reserve_tokens = 10
        controller.mem_pool_host = SimpleNamespace(size_per_token=16)
        controller.write_policy = "write_through"
        controller.write_queue, controller.ack_write_queue = [], []
        controller._move_write_operation = lambda op: (op.host_indices, op.device_indices, None)
        controller._l2_transfers = lambda *args: []
        controller.l2_transfer_engine = Mock()
        controller.l2_transfer_engine.submit_device_to_host.return_value = SimpleNamespace(
            start_event=None, finish_event=None, timing_enabled=False)
        cache = UnifiedRadixCache.__new__(UnifiedRadixCache)
        cache.stream_activity = activity
        cache.cache_controller = controller
        cache.buffer_pipeline = None
        cache.tree_core = Mock()
        cache.tree_core.build_backup_spec.return_value = (torch.arange(4), {})
        def write(node_id, device, comps, sidecar):
            host = torch.arange(len(device))
            controller.write_queue.append(CacheOperation(host, device, node_id))
            return host
        cache._execute_kv_backup = Mock(side_effect=write)
        cache._build_backup_sidecar = Mock(return_value=[])
        cache.inc_lock_ref = Mock(return_value=SimpleNamespace(to_dec_params=lambda: "lock-receipt"))
        cache._track_write_through_node = Mock()
        return cache, controller, activity

    def test_skip_declines_before_host_allocation_commit_or_lock(self):
        cache, controller, activity = self.setup_cache("skip")
        self.assertEqual(cache._execute_and_commit_kv_backup(BackupKV(node_ids=[1])), 0)
        cache._execute_kv_backup.assert_not_called()
        cache.tree_core.commit_backup.assert_not_called()
        cache.inc_lock_ref.assert_not_called()
        self.assertEqual(controller.write_queue, [])

    def test_defer_uses_existing_queue_and_lock_then_normal_submission(self):
        cache, controller, activity = self.setup_cache("defer")
        cache._execute_and_commit_kv_backup(BackupKV(node_ids=[1]))
        cache._track_write_through_node.assert_called_once()
        controller.start_writing()
        self.assertEqual(len(controller.write_queue), 1)
        controller.l2_transfer_engine.submit_device_to_host.assert_not_called()
        activity.handle(dict(event="TOOL_END", tool_id="test"))
        controller.start_writing()
        self.assertEqual(controller.write_queue, [])
        controller.l2_transfer_engine.submit_device_to_host.assert_called_once()
        self.assertEqual(controller.ack_write_queue[0].node_ids, [1])

    def test_allocation_critical_backup_bypasses_skip(self):
        cache, controller, activity = self.setup_cache("skip")
        cache._execute_and_commit_kv_backup(BackupKV(node_ids=[1]), write_back=True)
        cache.tree_core.commit_backup.assert_called_once()
        cache.inc_lock_ref.assert_not_called()
        controller.start_writing(force_reason="allocation_critical")
        controller.l2_transfer_engine.submit_device_to_host.assert_called_once()

    def test_normal_keeps_merged_submission_even_with_small_defer_limit(self):
        cache, controller, activity = self.setup_cache("normal")
        activity.max_tokens = 2
        cache._execute_and_commit_kv_backup(BackupKV(node_ids=[1, 2]))
        self.assertEqual(len(controller.write_queue), 2)
        controller.start_writing()
        self.assertEqual(controller.ack_write_queue[0].node_ids, [1, 2])
        self.assertEqual(controller.ack_write_queue[0].num_tokens, 8)

    def test_large_optional_node_forces_submission_instead_of_retention(self):
        cache, controller, activity = self.setup_cache("defer")
        activity.max_tokens = 2
        cache._execute_and_commit_kv_backup(BackupKV(node_ids=[1]))
        self.assertEqual(controller.write_queue, [])
        self.assertGreater(activity.counters["pressure_overrides"], 0)

    def test_reclamation_flushes_existing_queue_before_victim_selection(self):
        cache, controller, activity = self.setup_cache("defer")
        cache._execute_and_commit_kv_backup(BackupKV(node_ids=[1]))
        cache.disable = False
        cache.tree_components = (ComponentType.FULL,)
        cache.writing_check = Mock()
        cache._evict_request_by_type = Mock(return_value={ComponentType.FULL: 1})
        cache._evict_components = Mock()
        cache.update_eviction_metrics = Mock()
        cache._evict(EvictParams(num_tokens=1))
        self.assertEqual(controller.write_queue, [])
        cache.writing_check.assert_called_once_with(write_back=True)
        cache._evict_components.assert_called_once()

    def test_required_retraction_is_immediate_and_preserves_optional_queue(self):
        cache, controller, activity = self.setup_cache("defer")
        cache._execute_and_commit_kv_backup(BackupKV(node_ids=[1]))
        cache._retraction_device_transfers = Mock(return_value=(torch.arange(4), []))
        controller.allocate_host_transfers = Mock(return_value=(torch.arange(4), []))
        finish = Mock()
        controller.l2_transfer_engine.submit_device_to_host.return_value = SimpleNamespace(
            start_event=None, finish_event=finish, timing_enabled=False)
        cache.metrics_collector = None
        cache.discard_kv_cache_backup = Mock()
        backup = cache.backup_kv_cache(SimpleNamespace(seqlen=4))
        self.assertIsNotNone(backup)
        finish.synchronize.assert_called_once()
        self.assertEqual(len(controller.write_queue), 1)
        self.assertEqual(activity.pending_tokens, 4)
        self.assertEqual(activity.snapshot()["in_flight_batches"], 0)
        self.assertEqual(activity.counters["pressure_overrides"], 1)


if __name__ == "__main__":
    unittest.main()
