# TraceLab session replay for the HiCache controller

This adds a trace-shaped workload to the existing `normal`, `defer`, and `skip`
experiments. It uses our scheduler UNIX-socket controller and `write_through`
policy; it does not install the separate teammate `backup-policy/sglang.patch`.
No additional SGLang edits, Rust compiler, DuckDB, or new Python dependencies
are needed with the existing editable installation.

## Paper and data

[TraceLab: Characterizing Coding Agent Workloads for LLM Serving](https://arxiv.org/abs/2606.30560),
Kan Zhu et al., studies repeated model calls, growing contexts, tool execution,
and cache reuse in coding agents. Our input is its pinned
[v0.0.1 public dataset](https://github.com/uw-syfi/TraceLab/releases/tag/v0.0.1):
357,161 rounds, with 432,510 tool records. This is the paper-sized release, not
the larger newer pool advertised on the project website. The 53.6 MB compressed
JSONL is downloaded once, SHA256-verified, and read without decompression to disk.

The public release removes prompt text and tool arguments. Therefore this is
**trace-derived synthetic serving replay**, not execution of the original tasks.
Nothing in a trace is interpreted as a shell command. Native SGLang `/generate`
receives valid synthetic token IDs; the exact generated output IDs are carried
forward. The construction follows the approach documented by the
[authors' replay runner](https://github.com/uw-syfi/TraceLab/tree/5c6550b0d08beff983e9159fe2575e7a2334c8c1/replay),
adapted here to the native endpoint and our controller. We do not vendor or
execute their Rust code or require their large text corpus.

## What is preserved and what changes

- Each selected session remains an ordered, closed-loop chain of rounds.
  Rounds retain the trace's prefix/append/output counts before transformations.
- A round reuses the first `prefix_len` IDs of its previous prompt plus exact
  generated IDs, then appends deterministic new IDs. Unavailable initial history
  is filled synthetically and counted. Provider cache-read counts do not reveal
  actual token identity or cache residency; they are a reconstruction model,
  not a target cache hit rate enforced on SGLang.
- Tool offsets relative to the first emitted tool and tool durations preserve
  overlap within a round. Tools start after the local LLM response completes;
  the next round waits for all tools in that round. Original mid-generation
  tool launch, streaming partial tool results, and cross-round background tools
  are not reconstructed. Local generation replaces original generation timing.
- User-driven gaps are separate waits with no activity signal. Session arrivals
  are explicitly synthetic: simultaneous by default or evenly spaced using
  `--session-spacing`. This is not an original production arrival trace.
- The default plan uses four hash-selected sessions, their first eight rounds
  (at least four), 5% token lengths, 10% tool/user wait lengths, at most 32 output
  tokens per round and five seconds per tool/user gap. These are a **scaled pilot**,
  not the full dataset distribution. Selection rejects complete candidate
  sessions that exceed the context limit; it does not discard oversized rounds
  from within selected chains. All exclusions, scales, caps and source hashes
  are recorded. `--output-cap 0`, `--max-tool-seconds 0`, and `--human-wait-cap 0`
  disable those caps; larger pilots need more GPU context and wall time.

Two tool surrogates are kept separate:

| Workload | Actual subprocess | Interpretation |
| --- | --- | --- |
| `delay` (default) | Sleeps for the transformed duration | Trace timing and cache reuse without intentional memory stress |
| `stream` | Repeats complete, validated STREAM invocations until the duration is reached | Synthetic memory-stress intervention; not the tools' original bandwidth |

Both signal START and wait for scheduler ACK before process launch, renew leases,
and END after process termination, including failures/timeouts. All STREAM
initialization and kernels are covered. STREAM never truncates a kernel to fit
a duration. Its overruns are recorded; `--max-stream-processes 1` bounds aggregate
working-set memory and serializes competing STREAM surrogates. Queue delays are
recorded, so this intervention does change trace timing. Increasing this limit
can multiply the 1.5 GiB per-process footprint and OpenMP thread count. No bandwidth
estimation or original tool-pressure classification is claimed.

The controller cannot cancel a copy already submitted before a tool starts.
In this closed-loop replay it can affect optional work still pending, or work
from other sessions during tool activity. It may have little effect when those
opportunities rarely overlap; that is a valid experimental outcome.

## Prepare once, without a GPU

Run from the project root, in the existing environment:

```bash
cd ~/cs6222
module load python/3.12.5 cuda/13.0.1
source .venv/bin/activate

# /tmp is temporary and may be local to this login node. Set a persistent path
# under project/scratch storage if you need the raw trace later.
TRACE_DATA="${TMPDIR:-/tmp}/syfi_coding_trace-v0.0.1.jsonl.gz"
python scripts/trace-replay/prepare.py fetch --output "$TRACE_DATA"
python scripts/trace-replay/prepare.py prepare "$TRACE_DATA" \
  --sessions 4 --rounds 8 --seed 6222 \
  --token-scale 0.05 --time-scale 0.1 --output-cap 32 \
  --max-context 4096 --max-tool-seconds 5 --human-wait-cap 5 \
  --output scripts/results/tracelab/plan.json

python scripts/trace-replay/replay.py \
  --plan scripts/results/tracelab/plan.json --controller-mode normal --dry-run
```

Only `plan.json` is needed on the compute node. Keep this **identical file** for
all controller modes. Defaults select a bounded, deterministic subset; inspect
it before interpreting results. Use a new output directory for a different plan.

## Run on one allocated GPU with fixed NUMA placement

Enter your existing Slurm allocation; request a new one only if needed. Do not
launch a second server on an occupied GPU or port. Verify CPU IDs for this
allocation:

```bash
python -c 'import os; print(sorted(os.sched_getaffinity(0)))'
lscpu -e=CPU,CORE,SOCKET,NODE
nvidia-smi topo -m
```

Assign disjoint physical cores on the same chosen GPU-local NUMA node. The prior
values below are examples and **must match your actual allocation**:

```bash
export EXP_SERVER_CPUS=32-35
export EXP_TOOL_CPUS=36-47
export EXP_NUMA_NODE=2
export EXP_THREADS=12
export TRACE_TRIALS=3
export TRACE_RESULT_ROOT="$HOME/cs6222/scripts/results/tracelab-$SLURM_JOB_ID-delay"

bash scripts/trace-replay/run-comparison.sh \
  "$HOME/cs6222/scripts/results/tracelab/plan.json" delay
```

The helper checks allowed CPUs, distinct physical cores and NUMA membership.
For each mode it starts a fresh existing launcher with identical settings:
Qwen3-0.6B, 8,192 GPU KV tokens, 4,096 context, 4 GiB host cache, and write-through.
It waits for readiness, runs a real prefix-cache reporting probe and the longest
planned prompt shape as warmup, flushes warmup
state before each trial, replays sessions, performs one extra reuse request per
session after all trace work, drains transfers, waits for stable HTTP counters,
and stops only its owned server process group. It does not cancel your allocation.
Use `TRACE_TRIALS=1` for a first GPU pilot. Reverse order in a separate pass with
`TRACE_MODES="skip defer normal"` and a separate result root to inspect order effects.
The replay scheduling timeout is 600 seconds per trial. Startup, cleanup, drain
and accounting waits have additional bounds; this is not a whole-job timeout.

For the additional STREAM-stress comparison, keep the same plan and placement:

```bash
export TRACE_RESULT_ROOT="$HOME/cs6222/scripts/results/tracelab-$SLURM_JOB_ID-stream"
bash scripts/trace-replay/run-comparison.sh \
  "$HOME/cs6222/scripts/results/tracelab/plan.json" stream
```

This builds `stream-5` explicitly, leaving your normal 200-iteration STREAM build
alone. The helper already generates plots. To regenerate them:

```bash
python scripts/trace-replay/compare.py "$TRACE_RESULT_ROOT" \
  --output-dir "$TRACE_RESULT_ROOT/plots"
```

Or run against a manually launched server:

```bash
numactl --physcpubind="$EXP_TOOL_CPUS" --membind="$EXP_NUMA_NODE" \
  python scripts/trace-replay/replay.py \
  --plan scripts/results/tracelab/plan.json \
  --controller-mode defer --control-socket "$STREAM_CONTROL_ROOT/defer.sock" \
  --tool-workload delay --max-active-sessions 4 --trials 3 \
  --output-dir scripts/results/tracelab-manual/defer
```

## Measurements and interpretation

Each result records source versions/diff hashes, source release and plan digest,
settings, controller bounds/placement, job/host/CPU/NUMA metadata, exact-token
prompt/output hashes, per-request total latency and cached-token breakdown when
available, actual tool intervals, surrogate overruns/queue times, controller
events/ACKs, transfer counters, and failure status. Nonstreaming requests do not
measure TTFT or token-by-token latency; these remain unavailable.

Three windows are separate:

1. `tool`: union of actual subprocess lifetimes, with overlaps counted once.
2. `post_tool`: the final tool's exit through remaining trace inference, the
   fixed-count reuse tail, drain and accounting settle.
3. `whole`: all trace execution, human gaps, tool work, subsequent inference,
   draining and accounting overhead. Whole timing includes at least five seconds
   of stable-counter observation; `accounting_settle_seconds` records that cost.

Controller events are scoped by scheduler timestamps; they are not physical DMA
start/end measurements. Per-window request rates count fully contained requests;
spanning requests remain in the whole run. The controller journal gives per-window
backup submissions/completions. HTTP H2D counters are compared for the whole run,
not invented for individual tool windows. Missing metrics remain null/N/A.

The four separate PNGs show whole-run time, median request latency, measured
prefix hit fraction, and measured H2D restore payload. Bars show medians and black
whiskers min–max across trials; there are no individual trial dots. `summary.csv`
also keeps tool/post-tool/whole durations, backup bytes and rates separate. The
comparison refuses different plans, tool surrogates, source versions, binaries,
placements, controller bounds, or key server/replay settings.

For an actual HiCache claim, check `host_restore_measurement_available` and
`host_restore_observed`, and inspect actual backup/restore bytes and later request
latency. **Cached tokens alone do not establish host-cache reuse.** A scaled
working set that stays on the GPU may show no H2D traffic. Increase session
concurrency/length or reduce the GPU token budget consistently across every mode
for a separate cache-pressure sweep. Do not flush between rounds inside a session;
that would destroy the reuse being studied. Synthetic prefix reconstruction and
token scaling cannot establish original-model answer quality or original-fleet
performance. The original fresh-prompt experiment remains a useful diagnostic,
but cannot establish reuse benefits.

## CPU validation and remaining GPU checks

```bash
python -m unittest discover -s scripts/trace-replay -p 'test_*.py'
python -m unittest discover -s scripts/memory-experiment -p 'test_activity.py'
python -m unittest discover -s scripts/memory-experiment -p 'test_measurements.py'
```

The trace tests use temporary fixtures and local UNIX sockets; inference is
mocked. They cover deterministic selection, context bounds, missing tool timing,
overlapping tools, human gaps, exact output-ID carry-forward, missing metrics,
window accounting, subprocess failure cleanup, comparison compatibility, and a
complete replay/measurement lifecycle. Actual GPU inference, H2D/D2H traffic,
restore correctness, throughput and controller benefits still require a GPU run.

## Attribution

Trace data: **TraceLab (SyFI Lab, University of Washington), Kan Zhu et al.**,
<https://tracelab.cs.washington.edu>, release v0.0.1,
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
Changes: session selection, token/time scaling, duration/output caps, synthetic
prompt reconstruction, synthetic session arrivals, and optional STREAM substitution.
Raw data and derived experimental plans/results are local generated files and
are not committed to this repository.
