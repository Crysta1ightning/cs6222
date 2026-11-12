"""Failure-path checks for the reference experiment entry point."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import experiments


class EntryPointTests(unittest.TestCase):
    def test_changed_plan_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            plan = experiments.read(experiments.REFERENCE / 'baseline/plan.json')
            plan['transforms']['seed'] = 1
            (directory / 'baseline.json').write_text(json.dumps(plan))
            with self.assertRaisesRegex(ValueError, 'plan differs'):
                experiments.matching_plans(directory)

    def test_model_revision_override_cannot_start_benchmarks(self):
        with patch('sys.argv', ['experiments.py', 'run', '--plans', '/unused', '--output', '/unused']), \
                patch.object(experiments, 'check_environment'), \
                patch.object(experiments, 'matching_plans'), \
                patch.dict('os.environ', {'EXP_MODEL_REVISION': 'different'}), \
                patch.object(experiments.subprocess, 'run') as start:
            with self.assertRaisesRegex(ValueError, 'model revision'):
                experiments.main()
            start.assert_not_called()

    def test_failed_second_profile_invalidates_previous_complete_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / 'REPORT.md').write_text('Previously complete')
            for profile in experiments.PROFILES:
                matrix = directory / profile / 'matrix'
                matrix.mkdir(parents=True)
                (matrix / 'status.json').write_text('{"trials": 3}')
            calls = []
            def fail_second(script, *args, **kwargs):
                calls.append(script)
                if len(calls) == 3:
                    raise ValueError('second profile failed raw audit')
            with patch('sys.argv', ['experiments.py', 'report', '--output', tmp]), \
                    patch.object(experiments, 'run', side_effect=fail_second):
                with self.assertRaisesRegex(ValueError, 'second profile'):
                    experiments.main()
            self.assertIn('not a completed report', (directory / 'REPORT.md').read_text())


if __name__ == '__main__':
    unittest.main()
