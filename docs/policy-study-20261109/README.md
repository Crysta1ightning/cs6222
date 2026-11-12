# Completed policy hierarchy: baseline and 4× input/context

All **15 combinations are complete at both scales**: 180 validated measured trials
from November 9–10, 2026, audited November 12. Each configuration has three
waiting-tool trials and three STREAM-tool trials. Both studies used the same
Blackwell allocation and frozen source revision. No failed full-study attempts
or retries were needed in this allocation. Two strict pilots and 30 per-configuration
warmups are excluded from the 180 trials.

The larger workload makes a useful difference visible: skipping backups saves
copying but can lose reusable cache and increase model-request latency. There is
no consistent overall replay-time improvement from the tested controllers.

## Results and interpretation

For STREAM tools, the scaled write-through skip configuration reduces GPU-to-host
copy volume by **37.8%**, but raises median request latency by **34.1%**
(377 to 505 ms), with input reuse falling from **65.9% to 47.5%**.
Selective write-through skip cuts copying by **68.0%**, raises request latency
by **32.8%** (375 to 498 ms), and reduces reuse from **71.5% to 43.0%**.
Less copying here comes with more recomputation. These effects are larger than
in the small workload; copying less is insufficient evidence of a faster system.

The same comparisons show only small overall changes. Scaled selective
write-through skip increases replay time from 88.55 to 89.50 seconds, while
write-through skip remains near 88.9 seconds. A long prescribed tool chain
(about 80 seconds) dominates these replays. Total time also includes roughly
5.3–5.5 seconds of counter settling; inspect `whole.execution_seconds` to remove
that accounting wait. For example, baseline write-through skip has a slightly
lower total time but almost identical execution time. Tiny differences in total
time should not decide the policy.

STREAM tools execute memory work until a fixed trace deadline. If interference
slows a worker, it can finish fewer blocks in that window. Overall replay time
therefore cannot establish how fast a fixed amount of original tool work would
finish. The exported CSVs and charts include completed blocks and blocks divided
by summed actual worker seconds, alongside per-tool Triad GB/s. Whole-block
overrun is included in the denominator. Triad rates describe individual tool
kernels, not aggregate socket bandwidth. At 4×, write-through defer/victim
completes 502 median blocks versus normal's 536, and its normalized work rate is
6.4% lower. Selective through-victim raises the reported Triad rate by about
10.3%, while completed work per worker-second rises only 0.5%. The kernel-rate
number alone overstates evidence for improved useful progress.

Deferring work does not guarantee that it remains deferred. Pressure and age
safeguards force some copying during tools. Write-through victim and
through-victim variants each select partial reclamation three times per STREAM
trial at both scales; defer/all selects none. They still copy the same total
volume as normal and show no consistent execution-time benefit. Selective
write-through has zero partial selections even at the larger scale: this
workload does not exercise that branch of the strategy. At 4×, all defer variants record zero deferred decisions while pressure overrides
remain nonzero: their safety constraints leave little opportunity to postpone
work. Increasing workload size has also changed which safeguards dominate. The
[activation tables](COMPARISONS.md) distinguish a configured option from an
option that actually acted.

Write-back has no optional eager-copy queue in this implementation. Its five
controller variants have identical transfer volumes and reuse within each
scale, yet measured times and request latencies vary. These negative controls
show why small timing changes should be treated cautiously. Three trials and
min–max whiskers do not establish statistical significance. Configuration order
was shuffled with seed 6222; delay then STREAM order within each configuration
was fixed, and configurations ran at different times on a shared node.

Scaling is not a clean DMA-only experiment. Four times the input/context also
changes prefill and admission pressure while host cache remains fixed at 4 GB.
Normal write-through D2H volume grows 6.24× and write-back 7.64×. Extra cache
pressure/churn plausibly contributes, so these data cannot attribute a change
solely to physical copy/CPU overlap. Counters record logical transfers, not a
physical DMA-overlap timeline.

## Tables and figures

