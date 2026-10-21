"""Compare saved experiment settings; produces four PNGs, HTML, and summary CSV.

Each JSON is a separate setting. No experiments or network requests are run.
Uses Python's standard library and rsvg-convert for PNG rendering.
"""

import argparse
import csv
import html
import json
import math
import shutil
import statistics
import subprocess
import textwrap
from pathlib import Path


COLORS = ("#2563eb", "#e27602", "#059669", "#9333ea", "#dc2626", "#0891b2")
KERNELS = ("Copy", "Scale", "Add", "Triad")
NAMES = {
    ("memory-only", "stream"): "STREAM only",
    ("no-hicache", "stream"): "STREAM + inference (HiCache off)",
    ("hicache", "stream"): "STREAM + inference + HiCache (write-through)",
    ("no-hicache", "inference-only"): "Inference only + no-hicache",
    ("hicache", "inference-only"): "Inference only + hicache",
}
POLICY_NAMES = {
    "write_through": "write-through",
    "write_through_selective": "selective write-through",
    "write_back": "write-back",
}


def write_policy(data):
    info = data.get("server_info", {})
    return info.get("server_args", info).get("hicache_write_policy", data.get("write_policy"))


def setting_order(run):
    """Put directory-discovered baselines first, followed by cache policies."""
    mode = run["data"]["mode"]
    mode_order = {"memory-only": 0, "no-hicache": 1, "hicache": 2}
    policy_order = {"write_through": 0, "write_through_selective": 1, "write_back": 2}
    controller_order = {"normal": 0, "defer": 1, "skip": 2}
    return (mode_order[mode], policy_order.get(write_policy(run["data"]), 3),
            controller_order.get(run["data"].get("controller_mode", "normal"), 3), str(run["path"]))


def rate(trial, field):
    value = trial.get(field)
    if value is None:
        return None
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"Invalid {field}: {value!r}")
    return value


def values(run, metric):
    data = run["data"]
    window = run.get("window", "tool")
    stream = data.get("workload", "stream") == "stream"
    output = []
    for trial in data["trials"]:
        row = trial if window == "tool" else trial.get("windows", {}).get(window)
        if row is None:
            output.append(None)
            continue
        if metric in KERNELS:
            value = trial.get("kernels", {}).get(metric, {}).get("average_GB_per_second") if stream and window == "tool" else None
        elif metric == "inference":
            value = rate(row, "requests_per_second") if data["mode"] != "memory-only" else None
        elif metric == "backup":
            value = rate(row, "backup_payload_GB_per_second")
        else:
            delta = row.get("transfer_counter_deltas", {}).get("sglang:load_back_bytes_total")
            duration = rate(row, "process_seconds")
            value = float(delta) / duration / 1e9 if delta is not None and duration else None
        if value is not None:
            value = float(value)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{run['path']}: invalid {metric} value {value!r}")
        output.append(value)
    return output


def stats(samples):
    samples = [value for value in samples if value is not None]
    return (statistics.median(samples), min(samples), max(samples), len(samples)) if samples else (None, None, None, 0)


def change(value, baseline):
    return 100 * (value / baseline - 1) if value is not None and baseline not in (None, 0) else None


