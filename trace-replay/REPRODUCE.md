# Reproduce the policy hierarchy experiments

Run shell commands from the **workspace parent** of this repository, normally
`~/cs6222`, containing `scripts/`, `sglang/`, and `.venv/`. This repository is
the `scripts/` directory. Fresh checkouts can use:

```bash
mkdir -p ~/cs6222
git clone --branch experiment/policy-hierarchy-reproduction https://github.com/Crysta1ightning/cs6222.git ~/cs6222/scripts
cd ~/cs6222
```

## Quick start and reproducibility contract

Use [experiments.py](experiments.py) as the common entry point. It resolves code
paths relative to its own location; the commands below assume the workspace
layout shown above. `python experiments.py --help` and each subcommand's
`--help` list the required arguments. CPU-only inspection and drawing do not
require a GPU allocation.

```bash
# CPU only: verify the published plans, summaries and frozen measured Git sources.
python scripts/trace-replay/experiments.py verify-reference

# After the allocation/setup/build steps in sections 1–2:
python scripts/trace-replay/experiments.py check-environment
python scripts/trace-replay/experiments.py prepare \
  --plans scripts/results/policy-plans

# Run both complete matrices. Repeating this command resumes compatible work.
python scripts/trace-replay/experiments.py run \
  --plans scripts/results/policy-plans --output "$EXP_RESULTS"

# CPU only, once BOTH profiles are complete: strict raw audit + tables + all plots.
python scripts/trace-replay/experiments.py report --output "$EXP_RESULTS"

# CPU only: redraw published results without access to the raw PACE files.
python scripts/trace-replay/experiments.py plot \
  --csv scripts/docs/policy-study-20261109/token4x/summary.csv \
  --output scripts/results/reference-plots

# Draw the conceptual policy hierarchy, rather than measurement bars.
python scripts/trace-replay/experiments.py hierarchy \
  --output scripts/results/policy-hierarchy
```

The `run` command pins model/tokenizer revision automatically, requires plans
identical to the published baseline/token4x plans, and checks Python plus eight
core package versions before starting either matrix. It delegates allocation,
placement and resume checks to the existing runner. To study different software
or workloads, invoke `study.py` explicitly in a fresh output directory and record
the change; it is a separate experiment.

The `report` command only publishes a complete `REPORT.md` after both raw audits
pass, requiring three trials per cell. If it fails, that file says validation is
in progress or failed; do not use stale tables as evidence of completion. This
command changes only analysis outputs, never raw trials. `plot` validates CSV
structure and draws figures; it cannot certify raw measurements. `verify-reference`
checks committed plan/CSV hashes, plot input hashes, all 30 cells/profile and
frozen measured Git source hashes. It needs the measurement commit in Git
history (use a full clone, or fetch that commit if your clone is shallow).

**Reproducibility means a pinned, inspectable procedure and validated repeated
measurements, not guaranteed identical performance numbers or output text.**
Shared-node load, CPU frequency, GPU/MIG topology, drivers and kernel scheduling
can change timings. Seeded GPU inference is not guaranteed bit-identical; even
the reference data have some varying generated outputs and generated-history
tails. Preserve that limitation when interpreting request/cache comparisons.

There are three distinct checks:

1. **Artifact identity:** source/model/dataset/plan/worker hashes, package versions
   and settings must be recorded and checked. The reference driver checks core
   versions; [all recorded package versions](../docs/policy-study-20261109/python-packages.txt)
   support a fuller environment comparison. This reference list is not a tested
   cross-platform dependency lockfile; do not blindly install it over the setup
   environment. The driver does not certify the full dependency closure or driver.
2. **Execution validity:** every cell must pass raw timing, shape, counter,
   placement, drain, worker-validation and cleanup checks. All 15 cells/profile
   must have three delay and three STREAM trials. Preserve failed attempts and
   warmups, but exclude them from measured summaries. Never merge allocations.
3. **Numerical repeatability:** use independent complete reruns on comparable
   hardware to assess whether effect directions and sizes persist. Compare
   distributions/ranges, not equality to a reference rounded number. These
   three-repeat studies do not supply a statistical significance guarantee.

