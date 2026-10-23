"""Verify GPU-local 4+4 or 4+12 physical-core placement on an allocated L40S.

Run inside an 8- or 16-CPU Slurm step, after activating the project's environment.
This initializes a write-through server and runs STREAM once for placement
inspection. It does not measure inference/transfer interference.
"""

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.request

import psutil

ROOT = Path(__file__).resolve().parent.parent


def capture(command):
    return subprocess.check_output(command, text=True, stderr=subprocess.STDOUT)


def snapshot(pid, directory, expected_cpus):
    process = psutil.Process(pid)
    directory.mkdir(parents=True, exist_ok=True)
    maps = Path(f"/proc/{pid}/numa_maps").read_text()
    (directory / "numa_maps.txt").write_text(maps)
    (directory / "status.txt").write_text(Path(f"/proc/{pid}/status").read_text())
    (directory / "numastat.txt").write_text(capture(["numastat", "-p", str(pid)]))
    threads = {str(t.id): sorted(os.sched_getaffinity(t.id)) for t in process.threads()}
    if any(not set(cpus) <= set(expected_cpus) for cpus in threads.values()):
        raise RuntimeError(f"PID {pid} has a thread outside its assigned CPU set")
    # Report anonymous mappings separately from shared libraries/file cache.
    # These are process-wide counts, not an identification of the KV buffer.
    anon_bytes = {}
    for line in maps.splitlines():
        fields = dict(field.split("=", 1) for field in line.split() if "=" in field)
        if "anon" not in fields:
            continue
        page_bytes = int(fields.get("kernelpagesize_kB", "4")) * 1024
        for key, value in fields.items():
            if key.startswith("N") and key[1:].isdigit():
                anon_bytes[key] = anon_bytes.get(key, 0) + int(value) * page_bytes
    result = {"pid": pid, "command": process.cmdline(), "threads": threads,
              "anonymous_mapping_resident_bytes_by_node": anon_bytes}
    (directory / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    os.chdir(ROOT)
    output = Path(os.environ.get('CS6222_RESULTS_DIR', ROOT / 'results')) / f"numa-check-{os.environ.get('SLURM_JOB_ID', 'local')}-{time.time_ns()}"
    output.mkdir(parents=True)
    print(f"Results: {output}", flush=True)
    for name, command in {
        "topology": ["nvidia-smi", "topo", "-m"],
        "numa-hardware": ["numactl", "--hardware"],
        "cpu-topology": ["lscpu", "-e=CPU,CORE,SOCKET,NODE"],
    }.items():
        (output / f"{name}.txt").write_text(capture(command))
    rows = [row.split() for row in (output / "topology.txt").read_text().splitlines()]
    gpu = next(row for row in rows if row and row[0] == "GPU0")
    node = int(gpu[-2])
    topology = json.loads(capture(["lscpu", "--json", "-e=CPU,CORE,SOCKET,NODE"]))["cpus"]
    allowed = sorted(os.sched_getaffinity(0))
    local = [row for row in topology if int(row["cpu"]) in allowed and int(row["node"]) == node]
    cores = {(int(row["socket"]), int(row["core"])) for row in local}
    count = len(allowed)
    if count not in (8, 16) or len(local) != count or len(cores) != count:
        raise RuntimeError(f"Need 8 or 16 distinct physical cores on GPU NUMA {node}; allowed={allowed}, local={local}")
    server_cpus, tool_cpus = allowed[:4], allowed[4:]
    config = {"host": socket.gethostname(), "gpu_numa_node": node,
              "server_cpus": server_cpus, "tool_cpus": tool_cpus,
              "tool_threads": len(tool_cpus), "topology": local}
    (output / "bindings.json").write_text(json.dumps(config, indent=2) + "\n")
    print(json.dumps(config), flush=True)

    def bind(cpus):
        return ["numactl", "--physcpubind=" + ",".join(map(str, cpus)), f"--membind={node}"]

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = dict(os.environ, HF_HUB_OFFLINE="1")
    for key in list(env):
        if key.startswith("CS6222_") or key.startswith("SGLANG_ENABLE_NVTX_"):
            env.pop(key)
    server = stream = None
    try:
        with (output / "server.log").open("w") as log:
            command = bind(server_cpus) + [sys.executable, "-m", "sglang.launch_server",
                "--model-path", "Qwen/Qwen3-0.6B", "--host", "127.0.0.1", "--port", str(port),
                "--max-total-tokens", "8192", "--context-length", "4096", "--mem-fraction-static", "0.7",
                "--enable-metrics", "--disable-cuda-graph", "--enable-hierarchical-cache",
                "--hicache-size", "4", "--hicache-write-policy", "write_through"]
            server = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                      env=env, start_new_session=True)
            deadline = time.monotonic() + 600
            while True:
                if server.poll() is not None:
                    raise RuntimeError("Server exited; inspect server.log")
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
                        if response.status == 200:
                            break
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    raise RuntimeError("Server readiness timed out")
                time.sleep(1)
            processes = [psutil.Process(server.pid)] + psutil.Process(server.pid).children(recursive=True)
            report = {"server": [snapshot(p.pid, output / f"server-{p.pid}", server_cpus)
                                 for p in processes if p.is_running()]}
            stream_env = dict(env, OMP_NUM_THREADS=str(len(tool_cpus)), OMP_DYNAMIC="FALSE",
                              OMP_PROC_BIND="close", OMP_PLACES="cores")
            with (output / "stream.log").open("w") as stream_log:
                stream = subprocess.Popen(bind(tool_cpus) + [str(ROOT / "memory-experiment/stream")],
                    stdout=stream_log, stderr=subprocess.STDOUT, env=stream_env, start_new_session=True)
                time.sleep(1)
                report["stream"] = snapshot(stream.pid, output / f"stream-{stream.pid}", tool_cpus)
                if stream.wait(timeout=180) != 0:
                    raise RuntimeError("STREAM failed")
            if "Solution Validates" not in (output / "stream.log").read_text():
                raise RuntimeError("STREAM numerical validation failed")
            for component in (report["server"], [report["stream"]]):
                pages = {}
                for item in component:
                    for key, value in item["anonymous_mapping_resident_bytes_by_node"].items():
                        pages[key] = pages.get(key, 0) + value
                total = sum(pages.values())
                if not total or pages.get(f"N{node}", 0) / total < .95:
                    raise RuntimeError(f"Anonymous mapping placement is not >=95% local: {pages}")
            report["placement_check_passed"] = True
            report["scope"] = "Thread affinity and process-wide anonymous page placement; KV buffer not individually identified; no concurrent inference load."
            (output / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
            print("PASS: separate physical cores, same GPU-local NUMA node, >=95% local anonymous pages", flush=True)
    finally:
        for child in (stream, server):
            if child is not None:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    continue
                try:
                    child.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()


if __name__ == "__main__":
    main()
