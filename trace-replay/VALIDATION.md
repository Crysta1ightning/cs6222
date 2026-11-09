# Trace replay validation

## Prepared-runtime CPU validation — November 5, 2026

No new GPU trials were run. Twenty-three replay/worker CPU tests and six
existing persistent-worker regression tests passed. Tiny STREAM fixtures verify
buffer/process reuse, timed commands beyond the old cumulative iteration limit,
full-array validation, heartbeat renewal, in-flight cancellation, timeout cleanup,
and acknowledged activity boundaries. Inference is mocked in replay integration
tests. The diagnostic's default dry-run is tested with Torch imports disabled.

Prepared replay now waits in reusable threads for delay tools and uses warmed
persistent STREAM workers for stress tools. Worker timestamps separate actual
work from command round trips; idle OpenMP workers wait passively. Initialization
and final full-array verification are recorded outside measured trials. Runtime
and source hashes prevent combining the new protocol with historical results;
comparison requires worker validation and cleanup to finish.

An offline audit of the old STREAM-normal results found excessive duration
overruns for 81–82 of 82 tools per trial. New per-trial timing audits and the
optional strict check distinguish functional success from timing fidelity.
A SHA256-verified v0.0.1 candidate with longer-session selection has four sessions,
32 rounds and 121 tools, with 3.79-second median duration. Fifteen intervals are
still shorter than 50 ms; no hardware timing-fidelity claim is made yet. Selection
uses original tool-time scale and changes the workload distribution explicitly.

Artifacts and notes are under `scripts/results/tracelab-improvements/`.
The prepared copy-contention diagnostic has not been executed on a GPU. It is
a mechanism check, not an actual HiCache or repeated-policy result.

## GPU validation — November 4, 2026

Completed 18 measured trials in job 6818996 on an RTX Pro 6000 Blackwell
MIG 2g.48gb instance (48,512 MiB usable). Four server cores (72–75) and eight
tool cores (76–83) shared GPU-local NUMA node 1. All trials passed request/tool
counts, acknowledged event balance, lease, drain, backup-byte accounting,
token-shape and CPU/NUMA-placement checks. A live snapshot confirmed eight
STREAM worker threads on eight cores and approximately 99.9% of scheduler
resident host memory on node 1. These are functional/accounting checks, not
formal KV or output-equivalence proofs.

The existing four-session plan and Qwen3-0.6B cache settings were retained.
Experiment-local launchers used Triton attention, PyTorch sampling and server
seed 6222. MIG automatic NUMA detection was disabled because the NVML lookup
failed; explicit numactl binding remained active. Default FlashInfer startup
was blocked on NFS reads during Blackwell JIT compilation. An initial
OpenMP-induced replay affinity collapse to one tool core was discovered and
fixed by disabling OpenMP binding for the replay interpreter; STREAM children
enable their own binding after inheriting the full tool CPU set. Earlier
single-core measurements and startup pilots are retained but excluded.

Median whole times (normal/defer/skip) were 24.23/22.53/22.86 seconds with delay
tools and 121.64/122.30/119.61 seconds with STREAM. STREAM Triad medians were
107.50/107.89/108.05 GB/s. Defer retained normal's 81.9% cache-hit fraction;
skip reduced it to 69.6% while saving 14.25% of total backup payload and recording
zero backup completions during STREAM tool windows. Defer still had a median
33 pressure/age overrides per trial. The small runtime and bandwidth differences
do not establish a reliable overall gain. A later five-invocation STREAM-only
reference measured 106.06 GB/s median (103.13–110.97 range).

Artifacts, exact launchers, source diff, raw trials, journals, CSV tables, plots,
and a plain-English report are under
`scripts/results/pace-6818996-rtx_pro_6000_blackwell-numa1-t8/`.
The first H2D counter baseline was unavailable in each condition and remains
missing; H2D summaries use the two measured trials. Shared MIG/node hardware,
fixed mode order, three trials per condition, scaled synthetic tokens and
subprocess/STREAM duration overruns limit causal and production claims.

## GPU validation — November 3, 2026

