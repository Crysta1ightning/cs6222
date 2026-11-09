"""Prepared CPU surrogates: no per-tool Python startup or array allocation."""
import os
from pathlib import Path
import queue
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backup-policy'))
from persistent_stream import PersistentStream


class PreparedTools:
    def __init__(self, args):
        self.args = args
        self.workers = []
        self.available = queue.Queue()
        self.initial = dict(protocol='prepared-tools-v1', workers=[],
                            startup_start_ns=time.monotonic_ns())

    def __enter__(self):
        try:
            if self.args.tool_workload == 'stream':
                allowed = set(os.sched_getaffinity(0))
                if len(allowed) < self.args.threads * self.args.max_stream_processes:
                    raise RuntimeError('Need threads * workers distinct allocated tool cores; launch with OMP_PROC_BIND=FALSE')
                env = dict(os.environ, OMP_NUM_THREADS=str(self.args.threads),
                           OMP_DYNAMIC='FALSE', OMP_PROC_BIND='close', OMP_PLACES='cores',
                           OMP_WAIT_POLICY='PASSIVE')
                for index in range(self.args.max_stream_processes):
                    assigned = sorted(allowed)[index*self.args.threads:(index+1)*self.args.threads]
                    worker = PersistentStream(['taskset', '-c', ','.join(map(str, assigned)),
                                              str(self.args.stream_worker.resolve())], env,
                                              timeout=self.args.tool_timeout)
                    self.workers.append(worker)
                    worker.__enter__()
                    if worker.ready.get('protocol') != 'timed-stream-v1' or not worker.ready.get('full_validation'):
                        raise RuntimeError('Rebuild stream-worker: timed-stream-v1 with validated warmup required')
                    worker.start(0)
                    calibration = worker.finish()
                    threads = {}
                    for path in Path(f'/proc/{worker.process.pid}/task').iterdir():
                        threads[path.name] = sorted(os.sched_getaffinity(int(path.name)))
                    if any(not set(cpus) <= set(assigned) for cpus in threads.values()):
                        raise RuntimeError('Prepared worker escaped the assigned tool CPU set')
                    if len(set(cpu for cpus in threads.values() for cpu in cpus)) < self.args.threads:
                        raise RuntimeError('Prepared worker does not cover the requested tool cores')
                    self.initial['workers'].append(dict(pid=worker.process.pid, ready=worker.ready, assigned_cpus=assigned,
                        minimum_round_seconds=(calibration['end_ns']-calibration['start_ns'])/1e9,
                        threads=threads, numa_maps=Path(f'/proc/{worker.process.pid}/numa_maps').read_text()))
                    self.available.put(worker)
            self.initial['startup_end_ns'] = time.monotonic_ns()
            return self
        except BaseException:
            self.close(failed=True)
            raise

    def execute(self, tool, record, client, cancel, timeout):
        worker = self.available.get() if self.args.tool_workload == 'stream' else None
        tool_id = uuid.uuid4().hex
        record.update(tool_id=tool_id, warmup=False, runtime='prepared-tools-v1')
        deadline = time.monotonic() + timeout
        heartbeat_at = time.monotonic()

        def check():
            nonlocal heartbeat_at
            if cancel.is_set():
                raise RuntimeError('Prepared tool canceled after replay failure')
            if time.monotonic() >= deadline:
                raise TimeoutError('Prepared tool exceeded tool/replay timeout')
            if time.monotonic() >= heartbeat_at:
                client.rpc('TOOL_HEARTBEAT', tool_id=tool_id)
                heartbeat_at = time.monotonic() + min(2, record['start_ack']['lease_seconds'] / 3)

        try:
            record['start_ack'] = client.rpc('TOOL_START', tool_id=tool_id, warmup=False)
            heartbeat_at = time.monotonic() + min(2, record['start_ack']['lease_seconds'] / 3)
            command_start = time.monotonic_ns()
            if worker:
                started = worker.start(tool['duration_seconds'], on_wait=check,
                                       timeout=max(0.001, deadline-time.monotonic()))
                record['start_ns'] = started['start_ns']
                result = worker.finish(on_wait=check, timeout=max(0.001, deadline-time.monotonic()))
                record['end_ns'] = result['end_ns']
                if result['start_ns'] != record['start_ns']:
                    raise RuntimeError('Prepared worker command timestamps do not match')
                runs = [dict(kernels=result['kernels'], rounds=result['rounds'],
                    array_elements=worker.ready['array_elements'],
                    working_set_bytes=24*worker.ready['array_elements'])]
                record['worker_pid'] = worker.process.pid
            else:
                record['start_ns'] = time.monotonic_ns()
                end = time.monotonic() + tool['duration_seconds']
                while time.monotonic() < end:
                    check()
                    cancel.wait(min(0.02, max(0, end-time.monotonic())))
                check()
                record['end_ns'] = time.monotonic_ns()
                runs = []
            record['command_roundtrip_seconds'] = (time.monotonic_ns()-command_start)/1e9
            if not record['start_ack']['ack_monotonic_ns'] <= record['start_ns'] <= record['end_ns']:
                raise RuntimeError('Prepared tool ran before acknowledged activity')
            record['returncode'] = 0
            return dict(kind=self.args.tool_workload, target_seconds=tool['duration_seconds'],
                        elapsed_seconds=(record['end_ns']-record['start_ns'])/1e9,
                        stream_runs=runs, buffers_reused=bool(worker))
        except BaseException as exc:
            record['error'] = str(exc) or type(exc).__name__
            if worker:
                worker.terminate()  # END must follow stopping and reaping work.
            raise
        finally:
            record.setdefault('end_ns', time.monotonic_ns())
            try:
                record['end_ack'] = client.end(tool_id)
            finally:
                if worker:
                    self.available.put(worker)

    def close(self, failed=False):
        error = None
        for worker in self.workers:
            if worker.process is None:
                continue
            try:
                worker.__exit__(RuntimeError if failed else None, None, None)
            except BaseException as exc:
                error = error or exc
        if error:
            raise error

    def __exit__(self, exc_type, exc, traceback):
        self.close(failed=exc_type is not None)

    def final(self):
        return dict(protocol='prepared-tools-v1', workers=[dict(pid=w.process.pid,
            full_validation=w.full_validation, reaped=w.process.poll() is not None) for w in self.workers])
