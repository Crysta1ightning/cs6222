"""Build an offline, plain-English visual guide for saved TraceLab results.

Reads existing measurements only; never starts inference or modifies raw results.
"""
import argparse
import hashlib
import html
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import subprocess


def esc(value):
    return html.escape(str(value), quote=True)


class Diagram:
    def __init__(self, height):
        self.items = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{height}" viewBox="0 0 1200 {height}">',
                      '<rect width="100%" height="100%" fill="white"/>',
                      '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10Z" fill="#475569"/></marker></defs>',
                      '<g font-family="Arial, sans-serif" fill="#172033">']

    def text(self, x, y, lines, size=22, color='#172033', anchor='start'):
        if isinstance(lines, str):
            lines = [lines]
        for i, line in enumerate(lines):
            self.items.append(f'<text x="{x}" y="{y+i*(size+10)}" font-size="{size}" text-anchor="{anchor}" fill="{color}">{esc(line)}</text>')

    def box(self, x, y, w, h, color='#f1f5f9', border='#cbd5e1'):
        self.items.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{color}" stroke="{border}" stroke-width="2"/>')

    def arrow(self, x1, y1, x2, y2, dashed=False):
        dash = ' stroke-dasharray="8 6"' if dashed else ''
        self.items.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#475569" stroke-width="3" marker-end="url(#arrow)"{dash}/>')

    def save(self, path, renderer):
        source = '\n'.join(self.items + ['</g></svg>'])
        subprocess.run([renderer, '--format=png', '--output', str(path)], input=source, text=True, check=True)


def flow(path, renderer):
    d = Diagram(740)
    stages = [('Model generates', ['Read the conversation', 'and produce new tokens']),
              ('Tool runs', ['Wait, or run STREAM', 'to stress CPU memory']),
              ('Model runs again', ['Reuse previous work', 'where it is still cached']),
              ('Repeat + finish', ['Complete all sessions,', 'then test reuse and drain'])]
    for i, (title, body) in enumerate(stages):
        x = 30 + i * 295
        d.box(x, 35, 255, 155, '#eff6ff')
        d.text(x+15, 70, title, 23)
        d.text(x+15, 115, body, 18)
        if i < 3:
            d.arrow(x+260, 110, x+285, 110)
    d.text(40, 240, 'TraceLab supplies conversation lengths, reuse patterns and tool timing.', 23)
    d.text(40, 275, 'Synthetic inputs and substitute tools are used; original coding tasks are not executed.', 19)
    d.box(60, 320, 365, 205, '#eff6ff', '#93c5fd')
    d.text(85, 360, 'GPU memory', 28)
    d.text(85, 405, ['Holds the model\u2019s working notes', '(the KV cache). Reusing them', 'avoids repeating calculations.'], 20)
    d.box(775, 320, 365, 205, '#ecfdf5', '#6ee7b7')
    d.text(800, 360, 'CPU RAM / host memory', 26)
    d.text(800, 405, ['HiCache keeps backup copies here.', 'Backed-up data can be restored', 'when a future request needs it.'], 20)
    d.arrow(440, 375, 760, 375)
    d.text(600, 345, 'Backup: GPU \u2192 CPU', 21, anchor='middle')
    d.text(600, 410, 'Controller changes optional copies', 17, anchor='middle')
    d.arrow(760, 475, 440, 475)
    d.text(600, 455, 'Restore: CPU \u2192 GPU', 21, anchor='middle')
    d.text(600, 510, 'Always allowed when required', 17, anchor='middle')
    d.box(775, 595, 365, 105, '#fff7ed', '#fdba74')
    d.text(800, 635, ['STREAM reads and writes RAM.', 'It may compete with cache copies.'], 20)
    d.arrow(960, 585, 960, 540)
    d.text(70, 615, ['Four sessions can overlap:', 'one session may use the GPU', 'while another session runs a tool.'], 22)
    d.save(path, renderer)


