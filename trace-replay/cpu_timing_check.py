"""Measure prepared-tool timing on an allocated CPU node, without a GPU/model.

This is a harness diagnostic, not an inference or cache-policy experiment.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

from prepared_tools import PreparedTools
from timing import timing_fidelity

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts/memory-experiment'))
from activity import ActivityClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--bound-node', type=int)
    parser.add_argument('--array-elements', type=int, default=67108864)
    args = parser.parse_args()
    if not os.environ.get('SLURM_JOB_ID'):
        parser.error('Run inside a Slurm allocation, never on the login node')
    if not 262144 <= args.array_elements <= 134217728:
        parser.error('array-elements must be 262144..134217728; account for two workers and host RAM')
    source_paths = [Path(__file__), ROOT/'scripts/trace-replay/prepared_tools.py',
        ROOT/'scripts/trace-replay/timing.py', ROOT/'scripts/backup-policy/persistent_stream.py',
        ROOT/'scripts/memory-experiment/stream-worker.c', ROOT/'scripts/memory-experiment/stream.c',
        ROOT/'sglang/python/sglang/srt/mem_cache/stream_activity.py']
    source_hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in source_paths}
    allowed = sorted(os.sched_getaffinity(0))
    groups, seen = {}, set()
    for cpu in allowed:
        root = Path(f'/sys/devices/system/cpu/cpu{cpu}')
        identity = tuple((root/'topology'/name).read_text().strip()
                         for name in ['physical_package_id', 'core_id'])
        if identity in seen:
            continue
        seen.add(identity)
        for node in root.glob('node[0-9]*'):
            groups.setdefault(int(node.name[4:]), []).append(cpu)
    choices = [(node, cpus[:8]) for node, cpus in groups.items() if len(cpus) >= 8]
    if not choices:
        raise RuntimeError(f'Need eight distinct physical cores on one NUMA node: {groups}')
    if args.bound_node is None:
        node, cpus = choices[0]
        env = dict(os.environ, OMP_PROC_BIND='FALSE')
        subprocess.run(['numactl', '--physcpubind='+','.join(map(str, cpus)),
            '--membind='+str(node), sys.executable, str(Path(__file__).resolve()),
            '--output-dir', str(args.output_dir.resolve()), '--bound-node', str(node),
            '--array-elements', str(args.array_elements)],
            env=env, check=True)
        return
    numa = subprocess.check_output(['numactl', '--show'], text=True)
    policy = dict(line.split(':', 1) for line in numa.splitlines() if ':' in line)
    if (policy.get('policy', '').strip() != 'bind' or
            policy.get('membind', '').split() != [str(args.bound_node)]):
        raise RuntimeError('Missing verified NUMA memory binding')
    spec = importlib.util.spec_from_file_location('timing_controller',
        ROOT/'sglang/python/sglang/srt/mem_cache/stream_activity.py')
    control_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(control_module)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    measurements = []
    with tempfile.TemporaryDirectory(prefix='trace-timing-') as temporary:
        tmp = Path(temporary)
        worker = tmp/'worker'
        compile_command = ['gcc', '-O3', '-fopenmp', f'-DSTREAM_ARRAY_SIZE={args.array_elements}',
            '-DNTIMES=5', str(ROOT/'scripts/memory-experiment/stream-worker.c'), '-o', str(worker)]
        subprocess.run(compile_command, check=True)
        controller = control_module.ActivityController('normal', lease=0.5)
        control_socket = tmp/'control.sock'
        controller.serve(control_socket)
        try:
            for kind, workers, threads in [('delay', 0, 1), ('stream', 1, 8), ('stream', 2, 4)]:
                configuration = SimpleNamespace(tool_workload=kind, threads=threads,
                    max_stream_processes=workers, stream_worker=worker, tool_timeout=30)
                condition = dict(kind=kind, workers=workers, threads_per_worker=threads, samples=[])
                with PreparedTools(configuration) as prepared, ThreadPoolExecutor(max_workers=2) as pool:
                    condition['preparation'] = prepared.initial

                    def run_tool(seconds, epoch):
                        cancel = threading.Event()
                        cancel.wait(max(0, (epoch-time.monotonic_ns())/1e9))
                        record = dict(trace_tool=dict(duration_seconds=seconds), planned_start_ns=epoch)
                        client = ActivityClient(control_socket, 'normal')
                        result = prepared.execute(record['trace_tool'], record, client, cancel, timeout=30)
                        record.update(actual_seconds=result['elapsed_seconds'],
                            launch_lag_seconds=(record['start_ns']-epoch)/1e9,
                            result=result, activity_events=client.events)
                        return record

                    for seconds in [.004, .01, .05, .1, .5, 1, 2]:
                        for repeat in range(3):
                            epoch = time.monotonic_ns()+50_000_000
                            record = run_tool(seconds, epoch)
                            condition['samples'].append(dict(test='single', target_seconds=seconds,
                                repeat=repeat, tools=[record], timing=timing_fidelity([record])))
                    for repeat in range(3):
                        epoch = time.monotonic_ns()+50_000_000
                        futures = [pool.submit(run_tool, 1, epoch) for _ in range(2)]
                        records = [future.result() for future in futures]
                        condition['samples'].append(dict(test='simultaneous_pair', target_seconds=1,
                            repeat=repeat, tools=records, timing=timing_fidelity(records)))
                condition['final'] = prepared.final()
                measurements.append(condition)
                print(json.dumps(dict(kind=kind, workers=workers, threads=threads,
                    timing_passed=sum(s['timing']['passed'] for s in condition['samples']),
                    samples=len(condition['samples']))), flush=True)
            final = controller.snapshot()
            if final['active_tool_ids'] or final['counters']['TOOL_EXPIRED']:
                raise RuntimeError('Activity leases failed to close cleanly')
        finally:
            controller.close()
    result = dict(schema='cpu-timing-check-v1', job_id=os.environ['SLURM_JOB_ID'],
        hostname=socket.gethostname(), cpu_affinity=allowed, numa=numa,
        array_elements=args.array_elements, working_set_bytes=24*args.array_elements,
        source_sha256=source_hashes,
        cpu_description=subprocess.check_output(['lscpu'], text=True),
        compile_command=compile_command, measurements=measurements, controller_final=final,
        limitation='CPU-only harness measurement on this host. No GPU, model server or cache-policy result; calibration cannot be transferred to another CPU model.')
    output = args.output_dir/f'cpu-timing-{time.time_ns()}.json'
    output.write_text(json.dumps(result, indent=2)+'\n')
    print('Saved', output, flush=True)


if __name__ == '__main__':
    main()
