"""Measure isolated-request HiCache backup timing on a single-GPU server."""

import json
import time
import uuid
from pathlib import Path

import requests
from transformers import AutoTokenizer

BASE = "http://127.0.0.1:30000"
BYTE = "sglang:hicache_backup_bytes_total"
COUNT = "sglang:hicache_backup_duration_seconds_count"
SECONDS = "sglang:hicache_backup_duration_seconds_sum"


def snapshot():
    response = requests.get(BASE + "/metrics", timeout=30)
    response.raise_for_status()
    totals = {}
    for line in response.text.splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        name = fields[0].split("{")[0]
        if name in (BYTE, COUNT, SECONDS):
            totals[name] = totals.get(name, 0.0) + float(fields[1])
    if not all(name in totals for name in (BYTE, COUNT, SECONDS)):
        raise RuntimeError("Backup timing metrics missing. Enable HiCache and metrics, then restart the server.")
    return totals


def settle(require_count=None):
    # Counter stability allows acknowledgment/export lag; it is not a GPU trace.
    previous = snapshot()
    stable = 0
    for _ in range(30):
        time.sleep(1)
        current = snapshot()
        stable = stable + 1 if current == previous else 0
        if stable >= 3 and (require_count is None or current[COUNT] > require_count):
            return current
        previous = current
    raise RuntimeError("No timed backup observed, or counters did not settle within 30 seconds.")


def main():
    response = requests.get(BASE + "/health", timeout=10)
    response.raise_for_status()
    response = requests.get(BASE + "/server_info", timeout=30)
    response.raise_for_status()
    info = response.json()
    if not info.get("enable_hierarchical_cache") or not info.get("enable_metrics"):
        raise RuntimeError("The server on port 30000 must have both HiCache and metrics enabled.")
    tokenizer = AutoTokenizer.from_pretrained("Qwen/Qwen3-0.6B")
    # Prometheus creates labeled series lazily, on the first observation.
    # Trigger a backup before reading baseline timing counters.
    response = requests.post(BASE + "/generate", json={
        "input_ids": tokenizer.encode(uuid.uuid4().hex + "\n" + "Warmup record.\n" * 200)[:512],
        "sampling_params": {"max_new_tokens": 1, "temperature": 0},
    }, timeout=300)
    response.raise_for_status()
    time.sleep(3)
    rows = []
    for repeat in range(3):
        for tokens in (512, 1024, 2048):
            response = requests.post(BASE + "/flush_cache", timeout=30)
            # This endpoint returns plain text, with HTTP 200 on success.
            if response.status_code != 200:
                raise RuntimeError(f"Cache flush failed ({response.status_code}): {response.text.strip()}")
            before = settle()
            ids = tokenizer.encode(uuid.uuid4().hex + "\n" + "Example record for backup timing.\n" * 1500)[:tokens]
            response = requests.post(BASE + "/generate", json={
                "input_ids": ids,
                "sampling_params": {"max_new_tokens": 1, "temperature": 0},
            }, timeout=300)
            response.raise_for_status()
            after = settle(require_count=before[COUNT])
            delta = {name: after[name] - before[name] for name in before}
            if delta[SECONDS] <= 0 or delta[BYTE] <= 0:
                raise RuntimeError("Expected positive timed backup bytes and duration.")
            row = {
                "repeat": repeat + 1, "prompt_tokens": len(ids),
                "backup_bytes": delta[BYTE], "timed_batches": delta[COUNT],
                "total_copy_ms": delta[SECONDS] * 1000,
                "mean_batch_ms": delta[SECONDS] * 1000 / delta[COUNT],
                "active_payload_GB_s": delta[BYTE] / delta[SECONDS] / 1e9,
            }
            rows.append(row)
            print(json.dumps(row), flush=True)
    directory = Path(__file__).resolve().parent / "results"
    directory.mkdir(exist_ok=True)
    path = directory / f"backup-timing-{time.time_ns()}.json"
    path.write_text(json.dumps({"rows": rows, "note": "Single-GPU, isolated requests. CUDA-event copy timing excludes queue delay. Multiple batches yield an aggregate mean, not individual durations."}, indent=2) + "\n")
    print(f"Saved {path}")


if __name__ == "__main__":
    main()
