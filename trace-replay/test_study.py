"""Checkpoint acceptance, scaling and artifact compatibility checks."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import matrix_summary
import study


class StudyTests(unittest.TestCase):
    def test_checkpoint_requires_both_conditions_and_exact_trial_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            checked=[dict(kind=k) for k in ['delay','stream'] for _ in range(3)]
            with patch.object(matrix_summary,'load',return_value=({},checked)):
                study.valid_cell(root,'cell',3)
            with patch.object(matrix_summary,'load',return_value=({},checked[:-1])):
                with self.assertRaisesRegex(ValueError,'expected 3'):study.valid_cell(root,'cell',3)
            with patch.object(matrix_summary,'load',side_effect=ValueError('timing failure')):
                with self.assertRaisesRegex(ValueError,'timing failure'):study.valid_cell(root,'cell',3)

    def test_duplicate_valid_attempts_are_not_silently_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots=[Path(tmp)/name for name in ['a','b']]
            for root in roots:(root/'write_back--normal--all').mkdir(parents=True)
            with patch.object(study,'valid_cell'):
                with self.assertRaisesRegex(ValueError,'Ambiguous duplicate'):study.discover(roots,3)

    def test_scaling_preserves_tools_outputs_and_session_identity(self):
        baseline=dict(transforms=dict(max_context=4096),sessions=[dict(session_id='s',steps=[dict(
            original=dict(input_tokens=100,prefix_tokens=80),input_len=2,prefix_len=6,output_len=7,
            tools=[dict(duration_seconds=3)],human_wait_after_seconds=1)])])
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with patch.object(study.prepare,'download'),patch.object(study.prepare,'prepare',return_value=baseline):
                study.prepare_plans(root/'source.gz',root/'plans')
            scaled=study.read(root/'plans/token4x.json')
            a=baseline['sessions'][0]['steps'][0];b=scaled['sessions'][0]['steps'][0]
            self.assertEqual(b['input_len']+b['prefix_len'],32)
            self.assertEqual(b['output_len'],a['output_len'])
            self.assertEqual(b['tools'],a['tools'])
            self.assertEqual(b['human_wait_after_seconds'],a['human_wait_after_seconds'])
            self.assertEqual(baseline['transforms']['max_context'],4096)

    def test_incompatible_assembly_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'raw';source.mkdir()
            with patch.object(matrix_summary,'load',side_effect=ValueError('different placement')):
                with self.assertRaisesRegex(ValueError,'different placement'):
                    study.assemble(root/'view',{'write_back--normal--all':source},3)

    def test_partial_plot_keeps_missing_cells_visible_without_renderer(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'plot.png'
            with patch.object(matrix_summary.shutil,'which',return_value=None):
                matrix_summary.chart({},'write_back','metric','Test',target)
            self.assertIn('N/A',target.with_suffix('.svg').read_text())
            self.assertFalse(target.exists())


if __name__=='__main__':unittest.main()
