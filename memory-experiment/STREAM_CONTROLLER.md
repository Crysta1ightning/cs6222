# STREAM-activity-aware HiCache backups

This is an experimental single-scheduler controller driven by explicit tool
activity, not bandwidth pressure. It has no LIKWID integration or bandwidth
estimator. The existing fresh-prompt workload remains a diagnostic: it cannot
establish a benefit under KV reuse or whole-agent completion time.

## Source layout and reproducibility

- Experiments: `~/cs6222/scripts` (its own Git repository).
- SGLang: `~/cs6222/sglang` (a separate, sibling Git repository).
- Both local branches: `experiment/stream-aware-backups`.
- Verified starting SGLang commit: `e00930c5489053f26d86b179cee0d087f846acbb`.
- `sglang-patches/stream-aware-backups.patch`, its SHA256, and `base-commit.txt`
  preserve the SGLang changes in the experiment repository. Nothing is pushed.

On another checkout at the recorded base, apply the patch before installation:

```bash
cd ~/cs6222
bash scripts/memory-experiment/sglang-patches/apply.sh ~/cs6222/sglang
module load python/3.12.5 cuda/13.0.1
source .venv/bin/activate
# An existing editable installation picks up these Python changes directly.
# Only if installation is needed:
SGLANG_BUILD_RUST_EXTS=none python -m pip install --no-cache-dir -e ./sglang/python
```

After editing SGLang, regenerate the reproducible patch:

```bash
bash scripts/memory-experiment/sglang-patches/export.sh ~/cs6222/sglang
```

Existing local build-script/hash modifications were preserved. No teammate
activity-aware implementation was found in the local checkout or branches.
The controller reuses the existing merged write queue, node-split handling,
GPU lock receipts, write acknowledgments, and `flush_pending_backups` hooks.

## Semantics and supported scope

All three modes use the **same underlying `write_through` policy**:

| Controller | While any tool ID is active | After the last tool ends/expires |
| --- | --- | --- |
| `normal` | Existing cache scheduling and merging | Existing behavior |
| `defer` | Hold optional writes in the existing protected write queue | Resume through `start_writing` at the next safe scheduler flush |
| `skip` | Decline optional opportunities before host allocation, commit, or backup locks | Future cache operations can reconsider unbacked data |

Already-submitted copies are never canceled. Required H2D restores have no
activity gate. Explicit allocation-critical backups bypass optional decisions.
Before allocation eviction, queued copies are submitted and acknowledged so
their existing GPU locks cannot prevent reclamation. Under write-through, a
skipped/unbacked evictable entry may still be discarded by the existing cache
policy; its future reuse may require recomputation. Skip therefore changes cache
retention, not the contents of active requests.

Explicit cache reset also drains queued/in-flight writes before destroying
tree and host-pool metadata; no stale queued references survive a reset.

Deferred entries are the existing CacheOperations, not saved arbitrary node IDs.
Their ordinary locks and split/ack handling remain in force. Default bounds:

- Pending token limit: `min(4096, half of GPU token capacity)`.
- Pending operations: 256.
- Maximum pending age: 2 seconds, checked at safe scheduler flush points.
- GPU free-token reserve: max(page size, one eighth of token capacity).

Crossing bounds, reaching allocation pressure, or requesting reclamation causes
a logged override. One node larger than the pending limit is submitted
immediately; it is never retained as an oversized deferred backlog. Thus the
cap can be transiently exceeded by one operation during its construction, but
that operation is immediately submitted. Submitted copies retain their ordinary
locks until acknowledgment. These are not real-time deadlines: a blocked
scheduler cannot react until its next safe scheduling point.

This version rejects TP/PP/DP/DCP > 1, PD disaggregation, non-FULL trees (SWA/Mamba), sidecars,
buffer-only host memory, L3 storage, and non-write-through policy at startup.
Use the standard Qwen3-0.6B single-GPU setup here. Runtime L3 attachment is
outside the supported experiment; do not enable it during a controlled run.
No claim is made about untested stacks.

