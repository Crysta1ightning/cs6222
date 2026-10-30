"""Paired default/deferred HiCache runs with a CPU workload and fixed inference work."""
import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import math
import os
import signal
import shutil
import socket
import statistics
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path

from deferred_backup import active_tool
from persistent_stream import PersistentStream

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
URL = "http://127.0.0.1:30000"
METRICS = ("sglang:hicache_backup_bytes_total", "sglang:load_back_bytes_total",
           "sglang:evicted_tokens_total")


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def progress(message):
    """Optional coarse progress channel; call only outside measured work."""
    descriptor = os.environ.get('CS6222_PROGRESS_FD')
    if descriptor:
        try:
            os.write(int(descriptor), (message + '\n').encode())
        except (OSError, ValueError):
            pass


def tool_command(args, summary_path):
    if args.tool == "stream":
        command = [str(args.stream_binary)]
    else:
        command = [sys.executable, str(HERE / "tool-tests.py"), "--summary", str(summary_path)]
    return cpu_binding(getattr(args, "tool_cpus", None)) + command


def tool_summary(args, log_path, summary_path):
    if args.tool == "stream":
        # Reuse the existing numerical-validation and bandwidth parser.
        spec = importlib.util.spec_from_file_location("stream_benchmark", ROOT / "memory-experiment/run.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        summary = module.parse_stream(log_path.read_text())
        summary["workload"] = "stream"
        save(summary_path, summary)
        return summary
    summary = json.loads(summary_path.read_text())
    if summary.get("passed", 0) == 0:
        raise RuntimeError("Tool completed without any passing tests; inspect tool.log")
    summary["workload"] = "numpy-tests"
    return summary


def metrics():
    import requests
    response = requests.get(URL + "/metrics", timeout=15)
    response.raise_for_status()
    totals = dict.fromkeys(METRICS, 0.0)
    for line in response.text.splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        name = fields[0].split("{")[0]
        if name in totals:
            totals[name] += float(fields[1])
    return totals


def stop_group(process):
    # The launcher can exit before its workers; still stop its owned group.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)


def journal(path, offset=0):
    if not path.exists():
        return []
    with path.open() as file:
        file.seek(offset)
        rows = []
        for line in file:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # Last record may still be getting written.
    return rows


def verify_gate(state, log, ids):
    offset = log.stat().st_size if log.exists() else 0
    with active_tool(state):
        generate(ids[:256])
        rows = journal(log, offset)
        if not any(row["event"] == "defer" for row in rows):
            raise RuntimeError("No backup deferral observed; inspect policy patch and server.log")
        if any(row["event"] == "submit" for row in rows):
            raise RuntimeError("Verification backup submitted while tool active; inspect overrides")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if any(row["event"] == "submit" and row["reason"] == "tools-idle" for row in journal(log, offset)):
            print("GPU gate check passed: deferred during tool, submitted after tool end.", flush=True)
            return
        time.sleep(0.05)
    raise RuntimeError("No backup submission after tool end")


def generate(ids, output_tokens=1):
    import requests
    start = time.monotonic_ns()
    response = requests.post(URL + "/generate", json={"input_ids": ids,
                             "sampling_params": {"max_new_tokens": output_tokens, "temperature": 0,
                                                 "ignore_eos": True}}, timeout=120)
    response.raise_for_status()
    output = response.json()
    if "meta_info" not in output:
        raise RuntimeError(f"Unexpected generation response: {output}")
    if output["meta_info"].get("completion_tokens") != output_tokens:
        raise RuntimeError("Generation did not complete the fixed output-token count")
    return {"start_ns": start, "end_ns": time.monotonic_ns(),
            "prompt_tokens": len(ids), "meta_info": output["meta_info"]}


class RequestSchedule:
    """Rendezvous before numbered requests; never delay a trigger behind a tool."""

    def __init__(self, targets, timeout=30):
        self.targets = targets
        self.timeout = timeout
        self.reached = [threading.Event() for _ in targets]
        self.started = [threading.Event() for _ in targets]
        self.finished = [threading.Event() for _ in targets]
        self.trigger_ns = [None for _ in targets]
        self.error = None

    def abort(self, error):
        self.error = error
        for event in self.reached + self.started:
            event.set()

    def check(self):
        if self.error is not None:
            raise RuntimeError("Request-aligned workload aborted") from self.error

    def before_request(self, request_number):
        self.check()
        if request_number not in self.targets:
            return
        index = self.targets.index(request_number)
        if index and not self.finished[index - 1].is_set():
            raise RuntimeError(f"STREAM round {index} still running before request {request_number}; "
                               "increase request spacing (schedule was not shifted)")
        self.trigger_ns[index] = time.monotonic_ns()
        self.reached[index].set()
        if not self.started[index].wait(self.timeout):
            raise RuntimeError("Timed out waiting for STREAM launch")
        self.check()

    def wait_for_request(self, index):
        # A preceding request may take up to the generation HTTP timeout.
        # The worker calls abort on failure, so a failed request wakes this wait.
        if not self.reached[index].wait(300):
            raise RuntimeError("Timed out waiting for STREAM request boundary")
        self.check()
        return {"trigger_request": self.targets[index],
                "completed_requests_at_trigger": self.targets[index] - 1,
                "trigger_ns": self.trigger_ns[index]}


def execute_tools(args, directory, state, env, schedule=None):
    windows = []
    for round_index in range(args.stream_rounds):
        label = f"tool-round-{round_index + 1}"
        log_path = directory / f"{label}.log"
        summary_path = directory / f"{label}-summary.json"
        trigger = schedule.wait_for_request(round_index) if schedule else {}
        with active_tool(state):
            start = time.monotonic_ns()
            before = metrics()
            with log_path.open("w") as log:
                child = subprocess.Popen(tool_command(args, summary_path), cwd=ROOT, env=env,
                                         stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                launched = time.monotonic_ns()
                if schedule:
                    schedule.started[round_index].set()
                try:
                    code = child.wait(timeout=300)
                finally:
                    if child.poll() is None:
                        stop_group(child)
            end = time.monotonic_ns()
            after = metrics()
        if schedule:
            schedule.finished[round_index].set()
        if code != 0:
            raise RuntimeError(f"Tool failed with exit {code}; inspect {log_path}")
        windows.append({"round": round_index + 1, "start_ns": start, "end_ns": end,
                        **trigger, "process_launched_ns": launched,
                        "trigger_to_launch_ms": (launched - trigger["trigger_ns"]) / 1e6 if trigger else None,
                        "seconds": (end - start) / 1e9,
                        "counter_deltas": {name: after[name] - before[name] for name in METRICS},
                        "summary": tool_summary(args, log_path, summary_path)})
        save(directory / "tool-windows.json", windows)
        if schedule is None and round_index + 1 < args.stream_rounds:
            # User-requested inactive period; inference keeps running independently.
            time.sleep(args.stream_gap)
    return windows


def execute_prepared_tools(args, directory, state, schedule, worker):
    windows = []
    for index in range(args.stream_rounds):
        trigger = schedule.wait_for_request(index)
        with active_tool(state):
            active_start = time.monotonic_ns()
            started = worker.start()
            schedule.started[index].set()
            completed = worker.finish()
        active_end = time.monotonic_ns()
        schedule.finished[index].set()
        windows.append({"round": index + 1, **trigger,
            "start_ns": started['start_ns'], "end_ns": completed['end_ns'],
            "alignment_reference_ns": started['start_ns'],
            "process_launched_ns": worker.launched_ns,
            "active_signal_start_ns": active_start, "active_signal_end_ns": active_end,
            "seconds": (completed['end_ns'] - started['start_ns']) / 1e9,
            "trigger_to_loop_ms": (started['start_ns'] - trigger['trigger_ns']) / 1e6,
            "counter_deltas": None,
            "summary": {"workload": "prepared-stream", "sample_valid": True,
                "array_elements": worker.ready['array_elements'],
                "working_set_bytes": 24 * worker.ready['array_elements'],
                "kernels": completed['kernels']}})
    return windows


def tool_environment(args):
    threads = str(getattr(args, "tool_threads", 3))
    env = dict(os.environ, OMP_NUM_THREADS=threads, OPENBLAS_NUM_THREADS=threads,
                MKL_NUM_THREADS=threads, OMP_DYNAMIC="FALSE", OMP_PROC_BIND="close",
                OMP_PLACES="cores", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", CUDA_VISIBLE_DEVICES="")
    if getattr(args, 'stream_worker', None):
        env['OMP_WAIT_POLICY'] = 'PASSIVE'
    return env


def cpu_binding(cpus):
    return ["numactl", "--physcpubind=" + cpus] if cpus else []


def configure_schedule(args):
    if args.tool_schedule == "periodic":
        if args.stream_at_requests is not None:
            raise ValueError("--stream-at-requests requires --tool-schedule requests")
        args.stream_at_requests = []
        return
    if args.stream_gap:
        raise ValueError("Request scheduling uses request spacing; omit --stream-gap or explicitly select --tool-schedule periodic")
    if args.stream_at_requests is None:
        args.stream_at_requests = [1 + index * args.requests // args.stream_rounds
                                   for index in range(args.stream_rounds)]
    else:
        try:
            args.stream_at_requests = [int(value) for value in args.stream_at_requests.split(",")]
        except ValueError as error:
            raise ValueError("--stream-at-requests must be comma-separated request numbers") from error
    targets = args.stream_at_requests
    if (len(targets) != args.stream_rounds or targets != sorted(set(targets)) or
            not targets or targets[0] < 1 or targets[-1] > args.requests):
        raise ValueError("Provide one strictly increasing request number per STREAM round, within 1..requests")


def parse_cpus(value):
    cpus = set()
    for part in value.split(","):
        bounds = list(map(int, part.split("-")))
        if len(bounds) > 2 or min(bounds) < 0 or bounds[0] > bounds[-1]:
            raise ValueError("Invalid CPU list")
        cpus.update(range(bounds[0], bounds[-1] + 1))
    return cpus


def wait_ready(process):
    import requests
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Server exited during startup; inspect this run's server.log")
        try:
            if requests.get(URL + "/health", timeout=2).status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(1)
    raise RuntimeError("Server did not become ready within ten minutes")


def settle():
    previous = metrics()
    stable = 0
    for _ in range(30):
        time.sleep(0.5)
        current = metrics()
        stable = stable + 1 if current == previous else 0
        if stable >= 6:
            return current
        previous = current
    raise RuntimeError("Transfers did not settle within 15 seconds")


def measure_condition(args, condition, directory, prompts, state, policy_log, stream_enabled=True):
    if stream_enabled and getattr(args, 'stream_worker', None):
        command = cpu_binding(args.tool_cpus) + [str(args.stream_worker)]
        result = None
        try:
            with PersistentStream(command, tool_environment(args)) as worker:
                import psutil
                expected_cpus = parse_cpus(args.tool_cpus) if args.tool_cpus else set(os.sched_getaffinity(0))
                threads = {str(t.id): sorted(os.sched_getaffinity(t.id))
                           for t in psutil.Process(worker.process.pid).threads()}
                if any(not set(cpus) <= expected_cpus for cpus in threads.values()):
                    raise RuntimeError('Prepared STREAM thread escaped its assigned CPU set')
                save(directory / 'worker-placement.json', {'pid': worker.process.pid, 'threads': threads})
                (directory / 'worker-numa_maps.txt').write_text(Path(f'/proc/{worker.process.pid}/numa_maps').read_text())
                with active_tool(state):
                    pass  # Create and first-touch the registry outside measurement.
                result = _measure_condition(args, condition, directory, prompts, state, policy_log,
                                            stream_enabled=True, worker=worker)
            result['stream_full_validation'] = worker.full_validation
            result['measurement_valid'] = True
            save(directory / 'result.json', result)
            return result
        except BaseException:
            if result is not None:
                result['measurement_valid'] = False
                save(directory / 'result.json', result)
            raise
    return _measure_condition(args, condition, directory, prompts, state, policy_log, stream_enabled)


def _measure_condition(args, condition, directory, prompts, state, policy_log, stream_enabled=True, worker=None):
    before = settle()
    if args.nsys:
        import requests
        requests.post(URL + "/start_profile", json={
            "activities": ["CUDA_PROFILER"], "output_dir": str(directory),
            "detailed_annotations": True,
        }, timeout=30).raise_for_status()
    offset = policy_log.stat().st_size if policy_log.exists() else 0
    records = []
    tool_env = tool_environment(args)
    schedule = (RequestSchedule(args.stream_at_requests) if stream_enabled and
                getattr(args, "tool_schedule", "periodic") == "requests" else None)
    workflow_start = time.monotonic_ns()
    # Agent B has a fixed amount of fresh-prompt work, not a fixed run duration.
    def other_agent():
        try:
            for number, ids in enumerate(prompts[:args.requests], 1):
                boundary_start = time.monotonic_ns()
                if schedule:
                    schedule.before_request(number)
                boundary_end = time.monotonic_ns()
                record = generate(ids, args.output_tokens)
                record["request_number"] = number
                record["schedule_wait_ns"] = boundary_end - boundary_start if schedule else 0
                records.append(record)
            return time.monotonic_ns()
        except BaseException as error:
            if schedule:
                schedule.abort(error)
            raise

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(other_agent)
        try:
            if worker:
                windows = execute_prepared_tools(args, directory, state, schedule, worker)
            else:
                windows = execute_tools(args, directory, state, tool_env, schedule) if stream_enabled else []
        except BaseException as error:
            if schedule:
                schedule.abort(error)
            raise
        tool_start = windows[0]["start_ns"] if windows else None
        tool_end = windows[-1]["end_ns"] if windows else None
        # Agent A continues inference after the CPU workload ends; no inserted sleep.
        followup = [generate(ids, args.output_tokens) for ids in prompts[args.requests:args.requests + args.followup]]
        agent_a_end = time.monotonic_ns()
        agent_b_end = future.result()
    workflow_end = max(agent_a_end, agent_b_end)
    after = settle()
    if args.nsys:
        import requests
        requests.post(URL + "/stop_profile", timeout=60).raise_for_status()
    if condition == "no-hicache" and any(after[name] != before[name] for name in METRICS[:2]):
        raise RuntimeError("Unexpected host-cache transfers in the no-HiCache control")
    rows = journal(policy_log, offset)
    diagnostics = getattr(args, 'policy_log_mode', 'live') == 'live'
    if condition == "idle" and diagnostics and (not rows or any(row["event"] == "defer" for row in rows)):
        raise RuntimeError("Idle control did not submit normally; inspect policy.jsonl")
    if not stream_enabled and any(row["event"] == "defer" for row in rows):
        raise RuntimeError("Unexpected deferral with STREAM disabled; inspect tool registry")
    reasons = Counter(row["reason"] for row in rows if row["event"] == "submit")
    overlapping = [row for row in records if any(
        row["start_ns"] < window["end_ns"] and row["end_ns"] > window["start_ns"]
        for window in windows)]
    active_submissions = [row for row in rows if row["event"] == "submit" and (
        row.get("active_tools", 0) or any(window["start_ns"] <= row["monotonic_ns"] < window["end_ns"]
                                        for window in windows))]
    overrides = Counter(row["reason"] for row in active_submissions)
    for window in windows:
        window["overlapping_requests"] = sum(row["start_ns"] < window["end_ns"] and
                                               row["end_ns"] > window["start_ns"] for row in records)
        if schedule:
            request = records[window["trigger_request"] - 1]
            reference = window.get('alignment_reference_ns', window['process_launched_ns'])
            if request["start_ns"] < reference:
                raise RuntimeError("Request started before its scheduled STREAM launch")
            window["trigger_request_start_ns"] = request["start_ns"]
            window["alignment_to_request_ms"] = (request["start_ns"] - reference) / 1e6
            window['trigger_request_overlapped_work'] = request['start_ns'] < window['end_ns']
            if getattr(args, 'require_trigger_overlap', False) and not window['trigger_request_overlapped_work']:
                raise RuntimeError('Triggered request started after STREAM work had already finished')
    save(directory / "tool-windows.json", windows)
    result = {"condition": condition, "workflow_start_ns": workflow_start,
              "policy_diagnostics_available": diagnostics,
              "stream_protocol": 'prepared-loop-v1' if worker else 'process-launch',
              "tool_schedule": getattr(args, "tool_schedule", "periodic"),
              "request_schedule_wait_seconds": sum(row["schedule_wait_ns"] for row in records) / 1e9,
              "tool_start_ns": tool_start, "tool_end_ns": tool_end,
              "tool_seconds": sum(window["seconds"] for window in windows),
              "tool_sequence_seconds": (tool_end - tool_start) / 1e9 if windows else 0,
              "stream_enabled": stream_enabled,
              "tool_windows": windows, "overrides_during_active": dict(overrides) if diagnostics else None,
              "backup_submissions_during_active": len(active_submissions) if diagnostics else None,
              "agent_a_seconds": (agent_a_end - workflow_start) / 1e9,
              "agent_b_seconds": (agent_b_end - workflow_start) / 1e9,
              "both_agents_seconds": (workflow_end - workflow_start) / 1e9,
              "completed_agent_b_requests": len(records), "completed_followup_requests": len(followup),
              "agent_b_requests_overlapping_tool": len(overlapping),
              "agent_b_requests_started_after_tool": sum(row["start_ns"] >= tool_end for row in records) if windows else 0,
              "transfer_counter_deltas": {name: after[name] - before[name] for name in METRICS},
              "tool_window_counter_deltas": ({name: sum(window["counter_deltas"][name] for window in windows) for name in METRICS}
                                             if all(w['counter_deltas'] is not None for w in windows) else None),
              "submit_reasons": dict(reasons) if diagnostics else None,
              "defer_events": sum(row["event"] == "defer" for row in rows) if diagnostics else None,
              "max_logged_wait_seconds": max((row["wait_seconds"] for row in rows), default=0) if diagnostics else None,
              "tool_summary": windows[0]["summary"] if windows else None}
    save(directory / "requests.json", {"agent_b": records, "agent_a_followup": followup})
    save(directory / "result.json", result)
    policy_details = f'submissions {dict(reasons)}' if diagnostics else 'policy event logging off'
    print(f"{condition}: tool {result['tool_seconds']:.2f}s; both agents {result['both_agents_seconds']:.2f}s; "
          f"overlapping requests {len(overlapping)}; {policy_details}", flush=True)
    if windows and args.tool == "stream":
        triad = statistics.median(window["summary"]["kernels"]["Triad"]["average_GB_per_second"] for window in windows)
        print(f"  Median round STREAM Triad: {triad:.2f} GB/s; "
              f"Agent B requests started after tool end: {result['agent_b_requests_started_after_tool']}", flush=True)
    elif windows:
        print(f"  Actual test calls: {result['tool_summary']['call_seconds']:.2f}s; "
              f"selected tests: {result['tool_summary']['selected_tests']}; "
              f"Agent B requests started after tool end: {result['agent_b_requests_started_after_tool']}", flush=True)
    print(f"  Active-window overrides: {dict(overrides) if diagnostics else 'not observed (logging off)'}; "
          f"overlap requests by round: {[window['overlapping_requests'] for window in windows]}", flush=True)
    return result


def warm_server(args, prompt, replay_prompts=(), directory=None):
    import requests
    cold_records = []
    for index in range(args.warmup_requests):
        if index:
            requests.post(URL + '/flush_cache', timeout=30).raise_for_status()
        cold_records.append(generate(prompt, args.output_tokens))
        warmed = settle()
    # Cold-cache warmups alone miss the shorter prefill shapes created by radix
    # prefix reuse. Replay the beginning of the actual sequence without flushing
    # between requests, including its first decimal-record boundary (0..12).
    prefix_records = []
    if replay_prompts:
        requests.post(URL + '/flush_cache', timeout=30).raise_for_status()
        for ids in replay_prompts:
            prefix_records.append(generate(ids, args.output_tokens))
        warmed = settle()
    if directory is not None:
        save(directory / 'warmup-requests.json', {
            'cold_cache': cold_records, 'prefix_sequence': prefix_records,
            'scope': 'Full output length, cold cache and first 13 workload prompts; not all possible runtime paths.'})
    return warmed


def run_condition(args, condition, directory, prompts, state, stream_order=(False, True)):
    import requests
    directory.mkdir()
    env = os.environ.copy()
    for name in ("CS6222_TOOL_STATE", "CS6222_BACKUP_LOG", "CS6222_BACKUP_MAX_DELAY", "CS6222_BACKUP_MAX_BYTES"):
        env.pop(name, None)
    policy_log = directory / "policy.jsonl"
    if condition in ("idle", "deferred"):
        # Idle control retains the controller, registry polling and logging,
        # but reads a separate registry that no workload marks active.
        gate_state = state if condition == "deferred" else state + "-always-idle"
        env.update(CS6222_TOOL_STATE=gate_state,
                   CS6222_BACKUP_MAX_DELAY=str(args.max_delay), CS6222_BACKUP_MAX_BYTES=str(args.max_bytes))
        if args.policy_log_mode == 'live':
            env['CS6222_BACKUP_LOG'] = str(policy_log)
    command = [str(Path(sys.executable).with_name("sglang")), "serve", "Qwen/Qwen3-0.6B",
               "--host", "127.0.0.1", "--port", str(args.port), "--max-total-tokens", str(args.gpu_tokens),
               "--context-length", "4096", "--enable-metrics", "--disable-cuda-graph"]
    command.extend(['--random-seed', '6222'])
    if condition != "no-hicache":
        command.extend(["--enable-hierarchical-cache", "--hicache-size", "4",
                        "--hicache-write-policy", "write_through"])
    if args.nsys:
        env.update(SGLANG_ENABLE_NVTX_SCHEDULER="1", SGLANG_ENABLE_NVTX_OPERATIONS="1")
        command = ["nsys", "profile", "--sample=none", "--cpuctxsw=none",
                   "--trace=cuda,nvtx", "--capture-range=cudaProfilerApi",
                   "--capture-range-end=stop", "--kill=none",
                   "--output=" + str(directory / "gpu-timeline")] + command
    command = cpu_binding(args.server_cpus) + command
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", args.port)) == 0:
            raise RuntimeError(f"Port {args.port} is occupied; choose another --port.")
    save(directory / "server-command.json", {"command": command, "condition": condition,
                                             "max_delay": args.max_delay, "max_bytes": args.max_bytes})
    with (directory / "server.log").open("w") as log:
        server = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            print(f"Starting {condition}; log: {directory / 'server.log'}", flush=True)
            progress(f"Starting {condition}; log: {directory / 'server.log'}")
            wait_ready(server)
            warm_metrics = warm_server(args, prompts[-1], prompts[:min(args.requests, 13)], directory)
            if condition != "no-hicache" and warm_metrics[METRICS[0]] <= 0:
                raise RuntimeError("No completed HiCache backup after warmup; inspect metrics and server.log")
            info = requests.get(URL + "/server_info", timeout=30)
            info.raise_for_status()
            server_info = info.json()
            settings = server_info.get("server_args", server_info)
            if settings.get("enable_hierarchical_cache") != (condition != "no-hicache"):
                raise RuntimeError("Server HiCache setting does not match requested condition")
            save(directory / "server-info.json", server_info)
            requests.post(URL + "/flush_cache", timeout=30).raise_for_status()
            if condition == "deferred" and args.policy_log_mode == 'live':
                verify_gate(state, policy_log, prompts[-1])
                settle()
                requests.post(URL + "/flush_cache", timeout=30).raise_for_status()
            if args.verify_only:
                return {'gate_verified': True, 'condition': condition}
            if not args.stream_comparison:
                return measure_condition(args, condition, directory, prompts, state, policy_log)
            paired = {}
            for enabled in stream_order:
                label = "stream-on" if enabled else "stream-off"
                trial_dir = directory / label
                trial_dir.mkdir()
                requests.post(URL + "/flush_cache", timeout=30).raise_for_status()
                print(f"  Measuring {condition}, {label}", flush=True)
                paired[label] = measure_condition(args, condition, trial_dir, prompts, state,
                                                  policy_log, stream_enabled=enabled)
            return paired
        finally:
            if args.nsys:
                # Let Nsight finish exporting after cudaProfilerStop before
                # terminating the process group owned by this driver.
                report = directory / "gpu-timeline.nsys-rep"
                export_deadline = time.monotonic() + 60
                while not report.exists() and server.poll() is None and time.monotonic() < export_deadline:
                    time.sleep(0.5)
            stop_group(server)
            # Workers can take a moment to release the HTTP port after launcher exit.
            release_deadline = time.monotonic() + 15
            while time.monotonic() < release_deadline:
                with socket.socket() as probe:
                    if probe.connect_ex(("127.0.0.1", args.port)) != 0:
                        break
                time.sleep(0.2)


def main():
    global URL
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", type=int, default=3)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'results')
    parser.add_argument('--stream-worker', type=Path,
                        help='preinitialized STREAM worker binary; requires request scheduling')
    parser.add_argument('--warmup-requests', type=int, default=3,
                        help='untimed full-length inference requests, flushing cache between requests')
    parser.add_argument('--policy-log-mode', choices=('live', 'off'), default='live',
                        help='off disables policy event logging; use a separate live diagnostic first')
    parser.add_argument('--verify-only', action='store_true',
                        help='start only a deferred server and verify the GPU gate; no performance measurements')
    parser.add_argument('--require-trigger-overlap', action='store_true',
                        help='reject runs if a triggered request starts after its STREAM loop ends')
    parser.add_argument("--port", type=int, default=30000)
    parser.add_argument("--requests", type=int, default=256)
    parser.add_argument("--followup", type=int, default=8)
    parser.add_argument("--gpu-tokens", type=int, default=32768)
    parser.add_argument("--max-delay", type=float, default=0.5, help="maximum deferral seconds; 0 disables the deadline")
    parser.add_argument("--max-bytes", type=int, default=1073741824, help="queued backup byte cap; 0 disables the cap")
    parser.add_argument("--tool", choices=("stream", "numpy-tests"), default="stream")
    parser.add_argument("--stream-binary", type=Path, default=ROOT / "memory-experiment/stream")
    parser.add_argument("--stream-rounds", type=int, default=1)
    parser.add_argument("--stream-gap", type=float, default=0)
    parser.add_argument("--tool-schedule", choices=("requests", "periodic"), default="requests",
                        help="launch before numbered requests (default); periodic retains historical wall-clock gaps")
    parser.add_argument("--stream-at-requests", default=None,
                        help="comma-separated 1-based request numbers, one per STREAM round; default: evenly spaced")
    parser.add_argument("--tool-threads", type=int, default=3)
    parser.add_argument("--server-cpus", help="CPU list for SGLang; use together with --tool-cpus")
    parser.add_argument("--tool-cpus", help="disjoint CPU list for the CPU workload")
    parser.add_argument("--prompt-tokens", type=int, default=2048)
    parser.add_argument("--output-tokens", type=int, default=1)
    parser.add_argument("--comparison", choices=("deferred", "idle", "all", "cache-modes", "repeat-default"), default="deferred",
                        help="cache-modes compares off/normal/deferred; repeat-default compares two normal runs to measure variability")
    parser.add_argument("--stream-comparison", action="store_true",
                        help="pair STREAM off/on within each cache-mode server session")
    parser.add_argument("--nsys", action="store_true",
                        help="capture CUDA/NVTX execution during the workload; diagnostic timings include profiler overhead")
    args = parser.parse_args()
    if args.verify_only and (args.policy_log_mode != 'live' or args.nsys):
        parser.error('--verify-only requires live diagnostics without profiling')
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    URL = f"http://127.0.0.1:{args.port}"
    if args.nsys and args.stream_comparison:
        parser.error("--nsys currently captures one workload per server; omit --stream-comparison")
    if args.stream_comparison and (args.comparison != "cache-modes" or args.followup != 0):
        parser.error("--stream-comparison requires --comparison cache-modes and --followup 0")
    args.stream_binary = args.stream_binary.resolve()
    if args.stream_worker:
        args.stream_worker = args.stream_worker.resolve()
        if args.tool != 'stream' or args.tool_schedule != 'requests':
            parser.error('--stream-worker requires --tool stream and --tool-schedule requests')
        if not args.stream_worker.is_file() or not os.access(args.stream_worker, os.X_OK):
            parser.error('Build the persistent worker first: bash memory-experiment/build-worker.sh')
        if args.stream_rounds > 38:
            parser.error('Persistent STREAM supports at most 38 five-iteration rounds per worker')
    if args.warmup_requests < 1:
        parser.error('--warmup-requests must be positive')
    if min(args.pairs, args.requests, args.gpu_tokens, args.stream_rounds, args.prompt_tokens, args.output_tokens) <= 0:
        parser.error("Counts, GPU capacity, and token lengths must be positive")
    if not math.isfinite(args.max_delay) or args.max_delay < 0 or args.max_bytes < 0:
        parser.error("Backup limits must be finite and nonnegative; zero disables a limit")
    if args.followup < 0 or args.stream_gap < 0 or args.prompt_tokens + args.output_tokens > 4096:
        parser.error("followup/gap must be nonnegative; prompt + output must fit context length 4096")
    if not os.getenv("SLURM_JOB_ID") or len(os.sched_getaffinity(0)) < 4:
        parser.error("Run inside a compute allocation with at least four visible CPU cores")
    try:
        configure_schedule(args)
        if bool(args.server_cpus) != bool(args.tool_cpus):
            raise ValueError("Supply both --server-cpus and --tool-cpus")
        available = set(os.sched_getaffinity(0))
        if args.server_cpus:
            server_cpus, tool_cpus = parse_cpus(args.server_cpus), parse_cpus(args.tool_cpus)
            if server_cpus & tool_cpus or not (server_cpus | tool_cpus) <= available:
                raise ValueError("Server/tool CPU sets must be disjoint and inside the allocation")
            available = tool_cpus
        if not 1 <= args.tool_threads <= len(available):
            raise ValueError("--tool-threads must fit the tool's CPU set")
    except ValueError as error:
        parser.error(str(error))
    tool_metadata = {"tool": args.tool}
    if args.tool == "stream":
        binary = args.stream_binary
        if not binary.is_file() or not os.access(binary, os.X_OK):
            parser.error("Build STREAM first: bash memory-experiment/build.sh")
        tool_metadata.update(binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                             source_sha256=hashlib.sha256((binary.parent / "stream.c").read_bytes()).hexdigest())
        if args.stream_worker:
            tool_metadata.update(worker_sha256=hashlib.sha256(args.stream_worker.read_bytes()).hexdigest(),
                worker_source_sha256=hashlib.sha256((ROOT / 'memory-experiment/stream-worker.c').read_bytes()).hexdigest())
    else:
        import numpy as np
        import hypothesis  # Required by this installed NumPy's conftest.
        tool_metadata.update(numpy_version=np.__version__, hypothesis_version=hypothesis.__version__)
    from transformers import AutoTokenizer
    directory = args.output_dir.resolve() / f"backup-experiment-{time.time_ns()}"
    directory.mkdir(parents=True)
    source_dir = directory / 'source'
    source_dir.mkdir()
    for source in (Path(__file__), HERE / 'persistent_stream.py', HERE / 'deferred_backup.py',
                   ROOT / 'memory-experiment/stream-worker.c', ROOT / 'memory-experiment/stream.c',
                   ROOT / 'memory-experiment/build-worker.sh', HERE / 'run-stability.sh',
                   HERE / 'prepare-runtime.sh'):
        shutil.copyfile(source, source_dir / source.name)
    if args.stream_worker:
        shutil.copyfile(args.stream_worker, source_dir / 'stream-worker')
    state = f"/tmp/cs6222-{os.getuid()}-{os.environ['SLURM_JOB_ID']}-{os.getpid()}-tools.bin"
    tokenizer = AutoTokenizer.from_pretrained("Qwen/Qwen3-0.6B")
    # Pretokenize identical unique prompts for each condition.
    prompts = [tokenizer.encode(f"CS6222 record {index:08d}.\n" + "Example repository record.\n" * 1000)[:args.prompt_tokens]
               for index in range(args.requests + args.followup + 1)]
    save(directory / "metadata.json", {"parameters": {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()}, "hostname": socket.gethostname(),
                                       "policy_revision": "counter-mmap-v3",
                                       "experiment_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                                       "policy_patch_sha256": hashlib.sha256((HERE / "sglang.patch").read_bytes()).hexdigest(),
                                       "applied_source_sha256": {
                                           name: hashlib.sha256((ROOT / "sglang/python/sglang/srt" / name).read_bytes()).hexdigest()
                                           for name in ("managers/cache_controller.py", "mem_cache/unified_radix_cache.py", "mem_cache/deferred_backup.py",
                                                        "mem_cache/hybrid_cache/hybrid_cache_controller.py",
                                                        "model_executor/model_runner.py", "mem_cache/l2_transfer.py")},
                                       "slurm_job_id": os.environ["SLURM_JOB_ID"],
                                       "cpu_affinity": sorted(os.sched_getaffinity(0)),
                                       "tool_metadata": tool_metadata,
                                       "gpu_info": subprocess.check_output(["nvidia-smi", "--query-gpu=name,uuid,memory.total",
                                                                            "--format=csv,noheader"], text=True, timeout=10).strip(),
                                       "prompt_sha256": hashlib.sha256(json.dumps(prompts).encode()).hexdigest(),
                                       "tool_state": state, "workload": "fresh prompts; backup stress pilot, not reuse evaluation"})
    print(f"Results: {directory}", flush=True)
    progress(f'Results: {directory}')
    if args.verify_only:
        result = run_condition(args, 'deferred', directory / 'gate-check', prompts, state)
        save(directory / 'verification.json', result)
        progress('Separate GPU gate diagnostic passed')
        return
    # Tool preflight is outside timing and warms imports/filesystem in both modes.
    if args.stream_worker:
        with PersistentStream(cpu_binding(args.tool_cpus) + [str(args.stream_worker)], tool_environment(args)) as worker:
            worker.start()
            preflight = worker.finish()
        save(directory / 'preflight-tool-summary.json', {**preflight, 'full_validation': worker.full_validation})
    else:
        with (directory / "preflight-tool.log").open("w") as preflight_log:
            preflight = subprocess.run(tool_command(args, directory / "preflight-tool-summary.json"), cwd=ROOT,
                                      env=tool_environment(args),
                                      stdout=preflight_log, stderr=subprocess.STDOUT, timeout=300)
        if preflight.returncode:
            raise SystemExit(f"Tool preflight failed; inspect {directory / 'preflight-tool.log'}")
        tool_summary(args, directory / "preflight-tool.log", directory / "preflight-tool-summary.json")
    results = []
    for pair in range(args.pairs):
        candidate = "deferred" if args.comparison in ("all", "cache-modes") else args.comparison
        if args.comparison in ("all", "cache-modes"):
            conditions = ("default", "idle", "deferred") if args.comparison == "all" else ("no-hicache", "default", "deferred")
            rotation = pair % 3
            order = conditions[rotation:] + conditions[:rotation]
        else:
            order = ("default", candidate) if pair % 2 == 0 else (candidate, "default")
        paired = {}
        for condition_index, condition in enumerate(order):
            print(f"Pair {pair + 1}/{args.pairs}: {condition}", flush=True)
            paired[condition] = run_condition(args, condition, directory / f"pair-{pair + 1}-{condition}", prompts, state,
                                               stream_order=(False, True) if (pair + condition_index) % 2 == 0 else (True, False))
            elapsed = paired[condition].get('both_agents_seconds')
            progress(f'{args.comparison} pair {pair+1}: {condition} completed' +
                     (f' in {elapsed:.3f}s' if elapsed is not None else ''))
        if args.stream_comparison:
            result = {"pair": pair + 1, "order": list(order), "conditions": paired,
                      "comparison": "cache-modes-stream-on-off",
                      "stream_effect_percent": {
                          mode: 100 * (data["stream-on"]["agent_b_seconds"] / data["stream-off"]["agent_b_seconds"] - 1)
                          for mode, data in paired.items()},
                      "policy_comparisons": {
                          stream: {
                              label: 100 * (paired[left][stream]["agent_b_seconds"] / paired[right][stream]["agent_b_seconds"] - 1)
                              for label, left, right in (("hicache_vs_off", "default", "no-hicache"),
                                                         ("deferred_vs_hicache", "deferred", "default"),
                                                         ("deferred_vs_off", "deferred", "no-hicache"))}
                          for stream in ("stream-off", "stream-on")}}
            results.append(result)
            save(directory / "paired-results.json", results)
            continue
        result = {"pair": pair + 1, "order": list(order), "conditions": paired,
                  "comparison": args.comparison,
                  "tool_change_percent": 100 * (paired[candidate]["tool_seconds"] / paired["default"]["tool_seconds"] - 1),
                  "both_agents_change_percent": 100 * (paired[candidate]["both_agents_seconds"] / paired["default"]["both_agents_seconds"] - 1)}
        if args.comparison == "all":
            result["comparisons"] = {
                name: {"tool_change_percent": 100 * (paired[name]["tool_seconds"] / paired["default"]["tool_seconds"] - 1),
                       "both_agents_change_percent": 100 * (paired[name]["both_agents_seconds"] / paired["default"]["both_agents_seconds"] - 1)}
                for name in ("idle", "deferred")
            }
            result["deferred_vs_idle_change_percent"] = 100 * (
                paired["deferred"]["both_agents_seconds"] / paired["idle"]["both_agents_seconds"] - 1)
        elif args.comparison == "cache-modes":
            result["comparisons"] = {
                label: {"tool_change_percent": 100 * (paired[left]["tool_seconds"] / paired[right]["tool_seconds"] - 1),
                        "both_agents_change_percent": 100 * (paired[left]["both_agents_seconds"] / paired[right]["both_agents_seconds"] - 1),
                        "inference_change_percent": 100 * (paired[left]["agent_b_seconds"] / paired[right]["agent_b_seconds"] - 1)}
                for label, left, right in (("hicache_vs_off", "default", "no-hicache"),
                                           ("deferred_vs_hicache", "deferred", "default"),
                                           ("deferred_vs_off", "deferred", "no-hicache"))
            }
        results.append(result)
        save(directory / "paired-results.json", results)
    if args.stream_comparison:
        for mode in ("no-hicache", "default", "deferred"):
            change = statistics.median(row["stream_effect_percent"][mode] for row in results)
            print(f"Median STREAM effect on {mode} inference completion: {change:+.2f}%")
        for stream in ("stream-off", "stream-on"):
            for label in results[0]["policy_comparisons"][stream]:
                change = statistics.median(row["policy_comparisons"][stream][label] for row in results)
                print(f"Median {stream} {label} inference completion change: {change:+.2f}%")
    elif args.comparison == "all":
        for name in ("idle", "deferred"):
            for key in ("tool_change_percent", "both_agents_change_percent"):
                print(f"Median {name} vs default {key}: "
                      f"{statistics.median(row['comparisons'][name][key] for row in results):+.2f}% (negative is faster)")
        print("Median deferred vs idle completion change: "
              f"{statistics.median(row['deferred_vs_idle_change_percent'] for row in results):+.2f}%")
    elif args.comparison == "cache-modes":
        for label in results[0]["comparisons"]:
            for key in ("tool_change_percent", "inference_change_percent", "both_agents_change_percent"):
                print(f"Median {label} {key}: "
                      f"{statistics.median(row['comparisons'][label][key] for row in results):+.2f}% (negative is faster)")
    else:
        for key in ("tool_change_percent", "both_agents_change_percent"):
            print(f"Median paired {key}: {statistics.median(row[key] for row in results):+.2f}% (negative is faster)")
    print(f"Saved {directory / 'paired-results.json'}")


if __name__ == "__main__":
    main()
