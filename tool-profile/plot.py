"""Make a standalone SVG timeline without plotting dependencies."""
import html
import json
import sys
from pathlib import Path


def main():
    path = Path(sys.argv[1])
    data = json.loads(path.read_text())
    rows = data["samples"]
    duration = max(data["duration_seconds"], 0.001)
    events_path = path.with_name(path.stem + "-events.jsonl")
    events = [json.loads(line) for line in events_path.read_text().splitlines()] if events_path.exists() else []
    def x(seconds):
        return 90 + seconds / duration * 850
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="810" viewBox="0 0 1000 810">',
           '<rect width="1000" height="810" fill="white"/>',
           '<g font-family="sans-serif" font-size="13" fill="#202830">',
           f'<text x="40" y="30" font-size="20">Tool resource timeline — trial {data["trial"]}</text>',
           f'<text x="40" y="53">Duration {duration:.2f}s; workload exit code {data["returncode"]}</text>']
    panels = [("CPU use (%; 100% = one core)", [("cpu_percent", "#1565c0")]),
              ("Resident memory (GiB; process-tree RSS)", [("rss_GiB", "#00897b")]),
              ("Host DRAM bandwidth (GB/s; whole monitored sockets)", [("dram_read_GB_s", "#b45309"), ("dram_write_GB_s", "#7e22ce")])]
    for panel, (title, series) in enumerate(panels):
        top = 100 + panel * 200
        svg.append(f'<text x="90" y="{top - 15}">{title}</text>')
        values = [row[key] for key, _ in series for row in rows if row[key] is not None]
        maximum = max(values + [0.001]) * 1.1
        for tick in range(5):
            y = top + 140 - tick * 35
            svg.append(f'<line x1="90" x2="940" y1="{y}" y2="{y}" stroke="#e0e0e0"/>')
            svg.append(f'<text x="80" y="{y + 4}" text-anchor="end">{maximum * tick / 4:.2f}</text>')
        for tick in range(6):
            seconds = duration * tick / 5
            svg.append(f'<text x="{x(seconds):.1f}" y="{top + 160}" text-anchor="middle">{seconds:.2f}</text>')
        if not values:
            svg.append(f'<text x="160" y="{top + 75}">Unavailable: hardware counters inaccessible or unsupported</text>')
        for key, color in series:
            points = " ".join(f'{x(row["elapsed_seconds"]):.2f},{top + 140 - row[key] / maximum * 140:.2f}' for row in rows if row[key] is not None)
            svg.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="1.5"/>')
            if len(series) > 1:
                offset = 0 if "read" in key else 200
                svg.append(f'<text x="{550 + offset}" y="{top - 15}" fill="{color}">{html.escape(key)}</text>')
        for event in events:
            if event["kind"] == "collection_end":
                position = x((event["time_ns"] - data["start_ns"]) / 1e9)
                svg.append(f'<line x1="{position}" x2="{position}" y1="{top}" y2="{top + 140}" stroke="#555" stroke-dasharray="4 4"/>')
    svg.extend(['<text x="500" y="690" text-anchor="middle">Elapsed time (seconds)</text>',
                '<text x="40" y="722">Dashed line: pytest collection finished. Individual test timestamps are saved in events.jsonl.</text>',
                '<text x="40" y="745">CPU/RSS: sampled process tree; very short-lived children can be missed. DRAM includes other jobs.</text>',
                '<text x="40" y="768">Screening profile only: no KV backups are deliberately generated in this run.</text>', '</g></svg>'])
    path.with_suffix(".svg").write_text("\n".join(svg))


if __name__ == "__main__":
    main()