## Exact launch and experiment commands

Use one existing GPU allocation and one compute-node shell for all three modes.
Do not nest another `srun` if already inside that shell. At least 16 allocated
physical CPUs are needed for the example placement below.

```bash
cd ~/cs6222
module load python/3.12.5 cuda/13.0.1
source .venv/bin/activate
python -c 'import os; print(sorted(os.sched_getaffinity(0)))'
lscpu -e=CPU,CORE,SOCKET,NODE
nvidia-smi topo -m
```

Set placement once. **These CPU IDs are the prior NUMA-2 layout, not a promise
about a new allocation.** Replace them with disjoint allowed physical cores
on your chosen NUMA node, then keep them fixed for every mode:

```bash
export EXP_SERVER_CPUS=32-35
export EXP_TOOL_CPUS=36-47
export EXP_NUMA_NODE=2
export EXP_THREADS=12
export STREAM_CONTROL_ROOT="${TMPDIR:-/tmp}/stream-aware-$USER-$SLURM_JOB_ID"
export STREAM_RESULT_ROOT="$HOME/cs6222/scripts/results/stream-aware-$SLURM_JOB_ID"
```

Build STREAM only if needed. Keep the same binary for all conditions. Stop
any previously running server on port 30000 before continuing. The following
loop uses identical CPU/RAM placement, model, KV limits, write policy, bounds,
and post-tool duration, changing only controller mode:

```bash
mkdir -p "$STREAM_RESULT_ROOT"
for MODE in normal defer skip; do
  mkdir -p "$STREAM_RESULT_ROOT/$MODE"
  bash scripts/memory-experiment/launch-stream-aware.sh "$MODE" \
    > "$STREAM_RESULT_ROOT/$MODE/server.log" 2>&1 &
  SERVER_PID=$!
  READY=0
  for ((attempt=0; attempt<600; attempt++)); do
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then break; fi
    if curl --max-time 2 -fsS http://127.0.0.1:30000/health >/dev/null; then
      READY=1; break
    fi
    sleep 2
  done
  if [ "$READY" != 1 ]; then
    tail -n 60 "$STREAM_RESULT_ROOT/$MODE/server.log"
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
    break
  fi

  # Optional small integration check; sends inference, but does not run STREAM.
  python scripts/memory-experiment/smoke-controller.py \
    --controller-mode "$MODE" --control-socket "$STREAM_CONTROL_ROOT/$MODE.sock"
  SMOKE_STATUS=$?
  RUN_STATUS=$SMOKE_STATUS
  if [ "$SMOKE_STATUS" = 0 ]; then
    numactl --physcpubind="$EXP_TOOL_CPUS" --membind="$EXP_NUMA_NODE" \
      python scripts/memory-experiment/run.py --mode hicache \
      --controller-mode "$MODE" --control-socket "$STREAM_CONTROL_ROOT/$MODE.sock" \
      --threads "$EXP_THREADS" --trials 5 --post-tool-seconds 5 \
      --output-dir "$STREAM_RESULT_ROOT/$MODE"
    RUN_STATUS=$?
  fi
  kill "$SERVER_PID"
  wait "$SERVER_PID" 2>/dev/null || true
  if [ "$RUN_STATUS" != 0 ]; then echo "Failed: $MODE; inspect logs"; break; fi
done
```

The RPC checks controller identity and mode: connecting a defer runner to a
normal server fails. Only one experiment runner should drive this server when
comparing performance. Reverse the mode order in a second pass to assess drift.
Use `--trials 1` first for a pilot; keep pilots separate from measured runs.

Defaults are preserved for legacy runs without a control socket. A controlled
run defaults to five seconds of subsequent inference. Defer and skip require
a positive post-tool duration; they cannot hide work by disabling that phase.

The final drain requires three consecutive idle controller snapshots. A final
counter-stability check detects delayed publication. If counters still change,
the post-tool/whole intervals are conservatively extended and the trial records
`late_accounting_extension=true`; inspect this flag before interpreting timing.