For historical execution, the exact frozen scripts commit is
`120832cc1436f2ff2d6965ee2fb19644600078cd`; newer analysis adds STREAM work metrics.
To replay that implementation in a separate workspace, clone/check out that
commit there and follow its manual. Keep the current checkout for `experiments.py`
and post-run analysis. New runs from the current checkout record the current
source hashes and must remain separate from the historical allocation.

## 1. Allocate and verify resources

The reference layout is one NVIDIA GPU, **16 physical CPU cores**, 64 GiB host
RAM, four server cores and four STREAM workers with three cores each. Both CPU
groups must be disjoint and on the GPU-local NUMA node. The workload uses about
6 GiB for the four preallocated STREAM working sets, plus model/host KV memory.
Allow twelve hours for the baseline and larger matrix, including startup,
validation and possible retries. Each baseline configuration takes roughly
14 minutes on the recorded Blackwell MIG setup, not a guaranteed runtime.

PACE ICE login terminal:

```bash
tmux -L cs6222-policy new-session -s policy-hierarchy
python3 scripts/skills/pace-setup/scripts/setup.py \
  --project "$PWD" --cpus 16 --gpu rtx_pro_6000_blackwell \
  --partition ice-bw-gpu --memory 64G --time 12:00:00
```

Start `tmux` on the login node **before** allocating. Detach with Ctrl+B then D;
reattach on that same login host with `tmux -L cs6222-policy attach -t
policy-hierarchy`. This keeps the allocation-owning shell and driver alive when
SSH or the chat disconnects. It does not extend Slurm's time limit or survive a
login-host restart. Saving checkpoints alone cannot keep an allocation alive.

When using a direct Codex PTY instead of tmux, a controlling terminal is needed,
but that shell can be lost when the tool session closes:

```bash
script -q -e -c 'python3 scripts/skills/pace-setup/scripts/setup.py --project "$PWD" --cpus 16 --gpu rtx_pro_6000_blackwell --partition ice-bw-gpu --memory 64G --time 12:00:00' /dev/null
```

The helper checks current capacity, uses a bounded allocation wait, enters the
compute node, loads `python/3.12.5` and `cuda/13.0.1`, prepares the environment,
checks CUDA arithmetic, discovers CPU IDs, and writes `settings.json`, `env.sh`
and `session.bashrc` under `scripts/results/pace-setup/`. Wait for `status: ready`.
If capacity is unavailable, choose resources explicitly; `--queue --wait-seconds
600` opts into waiting. `--job-id JOB_ID` reuses your own allocation.

The first compute shell owns the allocation: keep it open. For another terminal,
copy the **exact** `second_terminal_command` from `settings.json`; it uses the
real job ID, CPU count and rc-file. Never source a saved environment on a login
node, a different allocation or an expired job. Changing CPU layout or GPU
creates a separate study; it cannot complete a controlled comparison by pooling
new rows with old hardware. Record whether CUDA sees a full GPU or a MIG slice.

## 2. Verify the controller and build the tool worker

The setup helper applies the bundled controller patch on a fresh SGLang clone.
For an existing checkout, inspect changes first, then run:

```bash
bash scripts/memory-experiment/sglang-patches/apply.sh "$PWD/sglang"
bash scripts/memory-experiment/build-worker.sh

# The setup helper pins Torch/SGLang but may resolve newer auxiliary packages.
# In the activated reference venv, pin the measured core package versions:
python -m pip install --no-cache-dir \
  -r scripts/trace-replay/reference-core-requirements.txt \
  --extra-index-url https://download.pytorch.org/whl/cu130
python -m pip check
python scripts/trace-replay/experiments.py check-environment
```

`apply.sh` verifies the patch checksum and recorded base, is idempotent, and
refuses to patch an incompatible/dirty checkout. The patch includes base-policy
handling and dependency-aware partial queue reclamation. It targets the base
in `sglang-patches/base-commit.txt`; current experiment implementation is based
on SGLang 0.5.21 and PyTorch 2.13.0+cu130. Package versions and local differences
are captured in the setup report and each run. No upstream SGLang push is needed.

The worker uses two iterations per block, matching the successful reference
replay. `STREAM_NTIMES=5 bash scripts/memory-experiment/build-worker.sh` explicitly
selects a different block size; its longer quantum may fail the trace timing
preflight. Use the same setting throughout a study.