def policies(path, renderer):
    d = Diagram(760)
    for i, (mode, body, color) in enumerate([
        ('NORMAL', ['Save optional backups', 'as usual.'], '#eff6ff'),
        ('DEFER', ['Try to postpone optional', 'backups until tools finish.'], '#fff7ed'),
        ('SKIP', ['Decline optional backups', 'during tool activity.'], '#ecfdf5')]):
        y = 20 + 245*i
        d.box(20, y, 1160, 225, color)
        d.text(45, y+45, mode, 26)
        d.text(45, y+90, body, 19)
        d.box(405, y+30, 370, 130, '#e2e8f0', '#cbd5e1')
        d.text(590, y+58, 'Tool activity', 20, anchor='middle')
        d.arrow(320, y+175, 1135, y+175)
        d.text(1080, y+207, 'Time', 17)
        if mode == 'NORMAL':
            d.box(470, y+90, 235, 50, '#dbeafe', '#60a5fa')
            d.text(587, y+123, 'Optional copy runs', 21, anchor='middle')
            d.text(810, y+105, ['Existing backup', 'behavior continues.'], 20)
        elif mode == 'DEFER':
            d.text(450, y+108, 'Copy opportunity', 19)
            d.arrow(530, y+130, 970, y+130)
            d.text(990, y+96, ['Copy after', 'tools finish'], 20, anchor='middle')
            d.text(450, y+207, 'Safety limits may require an earlier copy.', 19)
        else:
            d.text(500, y+123, '\u00d7', 45, '#b91c1c')
            d.text(555, y+112, ['No optional', 'backup now'], 19)
            d.text(945, y+96, ['Future cache operations', 'may reconsider the data.'], 19, anchor='middle')
            d.text(450, y+207, 'No deferred backlog is created by skip.', 19)
    d.save(path, renderer)


