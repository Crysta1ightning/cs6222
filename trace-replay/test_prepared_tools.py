"""CPU-only protocol, lifetime, timing and cancellation checks; no GPU trials."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from prepared_tools import PreparedTools
from timing import plan_timing, timing_fidelity
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'memory-experiment'))
from activity import ActivityClient
from contention_check import assess

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('prepared_controller', ROOT / 'sglang/python/sglang/srt/mem_cache/stream_activity.py')
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)


class PreparedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('gcc'):
            raise unittest.SkipTest('gcc unavailable')
        cls.tmp = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.tmp.name) / 'worker'
        subprocess.run(['gcc', '-O3', '-fopenmp', '-DSTREAM_ARRAY_SIZE=262144', '-DNTIMES=5',
            str(ROOT / 'scripts/memory-experiment/stream-worker.c'), '-o', str(cls.binary)],
            capture_output=True, check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.socket = Path(self.tmpdir.name) / 'controller.sock'
        self.control = controller.ActivityController('normal', lease=0.15)
        self.control.serve(self.socket)
        self.addCleanup(self.control.close)

    def args(self, kind):
        return SimpleNamespace(tool_workload=kind, threads=1, max_stream_processes=1,
                               stream_worker=self.binary, tool_timeout=5)

    def execute(self, prepared, seconds, cancel=None):
        client = ActivityClient(self.socket, 'normal')
        record = {}
        result = prepared.execute(dict(duration_seconds=seconds), record, client,
                                  cancel or threading.Event(), timeout=5)
        self.assertLessEqual(record['start_ack']['ack_monotonic_ns'], record['start_ns'])
        self.assertLessEqual(record['end_ns'], record['end_ack']['ack_monotonic_ns'])
        self.assertEqual(self.control.snapshot()['active_tool_ids'], [])
        return result, record, client

    def test_delay_uses_no_subprocess_and_renews_lease(self):
        with patch('subprocess.Popen', side_effect=AssertionError('delay launched a process')):
            with PreparedTools(self.args('delay')) as prepared:
                result, record, client = self.execute(prepared, 0.25)
        self.assertEqual(result['stream_runs'], [])
        self.assertGreaterEqual(result['elapsed_seconds'], 0.25)
        self.assertTrue(any(e['request']['event'] == 'TOOL_HEARTBEAT' for e in client.events))
        self.assertEqual(self.control.snapshot()['counters']['TOOL_EXPIRED'], 0)

    def test_verified_controller_identity_preserves_restart_detection(self):
        client = ActivityClient(self.socket, 'normal', expected_pid=os.getpid())
        self.assertFalse(client.events)
        client.rpc('TOOL_START', tool_id='known')
        self.assertEqual(client.events[0]['request']['event'], 'TOOL_START')
        client.end('known')
        stale = ActivityClient(self.socket, 'normal', expected_pid=os.getpid()+1)
        with self.assertRaisesRegex(RuntimeError, 'restarted'):
            stale.rpc('TOOL_START', tool_id='stale')
        client.end('stale')
        self.assertFalse(self.control.snapshot()['active_tool_ids'])

    def test_same_stream_process_and_buffers_for_many_commands(self):
        with PreparedTools(self.args('stream')) as prepared:
            pid = prepared.workers[0].process.pid
            self.assertTrue(prepared.initial['workers'][0]['ready']['full_validation'])
            for _ in range(42):  # More than the old cumulative 200-iteration limit.
                result, record, _ = self.execute(prepared, 0)
                self.assertEqual(record['worker_pid'], pid)
                self.assertTrue(result['buffers_reused'])
                self.assertEqual(set(result['stream_runs'][0]['kernels']), {'Copy','Scale','Add','Triad'})
                self.assertLess(prepared.initial['startup_end_ns'], record['start_ns'])
            result, _, _ = self.execute(prepared, 0.03)
            self.assertGreaterEqual(result['elapsed_seconds'], 0.03)
            _, _, client = self.execute(prepared, 0.25)
            self.assertTrue(any(e['request']['event'] == 'TOOL_HEARTBEAT' for e in client.events))
            self.assertEqual(self.control.snapshot()['counters']['TOOL_EXPIRED'], 0)
        self.assertTrue(prepared.final()['workers'][0]['full_validation'])
        self.assertTrue(prepared.final()['workers'][0]['reaped'])

    def test_cancel_reaps_worker_before_end_ack(self):
        prepared = PreparedTools(self.args('stream')).__enter__()
        client = ActivityClient(self.socket, 'normal')
        cancel = threading.Event()
        timer = threading.Timer(.03, cancel.set)
        timer.start()
        self.addCleanup(timer.cancel)
        record = {}
        with self.assertRaisesRegex(RuntimeError, 'canceled'):
            prepared.execute(dict(duration_seconds=2), record, client, cancel, timeout=5)
        self.assertIsNotNone(prepared.workers[0].process.poll())
        self.assertIn('start_ns', record)
        self.assertIn('end_ack', record)
        self.assertFalse(self.control.snapshot()['active_tool_ids'])
        prepared.close(failed=True)

    def test_multiple_workers_have_disjoint_cpu_assignments(self):
        if len(os.sched_getaffinity(0)) < 2:
            self.skipTest('Two allocated cores required')
        args = self.args('stream')
        args.max_stream_processes = 2
        with PreparedTools(args) as prepared:
            groups = [set(w['assigned_cpus']) for w in prepared.initial['workers']]
            self.assertFalse(groups[0] & groups[1])
            for worker in prepared.initial['workers']:
                self.assertTrue(all(set(mask) <= set(worker['assigned_cpus'])
                                    for mask in worker['threads'].values()))
        self.assertTrue(all(w['full_validation'] and w['reaped'] for w in prepared.final()['workers']))

    def test_fixed_work_executes_exact_round_counts_and_validates(self):
        with PreparedTools(self.args('stream')) as prepared:
            worker = prepared.workers[0]
            self.assertIn('WORK', worker.ready['commands'])
            for rounds in [1, 3, 42, 2]:
                worker.start(rounds=rounds)
                result = worker.finish()
                self.assertEqual(result['rounds'], rounds)
                self.assertEqual(result['target_rounds'], rounds)
                self.assertTrue(result['sample_valid'])
            for rounds in [0, -1, 100001, 1.5, True]:
                with self.assertRaises(ValueError):
                    worker.start(rounds=rounds)
            with self.assertRaises(ValueError):
                worker.start(1, rounds=1)
        self.assertTrue(prepared.final()['workers'][0]['full_validation'])

    def test_bad_duration_is_rejected_by_worker_protocol(self):
        with self.assertRaises(RuntimeError):
            with PreparedTools(self.args('stream')) as prepared:
                worker = prepared.workers[0]
                worker.send('RUN nan')
                worker.receive('started')

    def test_timeout_reaps_worker_and_ends_activity(self):
        prepared = PreparedTools(self.args('stream')).__enter__()
        client = ActivityClient(self.socket, 'normal')
        record = {}
        with self.assertRaises((RuntimeError, TimeoutError)):
            prepared.execute(dict(duration_seconds=1), record, client, threading.Event(), timeout=.01)
        self.assertIsNotNone(prepared.workers[0].process.poll())
        self.assertIn('end_ack', record)
        self.assertFalse(self.control.snapshot()['active_tool_ids'])
        prepared.close(failed=True)


class TimingTests(unittest.TestCase):
    def test_copy_diagnostic_default_never_imports_torch(self):
        import contention_check
        with patch.dict(sys.modules, {'torch': None}), patch.object(sys, 'argv', ['contention_check.py']), patch('builtins.print') as printed:
            contention_check.main()
        import json
        self.assertEqual(json.loads(printed.call_args.args[0])['status'], 'dry-run')

    def test_contention_requires_overlap_and_distinguishes_speedup(self):
        alone = dict(kernels={'Triad': dict(average_GB_per_second=100)})
        copying = dict(start_ns=10, end_ns=20, kernels={'Triad': dict(average_GB_per_second=80)})
        self.assertFalse(assess(alone, copying, [])['candidate_contention'])
        report = assess(alone, copying, [dict(start_ns=11, end_ns=19)])
        self.assertTrue(report['candidate_contention'])
        self.assertAlmostEqual(report['observed_slowdown_percent'], 20)
        copying['kernels']['Triad']['average_GB_per_second'] = 110
        self.assertFalse(assess(alone, copying, [dict(start_ns=11, end_ns=19)])['candidate_contention'])

    def test_duration_and_schedule_failures_remain_separate(self):
        tools = [dict(trace_tool=dict(duration_seconds=1), actual_seconds=1.01,
                      launch_lag_seconds=0.5, surrogate_queue_seconds=0.4)]
        report = timing_fidelity(tools)
        self.assertTrue(report['duration_fidelity_passed'])
        self.assertFalse(report['schedule_fidelity_passed'])
        self.assertFalse(report['passed'])
        tools[0].update(actual_seconds=1.5, launch_lag_seconds=0)
        self.assertFalse(timing_fidelity(tools)['duration_fidelity_passed'])

    def test_short_and_zero_tools_are_flagged_without_changing_plan(self):
        plan = dict(sessions=[dict(steps=[dict(tools=[dict(duration_seconds=0), dict(duration_seconds=.009), dict(duration_seconds=1)])])])
        report = plan_timing(plan, .05)
        self.assertEqual(report['tools_shorter_than_one_round'], 2)
        self.assertEqual(report['zero_duration_tools'], 1)
        self.assertEqual(plan['sessions'][0]['steps'][0]['tools'][1]['duration_seconds'], .009)


if __name__ == '__main__':
    unittest.main()
