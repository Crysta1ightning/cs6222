"""Measure STREAM alone, or alongside fresh-prompt SGLang inference."""

import argparse
import hashlib
import json
import os
import re
import socket
import statistics
import subprocess
import threading
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
COUNTERS = ("sglang:hicache_backup_bytes_total", "sglang:load_back_bytes_total")


def parse_stream(output):
    if "Solution Validates" not in output:
        raise RuntimeError("STREAM numerical validation failed; inspect raw log")
    array = re.search(r"Array size = (\d+)", output)
    if not array:
        raise RuntimeError("Could not read STREAM array size")
    elements = int(array.group(1))
    kernels = {}
    for name, best, avg, minimum, maximum in re.findall(
        r"^(Copy|Scale|Add|Triad):\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)",
        output, re.MULTILINE,
    ):
        accesses = 2 if name in ("Copy", "Scale") else 3
        avg = float(avg)
        if avg <= 0:
            raise RuntimeError("STREAM timing too short; use larger arrays")
        kernels[name] = {
            "average_seconds": avg,
            "average_GB_per_second": accesses * 8 * elements / avg / 1e9,
            "best_MB_per_second": float(best),
            "min_seconds": float(minimum), "max_seconds": float(maximum),
        }
    if len(kernels) != 4:
        raise RuntimeError("Missing STREAM kernel results")
    return {"array_elements": elements, "working_set_bytes": 3 * 8 * elements,
            "kernels": kernels}


def command_output(cmd):
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return {"returncode": result.returncode, "stdout": result.stdout,
                "stderr": result.stderr}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"error": str(exc)}


class Server:
    def __init__(self, url):
        import requests
        self.requests = requests
        self.url = url.rstrip("/")

    def get(self, path):
        response = self.requests.get(self.url + path, timeout=15)
        response.raise_for_status()
        return response

    def metrics(self):
        totals = dict.fromkeys(COUNTERS, 0.0)
        for line in self.get("/metrics").text.splitlines():
            if not line or line.startswith("#"):
                continue
            fields = line.split()
            name = fields[0].split("{")[0]
            if name in totals:
                totals[name] += float(fields[1])
        return totals

    def settle(self):
        # Observe at least five seconds with no counter changes after requests
        # stop. This is an accounting safeguard, not physical timing proof.
        previous = self.metrics()
        stable = 0
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            time.sleep(1)
            current = self.metrics()
            stable = stable + 1 if current == previous else 0
            if stable >= 5:
                return current
            previous = current
        raise RuntimeError("Transfer counters did not settle within 30 seconds")


