# HiCache policy experiments

Compare all **15** meaningful combinations of three base write policies and
normal, skip, defer/all, defer/victim, or defer/through-victim control.

- [Reproduction manual](trace-replay/REPRODUCE.md): allocate resources, install
  the controller patch, prepare workloads, run/resume, audit and draw figures.
- [Policy definitions and measurement limits](trace-replay/POLICY_MATRIX.md).
- [Completed baseline and 4× study](docs/policy-study-20261109/README.md): all
  15 combinations at each scale, 180 validated trials, settings and interpretation.
- [Historical November 5 results](docs/policy-study/README.md): 13 of 15 complete
  configurations, 78 validated trials, and the exact remaining work.
- [Trace replay details](trace-replay/README.md).
- [Standalone memory experiments](memory-experiment/README.md).

Results directories, downloaded traces, compiled workers and local environments
are excluded from Git. The report under `docs/` contains a compact validated
summary and figure examples. A queued or interrupted run is never a completed
experiment.
