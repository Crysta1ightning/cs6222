"""Real worker protocol/validation and full-warmup checks, without a GPU."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import experiment
from persistent_stream import PersistentStream


class WorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('gcc'):
            raise unittest.SkipTest('gcc unavailable')
        cls.directory = tempfile.TemporaryDirectory()
        cls.binary = str(Path(cls.directory.name) / 'worker')
        source = Path(__file__).resolve().parents[1] / 'memory-experiment/stream-worker.c'
        subprocess.run(['gcc', '-O3', '-fopenmp', '-DSTREAM_ARRAY_SIZE=262144', '-DNTIMES=5',
                        str(source), '-o', cls.binary], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def env(self):
        return dict(os.environ, OMP_NUM_THREADS='2', OMP_DYNAMIC='FALSE', OMP_WAIT_POLICY='PASSIVE')

    def test_same_worker_multiple_rounds_and_full_validation(self):
        with PersistentStream([self.binary], self.env(), timeout=5) as worker:
            pid = worker.process.pid
            previous_end = worker.launched_ns
            for _ in range(3):
                start = worker.start()['start_ns']
                result = worker.finish()
                self.assertGreater(start, previous_end)
                self.assertEqual(worker.process.pid, pid)
                self.assertTrue(result['sample_valid'])
                self.assertEqual(set(result['kernels']), {'Copy', 'Scale', 'Add', 'Triad'})
                previous_end = result['end_ns']
        self.assertTrue(worker.full_validation)

    def test_invalid_command_fails_and_reaps_worker(self):
        with self.assertRaises(RuntimeError):
            with PersistentStream([self.binary], self.env(), timeout=5) as worker:
                worker.send('INVALID')
                worker.receive('started')
        self.assertIsNotNone(worker.process.poll())
        self.assertFalse(worker.full_validation)

    def test_buffered_pipe_messages_and_failed_full_validation(self):
        code = "import sys; print('{\"event\":\"ready\"}',flush=True); sys.stdin.readline(); print('{\"event\":\"started\",\"start_ns\":1}\\n{\"event\":\"done\",\"start_ns\":1,\"end_ns\":2,\"sample_valid\":true}',flush=True); sys.stdin.readline(); print('{\"event\":\"validated\",\"valid\":false}',flush=True)"
        with self.assertRaisesRegex(RuntimeError, 'full-array'):
            with PersistentStream([sys.executable, '-c', code], self.env(), timeout=5) as worker:
                worker.start()
                worker.finish()

    def test_prepared_measurement_aligns_actual_loop_and_avoids_round_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = SimpleNamespace(stream_worker=Path(self.binary), tool_cpus=None, tool_threads=2,
                requests=5, followup=0, output_tokens=16, tool='stream', nsys=False,
                tool_schedule='requests', stream_at_requests=[3], stream_rounds=1,
                policy_log_mode='off')

            def generated(ids, output_tokens):
                now = time.monotonic_ns()
                return {'start_ns': now, 'end_ns': now+1, 'prompt_tokens': len(ids)}

            with patch.object(experiment, 'settle', return_value=dict.fromkeys(experiment.METRICS, 0)), \
                 patch.object(experiment, 'generate', side_effect=generated), \
                 patch.object(experiment, 'metrics', side_effect=AssertionError('round metric query')):
                result = experiment.measure_condition(args, 'default', root, [[i] for i in range(5)],
                    str(root / 'tools.bin'), root / 'policy.jsonl')
            self.assertTrue(result['stream_full_validation'])
            self.assertTrue(result['measurement_valid'])
            self.assertFalse(result['policy_diagnostics_available'])
            self.assertIsNone(result['tool_window_counter_deltas'])
            window = result['tool_windows'][0]
            self.assertLess(window['process_launched_ns'], window['trigger_ns'])
            self.assertGreaterEqual(window['start_ns'], window['trigger_ns'])
            self.assertGreaterEqual(window['trigger_request_start_ns'], window['start_ns'])
            self.assertTrue((root / 'worker-numa_maps.txt').exists())
            self.assertTrue(json.loads((root / 'result.json').read_text())['measurement_valid'])


class WarmupTests(unittest.TestCase):
    def test_prefix_sequence_runs_without_intermediate_flush_and_is_recorded(self):
        import requests
        response = SimpleNamespace(raise_for_status=lambda: None)
        args = SimpleNamespace(warmup_requests=2, output_tokens=16)
        events = []
        def generated(ids, tokens):
            events.append(('generate', ids, tokens))
            return {'prompt': ids, 'tokens': tokens}
        def posted(*args, **kwargs):
            events.append(('flush',))
            return response
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(experiment, 'generate', side_effect=generated), \
             patch.object(experiment, 'settle', return_value={'backup': 123}) as settle, \
             patch.object(requests, 'post', side_effect=posted):
            experiment.warm_server(args, [9], [[0], [1], [2]], Path(tmp))
            saved = json.loads((Path(tmp)/'warmup-requests.json').read_text())
        self.assertEqual(events, [('generate', [9], 16), ('flush',), ('generate', [9], 16),
                                  ('flush',), ('generate', [0], 16), ('generate', [1], 16),
                                  ('generate', [2], 16)])
        self.assertEqual(settle.call_count, 3)
        self.assertEqual(len(saved['cold_cache']), 2)
        self.assertEqual(len(saved['prefix_sequence']), 3)

    def test_full_token_warmups_and_cache_flush_between_requests(self):
        import requests
        response = SimpleNamespace(raise_for_status=lambda: None)
        args = SimpleNamespace(warmup_requests=3, output_tokens=16)
        with patch.object(experiment, 'generate') as generate, \
             patch.object(experiment, 'settle', return_value={'backup': 123}) as settle, \
             patch.object(requests, 'post', return_value=response) as post:
            result = experiment.warm_server(args, [1]*1024)
        self.assertEqual(generate.call_count, 3)
        self.assertTrue(all(call.args == ([1]*1024, 16) for call in generate.call_args_list))
        self.assertEqual(settle.call_count, 3)
        self.assertEqual(post.call_count, 2)
        self.assertTrue(all(call.args[0].endswith('/flush_cache') for call in post.call_args_list))
        self.assertEqual(result, {'backup': 123})


if __name__ == '__main__':
    unittest.main()