- [All 15 combinations, completion counts, relative changes and activation](COMPARISONS.md).
- Baseline: [absolute table](baseline/TABLES.md), [CSV with trial ranges](baseline/summary.csv).
- 4×: [absolute table](token4x/TABLES.md), [CSV with trial ranges](token4x/summary.csv).
- [4× write-through latency](token4x/plots/write_through/latency.svg),
  [copy volume](token4x/plots/write_through/backups.svg),
  [execution time](token4x/plots/write_through/execution.svg),
  [completed STREAM work rate](token4x/plots/write_through/stream-progress.svg).
- [4× selective latency](token4x/plots/write_through_selective/latency.svg)
  and [reuse](token4x/plots/write_through_selective/reuse.svg).

Bars are medians of three trials and whiskers are the observed minimum and
maximum. CSVs contain both tool conditions and all controller/queue counters.
Each profile's plots directory contains the complete set of SVG charts.

## Exact experimental settings

| Setting | Baseline | 4× input/context |
|---|---|---|
| Input/prefix scale | 0.08 | 0.32 |
| Output scale / cap | 0.08 / 32 tokens | unchanged |
| Planned input tokens | 36,217 | 144,866 |
| Planned output tokens | 328 | 328 |
| Largest planned context | 3,096 | 12,288 |
| Context / GPU token pool | 4,096 | 16,384 |
| Reserve tokens | 2,048 | 8,192 |
| Configured pending threshold | 4,096 | 16,384 |
| Effective controller flush threshold | 2,048 | 8,192 |
| Pending age bound | 2 seconds | 2 seconds |
| Host cache CLI capacity | 4 GB | 4 GB |

Pending bounds trigger flushing; an atomic operation may transiently exceed a
threshold. Actual peaks, rather than an assertion of a hard cap, are reported.
Each profile uses the same four sessions selected from 27 eligible sessions,
first four rounds per session, 12 tools and 20 model requests including four
reuse tails. Minimum individual tool duration is 2 seconds; maximum intrinsic
within-round tool overlap is one. Tool times are unscaled and uncapped
(2.418–72.709 seconds); human waits are capped at 5 seconds. The seed is 6222.
These are synthetic-token replays of a filtered timing diagnostic, rather than
execution of the original TraceLab tools or representative full-dataset results.

TraceLab v0.0.1 source SHA256 is
`9d265eae69a31cae203848bea936f018148eed7ca8bf56050c5abe96da0b4e6b`.
The [baseline plan](baseline/plan.json) and [scaled plan](token4x/plan.json)
include transforms, selected sessions, schedules and dataset attribution
(TraceLab, Kan Zhu et al., SyFI Lab, University of Washington, CC BY 4.0).

Model and tokenizer: `Qwen/Qwen3-0.6B`, both pinned to revision
`c1899de289a04d12100db370d81485cdf75e47ca`. Triton attention, PyTorch sampling,
seed 6222, CUDA graphs disabled, static GPU memory fraction 0.30.
Prepared STREAM runtime: four persistent workers, three disjoint physical cores
per worker, 1.5 GiB arrays per worker, `STREAM_ARRAY_SIZE=67108864`, `NTIMES=2`.
[Compiler, flags and binary hashes](stream-build.txt) record GCC 12.3.0, `-O3 -fopenmp`.

Allocation 6912917: RTX PRO 6000 Blackwell Server Edition, actual CUDA device
**MIG 2g.48gb**, 16 physical cores and 64 GB host RAM, requested for 12 hours.
CPU: Intel Xeon 6740P, two sockets, 48 cores per socket, one thread per core.
Server cores: `52,53,66,67`; worker cores:
`68,69,70,71,72,73,74,75,78,79,82,83`. CPU and host memory were bound to GPU-local
NUMA node 1; the setup's 32 MiB locality probe was 100% N1. Redundant SGLang
NUMA auto-binding was disabled to avoid its MIG/NVML lookup failure; external
CPU/memory binding remained active. The physical GPU has 97,887 MiB, but this
study used the 48 GB MIG slice, not the entire card.