The worker build records source/binary hashes, compiler version and the exact
compiler command (`build-command-worker.txt`). Keep the
same binary for every configuration within a study. `OMP_PROC_BIND` settings in
the launcher preserve all assigned server cores; the prepared tools bind each
worker to its disjoint subset. Arbitrary environment exports alone do not bind
processes.

Per-tool clients reuse the controller PID verified by the trial's parent client,
avoiding an extra synchronous STATUS handshake in the tool-start interval.
Every START/heartbeat/END still checks identity and mode; START must acknowledge
the active lease before any tool work. Controller restart detection is retained.

The launcher disables SGLang's redundant automatic NUMA lookup: PyTorch's NVML
index helper cannot resolve the allocated MIG UUID on this setup. The outer
`numactl --physcpubind=... --membind=...` remains responsible for the verified
placement, and children inherit it. This preserves Slurm's CUDA-visible device
mapping rather than substituting an unallocated physical GPU index.

## 3. Prepare deterministic baseline and larger plans

```bash
python scripts/trace-replay/study.py prepare \
  --source scripts/results/tracelab/source-v0.0.1.jsonl.gz \
  --output-dir scripts/results/policy-plans
```

The command downloads/verifies the pinned public TraceLab v0.0.1 release
(SHA256 `9d265eae69a31cae203848bea936f018148eed7ca8bf56050c5abe96da0b4e6b`).
It writes `baseline.json` and `token4x.json`. A different existing plan is never
overwritten. Seed 6222 selects four sessions and their first four consecutive
rounds, retaining tools of at least two seconds and no within-round overlap.
There are 27 eligible sessions in this deliberately filtered diagnostic subset.

| Setting | Baseline | token4x |
|---|---:|---:|
| Sessions / trace rounds / requests including reuse tail | 4 / 16 / 20 | Same |
| Tools / simultaneously active sessions | 12 / 4 | Same |
| Input and prefix scaling | 8% | 32% |
| Output lengths | 8%, cap 32 | Identical to baseline |
| Tool duration scaling / duration cap | 1 / uncapped | Identical |
| User wait cap | 5 seconds | Identical |
| Context limit / GPU KV pool, tokens | 4096 / 4096 | 16384 / 16384 |
| Reserve / configured pending limit, tokens | 2048 / 4096 | 8192 / 16384 |
| Effective pending limit after half-pool safety cap | 2048 | 8192 |
| Maximum deferral / host cache | 2 seconds / 4 GB | Same |

The controller caps the configured pending-token limit at half the actual GPU
KV capacity. `status.json` records requested settings; the replay JSON's
`controller_initial` records effective limits. Use those effective limits when
interpreting forced flushes. These are flush thresholds, not hard instantaneous
caps: a whole queued operation can transiently take pending tokens above them.
The summary records the observed per-trial peak pending tokens.

Use `python scripts/trace-replay/replay.py --controller-mode normal --plan
scripts/results/policy-plans/baseline.json --dry-run` to inspect shapes/timing.
The scale experiment increases the cache/payload regime as a whole: fixed host
capacity, admission pressure and prefill behavior can also change. It does not
isolate physical DMA interference or recreate original prompt/tool execution.

## 4. Run all fifteen configurations and resume

Inside the prepared compute shell:

```bash
export EXP_MODEL_REVISION=c1899de289a04d12100db370d81485cdf75e47ca
bash scripts/trace-replay/run-studies.sh scripts/results/policy-plans "$EXP_RESULTS"
```

This runs/resumes both profiles sequentially and preserves `study-driver.log`.
The revision export pins both server weights and replay tokenizer to
the model revision found in the November 9 cache; it is part of resume and
comparison compatibility checks. The entry point sets it automatically. Omit it only for an explicitly separate study.
For individual profiles, the equivalent commands are:

```bash
python scripts/trace-replay/study.py run \
  --plan scripts/results/policy-plans/baseline.json --profile baseline \
  --stream-worker scripts/memory-experiment/stream-worker \
  --output-dir "$EXP_RESULTS/baseline" --trials 3

python scripts/trace-replay/study.py run \
  --plan scripts/results/policy-plans/token4x.json --profile token4x \
  --stream-worker scripts/memory-experiment/stream-worker \
  --output-dir "$EXP_RESULTS/token4x" --trials 3
```

