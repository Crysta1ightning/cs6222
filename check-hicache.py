"""Check GPU/host KV traffic against the 8192-token setup below.

Run on the compute node hosting SGLang, in the activated environment.
This checks transfer functionality; it does not measure tool interference.
"""

import time

import requests
from transformers import AutoTokenizer

BASE = "http://127.0.0.1:30000"
COUNTERS = (
    "sglang:hicache_backup_bytes_total",
    "sglang:load_back_bytes_total",
    "sglang:evicted_tokens_total",
)


def metrics():
    response = requests.get(f"{BASE}/metrics", timeout=30)
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


def settled_metrics():
    # Allow completed asynchronous transfers to reach the exported counters.
    time.sleep(2)
    return metrics()


def report(label, before, after):
    print(f"\n{label}", flush=True)
    for name in COUNTERS:
        print(f"  {name}: +{after[name] - before[name]:,.0f}", flush=True)


def generate(ids):
    response = requests.post(
        f"{BASE}/generate",
        json={
            "input_ids": ids,
            "sampling_params": {"max_new_tokens": 1, "temperature": 0},
        },
        timeout=300,
    )
    response.raise_for_status()


def main():
    response = requests.get(f"{BASE}/health", timeout=10)
    response.raise_for_status()
    tokenizer = AutoTokenizer.from_pretrained("Qwen/Qwen3-0.6B")
    prompts = [
        tokenizer.encode(
            f"Document {i}.\n"
            + f"This is document {i}, containing example records.\n" * 500
        )[:2048]
        for i in range(8)
    ]
    before = settled_metrics()
    for i, ids in enumerate(prompts):
        generate(ids)
        print(f"Completed document {i}: {len(ids)} tokens", flush=True)
    filled = settled_metrics()
    report("Fill phase", before, filled)
    generate(prompts[0])
    restored = settled_metrics()
    report("Revisit document 0", filled, restored)
    backup = filled[COUNTERS[0]] > before[COUNTERS[0]]
    restore = restored[COUNTERS[1]] > filled[COUNTERS[1]]
    if backup and restore:
        print("\nPASS: GPU→host backup and host→GPU restore observed.")
    else:
        print("\nNOT VERIFIED: inspect cache capacity, metrics, and server logs.")
    print("This is a functionality check, not evidence of tool interference.")


if __name__ == "__main__":
    main()
