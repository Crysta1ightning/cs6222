"""Summarize matched runs without mixing controllers, write policies, or windows."""

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path


def median(values):
    available = [v for v in values if v is not None]
    return statistics.median(available) if available else None


def display(value):
    return f"{value:.3f}" if value is not None else "N/A"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", nargs="+", type=Path)
    parser.add_argument("--window", choices=("all", "tool", "post_tool", "whole"), default="all")
    args = parser.parse_args()
    grouped = defaultdict(list)
    configurations = set()
    for path in args.results:
        data = json.loads(path.read_text())
        if data.get("error"):
            parser.error(f"Incomplete/failed run: {path}: {data['error']}")
        configurations.add((data["hostname"], data["threads"], data["binary_sha256"], tuple(data["cpu_affinity"]),
                            data.get("numa", {}).get("stdout")))
        info = data.get("server_info", {})
        policy = data.get("write_policy", info.get("server_args", info).get("hicache_write_policy", "none"))
        controller = data.get("controller_mode", "normal") if data.get("controller_enabled") else "disabled"
        prefix = (data["mode"], data.get("workload", "stream"), policy, controller)
        for trial in data["trials"]:
            for warning in trial.get("warnings", []):
                print(f"WARNING: {path}, trial {trial['trial']}: {warning}")
            windows = {"tool": trial, **trial.get("windows", {})}
            for name, window in windows.items():
                if args.window not in ("all", name):
                    continue
                grouped[(*prefix, name)].append((trial, window))
    if len(configurations) > 1:
        parser.error("Runs differ in node, thread count, binary, CPU affinity, or NUMA policy; compare matched runs.")
    print("mode | workload | write policy | controller | window | trials | Triad GB/s | backup GB/s | requests/s | elapsed s | completed requests")
    for key, pairs in sorted(grouped.items(), key=lambda item: tuple(str(v) for v in item[0])):
        mode, workload, policy, controller, name = key
        triad = median([trial.get("kernels", {}).get("Triad", {}).get("average_GB_per_second")
                        for trial, _ in pairs]) if name == "tool" else None
        backup = median([window.get("backup_payload_GB_per_second") for _, window in pairs])
        inference = median([window.get("requests_per_second") for _, window in pairs]) if mode != "memory-only" else None
        elapsed = median([window.get("process_seconds") for _, window in pairs])
        completed = median([window.get("fully_contained_requests") for _, window in pairs]) if mode != "memory-only" else None
        print(" | ".join(map(str, (*key, len(pairs), display(triad), display(backup), display(inference), display(elapsed), display(completed)))))
    print("Tool-window gains must be assessed alongside post_tool and whole, which include subsequent inference and deferred draining.")
    print("Missing metrics are N/A. Fresh prompts are diagnostic and cannot establish KV-reuse or end-to-end agent benefits.")


if __name__ == "__main__":
    main()
