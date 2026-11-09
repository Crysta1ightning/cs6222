"""Prepare or run one STREAM-alone vs pinned GPU-to-host-copy mechanism check.

No model server is launched. The default dry-run is CPU-only; execution requires
--run explicitly. A copy-only stressor is not an actual HiCache policy result.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backup-policy'))
from persistent_stream import PersistentStream


def cpus(text):
    result = set()
    for item in text.split(','):
        ends = item.split('-')
        result.update(range(int(ends[0]), int(ends[-1])+1))
    return result


def assess(alone, copying, copies, minimum_slowdown=5):
    start, end = copying['start_ns'], copying['end_ns']
    contained = [c for c in copies if start <= c['start_ns'] < c['end_ns'] <= end]
    before = alone['kernels']['Triad']['average_GB_per_second']
    after = copying['kernels']['Triad']['average_GB_per_second']
    slowdown = 100*(1-after/before)
    return dict(valid_overlap=bool(contained), fully_contained_copies=len(contained),
        stream_alone_triad_GB_per_second=before, with_copies_triad_GB_per_second=after,
        observed_slowdown_percent=slowdown,
        minimum_slowdown_percent=minimum_slowdown,
        candidate_contention=bool(contained) and slowdown >= minimum_slowdown,
        inference='One paired copy-only diagnostic, fixed order; not a repeated policy trial or statistical proof.',
        overlap_scope='Host enqueue-to-synchronize envelopes, not exact physical DMA timestamps.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', action='store_true', help='Actually use the allocated GPU; omitted means dry-run')
    p.add_argument('--seconds', type=float, default=10)
    p.add_argument('--copy-mib', type=int, default=64)
    p.add_argument('--copy-period', type=float, default=0,
                   help='Seconds between copy starts; zero intentionally stresses continuous copying')
    p.add_argument('--minimum-slowdown-percent', type=float, default=5)
    p.add_argument('--order', choices=['alone-first', 'copies-first'], default='alone-first',
                   help='Counterbalance paired measurements across separate invocations')
    p.add_argument('--completion', choices=['timed', 'fixed-work'], default='timed',
                   help='Fixed-work compares equal operation counts so contention can lengthen completion time')
    p.add_argument('--server-cpus', default=os.environ.get('EXP_SERVER_CPUS'))
    p.add_argument('--tool-cpus', default=os.environ.get('EXP_TOOL_CPUS'))
    p.add_argument('--numa-node', default=os.environ.get('EXP_NUMA_NODE'))
    p.add_argument('--threads', type=int, default=int(os.environ.get('EXP_THREADS', '1')))
    p.add_argument('--worker', type=Path, default=Path(__file__).resolve().parents[1]/'memory-experiment/stream-worker')
    p.add_argument('--output-dir', type=Path, default=Path(__file__).resolve().parents[1]/'results/contention-check')
    args = p.parse_args()
    if not math.isfinite(args.seconds) or not 0 < args.seconds <= 300 or not 1 <= args.copy_mib <= 1024:
        p.error('seconds must be in (0,300]; copy-mib in [1,1024]')
    if not math.isfinite(args.copy_period) or args.copy_period < 0 or not math.isfinite(args.minimum_slowdown_percent) or args.minimum_slowdown_percent < 0 or args.threads < 1:
        p.error('Invalid period, slowdown threshold, or thread count')
    config = {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()}
    if not args.run:
        print(json.dumps(dict(status='dry-run', configuration=config,
            conditions=['prepared STREAM alone', 'same STREAM + pinned GPU-to-host copies'],
            guarantees=['Same worker, buffers, CPU/NUMA binding and kernel count per block',
                'Allocation, first touch, CUDA initialization and warmup outside measured intervals',
                'Require copy overlap and full numerical validation before interpreting slowdown'],
            interpretation='Tests whether PCIe D2H traffic can contend with CPU memory work; does not establish actual HiCache interference.'), indent=2))
        return
    if not os.environ.get('SLURM_JOB_ID') or not all([args.server_cpus, args.tool_cpus, args.numa_node]):
        p.error('Use an existing prepared Slurm allocation with server/tool CPUs and NUMA node')
    server, tool = cpus(args.server_cpus), cpus(args.tool_cpus)
    if not server or not tool or server & tool or not server | tool <= os.sched_getaffinity(0) or args.threads > len(tool):
        p.error('Need disjoint allocated CPU sets and enough tool cores; start with full allocation affinity')
    identities = set()
    for cpu in server | tool:
        root = Path(f'/sys/devices/system/cpu/cpu{cpu}')
        identity = tuple((root/'topology'/name).read_text().strip() for name in ['physical_package_id','core_id'])
        if not (root/f'node{args.numa_node}').exists() or identity in identities:
            p.error('All CPU IDs must be distinct physical cores on the selected NUMA node')
        identities.add(identity)
    numa = subprocess.check_output(['numactl', '--show'], text=True)
    policy = dict(line.split(':', 1) for line in numa.splitlines() if ':' in line)
    if policy.get('policy', '').strip() != 'bind' or policy.get('membind', '').split() != [str(args.numa_node)]:
        p.error('Launch with numactl --membind="$EXP_NUMA_NODE" so pinned buffers use the same memory node')
    with socket.socket() as connection:
        connection.settimeout(1)
        if connection.connect_ex(('127.0.0.1', 30000)) == 0:
            p.error('Stop the model server before the copy-only mechanism diagnostic')
    os.sched_setaffinity(0, server)
    os.environ['OMP_PROC_BIND'] = 'FALSE'
    import torch  # Only --run loads CUDA-capable libraries.
    torch.set_num_threads(1)
    torch.cuda.set_device(0)
    device = torch.ones(args.copy_mib*1024*1024, dtype=torch.uint8, device='cuda')
    host = torch.empty_like(device, device='cpu', pin_memory=True)
    host.copy_(device, non_blocking=True)
    torch.cuda.synchronize()
    if not bool((host == 1).all()):
        raise RuntimeError('Pinned-buffer warmup validation failed')
    env = dict(os.environ, OMP_NUM_THREADS=str(args.threads), OMP_DYNAMIC='FALSE',
               OMP_PROC_BIND='close', OMP_PLACES='cores', OMP_WAIT_POLICY='PASSIVE')
    command = ['numactl', '--physcpubind='+args.tool_cpus, '--membind='+str(args.numa_node), str(args.worker.resolve())]
    copies, errors = [], []
    ready, go, stop = threading.Event(), threading.Event(), threading.Event()

    def copy_work():
        try:
            torch.cuda.set_device(0)
            stream = torch.cuda.Stream()
            with torch.cuda.stream(stream):
                host.copy_(device, non_blocking=True)
                stream.synchronize()
                ready.set()
                if not go.wait(timeout=30):
                    raise RuntimeError('Copy start handshake timed out')
                while not stop.is_set():
                    start = time.monotonic_ns()
                    a, b = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
                    a.record(stream)
                    host.copy_(device, non_blocking=True)
                    b.record(stream)
                    b.synchronize()
                    copies.append(dict(start_ns=start, end_ns=time.monotonic_ns(),
                        bytes=host.numel(), cuda_copy_ms=a.elapsed_time(b)))
                    stop.wait(max(0, args.copy_period-(time.monotonic_ns()-start)/1e9))
        except BaseException as exc:
            errors.append(str(exc))
            ready.set()

    with PersistentStream(command, env, timeout=args.seconds+30) as worker:
        if worker.ready.get('protocol') != 'timed-stream-v1':
            raise RuntimeError('Rebuild the timed persistent STREAM worker')
        calibration = None
        rounds = None
        if args.completion == 'fixed-work':
            if 'WORK' not in worker.ready.get('commands', []):
                raise RuntimeError('Rebuild the worker with fixed-work support')
            worker.start(0)
            calibration = worker.finish()
            quantum = (calibration['end_ns']-calibration['start_ns'])/1e9
            rounds = max(1, math.ceil(args.seconds/quantum))
        threads = {path.name: sorted(os.sched_getaffinity(int(path.name)))
                   for path in Path(f'/proc/{worker.process.pid}/task').iterdir()}
        if any(not set(mask) <= tool for mask in threads.values()) or len(set(c for mask in threads.values() for c in mask)) < args.threads:
            raise RuntimeError('STREAM worker threads do not cover the requested allocated tool cores')
        def alone_work():
            worker.start(args.seconds if rounds is None else None, rounds=rounds)
            return worker.finish()

        def with_copy_work():
            thread = threading.Thread(target=copy_work)
            thread.start()
            try:
                if not ready.wait(timeout=30) or errors:
                    raise RuntimeError('CUDA copy warmup failed: '+str(errors))
                worker.start(args.seconds if rounds is None else None, rounds=rounds)
                go.set()
                return worker.finish()
            finally:
                stop.set()
                go.set()
                thread.join(timeout=30)
                if thread.is_alive() or errors:
                    raise RuntimeError('CUDA copy thread failed to finish: '+str(errors))

        if args.order == 'alone-first':
            alone, copying = alone_work(), with_copy_work()
        else:
            copying, alone = with_copy_work(), alone_work()
    if not worker.full_validation or not bool((host == 1).all()):
        raise RuntimeError('Final STREAM/pinned-copy validation failed')
    result = assess(alone, copying, copies, args.minimum_slowdown_percent)
    result['inference'] = 'One paired copy-only diagnostic; interpret with other counterbalanced pairs, not as a HiCache policy result.'
    result['order'] = args.order
    result['completion'] = args.completion
    result['same_round_count'] = alone['rounds'] == copying['rounds']
    result['observed_duration_increase_percent'] = 100*((copying['end_ns']-copying['start_ns'])/(alone['end_ns']-alone['start_ns'])-1)
    if rounds is not None and (alone['rounds'] != rounds or copying['rounds'] != rounds):
        raise RuntimeError('Fixed-work pair did not complete identical requested work')
    contained = [c for c in copies if copying['start_ns'] <= c['start_ns'] < c['end_ns'] <= copying['end_ns']]
    interval = (copying['end_ns']-copying['start_ns'])/1e9
    result['contained_copy_GB_per_second'] = sum(c['bytes'] for c in contained)/interval/1e9
    result['contained_cuda_copy_duty_fraction'] = sum(c['cuda_copy_ms'] for c in contained)/1000/interval
    if not result['valid_overlap']:
        raise RuntimeError('No copy completed inside the STREAM window; diagnostic is invalid')
    data = dict(schema='copy-contention-check-v1', job_id=os.environ['SLURM_JOB_ID'],
        hostname=socket.gethostname(), configuration=config, numa=numa,
        cuda_visible_devices=os.environ.get('CUDA_VISIBLE_DEVICES'), gpu=torch.cuda.get_device_name(0),
        worker_sha256=hashlib.sha256(args.worker.read_bytes()).hexdigest(),
        ready=worker.ready, stream_threads=threads, stream_full_validation=worker.full_validation,
        calibration=calibration, fixed_work_rounds=rounds,
        alone=alone, with_copies=copying, copies=copies, assessment=result,
        limitation='Continuous copies are an intentional upper-bound stressor, not the observed trace duty cycle, a model-server control, or proof of HiCache policy benefit.')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir/f'copy-contention-{time.time_ns()}.json'
    output.write_text(json.dumps(data, indent=2)+'\n')
    print(json.dumps(result, indent=2))
    print('Saved', output)


if __name__ == '__main__':
    def interrupted(signum, frame):
        raise KeyboardInterrupt(f'Copy diagnostic interrupted by signal {signum}')
    signal.signal(signal.SIGTERM, interrupted)
    main()
