# Trace replay validation

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