Run these sequentially. Each study has 15 configurations × 2 tool conditions ×
3 measured trials = **90 trials**, plus one excluded warmup per configuration.
The wrapper sets Triton attention, PyTorch sampling, seed 6222 and the profile's
context/cache bounds. The model is Qwen/Qwen3-0.6B, static fraction 0.30, CUDA
graphs disabled, host cache 4 GB. Configuration order is shuffled with seed 6222.

The runner owns each server; port 30000 must be free. A host lock prevents two
new study wrappers from benchmarking concurrently. It never stops someone
else's server or cancels an allocation. Ctrl+C stops the owned runner and server.
Timing failures stop the study and keep the full failed attempt. Do not weaken
timing thresholds to count a failed experiment as complete.

To continue after an interruption, repeat the exact command with `--resume`.
Successful configurations are validated and retained; the incomplete one gets
a fresh attempt directory. Resume rejects changed plans, worker binary, sources,
allocation, GPU identity or placement. **Commit code before measurements** and
keep it unchanged until the matrix finishes. A new allocation requires a fresh
output directory and a separate complete comparison. Repeat the same acceptance
criteria on every retry and report failed-attempt counts.

Each output contains `run-spec.json`, `progress.json`, `run.log`, immutable
`attempts/` and an assembled `matrix/`. Every attempt records commands, source
snapshots/diffs, placement, controller bounds, journals and raw replay JSON.
`STUDY_LOG.md` records settings links, accepted counts, active configuration and
last error in plain English whenever the checkpoint changes.
Keep those directories on persistent storage. A stale `running` status after
Slurm ends is not proof of completion; use the audit command.

## 5. Audit, combine compatible fragments, and draw results

No GPU is needed to audit or plot. For fragments from the **same** controlled
study (not different allocations):

```bash
python scripts/trace-replay/study.py assemble \
  /absolute/path/to/first-matrix /absolute/path/to/continuation \
  --output-dir scripts/results/assembled --trials 3
python scripts/trace-replay/matrix_summary.py scripts/results/assembled
```

`study.py audit` accepts the same inputs/output arguments without making result
links. `audit.json` lists accepted, rejected and missing configurations. Duplicate
valid attempts require an explicit selection of input roots; they are never
silently pooled. The assembler checks sources, placement, shapes, counters,
timing, full worker cleanup and exactly three trials in both conditions.
It only marks all 15 complete after validation. Failed fragments remain intact.

For an intentionally incomplete report:

```bash
python scripts/trace-replay/matrix_summary.py scripts/results/assembled --partial --plots
```

Outputs: `TABLES.md`, `summary.csv`, `validation.json`, and `plots/BASE_POLICY/`
with runtime, model-request latency, reuse, backup volume and STREAM bandwidth.
SVGs are always saved; install `rsvg-convert` (librsvg) for PNG export. Missing
partial cells are visibly N/A. Bars are trial medians and whiskers are min–max,
not confidence intervals. A complete matrix produces figures automatically.

To redraw an exported `summary.csv` (including a committed report) without the
GPU or ignored raw files:

```bash
python scripts/trace-replay/plot_summary.py scripts/docs/policy-study-20261109/token4x/summary.csv \
  --output-dir scripts/results/redrawn-summary
```

This checks aggregate row identities, sample counts and finite ordered
statistics, then draws SVG/optional PNG charts, including replay time before
counter settling. It also records the source CSV hash. This path does **not**
revalidate raw trials; use the raw audit commands above for that.

STREAM tools run for the trace's prescribed time, completing whole memory-work
blocks until the deadline. A slower worker can finish fewer blocks without
making the replay much longer. `tool.stream_rounds_total` records completed
blocks; `tool.stream_rounds_per_second` divides that total by the sum of actual
STREAM worker durations (including final-block overrun). Delay tools and legacy
artifacts without block counts show N/A. These metrics are derived from saved
runs; they require no new GPU run. Per-tool Triad GB/s is a kernel rate, not total
socket bandwidth. Inspect replay time before counter settling as well as total
time. Neither measures completion time for a fixed amount of original tool work.

