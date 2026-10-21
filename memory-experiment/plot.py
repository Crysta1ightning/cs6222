"""Plot saved experiment JSON as standalone SVG, without extra packages.

Example:
  python scripts/memory-experiment/plot.py result.json --output baseline.svg
  python scripts/memory-experiment/plot.py baseline.json concurrent.json alone.json
"""

import argparse
import html
import json
import math
import statistics
import subprocess
import sys
from collections import defaultdict
from pathlib import Path


LABELS = {
    ("memory-only", "stream"): ("STREAM alone", ""),
    ("no-hicache", "stream"): ("Inference + STREAM", "HiCache disabled"),
    ("hicache", "stream"): ("Inference + STREAM", "HiCache enabled"),
    ("no-hicache", "inference-only"): ("Inference alone", "HiCache disabled"),
    ("hicache", "inference-only"): ("Inference alone", "HiCache enabled"),
}
COLORS = ["#2563eb", "#0891b2", "#d97706", "#7c3aed"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if any("controller_mode" in json.loads(path.read_text()) for path in args.results):
        # The legacy plot groups by HiCache on/off only. Delegate rather than
        # mixing controller modes or treating new-schema null metrics as zeros.
        command = [sys.executable, str(Path(__file__).with_name("compare.py")),
                   *map(str, args.results), "--output", str(args.output or Path("memory-comparison.png"))]
        subprocess.run(command, check=True)
        return
    groups = defaultdict(list)
    configurations = set()
    job_ids = set()
    first = None
    for path in args.results:
        data = json.loads(path.read_text())
        if data.get("error"):
            parser.error(f"Failed/incomplete run: {path}: {data['error']}")
        first = first or data
        key = (data["mode"], data.get("workload", "stream"))
        if key not in LABELS:
            parser.error(f"Unknown experiment condition: {key}")
        configurations.add((data["hostname"], data["threads"],
                            data["binary_sha256"], tuple(data["cpu_affinity"]),
                            data.get("numa", {}).get("stdout", "")))
        job_ids.add(str(data.get("job_id", "unknown")))
        for trial in data["trials"]:
            for warning in trial.get("warnings", []):
                print(f"WARNING: {path}, trial {trial['trial']}: {warning}")
            groups[key].append(trial)
    if len(configurations) != 1:
        parser.error("Inputs differ in node, threads, binary, CPU affinity, or NUMA policy. Plot matched runs separately.")
    if len(job_ids) > 1:
        print("WARNING: inputs span allocations; shared-node conditions may differ.")
    groups = {key: groups[key] for key in LABELS if groups.get(key)}
    if not groups:
        parser.error("No completed trials found")
    streams = {key: rows for key, rows in groups.items() if key[1] == "stream"}
    inference = {key: rows for key, rows in groups.items() if key[0] != "memory-only"}
    panels = []
    if streams:
        panels.append(("STREAM bandwidth (GB/s; higher is better)", streams,
                       [(name, lambda row, name=name: row["kernels"][name]["average_GB_per_second"])
                        for name in ("Copy", "Scale", "Add", "Triad")]))
    if inference:
        panels.append(("Inference throughput (requests/s)", inference,
                       [("Completed requests", lambda row: row["requests_per_second"])]))
        panels.append(("Backup payload rate (GB/s; includes idle time)", inference,
                       [("GPU to host", lambda row: row["backup_payload_GB_per_second"])]))
    width = 1200
    height = 140 + len(panels) * 380 + 100
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
           '<rect width="100%" height="100%" fill="white"/>',
           '<g font-family="sans-serif" fill="#172033">']

    def text(x, y, value, size=14, anchor="start", fill="#172033"):
        svg.append(f'<text x="{x:.2f}" y="{y:.2f}" font-size="{size}" text-anchor="{anchor}" fill="{fill}">{html.escape(str(value))}</text>')

    text(40, 38, "Memory and inference experiment results", 25)
    text(40, 65, f"Host: {first['hostname']} | STREAM threads: {first['threads']} | Jobs: {', '.join(sorted(job_ids))}")
    text(40, 89, "CPU affinity: " + ", ".join(map(str, first["cpu_affinity"])))
    if len(job_ids) > 1:
        text(40, 113, "Different allocations: comparisons may include placement or temporal effects.", fill="#b45309")
    for panel_index, (title, selected, series) in enumerate(panels):
        top = 140 + panel_index * 380
        text(40, top + 10, title, 19)
        legend_x = 45
        for i, (label, _) in enumerate(series):
            svg.append(f'<rect x="{legend_x}" y="{top + 25}" width="12" height="12" fill="{COLORS[i]}"/>')
            text(legend_x + 19, top + 36, label, 12)
            legend_x += 220
        left, right, plot_top, bottom = 100, 1150, top + 65, top + 270
        values = [float(getter(row)) for rows in selected.values()
                  for _, getter in series for row in rows]
        if any(not math.isfinite(value) or value < 0 for value in values):
            parser.error("Rates must be finite and nonnegative")
        maximum = max(values + [0.01]) * 1.2
        for tick in range(5):
            value = maximum * tick / 4
            y = bottom - value / maximum * (bottom - plot_top)
            svg.append(f'<line x1="{left}" x2="{right}" y1="{y:.2f}" y2="{y:.2f}" stroke="#e2e8f0"/>')
            text(left - 10, y + 5, f"{value:.2f}", 12, "end")
        group_width = (right - left) / len(selected)
        for group_index, (key, rows) in enumerate(selected.items()):
            center = left + (group_index + 0.5) * group_width
            bar_width = min(65, group_width * 0.7 / len(series))
            for i, (_, getter) in enumerate(series):
                samples = [float(getter(row)) for row in rows]
                median = statistics.median(samples)
                x = center + (i - (len(series) - 1) / 2) * bar_width * 1.12
                bar_height = median / maximum * (bottom - plot_top)
                svg.append(f'<rect x="{x - bar_width / 2:.2f}" y="{bottom - bar_height:.2f}" width="{bar_width:.2f}" height="{bar_height:.2f}" fill="{COLORS[i]}" opacity="0.8"/>')
                for index, value in enumerate(samples):
                    offset = ((index % 5) - 2) * min(4, bar_width / 12) if len(samples) > 1 else 0
                    y = bottom - value / maximum * (bottom - plot_top)
                    svg.append(f'<circle cx="{x + offset:.2f}" cy="{y:.2f}" r="3" fill="#111827"/>')
                text(x, bottom - max(samples) / maximum * (bottom - plot_top) - 9,
                     f"{median:.2f}", 12, "middle")
            label, detail = LABELS[key]
            text(center, bottom + 25, label, 13, "middle")
            text(center, bottom + 45, detail, 12, "middle")
            text(center, bottom + 65, f"n = {len(rows)} trial(s)", 12, "middle")
    footer = 140 + len(panels) * 380
    text(40, footer + 15, "Bars and labels: median across trials. Dots: individual trial averages. No time series is recorded.", 13)
    text(40, footer + 38, "STREAM rates count logical bytes, not physical DRAM traffic. Backup rate is not active copy-engine bandwidth.", 13)
    if any(len(rows) == 1 for rows in groups.values()):
        text(40, footer + 61, "Single-trial conditions have no between-trial variability estimate.", 13, fill="#b45309")
    svg.append("</g></svg>")
    destination = args.output or (args.results[0].with_suffix(".svg") if len(args.results) == 1
                                  else Path("memory-comparison.svg"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(svg) + "\n")
    print(f"Saved {destination}")


if __name__ == "__main__":
    main()
