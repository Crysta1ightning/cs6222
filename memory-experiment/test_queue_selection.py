"""Allocation-critical partial queue tests with CPU tensors and mocked DMA."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock

import torch
from sglang.srt.managers.cache_controller import CacheOperation, HiCacheController
from sglang.srt.mem_cache.stream_activity import ActivityController
from sglang.srt.mem_cache.unified_radix_cache import UnifiedRadixCache, _OngoingWriteThrough
from sglang.srt.mem_cache.unified_cache.component_type import ComponentType


class QueueSelection(unittest.TestCase):
    def setup(self, policy='victim', parent=False):
        activity = ActivityController('defer', queue_policy=policy, max_tokens=100)
        activity.handle(dict(event='TOOL_START', tool_id='active'))
        self.addCleanup(activity.close)
        root = NS(id=0, parent=None, children={})
        nodes = {}
        for i in range(1, 6):
            node = NS(id=i, parent=root, children={},
                      component_data={ComponentType.FULL:NS(value=torch.arange(4),lock_ref=1)})
            nodes[i] = node
            root.children[i] = node
        if parent:
            root.children.pop(3)
            nodes[3].parent = nodes[1]
            nodes[1].children[3] = nodes[3]
            nodes[1].component_data[ComponentType.FULL].lock_ref = 2
        full = NS(_ensure_eviction_strategy=lambda:None,
                  session_ref_eviction_strategy=lambda n: (0 if n.id==3 else n.id+10))
        cache = UnifiedRadixCache.__new__(UnifiedRadixCache)
        cache.tree_core = NS(root_node=root, node_by_id=nodes.__getitem__,
                             components_by_type={ComponentType.FULL:full},evictable_device_leaves=set())
        cache.stream_activity = activity
        cache.metrics_collector = None
        cache.ongoing_write_through = {i:_OngoingWriteThrough(i, 'receipt', [i]) for i in nodes}
        cc = HiCacheController.__new__(HiCacheController)
        cache.cache_controller = cc
        cc.stream_activity = activity
        cc.write_queue = [CacheOperation(torch.arange(4),torch.arange(4),i) for i in nodes]
        cc.ack_write_queue = []
        cc.mem_pool_host = NS(size_per_token=16)
        cc.activity_free_tokens = lambda:0
        cc.activity_reserve_tokens = 0
        cc.activity_reclaim_callback = lambda:cache._flush_activity_victim(reason='gpu_pressure')
        cc._move_write_operation = lambda op:(op.host_indices,op.device_indices,None)
        cc._l2_transfers = lambda *args:[]
        self.order = []
        finish = NS(synchronize=lambda:self.order.append('wait'))
        cc.l2_transfer_engine = Mock()
        cc.l2_transfer_engine.submit_device_to_host.return_value = NS(
            start_event=None,finish_event=finish,timing_enabled=False)
        def complete(ack_id):
            self.assertTrue(self.order and self.order[-1]=='wait')
            entry = cache.ongoing_write_through.pop(ack_id)
            node = nodes[entry.node_id]
            while node is not root:
                node.component_data[ComponentType.FULL].lock_ref -= 1
                node = node.parent
        cache._finish_write_through_ack = complete
        return cache, cc, nodes, activity

    def test_victim_only_preserves_other_queue_entries_and_pins(self):
        cache, cc, nodes, activity = self.setup()
        self.assertTrue(cache._flush_activity_victim())
        self.assertEqual([op.node_ids for op in cc.write_queue], [[1],[2],[4],[5]])
        self.assertEqual(nodes[3].component_data[ComponentType.FULL].lock_ref, 0)
        self.assertEqual(nodes[1].component_data[ComponentType.FULL].lock_ref, 1)
        self.assertEqual(activity.pending_ops, 4)
        self.assertEqual(activity.snapshot()['in_flight_batches'], 0)
        self.assertEqual(activity.counters['completed_bytes'],64)

    def test_through_victim_keeps_later_entries(self):
        cache, cc, nodes, activity = self.setup('through_victim')
        self.assertTrue(cache._flush_activity_victim())
        self.assertEqual([op.node_ids for op in cc.write_queue], [[4],[5]])
        self.assertEqual(activity.counters['completed_bytes'],192)
        self.assertEqual(activity.pending_ops,2)

    def test_ancestor_backup_dependency_is_included_for_victim_only(self):
        cache, cc, nodes, activity = self.setup(parent=True)
        self.assertTrue(cache._flush_activity_victim())
        self.assertEqual([op.node_ids for op in cc.write_queue], [[2],[4],[5]])
        self.assertEqual(nodes[1].component_data[ComponentType.FULL].lock_ref,0)
        self.assertEqual(activity.counters['completed_bytes'],128)

    def test_active_request_pin_is_never_borrowed(self):
        cache, cc, nodes, activity = self.setup()
        nodes[3].component_data[ComponentType.FULL].lock_ref += 1
        self.assertTrue(cache._flush_activity_victim())
        self.assertIn([3],[op.node_ids for op in cc.write_queue])
        self.assertEqual(nodes[3].component_data[ComponentType.FULL].lock_ref,2)

    def test_all_active_leaves_do_not_trigger_blind_flush(self):
        cache, cc, nodes, activity = self.setup()
        for node in nodes.values():node.component_data[ComponentType.FULL].lock_ref += 1
        self.assertFalse(cache._flush_activity_victim())
        cc.l2_transfer_engine.submit_device_to_host.assert_not_called()

    def test_submitted_acks_are_finished_before_new_selection(self):
        cache, cc, nodes, activity = self.setup()
        cache._finish_submitted_write_acks = Mock(return_value=1)
        self.assertTrue(cache._flush_activity_victim())
        cc.l2_transfer_engine.submit_device_to_host.assert_not_called()

    def test_partial_submission_requires_a_force_reason(self):
        cache, cc, nodes, activity = self.setup()
        with self.assertRaises(ValueError):cc.start_writing(selected_node_ids={3})
        self.assertEqual(len(cc.write_queue),5)

    def test_admission_pressure_selects_a_victim_without_whole_flush(self):
        cache,cc,nodes,activity=self.setup()
        cc.activity_reserve_tokens=10
        cc.start_writing()
        self.assertEqual([op.node_ids for op in cc.write_queue],[[1],[2],[4],[5]])
        self.assertEqual(activity.counters['pressure_overrides'],1)
        self.assertEqual(activity.counters['completed_bytes'],64)

    def test_pressure_uses_existing_unlocked_leaves_before_copying(self):
        cache,cc,nodes,activity=self.setup()
        cache.tree_core.evictable_device_leaves.add(99)
        cc.activity_reserve_tokens=10
        cc.start_writing()
        self.assertEqual(len(cc.write_queue),5)
        self.assertEqual(activity.counters['pressure_overrides'],0)

    def test_pressure_without_eligible_victim_still_obeys_age_limit(self):
        cache,cc,nodes,activity=self.setup()
        for node in nodes.values():node.component_data[ComponentType.FULL].lock_ref+=1
        cc.activity_reserve_tokens=10
        cc.start_writing()
        self.assertEqual(len(cc.write_queue),5)
        activity.pending_since-=activity.max_age+1
        cc.start_writing()
        self.assertEqual(cc.write_queue,[])
        self.assertEqual(len(cc.ack_write_queue),1)

    def test_base_policy_and_queue_policy_are_separate_metadata(self):
        for base in ['write_through','write_through_selective','write_back']:
            for queue in ['all','victim','through_victim']:
                c=ActivityController('defer',base_policy=base,queue_policy=queue)
                self.assertEqual((c.snapshot()['base_policy'],c.snapshot()['queue_policy']),(base,queue))
                c.close()


if __name__=='__main__':unittest.main()
