# STREAM controller validation — October 21, 2026

No GPU allocation was active when checked. No GPU allocation, real inference
server, or measured STREAM workload was launched during implementation.

Passed:

- 29 new CPU-only tests: event handling, overlapping IDs, duplicate events,
  leases, malformed messages, lost START acknowledgments and heartbeats,
  subprocess timeout/failure cleanup, queue/age/pressure decisions, accounting,
  legacy runner defaults, and controller/window separation.
- These include seven cache-hook tests executing the real SGLang cache methods
  with CPU tensors and a mocked CUDA transfer engine: skip before allocation or
  metadata changes, defer through existing queue/locks, merged normal writes,
  large-node bounds, allocation reclamation, and immediate required retraction.
- Four existing upstream CPU tests selected with
  `TestUnifiedTreeNodeGetPrefixHashValues or TestUnifiedTreeCoreLoadBackPending`.
- Separate source-method check: cache reset submits and acknowledges queued
  work before clearing metadata.
- A synthetic three-mode whole-window plotting check produced four PNGs,
  preserved separate normal/defer/skip labels, and retained missing values as N/A.
- Python compilation, shell syntax checks, and Git whitespace checks.
- Patch applies to pristine files from the pinned base and reproduces all three
  changed SGLang files byte for byte; reverse-check succeeds.

The broad upstream cache suite was attempted on the login node, encountered
no-accelerator failures, and was stopped. It is **not** counted as passing;
the CPU subset does not rule out GPU regressions.

Still unverified on hardware:

- Real model inference with each mode.
- Actual optional D2H suppression/deferral, completed CUDA copies, and pressure
  overrides under GPU allocation stress.
- H2D restores, GPU KV contents, node splits/eviction under deferred writes,
  and sustained cache metadata/allocator invariants on a real GPU.
- Performance changes, post-tool repayment costs, and behavior under KV reuse.

Use `smoke-controller.py` for each mode and the existing `check-hicache.py`
outside an active tool window before interpreting experiment results.
The precise commands, supported scope, and measurement caveats are in
`STREAM_CONTROLLER.md`. Local `.log` test outputs are ignored by Git.
