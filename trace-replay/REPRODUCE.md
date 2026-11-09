# Reproduce the policy hierarchy experiments

Run shell commands from the **workspace parent** of this repository, normally
`~/cs6222`, containing `scripts/`, `sglang/`, and `.venv/`. This repository is
the `scripts/` directory. Fresh checkouts can use:

```bash
mkdir -p ~/cs6222
git clone --branch experiment/policy-hierarchy-reproduction https://github.com/Crysta1ightning/cs6222.git ~/cs6222/scripts
cd ~/cs6222
```

## 1. Allocate and verify resources

The reference layout is one NVIDIA GPU, **16 physical CPU cores**, 64 GiB host
RAM, four server cores and four STREAM workers with three cores each. Both CPU
groups must be disjoint and on the GPU-local NUMA node. The workload uses about
6 GiB for the four preallocated STREAM working sets, plus model/host KV memory.
Keep an eight-hour allocation for the baseline and larger matrix; scheduling
and retries can require more time. Each baseline configuration takes roughly
13 minutes on the recorded Blackwell MIG setup, not a guaranteed runtime.

PACE ICE login terminal:

```bash
python3 scripts/skills/pace-setup/scripts/setup.py \
  --project "$PWD" --cpus 16 --gpu rtx_pro_6000_blackwell \
  --partition ice-bw-gpu --memory 64G --time 08:00:00
```

From a Codex PTY, use a controlling terminal:

```bash
script -q -e -c 'python3 scripts/skills/pace-setup/scripts/setup.py --project "$PWD" --cpus 16 --gpu rtx_pro_6000_blackwell --partition ice-bw-gpu --memory 64G --time 08:00:00' /dev/null
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
| Reserve / pending queue bound, tokens | 2048 / 4096 | 8192 / 16384 |
| Maximum deferral / host cache | 2 seconds / 4 GB | Same |

Use `python scripts/trace-replay/replay.py --controller-mode normal --plan
scripts/results/policy-plans/baseline.json --dry-run` to inspect shapes/timing.
The scale experiment increases the cache/payload regime as a whole: fixed host
capacity, admission pressure and prefill behavior can also change. It does not
isolate physical DMA interference or recreate original prompt/tool execution.

## 4. Run all fifteen configurations and resume

Inside the prepared compute shell:

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
