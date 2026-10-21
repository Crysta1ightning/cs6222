# Plot saved memory experiments

## Compare separate settings and their individual trials

Use `compare.py` for the three-setting comparison. Each input JSON remains a
separate setting, even when mode names are identical. It does not run inference
or STREAM. PNG output uses the `rsvg-convert` executable available on PACE;
no additional Python packages are required. PNGs are rendered at twice the
logical resolution for sharper text. Explicit `.svg` output is still supported.

From `~/cs6222`, discover the result JSON files under one experiment directory:

```bash
python scripts/memory-experiment/compare.py \
  scripts/results/l40s-numa2-t12 \
  --baseline 2 \
  --output scripts/results/l40s-numa2-t12/comparison.png

python scripts/memory-experiment/compare.py \
  scripts/results/l40s-numa2-t12 \
  --baseline 2 --view trials \
  --output scripts/results/l40s-numa2-t12/trials.png
```

Directory inputs discover `memory-*.json` recursively. Without custom labels,
directory-only inputs are ordered as STREAM-only, inference with HiCache off,
then write-through, selective write-through, and write-back. The CLI prints
the numbered input order. Explicit file inputs retain the order supplied.
Use exact paths instead of a directory to exclude older runs or set a different
order:

```bash
python scripts/memory-experiment/compare.py \
  path/to/memory-only.json path/to/off.json path/to/on.json \
  --labels "STREAM only" "HiCache off" "HiCache on" \
  --baseline 2 --kernel Triad --output scripts/results/comparison.png
```

Replace the example paths with actual paths. `--baseline` uses a 1-based index;
choose 1 for STREAM-only as the reference or 2 for inference with HiCache off.
Four separate PNG graphs show the selected STREAM kernel (default Triad), inference
requests/s, backup GB/s, and restore GB/s. `--kernel Copy`, `Scale`, or `Add`
changes the STREAM graph. Restore rate is calculated from its byte-counter
delta divided by the recorded measurement duration.

Outputs share the requested stem:

- `.png`: the standalone STREAM graph.
- `-inference.png`, `-backup.png`, `-restore.png`: one standalone graph per metric.
- `.html`: the four separate graphs, with comparison details in a collapsible section.
- `.csv`: medians, minimum/maximum, sample counts, and relative changes for all
  four STREAM kernels, inference, backups, and restores.

The default figure is a bar chart: one colored bar per setting, with bar height
equal to the median across its trials, with an observed minimum–maximum
whisker and a black median marker. Individual trial dots are omitted.
All bar axes start at zero. A measured zero has a zero-height bar; unavailable
metrics are labeled N/A and have no bar. This compares settings, whereas a
histogram would show frequencies within numerical bins and would be sparse
with only five trials per setting. Ranges are not confidence intervals. The
`--view trials` figure shows the sequence of saved trials for each setting;
trial 1 in different files is not assumed to represent the same wall-clock time
or a matched pair. These are per-trial summaries, not per-iteration traces.
An experiment still running may have only some trials saved; rerun the plotter
when it finishes. Counts are shown rather than padding or inventing trials.

Graphs use the names `STREAM only`, `STREAM + inference (HiCache off)`, and
`STREAM + inference + HiCache (write-through)` directly on the categorical axis.
If a saved HiCache server specifies another write policy, its label uses that policy.
The additional policy names are `STREAM + inference + HiCache (selective write-through)`
and `STREAM + inference + HiCache (write-back)`.
The compact in-plot legend reads `Bars: median; error bars: min-max across trials`.
The black whiskers show the observed minimum and maximum, not standard deviation
or a confidence interval. Graphs have no top title
or bottom explanatory captions; metric names and units appear on the y-axis.
The trial-order view retains an in-plot legend with these names. Trial counts,
percentage comparisons, configuration details, and interpretation notes remain
available in the HTML/CSV outputs rather than being printed on the graphs.

Percentage change is `100 * (setting median / baseline median - 1)`. Missing
metrics and zero baselines yield N/A. STREAM-only inference is N/A rather than
zero performance. Transfer rates include idle time and are not active-copy
bandwidth. Higher backup throughput does not necessarily mean better application
performance.

Configuration differences are displayed as warnings so you can visualize
different thread counts or placements deliberately. Pass `--strict` to reject
node, thread, affinity, NUMA, GPU, OpenMP, working-set, or binary differences.
Failed/empty results and negative or non-finite rates are rejected. Warnings
embedded in each trial are retained. Sequential trials in one allocation do
not establish independent replications or statistical significance.

## Original grouped bar plot

For activity-controller runs, use `compare.py --window tool`, then
`--window post_tool` and `--window whole`, with different output stems.
Normal, defer, and skip are separately named and never pooled into one bar.
Only the tool window has a STREAM kernel bandwidth measurement; other windows
show N/A for that metric. The full launch/compare commands are in
[STREAM_CONTROLLER.md](STREAM_CONTROLLER.md). The legacy plot entry point
delegates controlled results to this renderer to avoid merging modes.

From `~/cs6222`, plot one result without rerunning any workload:

```bash
python scripts/memory-experiment/plot.py \
  scripts/results/memory-only/memory-memory-only-1792436093948966183.json
```

The SVG is saved beside the JSON. Open it in a browser or image viewer.
No additional Python packages are required.

To compare runs, pass the exact files you want to include:

```bash
python scripts/memory-experiment/plot.py \
  scripts/results/memory-only/<baseline>.json \
  scripts/results/hicache/<concurrent>.json \
  scripts/results/hicache/<inference-alone>.json \
  --output scripts/results/comparison.svg
```

Replace the placeholder paths with actual filenames. Multiple files in the
same condition are combined. Panels show Copy/Scale/Add/Triad bandwidth,
inference requests/s, and GPU-to-host backup payload GB/s when applicable.
STREAM-only data has no inference panels. Bars are medians; dots are individual
trial averages, not iteration samples or a timeline. One trial does not provide
a between-trial variability estimate. Logical STREAM bandwidth and backup
payload rate are not hardware DRAM bandwidth or active copy-engine bandwidth.

Inputs must match node, thread count, STREAM binary hash, CPU affinity, and
recorded NUMA policy. Different allocation IDs produce a warning. These checks
do not establish matching inference configuration, host-cache placement, load,
or shared-node conditions; confirm those before interpreting differences.

The recorded baseline used CPUs 16–23 and memory binding to NUMA node 1 on
`atl1-1-02-009-31-0`, with a Tesla V100-PCIE-16GB local to NUMA node 1.
It should not be directly compared with earlier L40S-node measurements.
