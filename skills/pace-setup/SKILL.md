---
name: pace-setup
description: "Prepare a PACE ICE GPU allocation and the cs6222 SGLang environment, discover disjoint physical CPU cores on one NUMA node, and report verified settings and shell exports. Use for experiment setup, resource changes, or a new allocation."
---

# PACE experiment setup

Use the bundled [setup helper](scripts/setup.py) to prepare `~/cs6222` following
the project's `MLsys_proj.pdf`. This prepares resources and verifies readiness;
it does not start an inference server or run benchmarks.

## Invocation and choices

Examples the user can type:

- `$pace-setup` — defaults: 16 physical CPU cores, one L40S, 32G host RAM, one hour.
- `$pace-setup with 32 CPU cores, an H100, and 128G RAM`.
- `$pace-setup using my existing job 1234567 with 8 CPU cores and an L40S`.

Interpret CPU count as `--cpus-per-task` on **one compute node**, not a count of
compute nodes. Keep user choices for this invocation; use defaults for omitted
choices. CPU IDs, NUMA node, GPU identity and job ID must be discovered afresh.
Default server/tool split is four physical cores for SGLang and the remainder for
tools; with fewer than five cores, reserve one for tools and use the rest for the
server. `--server-cores` overrides this split. At least two physical cores are
needed. Never reuse the historical CPU IDs 32–47 as defaults.

## Run setup

Run `python3 <this-skill>/scripts/setup.py --cpus 16 --gpu l40s` with optional
`--memory 64G`, `--time 02:00:00`, `--server-cores 4`, `--project PATH`, or
`--job-id ID`. Do not use `--compute` directly. `--dry-run` prints the request
without submitting, installing or allocating anything.

Use an explicitly supplied job or the current `SLURM_JOB_ID` if present. Otherwise
the helper submits a single persistent batch allocation, equivalent in resources
to the manual's interactive allocation. The batch keeps the job alive after the
agent's setup command exits, up to its Slurm time limit. It uses bounded Slurm
commands, a ten-minute queue wait and a thirty-minute setup timeout by default;
adjust `--wait-seconds` or `--setup-timeout` for the user's needs. Yield tool calls
and give updates during long waits; inspect the printed `allocation.json` and
`setup.log` for progress instead of launching duplicate allocations.

The helper loads `python/3.12.5` and `cuda/13.0.1`, reuses `.venv`, verifies the
recorded SGLang base is an ancestor, checks dependencies, identifies CUDA logical
device 0 by GPU UUID, discovers allowed physical cores and memory nodes, checks
binding with a first-touch memory probe, and runs the manual's small CUDA matrix
test. Installation occurs only for missing checkout/environment components.
`--repair-install` explicitly reinstalls the PDF's package pins; do not choose it
for a healthy environment. Existing Git changes are preserved; no pull/reset,
teammate patch installation, package upgrade on every run, or push is performed.

NUMA rules:

- Both CPU groups must be disjoint, represent distinct physical cores, and fit
  entirely on one permitted NUMA node. SMT siblings do not count as extra cores.
- Prefer and require the GPU's PCI-local NUMA node by default. The Slurm request
  uses GRES binding, but it does not assume that this guarantees NUMA placement.
- If placement cannot satisfy the requested core count, report per-node capacity
  and stop. Never silently reduce CPU count, cross NUMA nodes, or use unallocated
  cores. A remote common NUMA node requires explicit `--allow-remote-numa`;
  unknown GPU locality requires explicit `--numa-node` and must be reported as
  unverified GPU locality.
- On failure/timeout/interruption, the helper cancels only a new job it created.
  A reused user allocation is preserved. Do not cancel other jobs or retry
  allocations indefinitely. Successful allocations remain active for experiments;
  report their job ID and time limit so the user can release them later.

## Environment and subsequent work

Success writes `settings.json` and a guarded `env.sh` under the printed setup
directory in `scripts/results/pace-setup/`. These contain the **actual** settings.
Never call setup finished unless `settings.json` says `ready` and the GPU and
memory checks passed. The memory probe verifies its own 32 MiB allocation; it
does not prove future KV-buffer placement.

For every subsequent agent experiment command, use the printed full-allocation
`srun --jobid=ID --overlap -n1 -cN --cpu-bind=cores ...` prefix and source the
absolute `env.sh` in that compute step. Do not source it on a login node. The
guard rejects another host/job, insufficient CPU affinity or changed topology.
Shell environment changes cannot propagate into an unrelated user terminal;
explain that the user's own compute shell can source the same file once. There
is no need for them to manually rediscover/export CPU IDs on every allocation.

Environment variables describe placement; they do not bind arbitrary processes
by themselves. Existing `memory-experiment/launch-stream-aware.sh` and
`trace-replay/run-comparison.sh` apply CPU and memory binding. For other commands,
use `numactl --physcpubind="$EXP_SERVER_CPUS" --membind="$EXP_NUMA_NODE"` for the
server and the corresponding `EXP_TOOL_CPUS` binding for tools before allocation
of their working buffers. Preserve Slurm's `CUDA_VISIBLE_DEVICES` mapping.

## Required completion report

State job ID, compute hostname, requested and actual GPU model/UUID/memory,
selected CPU count, server/tool CPU IDs, shared NUMA node and GPU locality,
requested RAM/time and actual Slurm limits, modules/venv, package versions,
SGLang commit/local changes, and check results. Print the exact generated export
lines, especially `EXP_SERVER_CPUS`, `EXP_TOOL_CPUS`, `EXP_NUMA_NODE`, `EXP_THREADS`,
`EXP_RESULTS`, `EXP_JOB_ID`, `EXP_ENV_FILE`, `STREAM_CONTROL_ROOT`,
`STREAM_RESULT_ROOT` and `TRACE_RESULT_ROOT`. Link the report and environment file.
Say whether a server/experiment was started (default: neither). If setup is
pending or failed, say so and do not invent CPU IDs or present defaults as
verified values.