[Allocation settings](allocation-settings.json), [hardware](compute-hardware.json),
[baseline run specification](baseline/run-spec.json),
[scaled run specification](token4x/run-spec.json), and original
[baseline commands](baseline/commands.json)/[scaled commands](token4x/commands.json)
record all measured settings and source/binary identities. Environment modules:
Python 3.12.5 and CUDA 13.0.1; [package versions](python-packages.txt) are reference
metadata, not a portable installation lockfile. No NVIDIA driver version was
captured before allocation expiry.

## Validation, identity and provenance

Measured scripts commit: `120832cc1436f2ff2d6965ee2fb19644600078cd`.
Measured SGLang commit: `0c6af068abe77421063318e725283279d4778aa3`, based on
v0.5.21; the reproduction manual installs the committed patch. Sources stayed
frozen through both studies. Analysis commit `bd98d61` subsequently adds completed
STREAM-work metrics from saved counters without changing measured execution.

All accepted trials pass shape, placement, source compatibility, model revision,
binary identity, timing fidelity, transfer counter availability, final drain
and prepared-worker cleanup checks. [Provenance](provenance.json) lists the
60 raw result files and hashes, all 30 attempts and their status, plan/summary
hashes and measured source revisions. Each raw file contains three measured
trials. [Baseline validation](baseline/validation.json) and
[scaled validation](token4x/validation.json) enumerate all 180 accepted trials.
Raw files, server/controller logs and source snapshots remain under
`scripts/results/pace-6912917-rtx_pro_6000_blackwell-numa1-t12/` on the PACE host;
they are excluded from Git. Hashes identify them but cannot substitute for
access to the files when repeating a raw audit.

All 16 trace-round prompt hashes agree across configurations, conditions and
trials in each profile, and all 20 request token lengths agree. However, one of
the four generated-history reuse-tail prompt positions varies per profile.
Output hashes vary at six positions in baseline and four at 4×, including
normal/write-back controls. We therefore do not claim bit-identical answers or
identical content for every reuse tail. These differences alone establish
neither cache corruption nor cache correctness. The identity audits list the
positions: [baseline](baseline/token-identity-audit.json),
[4×](token4x/token-identity-audit.json).

## Experiment history and recovery

| Allocation / date | Outcome | Treatment |
|---|---|---|
| 6824964 / Nov 5 | 13 configurations, 78 accepted trials; an initial timing failure was rerun | [Historical report](../policy-study/README.md); kept separate |
| 6907229 / Nov 9 | Bounded queue wait expired, no GPU measurements | Excluded |
| 6907425 / Nov 9 | 12 baseline configurations, 72 accepted trials; process exited 137 before completion, no scaled study | Preserved raw, excluded from this cohort |
| 6912917 / Nov 9–10 | Both 15-configuration studies, 180 accepted trials, no failed full-study attempts | Complete report here |

Earlier pilot failures exposed the need for explicit backend selection,
external NUMA binding on MIG, shorter STREAM blocks (`NTIMES=2`) and avoiding
redundant tool-start status handshakes. Their fixes are part of the frozen
measured commit. The earlier allocation's pilot sequence was: initial backend
pilot; Triton/MIG auto-NUMA failure; externally bound pilot with blocks too long;
two-iteration pilot with excess start lag; verified-client pilot passing timing.
The final allocation's baseline and scaled strict pilots both passed first try.

The final run used persistent tmux plus per-configuration checkpoints. Both
studies finished at **04:20:46 Eastern on November 10**, over five hours before
the allocation expired at 09:24. The later Slurm TIMEOUT describes the idle
allocation shell, not an interrupted experiment. No additional GPU is needed
for this report or drawing its figures. Further scale alone is unlikely to
resolve the long fixed-window timing limitation; a future fixed-work tool study
would answer a different, useful completion-time question.

For a new session, start with this report, then the machine checkpoints and
per-profile `STUDY_LOG.md` in the raw root. Keep interrupted attempts and never
pool incompatible allocations. The [reproduction manual](../../trace-replay/REPRODUCE.md)
provides allocation, setup, resume, audit, CSV redraw and hierarchy diagram
commands. The 180-trial cohort is complete; there are no remaining policy cells.
