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
from collections import Counter
from pathlib import Path

from activity import ActivityClient, run_stream

HERE = Path(__file__).resolve().parent
COUNTERS = ("sglang:hicache_backup_bytes_total", "sglang:load_back_bytes_total")


def counter_delta(before, after):
    result = {}
    for key in COUNTERS:
        a, b = before.get(key), after.get(key)
        if a is None or b is None:
            result[key] = None
        elif b < a:
            raise RuntimeError("Transfer counters decreased; server reset/restart invalidates the trial")
        else:
            result[key] = b - a
    return result


def measurement_window(start, end, before, after, requests):
    seconds = (end - start) / 1e9
    deltas = counter_delta(before, after)
    completed = [r for r in requests if r["start_ns"] >= start and r["end_ns"] <= end]
    return dict(start_ns=start, end_ns=end, process_seconds=seconds,
                transfer_counter_deltas=deltas,
                backup_payload_GB_per_second=(deltas[COUNTERS[0]] / seconds / 1e9
                    if deltas[COUNTERS[0]] is not None and seconds > 0 else None),
                fully_contained_requests=len(completed),
                requests_per_second=len(completed) / seconds if seconds > 0 else None)


def source_version(path):
    revision = command_output(["git", "-C", str(path), "rev-parse", "HEAD"])
    diff = command_output(["git", "-C", str(path), "diff", "HEAD"])
    return dict(commit=revision.get("stdout", "").strip() or None,
                dirty_diff_sha256=hashlib.sha256(diff.get("stdout", "").encode()).hexdigest(),
                status=command_output(["git", "-C", str(path), "status", "--short"]))


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
        totals = dict.fromkeys(COUNTERS)
        for line in self.get("/metrics").text.splitlines():
            if not line or line.startswith("#"):
                continue
            fields = line.split()
            name = fields[0].split("{")[0]
            if name in totals:
                totals[name] = (totals[name] or 0.0) + float(fields[1])
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

    def start(self, warmup_requests=2):
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.errors = []
        self.ready_after = warmup_requests
        self.thread = threading.Thread(target=self.worker, daemon=True)
        self.thread.start()
        if not self.ready.wait(timeout=120):
            self.stop.set()
            raise RuntimeError("Inference did not warm up within 120 seconds")
        if self.errors:
            raise RuntimeError(self.errors[0])

    def one_request(self, session, *, warmup=False):
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
        self.records.append({"index": index, "input_tokens": len(ids), "warmup": warmup,
                             "start_ns": start, "end_ns": time.monotonic_ns()})

    def warmup(self):
        with self.server.requests.Session() as session:
            for _ in range(2):
                self.one_request(session, warmup=True)

    def worker(self):
        completed = 0
        try:
            with self.server.requests.Session() as session:
                if self.ready_after == 0:
                    self.ready.set()
                while not self.stop.is_set():
                    self.one_request(session)
                    completed += 1
                    if completed >= self.ready_after:
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
    parser.add_argument("--controller-mode", choices=("normal", "defer", "skip"), default="normal")
    parser.add_argument("--control-socket", type=Path, help="scheduler UNIX socket; explicit tool activity, not bandwidth")
    parser.add_argument("--post-tool-seconds", type=float, default=None,
                        help="subsequent inference duration; defaults to 5 with control channel, otherwise 0")
    parser.add_argument("--drain-timeout", type=float, default=30)
    parser.add_argument("--stream-timeout", type=float, default=180)
    args = parser.parse_args()
    if args.post_tool_seconds is None:
        args.post_tool_seconds = 5 if args.control_socket else 0
    if not 0 <= args.post_tool_seconds <= 3600 or not 0 < args.drain_timeout <= 300 or not 0 < args.stream_timeout <= 3600:
        parser.error("invalid post-tool duration, drain timeout, or STREAM timeout")
    if args.controller_mode != "normal" and (args.mode != "hicache" or not args.control_socket or args.post_tool_seconds <= 0):
        parser.error("defer/skip require --mode hicache, --control-socket, and a positive post-tool duration")
    if args.control_socket and args.mode != "hicache":
        parser.error("the activity controller is only supported with --mode hicache")
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
    stem = f"memory-{args.mode}-{'controller-' + args.controller_mode + '-' if args.control_socket else ''}{'inference-only-' if args.inference_only else ''}{time.time_ns()}"
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
            "controller_mode": args.controller_mode,
            "controller_enabled": bool(args.control_socket), "activity_signal": "STREAM subprocess activity; not bandwidth pressure",
            "post_tool_seconds": args.post_tool_seconds,
            "source_versions": {"experiments": source_version(HERE.parent),
                                "sglang": source_version(HERE.parent.parent / "sglang")},
            "trials": [], "requests": [], "stream_processes": [], "activity_events": [],
            "controller_events": [],
            "limitation": "HTTP counter observations bracket measurement windows and include scrape/accounting lag. Controller events are scheduler timestamps, not physical DMA start/end. Closed-loop fresh-prompt throughput can differ; this diagnostic cannot establish KV-reuse or end-to-end agent benefits."}
    server = load = client = None
    experiment_start_ns = time.monotonic_ns()

    def persist():
        data["requests"] = load.records if load else []
        if client:
            data["activity_events"] = client.events
            log = client.initial.get("log_path")
            if log:
                try:
                    lines = Path(log).read_text().splitlines(keepends=True)
                    data["controller_events"] = [event for line in lines if line.endswith("\n")
                        for event in [json.loads(line)] if event["monotonic_ns"] >= experiment_start_ns]
                    for trial in data["trials"]:
                        for window in trial["windows"].values():
                            events = [e for e in data["controller_events"]
                                      if window["start_ns"] <= e["monotonic_ns"] < window["end_ns"]]
                            window["controller_event_counts"] = dict(Counter(e["event"] for e in events))
                            window["backup_submitted_bytes"] = sum(e["bytes"] for e in events if e["event"] == "BACKUP_SUBMITTED")
                            window["backup_completed_bytes"] = sum(e["bytes"] for e in events if e["event"] == "BACKUP_COMPLETED")
                            window["controller_timing_scope"] = "scheduler event timestamps; completion acknowledgment is not physical copy start/end"
                except (OSError, ValueError) as exc:
                    data["controller_log_error"] = str(exc)
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
            data["write_policy"] = settings.get("hicache_write_policy")
            if args.control_socket:
                if settings.get("hicache_write_policy") != "write_through":
                    raise RuntimeError("Activity comparisons require the underlying write_through policy")
                client = ActivityClient(args.control_socket, args.controller_mode)
                if not client.initial.get("log_path"):
                    raise RuntimeError("Controller must have a JSONL event log configured")
                if client.initial["active_tool_ids"]:
                    raise RuntimeError("Another tool is active; use an isolated experiment server")
                data["controller_initial"] = client.initial
            load = Load(server, args.model)
        print(f"Mode={args.mode}; threads={args.threads}; trials={args.trials}", flush=True)
        if not args.inference_only:
            print("Warming STREAM outside timed trials...", flush=True)
            warm_record = {}
            data["stream_processes"].append(warm_record)
            warm = run_stream(binary, env, client=client, timeout=args.stream_timeout,
                              warmup=True, record=warm_record)
            warm.check_returncode()
            parse_stream(warm.stdout)
        else:
            print(f"Inference alone: {args.seconds:g} seconds per trial; STREAM will not run.", flush=True)
        for trial in range(args.trials):
            before = dict.fromkeys(COUNTERS)
            if server:
                server.settle()
                if client:
                    load.warmup()
                    client.wait_drained(args.drain_timeout)
                    server.settle()
                else:
                    load.start()
                before = server.metrics()
            started = time.monotonic_ns()
            whole_start = started
            result = None
            stream_record = {"trial": trial}
            metric_observations = [{"phase": "before_tool", "observed_ns": time.monotonic_ns(), "values": before}]
            if args.inference_only:
                if client:
                    load.start(warmup_requests=0)
                time.sleep(args.seconds)
                ended = time.monotonic_ns()
            else:
                data["stream_processes"].append(stream_record)
                result = run_stream(binary, env, client=client, timeout=args.stream_timeout, record=stream_record,
                                    before_launch=(lambda: load.start(warmup_requests=0)) if client else None)
                started, ended = stream_record["start_ns"], stream_record["end_ns"]
            after = server.metrics() if server else dict(before)
            metric_observations.append({"phase": "after_tool", "observed_ns": time.monotonic_ns(), "values": after})
            if args.post_tool_seconds:
                remaining = args.post_tool_seconds - (time.monotonic_ns() - ended) / 1e9
                if remaining > 0:
                    time.sleep(remaining)
            drain_start = time.monotonic_ns()
            if load:
                load.finish()
            drain_state = client.wait_drained(args.drain_timeout) if client else None
            whole_end = time.monotonic_ns()
            final = server.metrics() if server else dict(after)
            metric_observations.append({"phase": "after_post_tool_and_drain", "observed_ns": time.monotonic_ns(), "values": final})
            late_accounting_extension = False
            if server:
                settled = server.settle()
                if client and settled != final:
                    # Conservatively include late acknowledgments/publication
                    # rather than crediting a shorter, incompletely drained run.
                    drain_state = client.wait_drained(args.drain_timeout)
                    final = server.metrics()
                    whole_end = time.monotonic_ns()
                    late_accounting_extension = True
                    metric_observations.append({"phase": "late_accounting_extension", "observed_ns": whole_end, "values": final})
            raw = None
            measurement = {}
            if result is not None:
                raw = args.output_dir / f"{stem}-trial-{trial}.txt"
                raw.write_text(result.stdout + "\nSTDERR:\n" + result.stderr)
                result.check_returncode()
                measurement = parse_stream(result.stdout)
            requests = load.records if load else []
            tool = measurement_window(started, ended, before, after, requests)
            post = measurement_window(ended, whole_end, after, final, requests)
            whole = measurement_window(whole_start, whole_end, before, final, requests)
            deltas = tool["transfer_counter_deltas"]
            warnings = []
            if args.mode == "hicache" and deltas[COUNTERS[0]] is None:
                warnings.append("Backup counter missing: activity is unmeasured, not zero")
            elif args.mode == "hicache" and deltas[COUNTERS[0]] <= 0 and args.controller_mode == "normal":
                warnings.append("No backup activity observed during tool window")
            if load and not tool["fully_contained_requests"]:
                warnings.append("No full inference request completed inside benchmark window")
            if args.mode == "no-hicache" and any(v for v in deltas.values() if v is not None):
                warnings.append("Unexpected HiCache transfer counters in disabled mode")
            record = {"trial": trial, **tool, **measurement,
                      "windows": {"tool": tool, "post_tool": post, "whole": whole},
                      "drain_start_ns": drain_start, "drain_end_ns": whole_end,
                      "controller_after_drain": drain_state, "metric_observations": metric_observations,
                      "late_accounting_extension": late_accounting_extension,
                      "raw_log": str(raw) if raw else None, "warnings": warnings}
            data["trials"].append(record)
            persist()
            description = "inference alone" if args.inference_only else f"Triad {record['kernels']['Triad']['average_GB_per_second']:.2f} GB/s"
            print(f"Trial {trial + 1}/{args.trials}: {description}; "
                  f"backup {record['backup_payload_GB_per_second'] if record['backup_payload_GB_per_second'] is not None else 'N/A'} GB/s; "
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