def render_metric(runs, samples, metric, ylabel, view):
    """One metric per figure, with axis labels and no title or captions."""
    esc = lambda value: html.escape(str(value), quote=True)
    width = max(900, 250 * len(runs) + 150)
    height = 490
    left, right, top, bottom = 95, width - 30, 30, 405
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
           '<rect width="100%" height="100%" fill="white"/>',
           '<g font-family="Arial, sans-serif" fill="#172033">']

    def text(x, y, value, size=15, anchor="start", color="#172033"):
        svg.append(f'<text x="{x:.2f}" y="{y:.2f}" font-size="{size}" text-anchor="{anchor}" fill="{color}">{esc(value)}</text>')

    svg.append(f'<text transform="translate(25 {(top + bottom) / 2}) rotate(-90)" font-size="17" text-anchor="middle">{esc(ylabel)}</text>')
    all_values = [v for sample in samples for v in sample[metric] if v is not None]
    maximum = max(all_values or [0])
    step = 10 ** math.floor(math.log10(maximum)) if maximum > 0 else 0.2
    upper = math.ceil(maximum * 1.16 / step) * step if maximum > 0 else 1

    def ycoord(value):
        return bottom - value / upper * (bottom - top)

    for tick in range(6):
        value = upper * tick / 5
        y = ycoord(value)
        svg.append(f'<line x1="{left}" x2="{right}" y1="{y:.2f}" y2="{y:.2f}" stroke="#e2e8f0"/>')
        text(left - 12, y + 5, f"{value:.2f}", 14, "end", "#64748b")
    svg.append(f'<line x1="{left}" x2="{right}" y1="{bottom}" y2="{bottom}" stroke="#64748b"/>')
    max_trials = max(len(sample[metric]) for sample in samples)
    for index, run in enumerate(runs):
        series = samples[index][metric]
        median, low, high, count = stats(series)
        color = run["color"]
        if view == "summary":
            x = left + (index + 0.5) * (right - left) / len(runs)
            if count:
                bar_width = min(125, (right - left) / len(runs) * 0.5)
                svg.append(f'<rect class="comparison-bar" data-setting="{index + 1}" data-metric="{esc(metric)}" x="{x - bar_width / 2:.2f}" y="{ycoord(median):.2f}" width="{bar_width:.2f}" height="{bottom - ycoord(median):.2f}" fill="{color}" fill-opacity="0.65"><title>{esc(run["label"])}; median: {median:.4f}; trials: {count}</title></rect>')
                svg.append(f'<line class="range-whisker" x1="{x:.2f}" x2="{x:.2f}" y1="{ycoord(low):.2f}" y2="{ycoord(high):.2f}" stroke="#0f172a" stroke-width="2"/>')
                for value in (low, high):
                    svg.append(f'<line x1="{x - 9:.2f}" x2="{x + 9:.2f}" y1="{ycoord(value):.2f}" y2="{ycoord(value):.2f}" stroke="#0f172a"/>')
                svg.append(f'<line x1="{x - 21:.2f}" x2="{x + 21:.2f}" y1="{ycoord(median):.2f}" y2="{ycoord(median):.2f}" stroke="#0f172a" stroke-width="3"/>')
                text(x, ycoord(high) - 12, f"{median:.2f}", 16, "middle")
            else:
                text(x, bottom - 15, "N/A", 16, "middle", "#64748b")
            for line, label in enumerate(textwrap.wrap(run["label"], width=25)):
                text(x, bottom + 32 + line * 19, label, 15, "middle")
        elif count:
            points = []
            for trial_index, value in enumerate(series):
                if value is None:
                    continue
                x = left + (trial_index + 0.5) / max_trials * (right - left)
                points.append(f"{x:.2f},{ycoord(value):.2f}")
                svg.append(f'<circle cx="{x:.2f}" cy="{ycoord(value):.2f}" r="4" fill="{color}"><title>{esc(run["label"])}; trial position {trial_index + 1}: {value:.4f}</title></circle>')
            if all(value is not None for value in series):
                svg.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="2"/>')
    if view == "summary":
        # Explain the statistic in a compact in-plot legend, not a caption.
        text(right - 12, top + 17, "Bars: median; error bars: min-max across trials", 12, "end")
    if view == "trials":
        for trial_index in range(max_trials):
            if max_trials > 12 and trial_index not in {0, max_trials - 1} and trial_index % math.ceil(max_trials / 10):
                continue
            x = left + (trial_index + 0.5) / max_trials * (right - left)
            text(x, bottom + 25, trial_index + 1, 14, "middle")
        text((left + right) / 2, bottom + 60, "Trial", 16, "middle")
        # Directly named legend inside the plotting area, not a footer.
        for index, run in enumerate(runs):
            y = top + 17 + index * 24
            svg.append(f'<line x1="{right - 270}" x2="{right - 247}" y1="{y - 5}" y2="{y - 5}" stroke="{run["color"]}" stroke-width="3"/>')
            label = run["label"]
            if not any(v is not None for v in samples[index][metric]):
                label += " (N/A)"
            text(right - 238, y, label, 13)
    svg.append("</g></svg>")
    return "\n".join(svg)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path, help="JSON files, or directories searched for memory-*.json")
    parser.add_argument("--labels", nargs="+", help="one label per discovered/input file, in printed order")
    parser.add_argument("--baseline", type=int, default=1, help="1-based input index for percentage comparisons (default: 1)")
    parser.add_argument("--kernel", choices=KERNELS, default="Triad", help="STREAM kernel shown in the figure; CSV contains all four")
    parser.add_argument("--window", choices=("tool", "post_tool", "whole"), default="tool",
                        help="keep tool, post-tool/drain, and combined measurements separate")
    parser.add_argument("--view", choices=("summary", "trials"), default="summary", help="median bars with min/max ranges, or trial-order lines")
    parser.add_argument("--strict", action="store_true", help="reject placement/build differences instead of reporting warnings")
    parser.add_argument("--output", type=Path, default=Path("scripts/results/comparison.png"),
                        help="STREAM graph path (.png or .svg); three sibling graphs and HTML/CSV are also written")
    args = parser.parse_args()
    paths = []
    for item in args.inputs:
        found = sorted(item.rglob("memory-*.json")) if item.is_dir() else [item]
        if not found:
            parser.error(f"No memory experiment JSON files found in {item}")
        for path in found:
            if path.resolve() in [p.resolve() for p in paths]:
                parser.error(f"Duplicate input: {path}")
            paths.append(path)
    if args.labels and len(args.labels) != len(paths):
        parser.error("--labels must have exactly one label per input JSON")
    if not 1 <= args.baseline <= len(paths):
        parser.error("--baseline must identify an input JSON, starting at 1")
    suffix = args.output.suffix.lower()
    if suffix not in (".png", ".svg"):
        parser.error("--output must end in .png or .svg; HTML and CSV siblings are also written")
    renderer = shutil.which("rsvg-convert") if suffix == ".png" else None
    if suffix == ".png" and not renderer:
        parser.error("PNG rendering requires rsvg-convert in PATH. It is available in the current PACE environment; SVG output is also supported.")
    runs, warnings = [], []
    try:
        for index, path in enumerate(paths):
            data = json.loads(path.read_text())
            if data.get("error") or not data.get("trials"):
                raise ValueError(f"{path}: failed or empty run ({data.get('error')})")
            key = data["mode"], data.get("workload", "stream")
            if key not in NAMES:
                raise ValueError(f"{path}: unsupported condition {key}")
            name = args.labels[index] if args.labels else NAMES[key]
            if not args.labels and key == ("hicache", "stream"):
                policy = write_policy(data)
                if policy:
                    name = "STREAM + inference + HiCache (" + POLICY_NAMES.get(policy, policy.replace("_", "-")) + ")"
            if not args.labels and data.get("controller_enabled"):
                policy_name = POLICY_NAMES.get(write_policy(data), data.get("write_policy", "write-through"))
                prefix = "STREAM + inference + HiCache" if key[1] == "stream" else "Inference + HiCache"
                name = f"{prefix} ({policy_name}; mode: {data['controller_mode']})"
            runs.append({"path": path, "data": data, "label": name,
                         "color": COLORS[index % len(COLORS)], "window": args.window})
            for trial in data["trials"]:
                for warning in trial.get("warnings", []):
                    warnings.append(f"{name}, recorded trial {trial.get('trial', '?')}: {warning}")
        if not args.labels and all(item.is_dir() for item in args.inputs):
            runs.sort(key=setting_order)
            for index, run in enumerate(runs):
                run["color"] = COLORS[index % len(COLORS)]
        # Keep separate files separate, even when their modes are identical.
        if not args.labels:
            original = [run["label"] for run in runs]
            for index, run in enumerate(runs):
                if original.count(original[index]) > 1:
                    run["label"] += f" / file {index + 1}"
        checks = {
            "hostname": lambda d: d.get("hostname"),
            "STREAM thread count": lambda d: d.get("threads"),
            "CPU affinity": lambda d: d.get("cpu_affinity"),
            "NUMA policy": lambda d: d.get("numa", {}).get("stdout"),
            "STREAM binary": lambda d: d.get("binary_sha256"),
            "GPU hardware": lambda d: d.get("gpu", {}).get("stdout"),
            "OpenMP settings": lambda d: d.get("omp"),
            "STREAM working set": lambda d: sorted({t.get("working_set_bytes") for t in d["trials"] if "kernels" in t}),
        }
        mismatches = [name for name, getter in checks.items()
                      if len({json.dumps(getter(run["data"]), sort_keys=True) for run in runs}) > 1]
        if mismatches:
            message = "Settings differ in " + ", ".join(mismatches) + "; percentages are descriptive, not controlled causal effects."
            if args.strict:
                raise ValueError(message)
            warnings.append(message)
        if len({run["data"].get("job_id") for run in runs}) > 1:
            warnings.append("Runs span Slurm allocations; shared-node activity and timing may differ.")
        # Precompute every metric before writing anything, catching invalid data.
        samples = [{metric: values(run, metric) for metric in (*KERNELS, "inference", "backup", "restore")} for run in runs]
    except (OSError, KeyError, TypeError, ValueError) as error:
        parser.error(str(error))

    baseline = args.baseline - 1
    rows = []
    for index, run in enumerate(runs):
        for metric in (*KERNELS, "inference", "backup", "restore"):
            median, low, high, count = stats(samples[index][metric])
            base = stats(samples[baseline][metric])[0]
            rows.append({"setting": run["label"], "file": str(run["path"]), "metric": metric,
                         "window": args.window,
                         "unit": "requests/s" if metric == "inference" else "GB/s",
                         "n": count, "median": median, "min": low, "max": high,
                         "change_vs_baseline_percent": change(median, base)})

    esc = lambda value: html.escape(str(value), quote=True)
    panels = [(args.kernel, f"{args.kernel} bandwidth (GB/s)", args.output),
              ("inference", "Inference throughput (requests/s)", args.output.with_name(args.output.stem + "-inference" + suffix)),
              ("backup", "GPU to host backup rate (GB/s)", args.output.with_name(args.output.stem + "-backup" + suffix)),
              ("restore", "Host to GPU restore rate (GB/s)", args.output.with_name(args.output.stem + "-restore" + suffix))]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for metric, ylabel, destination in panels:
        figure = render_metric(runs, samples, metric, ylabel, args.view)
        if suffix == ".png":
            result = subprocess.run([renderer, "--format=png", "--zoom=2", "--output", str(destination)],
                                    input=figure.encode(), capture_output=True)
            if result.returncode:
                parser.error(f"PNG rendering failed: {result.stderr.decode(errors='replace')}")
        else:
            destination.write_text(figure + "\n")
    figures_html = "".join(
        f'<a class="plot" href="{esc(destination.name)}"><img src="{esc(destination.name)}" alt="{esc(ylabel)}"></a>'
        for _, ylabel, destination in panels
    )
    csv_path = args.output.with_suffix(".csv")
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    header = "<tr>" + "".join(f"<th>{esc(name)}</th>" for name in ("Setting", "Metric", "Unit", "Trials", "Median", "Min", "Max", "Change vs baseline")) + "</tr>"
    table = [header]
    for row in rows:
        formatted = [row["setting"], row["metric"], row["unit"], row["n"],
                     *[f"{row[key]:.3f}" if row[key] is not None else "N/A" for key in ("median", "min", "max")],
                     f"{row['change_vs_baseline_percent']:+.2f}%" if row["change_vs_baseline_percent"] is not None else "N/A"]
        table.append("<tr>" + "".join(f"<td>{esc(value)}</td>" for value in formatted) + "</tr>")
    metadata = []
    for index, run in enumerate(runs):
        d = run["data"]
        meta = {"file": str(run["path"]), "hostname": d.get("hostname"), "job_id": d.get("job_id"),
                "controller_mode": d.get("controller_mode"), "controller_enabled": d.get("controller_enabled", False),
                "measurement_window": args.window,
                "threads": d.get("threads"), "cpu_affinity": d.get("cpu_affinity"),
                "numa": d.get("numa", {}).get("stdout"), "binary_sha256": d.get("binary_sha256")}
        metadata.append(f"<details><summary>{esc(run['label'])}</summary><pre>{esc(json.dumps(meta, indent=2))}</pre></details>")
    warning_html = "".join(f"<li>{esc(message)}</li>" for message in warnings)
    report = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Memory experiment comparison</title><style>body{{font:15px Arial,sans-serif;margin:24px;color:#172033}}.plot{{display:block;max-width:1000px;margin:0 auto 30px}}.plot img{{display:block;width:100%;height:auto}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ddd;padding:8px;text-align:left}}th{{background:#eef2ff}}pre{{white-space:pre-wrap}}li{{margin:8px 0}}.warnings{{color:#92400e}}a{{color:#2563eb}}</style>
{figures_html}
<details><summary>Comparison table and input details</summary>
<p>Bars show medians and whiskers show the observed minimum–maximum (not confidence intervals).
Percentage change = 100 × (setting median / baseline median − 1). Baseline: {esc(runs[baseline]['label'])}.
Transfer rates include idle time. STREAM reports logical bytes. Missing values are N/A.</p>
<ul class="warnings">{warning_html}</ul><p><a href="{esc(csv_path.name)}">Download summary CSV</a></p>
<table>{''.join(table)}</table>{''.join(metadata)}
<p>These checks do not verify equal inference settings, request rates, host-cache placement, or other users' activity.
Trial-order curves do not imply simultaneous or paired runs. Missing metrics and zero baselines have no percentage comparison.</p></details></html>'''
    args.output.with_suffix(".html").write_text(report)
    for index, run in enumerate(runs):
        print(f"Setting {index + 1}: {run['label']} ({len(run['data']['trials'])} trials): {run['path']}")
    for message in warnings:
        print("WARNING:", message)
    for path in ([panel[2] for panel in panels] + [args.output.with_suffix(".html"), csv_path]):
        print("Saved", path)


if __name__ == "__main__":
    main()
