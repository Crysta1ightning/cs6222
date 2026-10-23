# Tool-aware KV-backup experiment

This directory contains the counter/mmap scheduler and the current NUMA-verified,
request-aligned STREAM experiment. Prefill and decode share one GPU. Optional
GPU-to-host backups wait while a CPU tool is active; host-to-GPU restores remain
immediate. Required-progress flushing remains enabled.

## Files to review

| Purpose | Files |
|---|---|
| Scheduler logic and tool activity signal | `deferred_backup.py` |
| SGLang integration and installer | `sglang.patch`, `apply.py` |
| Batch entry point | `reproducible.sbatch` |
| Runtime preparation and orchestration | `prepare-runtime.sh`, `run-stability.sh` |
| Inference workload and request-triggered tool execution | `experiment.py`, `persistent_stream.py` |
| Persistent STREAM worker and build | `../memory-experiment/stream-worker.c`, `../memory-experiment/build-worker.sh` |
| NUMA/core/memory placement validation | `../memory-experiment/check-numa.py`, `../memory-experiment/numa-check.sbatch` |
| CPU validation | `test_policy.py`, `test_experiment.py`, `test_persistent_stream.py` |

`apply.py` supports a clean pinned SGLang checkout or an already installed current
patch. It refuses conflicting edits and does not migrate older prototypes.
`tool-tests.py` supports the experiment driver's alternative NumPy-test workload.
The STREAM source, parser and ordinary build script remain under
`../memory-experiment/`.

## Prepare and submit on PACE ICE

The existing Python environment and editable SGLang checkout must be installed
as described in `../setup-run.md`. The checkout must match `../sglang-commit.txt`.
The commands below use the project's existing path and cached Qwen3-0.6B model.
The runtime staging script packages installed FlashInfer/FFI files; rebuild the
archive when those packages change, before starting a batch.

```bash
cd /home/hice1/twu474/cs6222
module load python/3.12.5 cuda/13.0.1
source .venv/bin/activate
python backup-policy/apply.py
python -m unittest discover -s backup-policy -p 'test_*.py'
bash backup-policy/prepare-runtime.sh
mkdir -p results
sbatch backup-policy/reproducible.sbatch
```

The batch requests one A40/L40S/H100/H200 GPU, eight physical CPU cores, 32 GiB
RAM and one hour. `run-stability.sh` builds the STREAM binaries and worker,
applies the scheduler, runs CPU tests, and validates GPU-local NUMA placement.
The verifier requires all eight allocated physical cores on the GPU-local NUMA
node, splits them four/four between server and tool, inspects actual worker
thread affinities, and requires at least 95% of resident anonymous pages local.
It stops before measurements if placement fails. The separate
`memory-experiment/numa-check.sbatch` runs placement validation only and requests
16 cores (four server, twelve tool); the main experiment uses eight.

Then the batch runs a separate logged GPU gate diagnostic, four normal/normal
control pairs and four normal/deferred pairs, alternating ABBA ABBA. Each arm
starts a fresh server. The current workload is 128 sequential requests, 1,024
input/16 output tokens, a 32,768-token GPU cache and 4 GiB host cache.

One persistent four-thread STREAM worker initializes, first-touches, warms and
validates three arrays totalling 1.5 GiB before measurement. Ten five-iteration
rounds start before requests 1, 13, 25, 37, 49, 61, 73, 85, 97 and 109. A loop-start
acknowledgement releases the paired request; this does not align individual GPU
kernels. A round still active at the next target, a missed overlap, protocol
failure or failed numerical validation invalidates the measurement.

The batch disables optional queue/time caps with `--max-bytes 0 --max-delay 0`;
allocation-critical progress can still force transfers. Ordinary policy polls
read a binary mmap header and the queued-byte counter. Queue bytes are updated
at enqueue and cleared on submission/reset. Writers lock only during tool
transitions; periodic stale-process maintenance remains. Use a fresh `.bin`
state path and restart server/wrappers when changing the binary protocol.

## Measurement and output

Detailed outputs, JIT caches and staged FlashInfer/FFI files use node-local `/tmp`.
Other installed dependencies can still involve shared reads. Timed arms disable
policy event logging and buffer round results; whole-run transfer counters remain.
A separate gate diagnostic verifies defer/resume, but does not prove every timed
transfer placement or observe forced-event counts with performance logging off.

Warmup uses full-length cold-cache requests, then the first 13 workload prompts
without intermediate flushing to exercise early prefix-reuse shapes. A final
flush precedes timing. `warmup-requests.json` records both sequences. The added
prefix sequence addresses a first-use prefill stall observed in job 6467391;
it passes CPU tests but has not yet been validated in another GPU run.

Progress: `results/reproducible-batch-JOBID.log`.
On exit, detailed logs, source snapshots, metadata, request timings, pair results,
NUMA checks and sampled GPU clocks are archived to `results/stability-JOBID/`.
Resources are released automatically. Raw data and compiled/generated files stay
local and are excluded from Git. Completion includes the handshake but excludes
the final transfer-settling tail. Logical STREAM bandwidth is not a direct DRAM
counter measurement; these synthetic fresh-prefix runs have no useful host-cache
reuse and do not establish a reproducible overall speedup.
