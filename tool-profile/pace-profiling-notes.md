# PACE ICE memory-bandwidth profiling

Checked October 8, 2026. The current compute-node run denied our direct Linux
IMC counter reader with `EACCES`. This does not establish that every profiling
tool is unavailable, or explain PACE's policy.

PACE's module catalog currently lists:

- `likwid/5.3.0` (load directly).
- `intel-oneapi-vtune/2025.0.0` and `2023.1.0`.
- No module matching `pcm` was found in this catalog search.

Installed software does not establish working hardware-counter access. The
LIKWID group listing returned `Unknown Intel Processor` in our login/tool
environment; check the allocated compute node separately.

Update: the user also checked compute node `atl1-1-03-004-21-0`.
LIKWID reports `INTEL(R) XEON(R) GOLD 6548Y+`, `Unknown Intel Processor`,
and `Unsupported Processor`; the MEM access check cannot initialize counters.
This is a processor-support failure, not proof that daemon permissions were denied.
Do not repeat that LIKWID check on this installation and CPU.

The remaining installed candidate is VTune. A short access check, with automatic
DRAM-limit benchmarking disabled, is:

```bash
module load intel-oneapi-vtune/2025.0.0
vtune -collect memory-access -knob dram-bandwidth-limits=false \
  -result-dir "results/vtune-access-check-$(date +%s)" -- sleep 2
```

This is a diagnostic, not a memory-intensive workload or interference result.
VTune access remains unverified. The command syntax is documented in Intel's
[VTune user guide](https://cdrdv2-public.intel.com/848603/vtune-profiler_user-guide_2025.1-766319-848603.pdf).
If this fails, include both the perf permission error and LIKWID's unsupported
CPU error in the support request below.

Update: VTune's compute-node check also failed:

```text
Cannot collect memory bandwidth data. Make sure the sampling driver is
installed and enabled on your system.
```

Its message also mentions virtualized environments as a possible limitation;
this does not establish that the ICE node is virtualized. There is no verified
working host DRAM bandwidth collector in the current setup. Ask PACE which
counter/driver/service configuration they support before further profiling-tool
installation attempts.

## Next check, inside the compute allocation

```bash
module load likwid/5.3.0
likwid-perfctr -a
PROFILE_CPU=$(python -c 'import os; print(min(os.sched_getaffinity(0)))')
likwid-perfctr -M 1 -c "$PROFILE_CPU" -g MEM -S 1s
```

The last command is a one-second counter-access check on the socket associated
with an allocated CPU, not an interference experiment. If `MEM` is unsupported,
or counter access fails, retain the output for PACE support. Do not use `--force`
or change system permissions. Socket measurements include other jobs and do not
attribute traffic to one process.

LIKWID documents memory-performance groups, timeline mode, and an
administrator-configured access-daemon route for ordinary users. That route's
availability on ICE remains unverified. See the
[official LIKWID guide](https://github.com/RRZE-HPC/likwid/wiki/likwid-perfctr).

## Support question if the check fails

PACE lists `pace-support@oit.gatech.edu` and consultation sessions on its
[User Support page](https://pace.gatech.edu/user-support/).

Draft only; no message has been sent:

> I am working on a CS6222 project on ICE, measuring interference between CPU
> tool workloads and GPU-to-host KV-cache backups. On
> atl1-1-03-004-21-0, Linux perf_event_open for uncore IMC CAS read/write events
> returns Permission denied.
> LIKWID 5.3.0 reports Unsupported Processor for the Xeon Gold 6548Y+.
> VTune 2025.0.0 reports Cannot collect memory bandwidth data and asks for an
> installed and enabled sampling driver. Is there a supported way to collect
> host DRAM
> read/write bandwidth over time (approximately 100-ms samples), such as the
> installed LIKWID access daemon, VTune, PCM, or an administrator-provided
> monitoring service? Does profiling require an exclusive allocation, and is
> that available for this course? I can provide the job ID and diagnostic output.

## Sources and limits

The [ICE overview](https://pace.gatech.edu/ice-cluster/) describes the instructional
cluster, but the public material searched did not establish an IMC-counter access
policy. Direct fetches of some PACE pages returned HTTP 502; indexed official
page text and the local module catalog were used. The catalog observations are
local evidence, not a promise about every node. CPU/RSS timelines and CUDA
transfer timings do not substitute for measured host DRAM bandwidth.