## Measurements and comparisons

Each trial contains three distinct `windows`:

- `tool`: actual Popen-through-termination STREAM interval, including array
  initialization and all kernels. ACK happens before launch; END follows
  termination, even after timeout/failure. STREAM warmup is separately tagged.
- `post_tool`: from STREAM termination through at least five seconds of
  subsequent inference, the final in-flight request, and deferred-copy draining.
- `whole`: the combined interval; use this alongside the tool-window result.

For controlled runs, the two inference warmup requests complete and their
backups drain before baseline snapshots. Inference submission then starts
after TOOL_START is acknowledged. `whole` also includes this control/setup
interval immediately before STREAM launch, so its cost is not hidden.

Counters are observed through HTTP around boundaries and can include scrape
overhead or delayed publication. They do not prove physical transfer overlap.
Controller JSONL events additionally record submission/completion acknowledgments,
bytes, pending volume, skips, deferrals, and pressure overrides, with monotonic
and Unix timestamps. CUDA copy duration is logged when available. Per-window
event aggregates use scheduler timestamps, not physical DMA start/end times.
Requests spanning boundaries are counted in `whole` when fully contained there;
per-window fully-contained request counts need not add up to the whole count.

Results include tool/client ACK events, scheduler placement, leases, queue
bounds, job/host/affinity/NUMA metadata, source versions/diffs, and raw STREAM
output. Missing metrics remain null/N/A. Trial failure or drain timeout is
saved as an error and rejected by the comparison tools.

```bash
python scripts/memory-experiment/summarize.py \
  "$STREAM_RESULT_ROOT"/normal/memory-*.json \
  "$STREAM_RESULT_ROOT"/defer/memory-*.json \
  "$STREAM_RESULT_ROOT"/skip/memory-*.json

for WINDOW in tool post_tool whole; do
  python scripts/memory-experiment/compare.py "$STREAM_RESULT_ROOT" \
    --window "$WINDOW" --baseline 1 \
    --output "$STREAM_RESULT_ROOT/comparison-$WINDOW.png"
done
```

Automatic directory ordering places normal before defer before skip. Separate
files/modes never merge. The older plot entry point delegates controlled
results to this renderer instead of grouping controllers together. STREAM
kernel bandwidth is N/A in post-tool/whole plots because those windows contain
no continuous STREAM kernel measurement. Their inference/transfer plots remain
available. PNG rendering requires the existing `rsvg-convert` executable.

Compare tool Triad bandwidth, then post-tool drain time/backlog and whole-window
elapsed time, completed requests, inference rate, and transferred bytes. A
tool-only gain with a larger post-tool cost is not an overall improvement.
This fixed-duration fresh-prompt diagnostic does not measure fixed-work agent
completion time and does not establish cache-reuse benefits. A subsequent
multi-session, reuse-bearing workload is required for those claims.

## Validation

CPU-only protocol and accounting tests:

```bash
python -m unittest discover -s scripts/memory-experiment -p test_activity.py -v
python -m unittest discover -s scripts/memory-experiment -p test_measurements.py -v
PYTHONPATH="$HOME/cs6222/sglang/python" CUDA_VISIBLE_DEVICES=999 \
  python -m unittest discover -s scripts/memory-experiment -p test_cache_hooks.py -v
```

Protocol tests bind a local UNIX socket. A sandbox prohibiting socket creation
must allow that local test operation; no external network or GPU is needed.
Cache-hook tests use real cache methods and CPU tensors with a mocked transfer
engine. They cannot establish real CUDA transfer or GPU KV correctness.
Run the smoke check and existing `check-hicache.py` on an allocated GPU to
verify real submissions, completed copies, inference, and restores. At
implementation time no GPU allocation was available; those checks remain
unverified. The broad upstream cache suite also contains GPU-dependent tests
and cannot be reported as passing on this login node.