class Load:
    def __init__(self, server, model):
        from transformers import AutoTokenizer
        self.server = server
        tokenizer = AutoTokenizer.from_pretrained(model)
        # All tokenization happens before measurement. Each request gets a new
        # ID before its body, so its long prefix has not already been cached.
        self.prefix = tokenizer.encode(f"Run {uuid.uuid4().hex}. Record ")
        self.digits = {c: tokenizer.encode(c, add_special_tokens=False)
                       for c in "0123456789"}
        self.separator = tokenizer.encode(".\n", add_special_tokens=False)
        self.body = tokenizer.encode("Repository descriptions and example records.\n" * 600,
                                     add_special_tokens=False)
        self.index = 0
        self.records = []

    def start(self):
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.errors = []
        self.thread = threading.Thread(target=self.worker, daemon=True)
        self.thread.start()
        if not self.ready.wait(timeout=120):
            self.stop.set()
            raise RuntimeError("Inference did not warm up within 120 seconds")
        if self.errors:
            raise RuntimeError(self.errors[0])

    def worker(self):
        completed = 0
        try:
            with self.server.requests.Session() as session:
                while not self.stop.is_set():
                    index = self.index
                    self.index += 1
                    prefix = self.prefix + [t for c in f"{index:012d}" for t in self.digits[c]] + self.separator
                    ids = prefix + self.body[:2048 - len(prefix)]
                    start = time.monotonic_ns()
                    response = session.post(self.server.url + "/generate", json={
                        "input_ids": ids,
                        "sampling_params": {"max_new_tokens": 1, "temperature": 0},
                    }, timeout=120)
                    response.raise_for_status()
                    self.records.append({"index": index, "input_tokens": len(ids),
                                         "start_ns": start, "end_ns": time.monotonic_ns()})
                    completed += 1
                    if completed >= 2:
                        self.ready.set()
        except Exception as exc:
            self.errors.append(str(exc))
            self.ready.set()

    def finish(self):
        self.stop.set()
        self.thread.join(timeout=130)
        if self.thread.is_alive():
            raise RuntimeError("Inference worker failed to stop; do not start the next trial")
        if self.errors:
            raise RuntimeError(self.errors[0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("memory-only", "hicache", "no-hicache"), required=True)
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--threads", type=int, default=3)
    parser.add_argument("--inference-only", action="store_true",
                        help="measure the same inference workload without running STREAM")
    parser.add_argument("--seconds", type=float, default=30,
                        help="measurement seconds per inference-only trial")
    parser.add_argument("--url", default="http://127.0.0.1:30000")
    parser.add_argument("--model", default="Qwen/Qwen3-0.6B")
    parser.add_argument("--binary", type=Path, default=HERE / "stream")
    parser.add_argument("--output-dir", type=Path, default=HERE.parent / "results")
    args = parser.parse_args()
    if args.trials < 1 or args.threads < 1 or not 0 < args.seconds <= 3600:
        parser.error("trials/threads must be positive; seconds must be in (0, 3600]")
    if args.inference_only and args.mode == "memory-only":
        parser.error("--inference-only requires hicache or no-hicache mode")
    available = len(os.sched_getaffinity(0))
    allocated = int(os.environ.get("SLURM_CPUS_PER_TASK", available))
    if args.threads > min(available, allocated):
        parser.error("threads exceeds the visible or allocated CPU count")
    binary = args.binary.resolve()
    if not binary.is_file():
        parser.error("STREAM binary missing; run bash memory-experiment/build.sh first")
    env = dict(os.environ, OMP_NUM_THREADS=str(args.threads), OMP_DYNAMIC="FALSE",
               OMP_PROC_BIND="close", OMP_PLACES="cores")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    workload = "inference-only" if args.inference_only else "stream"
    stem = f"memory-{args.mode}-{'inference-only-' if args.inference_only else ''}{time.time_ns()}"
    output = args.output_dir / f"{stem}.json"
    data = {"mode": args.mode, "workload": workload, "hostname": socket.gethostname(),
            "measurement_seconds": args.seconds if args.inference_only else None,
            "job_id": os.environ.get("SLURM_JOB_ID"), "threads": args.threads,
            "cpu_affinity": sorted(os.sched_getaffinity(0)),
            "omp": {k: env[k] for k in ("OMP_NUM_THREADS", "OMP_DYNAMIC", "OMP_PROC_BIND", "OMP_PLACES")},
            "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "lscpu": command_output(["lscpu"]),
            "gpu": command_output(["nvidia-smi", "--query-gpu=name,memory.total,driver_version,pci.bus_id", "--format=csv"]),
            "gpu_topology": command_output(["nvidia-smi", "topo", "-m"]),
            "numa": command_output(["numactl", "--show"]),
            "trials": [], "requests": [],
            "limitation": "Transfer counters bracket the whole STREAM process, including initialization; they do not prove per-kernel physical overlap. Closed-loop request throughput can differ between server modes."}
    server = load = None

    def persist():
        data["requests"] = load.records if load else []
        output.write_text(json.dumps(data, indent=2) + "\n")

    try:
        if args.mode != "memory-only":
            server = Server(args.url)
            server.get("/health")
            data["server_info"] = server.get("/server_info").json()
            settings = data["server_info"].get("server_args", data["server_info"])
            enabled = settings.get("enable_hierarchical_cache")
            if not isinstance(enabled, bool):
                raise RuntimeError("Cannot verify server HiCache setting from /server_info")
            if enabled != (args.mode == "hicache"):
                raise RuntimeError("Server HiCache setting does not match --mode")
            if not settings.get("enable_metrics"):
                raise RuntimeError("Restart server with --enable-metrics")
            load = Load(server, args.model)
        print(f"Mode={args.mode}; threads={args.threads}; trials={args.trials}", flush=True)
        if not args.inference_only:
            print("Warming STREAM outside timed trials...", flush=True)
            warm = subprocess.run([str(binary)], env=env, capture_output=True, text=True, timeout=180)
            warm.check_returncode()
            parse_stream(warm.stdout)
        else:
            print(f"Inference alone: {args.seconds:g} seconds per trial; STREAM will not run.", flush=True)
        for trial in range(args.trials):
            before = dict.fromkeys(COUNTERS, 0.0)
            if server:
                server.settle()
                load.start()
                before = server.metrics()
            started = time.monotonic_ns()
            result = None
            if args.inference_only:
                time.sleep(args.seconds)
            else:
                result = subprocess.run([str(binary)], env=env, capture_output=True, text=True, timeout=180)
            ended = time.monotonic_ns()
            after = server.metrics() if server else dict(before)
            if load:
                load.finish()
                server.settle()
            raw = None
            measurement = {}
            if result is not None:
                raw = args.output_dir / f"{stem}-trial-{trial}.txt"
                raw.write_text(result.stdout + "\nSTDERR:\n" + result.stderr)
                result.check_returncode()
                measurement = parse_stream(result.stdout)
            duration = (ended - started) / 1e9
            completed = [r for r in (load.records if load else [])
                         if r["start_ns"] >= started and r["end_ns"] <= ended]
            deltas = {k: after[k] - before[k] for k in COUNTERS}
            warnings = []
            if args.mode == "hicache" and deltas[COUNTERS[0]] <= 0:
                warnings.append("No backup activity observed: not a valid backup-interference trial")
            if load and not completed:
                warnings.append("No full inference request completed inside benchmark window")
            if args.mode == "no-hicache" and any(deltas.values()):
                warnings.append("Unexpected HiCache transfer counters in disabled mode")
            record = {"trial": trial, "start_ns": started, "end_ns": ended,
                      "process_seconds": duration, **measurement,
                      "transfer_counter_deltas": deltas,
                      "backup_payload_GB_per_second": deltas[COUNTERS[0]] / duration / 1e9,
                      "fully_contained_requests": len(completed),
                      "requests_per_second": len(completed) / duration,
                      "raw_log": str(raw) if raw else None, "warnings": warnings}
            data["trials"].append(record)
            persist()
            description = "inference alone" if args.inference_only else f"Triad {record['kernels']['Triad']['average_GB_per_second']:.2f} GB/s"
            print(f"Trial {trial + 1}/{args.trials}: {description}; "
                  f"backup {record['backup_payload_GB_per_second']:.2f} GB/s; "
                  f"inference {record['requests_per_second']:.2f} req/s", flush=True)
            for warning in warnings:
                print(f"  WARNING: {warning}", flush=True)
        if args.inference_only:
            values = [r["requests_per_second"] for r in data["trials"]]
            print(f"Median inference: {statistics.median(values):.2f} req/s")
        else:
            values = [r["kernels"]["Triad"]["average_GB_per_second"] for r in data["trials"]]
            print(f"Median Triad: {statistics.median(values):.2f} GB/s")
    except BaseException as exc:
        data["error"] = str(exc) or type(exc).__name__
        raise
    finally:
        if load and hasattr(load, "thread") and load.thread.is_alive():
            load.stop.set()
            load.thread.join(timeout=130)
        persist()
        print(f"Saved {output}", flush=True)


if __name__ == "__main__":
    main()
