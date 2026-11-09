# Comparing backup policies

Run inside a prepared GPU allocation with EXP_SERVER_CPUS and EXP_TOOL_CPUS set to disjoint physical cores on EXP_NUMA_NODE. The default experiment uses 4 server cores and four STREAM workers with 3 cores each. Changing allocation size requires changing worker placement; it is not enough to change OMP_NUM_THREADS.

```bash
python scripts/trace-replay/policy_matrix.py \
  --plan "$EXP_RESULTS/plan.json" \
  --stream-worker "$EXP_RESULTS/stream-worker-2" \
  --output-dir "$EXP_RESULTS/matrix" --trials 3
python scripts/trace-replay/matrix_summary.py "$EXP_RESULTS/matrix"
```

Use a fresh output directory. The matrix starts and stops its own SGLang server for every configuration, specifying the base policy at server startup. It refuses to replace an existing server on port 30000. Ctrl+C stops the owned server and records an interrupted matrix; it does not cancel the allocation.

There are 15 configurations: three base policies (write-through, selective write-through and write-back), each combined with normal, skip, defer/all, defer/victim, or defer/through-victim. Each configuration runs both waiting tools and STREAM tools. An excluded warmup replay precedes measured trials. Configuration order is shuffled with seed 6222. Each trial resets the cache and preserves exact generated token IDs for subsequent requests within its session.

Defaults deliberately constrain the GPU KV pool to 4096 tokens despite ample physical GPU memory. A 2048-token free-space reserve, 4096-token deferred-queue bound and two-second age bound apply equally to all configurations. These are cache-pressure diagnostics, not production-optimal settings. Override with --gpu-cache-tokens, --reserve-tokens and --max-pending-tokens. Do not compare results with different bounds as though only the controller changed.

The pending-token argument above is a configured ceiling. The controller also
caps it at half the actual GPU pool, making the baseline effective pending
limit 2048 tokens. Read `controller_initial.max_pending_tokens` in raw replay
JSON for the actual value; `status.json` records the requested argument.

Victim selection preserves pending host-prefix dependencies and active-request locks. Through-victim includes the FIFO prefix through those dependencies. Required write-back copies and host-to-GPU restores proceed regardless of tool activity. Age and queue limits may flush more than a selected victim. Already submitted transfers are never cancelled. Write-back has no eager optional backup queue; its controller variants may be behaviorally equivalent. Activation counts must support any claim about queue-selection behavior.

STREAM is synthetic memory traffic replacing tool computation; waiting tools preserve tool timing without that traffic. Neither executes the original tool commands. The selected public TraceLab sessions supply request structure and tool durations; scaled token lengths and synthetic token content remain limitations. Fresh-prompt experiments cannot establish a benefit from KV reuse; this replay also includes growing session prefixes and a reuse tail.

The runner records source snapshots, exact launch/replay commands, CPU/NUMA/GPU placement, bounds, controller journals and raw JSON. The summarizer rejects timing failures, incomplete drains, inconsistent sources or placement, changed request sizes and mismatched transfer accounting. It retains missing counters as N/A. It excludes warmup and pilot directories. Tables separate waiting and STREAM conditions; tool-window and post-tool measurements remain separate in summary.csv. Plots show trial medians with minimum-to-maximum whiskers, not statistical confidence intervals.
