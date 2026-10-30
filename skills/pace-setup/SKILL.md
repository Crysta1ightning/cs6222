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
`--job-id ID`. Do not use internal `--compute`/`--enter` directly. `--dry-run` prints the request
without submitting, installing or allocating anything.

Before a new allocation, the helper reads Slurm's current node inventory. It
checks partition membership, node state, GPU totals minus allocated GPUs,
scheduler CPU capacity minus allocated CPUs, and configured RAM minus allocated
RAM/reserved memory. `MIXED` alone does not mean a GPU is free; `FreeMem` and CPU
load do not determine schedulable capacity. Drained, reserved and down nodes are
excluded. The socket/core check is only a coarse capacity filter; NUMA locality
and the actual CPU IDs still must be verified after allocation.

If the preferred/default combination has a capacity candidate, **proceed directly
with that same request**. Do not show a resource menu, alternatives, or ask the
user to reconfirm it. If it does not, do not submit a queue request by default:
give a concise recommendation from compatible NVIDIA GPUs, retaining the
requested CPU count/RAM where possible. If none fits, label any smaller-CPU
suggestion as a pilot with a changed server/tool split. Never silently change GPU
type, CPU count or memory. Wait for the user's alternative selection, unless
they have explicitly authorized one. Do not suggest AMD GPUs for this CUDA
environment. Save the snapshot in `availability.json`.

`--check-resources` explicitly prints the current read-only snapshot without
allocating. `--queue --wait-seconds 600` explicitly opts into waiting despite a
busy snapshot. Default allocation wait is 30 seconds to bound races or scheduling
constraints after a favorable snapshot. A free-capacity snapshot does not bypass
Slurm priorities, reservations, account restrictions or GRES/NUMA constraints.

Use an explicitly supplied job or the current `SLURM_JOB_ID` if present. Otherwise
the helper runs **salloc**, waits for its allocation, and uses **srun --pty** to
enter the compute node. It performs setup there, then opens an interactive Bash
shell that automatically sources the discovered environment. The first shell
owns the new allocation: keep it open while experiments run; exiting it releases
the allocation. It uses bounded Slurm commands, a thirty-second allocation wait and a
thirty-minute setup timeout by default;
adjust `--wait-seconds` or `--setup-timeout` for the user's needs. Run this helper
with a PTY (`exec_command` with `tty: true`) and yield during allocation/startup.
On this Codex host, also give `salloc` a controlling terminal by wrapping the
helper as `script -q -e -c 'python3 /ABSOLUTE/pace-setup/scripts/setup.py OPTIONS' /dev/null`.
Keep `tty: true` for that wrapper. A tool PTY alone can lack a controlling
terminal even though an ordinary user login terminal works directly.
Keep that exec session alive at the ready compute prompt; do not send Ctrl+D or
terminate it when reporting completion. Subsequent agent commands can use the
same session. Inspect `request.json`, `allocation.json` and `setup.log` for
progress instead of launching duplicate allocations. A user can also run the
helper directly in their ICE login terminal to get their own prepared compute
shell. No batch keeper job is created.

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
- A failed setup closes its compute step; `salloc` releases its newly created
  allocation when its command ends. A reused user allocation remains owned by
  its original shell. Do not cancel other jobs or retry indefinitely. Report
  the job ID/time limit and allocation lifetime clearly.

## Environment and subsequent work

Success writes `settings.json`, a guarded `env.sh`, and `session.bashrc` under the printed setup
directory in `scripts/results/pace-setup/`. These contain the **actual** settings.
Never call setup finished unless `settings.json` says `ready` and the GPU and
memory checks passed. The memory probe verifies its own 32 MiB allocation; it
does not prove future KV-buffer placement.

The first compute shell already has the exports loaded; run experiments there.
For another terminal, always print the **complete command with actual job ID,
allocated CPU count, and absolute rc-file path**, also saved as
`second_terminal_command` in `settings.json`. Its form is:

```bash
srun --jobid=JOB_ID --overlap -N1 -n1 -cALLOCATED_CPUS --cpu-bind=cores \
  --immediate=30 --pty bash --rcfile /ABSOLUTE/SETUP/session.bashrc -i
```

Run that from another ICE **login** terminal. It enters the same compute node
and automatically loads modules, venv and identical exports; no manual CPU
discovery or export is needed. Exiting this second shell only ends its step.
Use the full allocated CPU count for entry, then let experiment launchers bind
to their disjoint subsets. `--overlap` allows the shell steps to coexist; it
does not authorize overlapping benchmarks on the same experiment resources.
The environment guard rejects another host/job, insufficient CPU affinity or
changed topology. Do not source `env.sh` on a login node. For noninteractive
agent calls outside the original PTY, use the printed `srun` prefix and source
the same environment in that compute step.

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
Include the exact **second-terminal command** every time setup succeeds, and
state that the first compute prompt already has these exports loaded.
Say whether a server/experiment was started (default: neither). If setup is
pending or failed, say so and do not invent CPU IDs or present defaults as
verified values.
