"""Profile a real command; default is a fixed CPU-registered SGLang test subset."""
import argparse
import json
import os
import platform
import signal
import subprocess
import sys
import time
from pathlib import Path

import psutil

from dram import Dram

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent


def sample_tree(root):
    cpu = rss = 0
    processes = {}
    try:
        children = [root] + root.children(recursive=True)
    except psutil.Error:
        return processes, rss
    for process in children:
        try:
            timing = process.cpu_times()
            processes[(process.pid, process.create_time())] = timing.user + timing.system
            rss += process.memory_info().rss
        except psutil.Error:
            pass
    return processes, rss


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--interval", type=float, default=0.1)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--check-counters", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.interval <= 0 or args.trials < 1:
        parser.error("interval and trials must be positive")
    dram = None
    warning = None
    try:
        dram = Dram()
        print(f"DRAM counters available: {len(dram.counters)} socket/channel events", flush=True)
    except Exception as error:
        warning = str(error)
        print(f"DRAM bandwidth unavailable: {warning}. CPU/RSS profiling remains available.", flush=True)
    if args.check_counters:
        if dram:
            before = dram.read()
            start = time.monotonic()
            time.sleep(0.2)
            print("Read/write GB/s:", dram.rates(before, dram.read(), time.monotonic() - start))
            dram.close()
        return
    command = args.command
    if command[:1] == ["--"]:
        command = command[1:]
    default = not command
    if default:
        command = [sys.executable, "-m", "pytest", "-q", "--import-mode=importlib", "-p", "timeline_plugin",
                   "sglang/test/registered/unit/mem_cache/test_evict_policy.py",
                   "sglang/test/registered/unit/sampling/test_sampling_params.py"]
    directory = args.output_dir or ROOT / "results" / f"tool-profile-{time.time_ns()}"
    directory.mkdir(parents=True, exist_ok=False)
    try:
        commit = subprocess.check_output(["git", "-C", str(ROOT / "sglang"), "rev-parse", "HEAD"], text=True).strip()
    except subprocess.SubprocessError:
        commit = None
    metadata = {"command": command, "host": platform.node(), "slurm_job_id": os.getenv("SLURM_JOB_ID"),
                "affinity": sorted(os.sched_getaffinity(0)), "interval_seconds": args.interval,
                "sglang_commit": commit, "dram_unavailable_reason": warning,
                "dram_scope": "All exposed IMC sockets/channels, including other users",
                "dram_events": [list(counter[1:]) for counter in dram.counters] if dram else [],
                "cpu_scope": "Sampled command process tree; 100 percent means one CPU core",
                "rss_scope": "Sum of process RSS; shared pages may be counted more than once"}
    (directory / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    status = 0
    try:
        for trial in range(1, args.trials + 1):
            events = directory / f"trial-{trial}-events.jsonl"
            env = os.environ.copy()
            # SGLang test_utils parses the first character as a port offset.
            # An invalid numeric ordinal hides GPUs without its empty-string bug.
            env.update(CUDA_VISIBLE_DEVICES="999", OMP_NUM_THREADS="3", MKL_NUM_THREADS="3",
                       PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", TEST_TIMELINE_FILE=str(events.resolve()),
                       PYTHONPATH=os.pathsep.join([str(ROOT / "sglang/python"), str(HERE), env.get("PYTHONPATH", "")]))
            rows = []
            with (directory / f"trial-{trial}.log").open("w") as log:
                start_ns = time.monotonic_ns()
                process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                root = psutil.Process(process.pid)
                previous, _ = sample_tree(root)
                old_dram = dram.read() if dram else None
                previous_ns = time.monotonic_ns()
                try:
                    while process.poll() is None:
                        time.sleep(args.interval)
                        now = time.monotonic_ns()
                        current, rss = sample_tree(root)
                        dt = (now - previous_ns) / 1e9
                        cpu_seconds = sum(max(0, total - previous.get(key, 0)) for key, total in current.items())
                        reads = writes = None
                        if dram:
                            new_dram = dram.read()
                            reads, writes = dram.rates(old_dram, new_dram, dt)
                            old_dram = new_dram
                        rows.append({"elapsed_seconds": (now - start_ns) / 1e9,
                                     "cpu_percent": cpu_seconds / dt * 100,
                                     "rss_GiB": rss / 2**30, "dram_read_GB_s": reads, "dram_write_GB_s": writes})
                        previous, previous_ns = current, now
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGTERM)
                        process.wait()
                returncode = process.wait()
                end_ns = time.monotonic_ns()
            data = {"trial": trial, "start_ns": start_ns, "end_ns": end_ns,
                    "duration_seconds": (end_ns - start_ns) / 1e9, "returncode": returncode, "samples": rows}
            path = directory / f"trial-{trial}.json"
            path.write_text(json.dumps(data, indent=2) + "\n")
            subprocess.run([sys.executable, str(HERE / "plot.py"), str(path)], check=True)
            print(f"Trial {trial}: {data['duration_seconds']:.2f}s, exit={returncode}; {path.with_suffix('.svg')}", flush=True)
            if returncode:
                print(f"Workload failed; inspect {directory / f'trial-{trial}.log'}", flush=True)
                status = 1
                break
    finally:
        if dram:
            dram.close()
    print(f"Saved {directory}", flush=True)
    return status


if __name__ == "__main__":
    sys.exit(main())
