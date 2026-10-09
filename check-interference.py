"""Screen for tool slowdown under concurrent inference; not causal proof.

Example: python check-interference.py --label hicache --pairs 10
Run against a fresh server on the same compute node. Repeat against a server
with HiCache disabled, with the same other settings, using --label no-hicache.
"""

import argparse
import json
import random
import shutil
import statistics
import subprocess
import threading
import time
import uuid
from pathlib import Path

import requests
from transformers import AutoTokenizer

BASE = "http://127.0.0.1:30000"
COUNTERS = ("sglang:hicache_backup_bytes_total", "sglang:load_back_bytes_total")


def metrics():
    response = requests.get(f"{BASE}/metrics", timeout=15)
    response.raise_for_status()
    totals = dict.fromkeys(COUNTERS, 0.0)
    for line in response.text.splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        name = fields[0].split("{")[0]
        if name in totals:
            totals[name] += float(fields[1])
    return totals


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--pairs", type=int, default=10)
    parser.add_argument("--searches", type=int, default=10)
    parser.add_argument("--workload", choices=("backups", "restores"), default="backups",
                        help="backups uses unique prompts; restores cycles eight prompts")
    args = parser.parse_args()
    if args.pairs < 1 or args.searches < 1:
        parser.error("pairs and searches must be positive")
    root = Path(__file__).resolve().parent
    repo = root / "sglang" / "python"
    rg = shutil.which("rg")
    if not rg:
        raise SystemExit("rg is missing from PATH; load/install ripgrep first.")
    tool_cmd = [rg, "--threads", "1", "--no-ignore", "-g", "*.py",
                "-c", "cache|scheduler|transfer", str(repo)]
    info = requests.get(f"{BASE}/get_server_info", timeout=30)
    info.raise_for_status()
    tokenizer = AutoTokenizer.from_pretrained("Qwen/Qwen3-0.6B")
    prompts = [tokenizer.encode(f"Document {i}.\n" +
               f"This is document {i}, containing example records.\n" * 500)[:2048]
               for i in range(8)]
    # Tokenize outside timed trials. Put a unique request identifier before
    # the body so each backup request has a new full prefix-cache path.
    run_prefix = tokenizer.encode(f"Run {uuid.uuid4().hex}. Document ")
    digits = {c: tokenizer.encode(c, add_special_tokens=False) for c in "0123456789"}
    separator = tokenizer.encode(".\n", add_special_tokens=False)
    body = tokenizer.encode("Example repository records and descriptions.\n" * 600,
                            add_special_tokens=False)
    request_index = 0

    def next_prompt():
        nonlocal request_index
        index = request_index
        request_index += 1
        if args.workload == "restores":
            return index, prompts[index % len(prompts)]
        prefix = run_prefix + [token for c in f"{index:012d}" for token in digits[c]] + separator
        return index, prefix + body[:2048 - len(prefix)]

    print(f"Workload: {args.workload}; {args.pairs} pairs; {args.searches} searches per trial",
          flush=True)
    records = []
    request_records = []
    rng = random.Random(6222)

    def search():
        started = time.monotonic_ns()
        for _ in range(args.searches):
            result = subprocess.run(tool_cmd, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE, text=True, timeout=120)
            if result.returncode != 0:
                raise RuntimeError(f"rg failed: {result.stderr}")
        ended = time.monotonic_ns()
        return started, ended

    # Warm filesystem caches in both conditions; this experiment is warm-cache.
    search()
    for pair in range(args.pairs):
        conditions = ["tool-only", "concurrent-inference"]
        rng.shuffle(conditions)
        for condition in conditions:
            stop = threading.Event()
            ready = threading.Event()
            errors = []

            def worker():
                try:
                    while not stop.is_set():
                        index, ids = next_prompt()
                        started = time.monotonic_ns()
                        ready.set()
                        response = requests.post(f"{BASE}/generate", json={
                            "input_ids": ids,
                            "sampling_params": {"max_new_tokens": 1, "temperature": 0},
                        }, timeout=120)
                        response.raise_for_status()
                        request_records.append({"pair": pair, "index": index,
                                                "start_ns": started,
                                                "end_ns": time.monotonic_ns()})
                except Exception as exc:
                    errors.append(str(exc))
                    ready.set()

            before = metrics()
            thread = None
            if condition == "concurrent-inference":
                thread = threading.Thread(target=worker, daemon=True)
                thread.start()
                ready.wait(timeout=15)
            started, ended = search()
            # Counter observations bracket the tool but include HTTP scrape
            # overhead and delayed metric publication. They are not GPU timestamps.
            after = metrics()
            stop.set()
            if thread:
                thread.join(timeout=130)
                if thread.is_alive():
                    errors.append("request worker did not stop")
            record = {"pair": pair, "condition": condition,
                      "tool_start_ns": started, "tool_end_ns": ended,
                      "tool_seconds": (ended - started) / 1e9,
                      "counter_deltas": {key: after[key] - before[key] for key in COUNTERS},
                      "errors": errors}
            records.append(record)
            print(json.dumps(record), flush=True)
            if errors:
                break
        if any(record["errors"] for record in records):
            break
    destination = root / "results"
    destination.mkdir(exist_ok=True)
    output = destination / f"interference-{args.label}-{time.time_ns()}.json"
    output.write_text(json.dumps({"label": args.label, "workload": args.workload,
        "server_info": info.json(),
        "hostname": __import__("socket").gethostname(), "tool_command": tool_cmd,
        "searches_per_trial": args.searches, "records": records,
        "requests": request_records,
        "limitation": "Counter windows do not prove exact physical transfer overlap; concurrent inference is a confound."
    }, indent=2) + "\n")
    for condition in ("tool-only", "concurrent-inference"):
        values = [r["tool_seconds"] for r in records
                  if r["condition"] == condition and not r["errors"]]
        if values:
            print(f"{condition}: median {statistics.median(values):.3f}s ({len(values)} trials)")
    print(f"Saved {output}")
    print("Screening only: compare with HiCache-disabled runs before attributing slowdown to transfers.")


if __name__ == "__main__":
    main()