The [completed baseline and 4× report](../docs/policy-study-20261109/README.md)
contains all 180 accepted measurements, settings and source identities. The
[November 5 report](../docs/policy-study/README.md) is a separate historical cohort.

Draw the **design hierarchy** and queue illustration separately from measured
result charts:

```bash
python scripts/memory-experiment/draw-backup-hierarchy.py \
  --output-dir scripts/results/backup-policy-hierarchy
```

This diagram command needs `rsvg-convert` and saves SVG and PNG. The hierarchy
describes options, not completed measurements. Compare controllers against
normal within each base policy/tool condition; inspect activation counts,
post-tool costs, restores and reuse before interpreting a speedup. See
[POLICY_MATRIX.md](POLICY_MATRIX.md) for correctness and interpretation limits.

## 6. Tests

With modules/venv loaded, CPU-only tests use small buffers and no model/GPU:

```bash
python -m unittest discover -s scripts/trace-replay -p 'test_*.py' -v
python -m unittest discover -s scripts/memory-experiment -p 'test_queue_selection.py' -v
```

Local UNIX sockets must be permitted; a socket-blocking sandbox cannot execute
the protocol tests. A test pass validates the harness, not GPU performance.

## 7. Read the outputs and interpret comparisons

| Output / metric | Meaning and interpretation |
|---|---|
| `REPORT.md` | Both-profile completion marker from `experiments.py report`; use only after command exit 0. |
| `PROFILE/run-spec.json`, `progress.json`, `STUDY_LOG.md` | Settings, hashes, exact commands, resume state, accepted and failed attempts. |
| `PROFILE/attempts/` | Preserve all raw JSON, server/controller logs and source snapshots. Assembled `matrix/` contains links into these attempts, so archiving only `matrix/` is insufficient. |
| `PROFILE/matrix/validation.json` | One acceptance record per measured trial: 90/profile. |
| `summary.csv` | Rows contain configuration, tool condition, metric, n, median, min, max. Missing data are N/A, not zero. |
| `whole.seconds` / `whole.execution_seconds` | Total replay including accounting settle / replay before settle. Long fixed tool waits can hide request-level changes. |
| `whole.latency_median_seconds` | Median model-request time within each trial, then median across trials; not a pooled-request median or tail percentile. |
| `whole.server_prefix_hit_rate` | Fraction of input tokens reused, not fraction of requests that hit. Lower reuse with lower copying may mean recomputation. |
| `whole.backup_GB` / `whole.restore_GB` | GPU-to-host / host-to-GPU logical payload in decimal GB; not PCIe utilization or measured DMA overlap. |
| `tool.stream_rounds_total` / `tool.stream_rounds_per_second` | Completed blocks / blocks per summed actual worker-second. Inspect these for timed tools even when replay times are similar. |
| `tool.stream_median_triad_GB_per_second` | Median per-tool kernel rate, not completed tool work or aggregate socket throughput. |
| Controller decisions, overrides, partial selections and peak pending tokens | Establish whether a strategy acted, or its pressure/age safeguards took over. Repeated polls are not unique cache entries. |

Compare against `normal/all` within the **same base policy, workload scale and
tool condition**. Percentage change is `100 × (candidate median / normal median − 1)`.
Negative time/latency/copy changes mean lower values; positive completed-work
rate changes mean more progress. Inspect absolute values and min/max ranges as
well as percentages. Do not interpret every decrease as a benefit: fewer
restores/backups can mean reusable cache was lost.

Use write-back's controller variants as negative controls: they have no optional
eager-copy queue here. If transfer counts are identical but timings differ,
shared-system variation is a plausible explanation. For defer policies check
whether deferred decisions and partial selections are nonzero before attributing
a speed change to deferral. The scale-up also changes prefill/admission/cache
pressure, so it does not isolate DMA contention. The reference report discusses
these cases in plain English with the actual measured values.

For durable recovery/archival, preserve both profile directories (including
`attempts/`), plans, setup `settings.json`/`env.sh`, compiler metadata, the full
Git source history and package versions on persistent storage. Preserve symlinks
and their targets together, or dereference links in an archive. Authentication
files and model/download caches are not result artifacts. Published aggregate
CSVs and their hashes allow independent redraw; raw trials are required for an
independent acceptance audit.