Completed 18 measured trials on PACE job 6793793: three per controller mode
(`normal`, `defer`, `skip`), separately for delay and STREAM surrogates. Each
trial completed 36 inference requests and 82 tool subprocesses. All had balanced
acknowledged tool events, no expired leases, empty final active/pending/in-flight
state, equal submitted/completed backup bytes, and matching HTTP backup counters.
H2D counters were available for trials two and three in each condition; the first
baseline was missing and remained missing. Output hashes varied within modes;
token shapes matched, but bitwise KV/output equivalence is not established.

Hardware: one L40S, four physical cores; server CPUs 24–26, tool CPU 27, host
memory on NUMA 1, GPU attached to NUMA 3. A live placement probe confirmed host
binding. This remote NUMA path and one tool core limit bandwidth conclusions.
Model: Qwen3-0.6B, context 4096, GPU KV capacity 8192 tokens, 4 GB host cache,
write-through, CUDA graphs disabled. The existing scaled four-session plan was
used unchanged. Measured scripts commit: `9281751`; SGLang: `4a817e8`.

The GPU pilot exposed inherited OpenMP affinity collapsing server processes
onto one core, stale sockets after forced shutdown, and bursty control-channel
timeouts. The launcher now isolates server OpenMP placement; comparisons use
private socket namespaces and node-local journals archived at shutdown. The
client bounds queue retries, and replay records a configurable 10-second ACK
timeout below the 15-second tool lease. Eighteen activity tests and twelve trace
tests passed, including busy-queue recovery and bounded timeout tests. Failed
attempts and the pilot were excluded from the final comparison.

Results are retained locally under
`scripts/results/pace-6793793-l40s-numa1-t1/`, including `FINDINGS.md`,
`validation.json`, raw trial JSON, archived journals, CSV summaries and separate
PNG figures. Median STREAM whole times were 236.32 / 234.14 / 235.25 seconds
(normal/defer/skip); Triad rates were 14.61 / 14.71 / 14.70 GB/s. These small
differences do not establish an overall benefit. Skip reduced backup payload
but lowered prefix hits from 81.9% to 67.4%. Original task semantics, full-trace
representativeness, socket-bandwidth saturation and formal KV correctness remain
unverified. The runner stopped its servers and preserved the user's allocation.

## Original CPU validation — October 30, 2026

Validation performed locally on October 30, 2026:

- Downloaded the paper's pinned v0.0.1 JSONL release to temporary storage and
  verified SHA256 `9d265eae69a31cae203848bea936f018148eed7ca8bf56050c5abe96da0b4e6b`.
  Read all 357,161 records without extracting the dataset to disk.
- Generated `scripts/results/tracelab/plan.json` with default settings: four
  sessions (two Codex, two Claude), 32 rounds, 82 tools, maximum scaled context
  including output 3,870 tokens. Final prompt lengths sum to 12,058 tokens;
  this exceeds the default GPU token pool but does not itself prove eviction
  or host restore. The chosen subset had no per-tool duration caps activated.
- Deterministic selection found 3,097 eligible sessions. Exclusions: 747 too
  short, 281 context overflow, 57 invalid/missing fields, 80 no tools or reuse,
  and three nonconsecutive/duplicate-round cases. These counts describe the
  bounded first-eight-round selection and are not full-trace statistics.
- Twelve new CPU tests passed. These include complete mocked-inference replay
  using the actual local controller socket and real delay subprocesses, failed
  child cleanup, normalization, exact output-ID carry-forward, window accounting,
  missing metrics, and generating four PNGs/CSV from synthetic measured data.
- Sixteen existing activity tests and six existing measurement tests passed
  after generalizing the subprocess helper. Shell syntax and Python compilation
  checks passed. No SGLang source changes were needed.

The test environment's filesystem sandbox blocks UNIX socket binding; socket
tests ran outside that sandbox with automatic approval. No external services
were contacted by those tests, and model inference was mocked. The source
download accessed the public GitHub release and reference files only.

Not validated here: actual SGLang GPU inference for this replay, measured
GPU-to-host or host-to-GPU traffic, cache restore correctness, real STREAM tool
stress on a GPU allocation, NUMA placement on a compute node, or performance
benefits. No allocation was requested and no model server was launched. Use a
one-trial GPU pilot before collecting comparison results.
