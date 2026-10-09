"""Summarize explicitly selected result files from the three conditions."""

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path


def main():
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python memory-experiment/summarize.py <result.json> ...")
    grouped = defaultdict(list)
    configurations = set()
    for name in sys.argv[1:]:
        data = json.loads(Path(name).read_text())
        if data.get("error"):
            raise SystemExit(f"Incomplete/failed run: {name}: {data['error']}")
        configurations.add((data["hostname"], data["threads"], data["binary_sha256"],
                            tuple(data["cpu_affinity"])))
        for trial in data["trials"]:
            if trial["warnings"]:
                print(f"WARNING: {name}, trial {trial['trial']}: {trial['warnings']}")
            grouped[(data["mode"], data.get("workload", "stream"))].append(trial)
    if len(configurations) > 1:
        raise SystemExit("Runs differ in node, thread count, binary, or CPU affinity; compare matched runs.")
    medians = {}
    inference_rates = {}
    print("\nCondition            Trials   Median Triad GB/s   Median backup GB/s   Median requests/s")
    for mode, workload in (("memory-only", "stream"), ("no-hicache", "stream"),
                           ("hicache", "stream"), ("no-hicache", "inference-only"),
                           ("hicache", "inference-only")):
        trials = grouped[(mode, workload)]
        if not trials:
            continue
        bandwidth = None
        if workload == "stream":
            bandwidth = statistics.median(t["kernels"]["Triad"]["average_GB_per_second"] for t in trials)
            medians[mode] = bandwidth
        backup = statistics.median(t["backup_payload_GB_per_second"] for t in trials)
        requests = statistics.median(t["requests_per_second"] for t in trials)
        inference_rates[(mode, workload)] = requests
        name = mode if workload == "stream" else mode + " alone"
        bandwidth_text = f"{bandwidth:.2f}" if bandwidth is not None else "N/A"
        print(f"{name:20} {len(trials):6} {bandwidth_text:>19} {backup:20.2f} {requests:19.2f}")
    if "hicache" in medians and "no-hicache" in medians:
        change = 100 * (medians["hicache"] / medians["no-hicache"] - 1)
        print(f"\nHiCache vs inference without HiCache: {change:+.1f}% memory bandwidth")
    print("Lower bandwidth means worse memory-workload performance.")
    for mode in ("no-hicache", "hicache"):
        alone = inference_rates.get((mode, "inference-only"))
        concurrent = inference_rates.get((mode, "stream"))
        if alone and concurrent is not None:
            print(f"{mode}: inference throughput change with STREAM: {100 * (concurrent / alone - 1):+.1f}%")
    print("Compare individual trials and inference rates; this is a synthetic diagnostic, not a real-tool or scheduling result.")


if __name__ == "__main__":
    main()