def timing(path, renderer, findings):
    d = Diagram(570)
    normal = findings['stream']['normal']
    delay = findings['delay']['normal']
    values = [normal['tool.planned_duration_median_seconds']['median'],
              delay['tool.actual_duration_median_seconds']['median'],
              normal['tool.actual_duration_median_seconds']['median']]
    if any(v is None or not math.isfinite(v) or v < 0 for v in values):
        raise ValueError('Measured tool durations are required; missing is not zero')
    upper = max(values)*1.25
    labels = [('Trace target', '#64748b'), ('Delay process', '#60a5fa'), ('STREAM process', '#f59e0b')]
    for i, ((label, color), value) in enumerate(zip(labels, values)):
        y = 55+i*115
        d.text(35, y+36, label, 23)
        d.box(285, y, 750*value/upper, 55, color, color)
        d.text(300+750*value/upper, y+36, f'{value*1000:,.0f} ms', 23)
    d.box(35, 405, 1130, 135, '#fff7ed', '#fdba74')
    d.text(60, 442, 'The old experiment stretched very short tool intervals.', 25)
    d.text(60, 480, ['Starting processes and executing full STREAM blocks took longer than the trace allowed.',
                    'Queued tools waited even longer. Successful execution does not prove faithful timing.'], 20)
    d.save(path, renderer)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path, help='One completed allocation result directory')
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    root = args.results.resolve()
    output = (args.output_dir or root/'explained').resolve()
    findings_path = root/'findings.json'
    findings = json.loads(findings_path.read_text())
    # Reject mixed/failed raw data and verify the displayed summary against it.
    spec = importlib.util.spec_from_file_location('trace_compare', Path(__file__).with_name('compare.py'))
    compare = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compare)
    sources = [findings_path]
    for kind in ['delay', 'stream']:
        paths = sorted((root/f'trace-{kind}').glob('*/trace-*.json'))
        groups = compare.load(paths)
        if set(groups) != {'normal', 'defer', 'skip'}:
            raise ValueError(f'{kind}: need completed normal, defer and skip results')
        sources.extend(paths)
        for mode, trials in groups.items():
            for metric in ['whole.seconds', 'whole.server_prefix_hit_rate', 'whole.backup_GB']:
                measured = statistics.median(compare.metrics(t)[metric] for t in trials)
                if not math.isclose(measured, findings[kind][mode][metric]['median'], rel_tol=1e-9):
                    raise ValueError(f'Summary disagrees with raw results: {kind}/{mode}/{metric}')
    renderer = shutil.which('rsvg-convert')
    if not renderer:
        raise RuntimeError('PNG generation requires rsvg-convert')
    output.mkdir(parents=True, exist_ok=True)
    flow(output/'experiment-flow.png', renderer)
    policies(output/'controller-modes.png', renderer)
    timing(output/'timing-problem.png', renderer, findings)
    relative = lambda p: esc(os.path.relpath(p, output))
    sections = []
    markdown = ['# TraceLab visual guide', '',
                'An explanation of saved measurements. No new GPU experiments were run.', '']

    def section(title, description, path):
        sections.append(f'<section><h2>{esc(title)}</h2><p>{esc(description)}</p><a href="{relative(path)}"><img src="{relative(path)}" alt="{esc(title)}" loading="lazy"></a></section>')
        markdown.extend([f'## {title}', '', description, '', f'![{title}]({os.path.relpath(path, output)})', ''])

    section('1. What runs in the experiment',
            'Trace-derived conversations go through a real GPU model. Tools are replaced by waits or memory-heavy STREAM work. KV cache means reusable model calculations, not saved answers.', output/'experiment-flow.png')
    section('2. What the controller changes',
            'Illustration only, not measured transfer timing. Control applies to optional GPU-to-CPU backups. Required restores remain allowed, and already-submitted transfers are not cancelled. Defer has space/age safety overrides.', output/'controller-modes.png')
    descriptions = [
        ('whole-time.png', 'Did the whole workload finish sooner?', 'Lower is better. Includes later inference, draining and about five seconds of counter observation. This is the main overall outcome; it is not GPU compute time alone.'),
        ('request-latency.png', 'Did individual model requests return sooner?', 'Lower is better. This measures a request\u2019s response time, excluding the tool wait between requests.'),
        ('prefix-hit-rate.png', 'How much earlier model work was reused?', 'Higher means more input tokens reused cached calculations. A fraction of 0.82 means about 82%; it does not mean all of that data came from CPU RAM.'),
        ('backup-total.png', 'How much data was backed up in total?', 'Lower means fewer GPU-to-CPU bytes copied. This is not automatically better: missing backups can force recalculation later.'),
        ('backup-tool-window.png', 'Were backups avoided while tools ran?', 'This checks the mechanism. Lower means fewer backup bytes completed inside actual tool-execution windows; event windows are not exact physical DMA timing.'),
        ('post-tool-time.png', 'Was work merely moved until after the tools?', 'Read this together with total runtime. This includes the final inference tail, draining and accounting; shorter tool-window activity alone is insufficient.'),
        ('stream-triad.png', 'Did STREAM itself process memory faster?', 'Higher GB/s is better for STREAM. It is a benchmark processing rate, not the percentage of all socket memory bandwidth being used.'),
        ('host-restore.png', 'Was saved host data actually brought back to the GPU?', 'A positive measured value confirms host-to-GPU traffic. Missing baselines remain unavailable, not zero. Cache-hit fraction alone cannot establish host-cache reuse.')]
    for filename, title, description in descriptions:
        p = root/'trace-stream/plots'/filename
        if p.exists():
            section(title, description, p)
    section('3. Why the timing limits our conclusions',
            'These are normal-mode medians of per-trial median tool durations, on one linear scale. They describe the old subprocess runtime used in these GPU measurements. New prepared workers and longer-tool plans require their own GPU validation.', output/'timing-problem.png')
    rows = []
    for mode in ['normal', 'defer', 'skip']:
        m = findings['stream'][mode]
        value = lambda key: m[key]['median']
        rows.append(f'<tr><th>{mode}</th><td>{value("whole.seconds"):.2f} s</td><td>{value("whole.server_prefix_hit_rate")*100:.1f}%</td><td>{value("whole.backup_GB"):.3f} GB</td><td>{value("tool.stream_median_triad_GB_per_second"):.2f} GB/s</td></tr>')
    intro = ('The controller changes backup behavior, but these measurements do not establish a reliable overall speedup. '
             'Compare total runtime, model latency, reuse and STREAM rate together. Three trials, fixed mode order, '
             'shared hardware and stretched tool timing limit causal conclusions.')
    page = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    page += '<title>TraceLab experiment visual guide</title><style>body{margin:0;background:#f1f5f9;color:#172033;font:18px/1.6 system-ui,sans-serif}main{max-width:1120px;margin:auto;padding:30px 20px}section,header{background:white;padding:25px;margin-bottom:24px;border:1px solid #cbd5e1;border-radius:12px}h1{font-size:32px}h2{font-size:25px}img{width:100%;height:auto;display:block}a{color:#1d4ed8}table{width:100%;border-collapse:collapse;font-size:16px}th,td{text-align:left;padding:9px;border-bottom:1px solid #cbd5e1}.scroll{overflow-x:auto}@media print{section{break-inside:avoid}body{background:white}}</style><main>'
    page += f'<header><h1>Understand the TraceLab experiment</h1><p>{esc(intro)}</p><p>Saved result set: <code>{esc(root.name)}</code>. No new GPU work was run to create this guide.</p><div class="scroll"><table><thead><tr><th>Mode</th><th>Total time</th><th>Input reuse</th><th>Backup bytes</th><th>STREAM rate</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div><p>Bars in the measured plots show medians; black whiskers show the minimum and maximum across trials, not confidence intervals. STREAM results are shown here; delay-tool comparisons remain separate.</p>'
    page += f'<a href="{relative(root/"FINDINGS.md")}">Full findings and limitations</a> · <a href="{relative(root/"summary.csv")}">Summary CSV</a> · <a href="{relative(root/"trace-delay/plots/whole-time.png")}">Delay-tool runtime</a></header>'
    page += ''.join(sections)+'</main></html>'
    (output/'index.html').write_text(page)
    (output/'README.md').write_text('\n'.join(markdown))
    (output/'sources.json').write_text(json.dumps({str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}, indent=2)+'\n')
    print(f'Saved {output}/index.html and three explanatory PNG diagrams')


if __name__ == '__main__':
    main()
