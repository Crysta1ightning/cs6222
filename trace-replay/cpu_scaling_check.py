"""Equal-work CPU core scaling on one allocated NUMA node; no GPU/model."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts/backup-policy'))
from persistent_stream import PersistentStream


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--cores', type=int, default=32)
    parser.add_argument('--rounds', type=int, default=8)
    parser.add_argument('--bound-node', type=int)
    args = parser.parse_args()
    if not os.environ.get('SLURM_JOB_ID') or args.cores not in [8, 16, 32] or not 1 <= args.rounds <= 100:
        parser.error('Use a Slurm compute allocation, 8/16/32 cores and 1..100 rounds')
    allowed, groups, seen = sorted(os.sched_getaffinity(0)), {}, set()
    for cpu in allowed:
        root = Path(f'/sys/devices/system/cpu/cpu{cpu}')
        identity = tuple((root/'topology'/name).read_text().strip()
                         for name in ['physical_package_id', 'core_id'])
        if identity in seen:
            continue
        seen.add(identity)
        for node in root.glob('node[0-9]*'):
            groups.setdefault(int(node.name[4:]), []).append(cpu)
    choices = [(node, cpus[:args.cores]) for node, cpus in groups.items() if len(cpus) >= args.cores]
    if not choices:
        raise RuntimeError(f'Need {args.cores} distinct physical cores on one NUMA node: {groups}')
    if args.bound_node is None:
        node, cpus = choices[0]
        subprocess.run(['numactl', '--physcpubind='+','.join(map(str, cpus)), '--membind='+str(node),
            sys.executable, str(Path(__file__).resolve()), '--output-dir', str(args.output_dir.resolve()),
            '--cores', str(args.cores), '--rounds', str(args.rounds), '--bound-node', str(node)],
            env=dict(os.environ, OMP_PROC_BIND='FALSE'), check=True)
        return
    numa = subprocess.check_output(['numactl', '--show'], text=True)
    policy = dict(line.split(':', 1) for line in numa.splitlines() if ':' in line)
    if policy.get('policy', '').strip() != 'bind' or policy.get('membind', '').split() != [str(args.bound_node)]:
        raise RuntimeError('NUMA memory policy is not bound to the selected node')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir/f'cpu-scaling-{time.time_ns()}.json'
    paths = [Path(__file__), ROOT/'scripts/backup-policy/persistent_stream.py',
             ROOT/'scripts/memory-experiment/stream-worker.c', ROOT/'scripts/memory-experiment/stream.c']
    data = dict(schema='cpu-scaling-check-v1', status='running', job_id=os.environ['SLURM_JOB_ID'],
        hostname=socket.gethostname(), cpu_affinity=allowed, numa=numa,
        cpu_description=subprocess.check_output(['lscpu'], text=True), rounds_per_command=args.rounds,
        working_set_bytes=24*67108864, measurements=[],
        source_sha256={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        limitation='CPU-only equal-work scaling on a shared host. No GPU-copy contention, no memory-controller counters, no inference or cache-policy benefit is established.')

    def save():
        temporary = output.with_suffix('.tmp')
        temporary.write_text(json.dumps(data, indent=2)+'\n')
        temporary.replace(output)

    with tempfile.TemporaryDirectory(prefix='trace-scaling-') as temporary:
        binary = Path(temporary)/'worker'
        subprocess.run(['gcc', '-O3', '-fopenmp', '-DSTREAM_ARRAY_SIZE=67108864', '-DNTIMES=5',
            str(ROOT/'scripts/memory-experiment/stream-worker.c'), '-o', str(binary)], check=True)
        data['worker_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
        data['compiler'] = subprocess.check_output(['gcc', '--version'], text=True)
        counts = [n for n in [1, 2, 4, 8, 16, 32] if n <= args.cores]
        save()
        for order, sequence in [('ascending', counts), ('descending', list(reversed(counts)))]:
            for threads in sequence:
                cpus = allowed[:threads]
                env = dict(os.environ, OMP_NUM_THREADS=str(threads), OMP_DYNAMIC='FALSE',
                    OMP_PROC_BIND='close', OMP_PLACES='cores', OMP_WAIT_POLICY='PASSIVE')
                command = ['taskset', '-c', ','.join(map(str, cpus)), str(binary)]
                measurement = dict(order=order, threads=threads, assigned_cpus=cpus, samples=[])
                with PersistentStream(command, env, timeout=180) as worker:
                    if 'WORK' not in worker.ready.get('commands', []):
                        raise RuntimeError('Worker lacks equal-work support')
                    masks = {p.name: sorted(os.sched_getaffinity(int(p.name)))
                             for p in Path(f'/proc/{worker.process.pid}/task').iterdir()}
                    if (any(not set(mask) <= set(cpus) for mask in masks.values()) or
                            len(set(cpu for mask in masks.values() for cpu in mask)) != threads):
                        raise RuntimeError('OpenMP worker affinity differs from requested distinct cores')
                    measurement.update(ready=worker.ready, thread_masks=masks,
                        numa_maps=Path(f'/proc/{worker.process.pid}/numa_maps').read_text())
                    for _ in range(3):
                        worker.start(rounds=args.rounds)
                        result = worker.finish()
                        if result['rounds'] != args.rounds:
                            raise RuntimeError('Wrong work count')
                        measurement['samples'].append(result)
                measurement.update(full_validation=worker.full_validation, reaped=worker.process.poll() is not None)
                data['measurements'].append(measurement)
                save()
                print(json.dumps(dict(order=order, threads=threads,
                    seconds=[(r['end_ns']-r['start_ns'])/1e9 for r in measurement['samples']],
                    triad_GB_per_second=[r['kernels']['Triad']['average_GB_per_second'] for r in measurement['samples']])), flush=True)
    data['status'] = 'complete'
    save()
    print('Saved', output, flush=True)


if __name__ == '__main__':
    main()
