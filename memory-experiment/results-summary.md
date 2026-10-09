# Memory-bandwidth diagnostic results

Five trials per condition on the same L40S compute node and Slurm allocation (`6114364`), using three STREAM threads and the same CPU affinity (`0–3`). Conditions ran in this order: HiCache, inference without HiCache, memory only.

| Condition | Median Triad bandwidth | Trial range | Median backup throughput | Median inference rate |
| --- | ---: | ---: | ---: | ---: |
| Memory only | 40.08 GB/s | 40.06–40.18 GB/s | 0 | 0 |
| Inference without HiCache | 38.42 GB/s | 37.61–39.80 GB/s | 0 | 23.74 requests/s |
| Inference with HiCache | 37.61 GB/s | 36.86–38.47 GB/s | 4.31 GB/s | 18.53 requests/s |

Median bandwidth was approximately 4.1% below baseline during inference without HiCache and 6.2% below baseline with HiCache. HiCache's median was 2.1% below the inference-only condition. The concurrent-condition ranges overlap.

The two server modes used the same model, attention backend, token/context limits, static memory fraction, and disabled CUDA graphs. HiCache backup traffic was confirmed. These are logical application bandwidth estimates and transfer-counter windows, not hardware DRAM bandwidth measurements or precise physical transfer timelines.

This is a small descriptive difference consistent with possible additional interference; it does not isolate its cause. Inference request rates differed, HiCache adds host-side bookkeeping as well as transfers, and the conditions ran in separate blocks on a shared node. Five sequential trials per block are not independent replications across allocations. The lower inference rate with HiCache also does not establish that STREAM caused it: we have not measured inference throughput without STREAM in both server modes.

The prior repository-search experiments found no consistent tool slowdown. Taken together, these results do not yet justify a dynamic backup controller or establish an end-to-end agent benefit.

Next decision: repeat matched-rate, controlled measurements only if needed to resolve the small synthetic effect; prioritize a second real tool (fixed test execution). If real tools remain unaffected under representative backup loads, report the negative result rather than increase synthetic pressure indefinitely.

## Follow-up: inference-alone baselines

Five 30-second trials per server mode used the same fresh 2048-token inputs, one output token, and sequential closed-loop requests, without STREAM.

| Configuration | Median inference alone | Median with STREAM | Observed throughput decrease |
| --- | ---: | ---: | ---: |
| HiCache disabled | 24.33 requests/s | 23.74 requests/s | About 2.4% |
| HiCache write-through enabled | 19.43 requests/s | 18.53 requests/s | About 4.6% |

There is a descriptive difference of about 2.2 percentage points between these relative decreases, consistent with possible additional sensitivity when HiCache is enabled. This is not an isolated measurement of transfer-induced interference: the baselines and concurrent runs occurred in separate time blocks, request rates differ, and HiCache adds other work as well as transfers.

Even without STREAM, HiCache-enabled request throughput was about 20% below the disabled mode on these fresh prompts. This is the cost of this no-reuse configuration, not evidence that STREAM caused that entire difference or that HiCache is generally inferior. A reuse-bearing workload is needed to evaluate its cache benefit and any backup-deferral policy.

These observations justify a controlled follow-up if pursuing the hypothesis, but do not establish statistical significance, resource priority, or a controller's achievable gain. Alternating inference-alone and concurrent trials within each server session would reduce temporal confounding. Fixed offered request rates and actual transfer timelines would further improve attribution.

Additional files:

- `../results/memory-no-hicache-inference-only-1791510862544831351.json`
- `../results/memory-hicache-inference-only-1791511276362731904.json`

### Original three-condition files

- `../results/memory-hicache-1791508389926806934.json`
- `../results/memory-no-hicache-1791508622943090413.json`
- `../results/memory-memory-only-1791508785583776887.json`

These paths are relative to this directory. Each file contains raw-log paths and configuration snapshots.

## Follow-up: alternating inference-alone and STREAM trials

Five adjacent pairs per server setting alternated the within-pair order. Rounded displayed results:

| Setting | Median inference alone | Median inference + STREAM | Throughput decrease |
| --- | ---: | ---: | ---: |
| HiCache enabled | 19.33 requests/s | 18.46 requests/s | About 4.5% |
| HiCache disabled | 24.57 requests/s | 23.73 requests/s | About 3.4% |

All five pairs in each setting had lower inference throughput with STREAM. This strengthens the evidence that this synthetic CPU workload interferes with inference under either setting. The difference between relative decreases is about 1.1 percentage points; it does not demonstrate that backup transfers specifically cause the entire observed interference or that deferring backups would recover it.

HiCache and no-HiCache pairs ran in different Slurm allocations, although the displayed hostname is the same. Placement, shared-node activity, and timing may differ. A direct cross-setting causal comparison remains limited. This workload has fresh prompts and no useful long-prefix reuse, and inference's host-side work competes for the same allocated CPU cores as STREAM.

Files are in `../results/paired-1791511936` (HiCache) and `../results/paired-1791512866` (no HiCache). These results measure CPU-workload effects on inference; the original proposal additionally requires effects on real tools and an improvement in whole-agent completion time from a controllable backup policy.
