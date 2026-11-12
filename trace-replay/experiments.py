"""Prepare, run, audit and plot the two reference policy studies.

Only run requires a prepared GPU allocation. See REPRODUCE.md for setup.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REFERENCE = ROOT / 'docs/policy-study-20261109'
MODEL_REVISION = 'c1899de289a04d12100db370d81485cdf75e47ca'
PROFILES = ('baseline', 'token4x')


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(script, *args, env=None):
    subprocess.run([sys.executable, str(HERE / script), *map(str, args)], check=True, env=env)


def verify_reference():
    from plot_summary import load_csv
    from policy_matrix import configurations
    provenance = read(REFERENCE / 'provenance.json')
    for profile in PROFILES:
        folder = REFERENCE / profile
        recorded = provenance['profiles'][profile]
        for name, field in [('plan.json', 'plan_sha256'), ('summary.csv', 'summary_sha256')]:
            if digest(folder / name) != recorded[field]:
                raise ValueError(f'{profile}/{name}: reference hash mismatch')
        if read(folder / 'plots/plot-provenance.json')['source_sha256'] != recorded['summary_sha256']:
            raise ValueError(f'{profile}: plot source hash mismatch')
        groups = load_csv(folder / 'summary.csv')
        expected = {(c['key'], kind) for c in configurations() for kind in ('delay', 'stream')}
        if set(groups) != expected:
            raise ValueError(f'{profile}: missing reference cells')
        for cell, metrics in groups.items():
            if metrics['whole.seconds']['n'] != 3:
                raise ValueError(f'{profile}/{cell}: expected three measured trials')
        validation = read(folder / 'validation.json')
        if len(validation) != 90 or any(not v['timing_passed'] or not v['drained'] or
                v['missing_backup_counter'] or v['missing_restore_counter'] for v in validation):
            raise ValueError(f'{profile}: incomplete reference validation')
        for path, expected_hash in read(folder / 'run-spec.json')['sources'].items():
            source = subprocess.check_output(['git', '-C', str(ROOT), 'show',
                f'{provenance["measurement_scripts_commit"]}:{path}'])
            if hashlib.sha256(source).hexdigest() != expected_hash:
                raise ValueError(f'{profile}/{path}: measured Git source mismatch')
        print(f'{profile}: reference hashes, 30 cells, 90 validation records and measured Git sources verified')
    print('Committed evidence verified. Raw-trial validation requires the original files; use report on a run directory.')


def check_environment():
    expected = dict(line.split('==', 1) for line in
        (REFERENCE / 'python-packages.txt').read_text().splitlines()
        if '==' in line and not line.startswith('#'))
    expected['sglang'] = '0.5.21'
    mismatches = []
    if sys.version_info[:3] != (3, 12, 5):
        mismatches.append(f'Python: expected 3.12.5, found {sys.version.split()[0]}')
    for package in ('sglang', 'torch', 'numpy', 'requests', 'psutil', 'transformers', 'tokenizers', 'triton'):
        try:
            actual = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            actual = 'missing'
        if actual != expected[package]:
            mismatches.append(f'{package}: expected {expected[package]}, found {actual}')
    if mismatches:
        raise ValueError('Reference environment mismatch; use the prepared environment. '
                         'Changed software requires a separate study.\n' + '\n'.join(mismatches))
    print('Reference Python and eight core package versions match.')


def matching_plans(directory):
    for profile in PROFILES:
        if read(directory / f'{profile}.json') != read(REFERENCE / profile / 'plan.json'):
            raise ValueError(f'{profile}: plan differs from reference; changed workloads require a separate study')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('verify-reference', help='Verify published evidence and measured Git source hashes')
    commands.add_parser('check-environment', help='Check Python and core package versions without importing CUDA')
    p = commands.add_parser('prepare', help='Download/verify TraceLab and prepare both pinned plans')
    p.add_argument('--plans', type=Path, required=True)
    p.add_argument('--source', type=Path, default=ROOT / 'results/tracelab/source-v0.0.1.jsonl.gz')
    p = commands.add_parser('run', help='Run/resume both matrices in a prepared allocation')
    p.add_argument('--plans', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p = commands.add_parser('report', help='Strict raw audit, tables and all plots for both complete profiles')
    p.add_argument('--output', type=Path, required=True)
    p = commands.add_parser('plot', help='Redraw one exported CSV without raw files or a GPU')
    p.add_argument('--csv', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p = commands.add_parser('hierarchy', help='Draw policy hierarchy (requires rsvg-convert)')
    p.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'verify-reference':
        verify_reference()
    elif args.command == 'check-environment':
        check_environment()
    elif args.command == 'prepare':
        run('study.py', 'prepare', '--source', args.source.resolve(), '--output-dir', args.plans.resolve())
        matching_plans(args.plans)
        print('Both prepared plans exactly match the committed reference plans.')
    elif args.command == 'run':
        check_environment()
        matching_plans(args.plans)
        env = dict(os.environ)
        if env.get('EXP_MODEL_REVISION', MODEL_REVISION) != MODEL_REVISION:
            raise ValueError('EXP_MODEL_REVISION differs from the reference model revision')
        env['EXP_MODEL_REVISION'] = MODEL_REVISION
        subprocess.run(['bash', str(HERE / 'run-studies.sh'), str(args.plans.resolve()),
                        str(args.output.resolve())], env=env, check=True)
    elif args.command == 'report':
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / 'REPORT.md').write_text('Validation in progress or failed. This is not a completed report.\n')
        for profile in PROFILES:
            matrix = args.output.resolve() / profile / 'matrix'
            if read(matrix / 'status.json').get('trials') != 3:
                raise ValueError(f'{profile}: reference report requires three trials per cell')
            run('matrix_summary.py', matrix)
            run('plot_summary.py', matrix / 'summary.csv', '--output-dir', matrix / 'plots')
        (args.output / 'REPORT.md').write_text(
            '# Complete audited policy studies\n\n'
            'Both profiles passed the strict raw-data audit: 15 configurations, '
            'two tool conditions and three trials per cell (180 total).\n\n'
            '- [Baseline table](baseline/matrix/TABLES.md), [CSV](baseline/matrix/summary.csv), '
            '[figures](baseline/matrix/plots/).\n'
            '- [4× table](token4x/matrix/TABLES.md), [CSV](token4x/matrix/summary.csv), '
            '[figures](token4x/matrix/plots/).\n\n'
            'Read trace-replay/REPRODUCE.md in the scripts repository for metric definitions '
            'and interpretation limits. Preserve raw files and attempt histories.\n')
        print(f'Complete audited report: {args.output / "REPORT.md"}')
    elif args.command == 'plot':
        run('plot_summary.py', args.csv.resolve(), '--output-dir', args.output.resolve())
    elif args.command == 'hierarchy':
        subprocess.run([sys.executable, str(ROOT / 'memory-experiment/draw-backup-hierarchy.py'),
                        '--output-dir', str(args.output.resolve())], check=True)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f'Experiment workflow failed: {exc}') from exc
