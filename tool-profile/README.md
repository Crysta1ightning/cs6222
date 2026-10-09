# CPU tool profiling

Run on an allocated compute node, with inference stopped for the first isolated
profile. This does not require a running SGLang server. The default candidate is
the existing CPU-registered eviction-policy and sampling-parameter tests, pinned
to the recorded checkout commit. This screens a real test command; it does not
assume that these tests are memory-intensive or have useful phases.

```bash
module load python/3.12.5 cuda/13.0.1
cd ~/cs6222
source .venv/bin/activate
python -m pip install psutil pytest
python tool-profile/run.py --check-counters
python tool-profile/run.py --trials 3 --interval 0.1
```

Each trial saves a log, sampled JSON, pytest event timestamps, and an SVG graph
in `results/tool-profile-*/`. Failed test commands are labeled and stop subsequent
trials: do not interpret them as successful workload measurements. CUDA visibility
is disabled with invalid GPU ordinal `999` in the tool subprocess: SGLang's
test helpers fail on an empty `CUDA_VISIBLE_DEVICES`. The default uses pytest's
`--import-mode=importlib` and explicitly adds `sglang/python` to `PYTHONPATH` to
avoid confusing the checkout's test directory with the installed support package.
Pytest plugin autoload is disabled to reduce
environment variation; the timeline plugin is explicitly loaded. OMP/MKL threads
are limited to three. Affinity, job ID, hostname, and commit are recorded.

The first run can include cold filesystem/import effects; compare the individual
timelines rather than treating all trials as interchangeable. Startup/import,
collection, and test execution belong to the command's total duration. The
collection-end marker helps distinguish them. Instrumentation overhead has not
yet been quantified; compare instrumented and plain runs before making performance
claims. At 100 ms this is broad phase screening, not resolution of 3–14 ms backups.

Host bandwidth uses Intel IMC `cas_count_read/write` events, with encodings and
unit scales read from sysfs through Linux `perf_event_open`. Kernel permission
denials are reported; there is no sudo or permission modification. The graph
shows bandwidth as unavailable, rather than replacing it with RSS or zeros.
See https://man7.org/linux/man-pages/man2/perf_event_open.2.html.

IMC counters measure all monitored sockets/channels, including other users and
the profiler, not this process alone. They cannot independently attribute traffic
to the tool. CPU is process-tree CPU time (100% = one core); RSS is summed resident
memory, which can double-count shared pages. Short-lived subprocesses can escape
sampling. The default tests do not deliberately spawn detached tool workers.

For another command, supply it after `--` (working directory is `~/cs6222`):

```bash
python tool-profile/run.py --trials 3 -- python -m pytest -q path/to/tests
```

Custom commands get CPU/RSS/DRAM sampling but need `-p timeline_plugin` when using
pytest if test-boundary markers are desired. If hardware counters are blocked,
CPU/RSS plots alone cannot establish low memory-bandwidth periods: the next step
is to check whether PACE provides PCM/perf access or another approved counter
service. No claim about test phase scheduling is justified until traffic and
interference are measured.
