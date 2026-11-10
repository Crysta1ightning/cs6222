"""Validate exported statistics before drawing figures without raw trials."""
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import plot_summary as plots


class PlotSummaryTests(unittest.TestCase):
    def read_rows(self, rows):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'summary.csv'
            with path.open('w', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(self.row()))
                writer.writeheader()
                writer.writerows(rows)
            return plots.load_csv(path)

    def row(self):
        return dict(configuration='write_back--normal--all', tool_workload='stream',
                    metric='whole.seconds', n='3', min='1', median='2', max='3')

    def changed(self, **updates):
        return dict(self.row(), **updates)

    def test_valid_statistics_and_missing_measurements(self):
        missing = self.changed(metric='whole.restore_GB', n='0', min='', median='', max='')
        groups = self.read_rows([self.row(), missing])
        data = groups[('write_back--normal--all', 'stream')]
        self.assertEqual(data['whole.seconds'], dict(n=3, min=1., median=2., max=3.))
        self.assertEqual(data['whole.restore_GB']['n'], 0)
        with tempfile.TemporaryDirectory() as tmp, patch.object(plots.chart.__globals__['shutil'], 'which', return_value=None):
            path=Path(tmp)/'runtime.png'
            plots.chart(groups, 'write_back', 'whole.seconds', 'Seconds', path)
            self.assertIn('N/A', path.with_suffix('.svg').read_text())

    def test_invalid_rows_are_rejected(self):
        for updates in [dict(n='1.5'), dict(n='-1'), dict(n='nan'), dict(median='inf'),
                        dict(min='4'), dict(max='1'), dict(median=''),
                        dict(configuration='invented'), dict(tool_workload='invented'),
                        dict(metric=''), dict(n='0')]:
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                self.read_rows([self.changed(**updates)])

    def test_duplicate_measurements_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.read_rows([self.row(), self.row()])


if __name__ == '__main__':
    unittest.main()
