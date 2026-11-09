"""Draw the HiCache backup design options; no GPU or model execution."""
import argparse
import html
from pathlib import Path
import shutil
import subprocess


class Figure:
    def __init__(self, width, height):
        self.parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
                      '<rect width="100%" height="100%" fill="white"/>',
                      '<defs><marker id="tip" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10Z" fill="#64748b"/></marker></defs>',
                      '<g font-family="Arial,sans-serif" fill="#172033">']

    def text(self, x, y, lines, size=23, color='#172033', anchor='start'):
        for i, line in enumerate([lines] if isinstance(lines, str) else lines):
            self.parts.append(f'<text x="{x}" y="{y+i*(size+9)}" font-size="{size}" text-anchor="{anchor}" fill="{color}">{html.escape(line)}</text>')

    def box(self, x, y, w, h, title, body, active=True):
        fill, stroke = ('#eff6ff', '#3b82f6') if active else ('#f8fafc', '#94a3b8')
        dash = '' if active else ' stroke-dasharray="9 6"'
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="13" fill="{fill}" stroke="{stroke}" stroke-width="2"{dash}/>')
        self.text(x+20, y+39, title, 26)
        self.text(x+20, y+78, body, 21)

    def arrow(self, x1, y1, x2, y2, dashed=False):
        dash = ' stroke-dasharray="8 6"' if dashed else ''
        self.parts.append(f'<path d="M{x1} {y1} L{x2} {y2}" fill="none" stroke="#64748b" stroke-width="3" marker-end="url(#tip)"{dash}/>')

    def save(self, output, renderer):
        svg = '\n'.join(self.parts+['</g></svg>'])
        # Keep vector source too, for editable/export-quality diagrams.
        output.with_suffix('.svg').write_text(svg)
        subprocess.run([renderer, '--format=png', '--output', str(output)], input=svg, text=True, check=True)


def hierarchy(output, renderer):
    f = Figure(1500, 1280)
    f.box(465, 20, 570, 105, 'HiCache enabled', ['GPU KV data may be backed up to CPU RAM.'])
    f.text(40, 175, '1. Base write policy: which backup opportunities are created?', 28)
    policies = [
        ('Write-through', ['Offer backups eagerly.', 'Create host copies ahead of eviction.'], True),
        ('Selective write-through', ['Offer eager backups for entries', 'that meet the reuse threshold.'], True),
        ('Write-back', ['Try to preserve data at eviction,', 'rather than backing it up eagerly.'], True)]
    for i, (name, body, active) in enumerate(policies):
        f.box(40+490*i, 200, 450, 140, name, body, active)
    f.parts.append('<path d="M265 345 V375 H1245 V345 M755 345 V375" fill="none" stroke="#64748b" stroke-width="3"/>')
    f.arrow(755, 375, 755, 415)
    f.text(40, 445, 'Choose one base write policy, then choose its tool-activity behavior.', 22)
    f.text(40, 480, '2. Tool-activity control: what happens to optional backups?', 28)
    modes = [
        ('Normal', ['Keep ordinary backup scheduling.', 'Tool activity does not gate copies.']),
        ('Defer', ['Hold optional backups during tools.', 'Resume normally when tools end.']),
        ('Skip', ['Decline optional backups during tools.', 'Do not create a deferred backlog.'])]
    for i, (name, body) in enumerate(modes):
        f.box(40+490*i, 505, 450, 140, name, body)
    f.arrow(755, 650, 755, 700)
    f.box(320, 710, 870, 150, 'When deferred work must make progress',
          ['GPU-space pressure, queue bounds, or the age limit can force copies.',
           'If eviction needs data that is not yet backed up, choose how much',
           'of the pending queue to copy before reclaiming that GPU storage.'])
    f.arrow(755, 865, 755, 880)
    f.text(40, 915, '3. Allocation-critical queue selection: how much would we copy?', 28)
    choices = [
        ('Flush the whole queue', ['Copy every pending entry.', 'Empty the pending queue.'], True),
        ('Extract victim only', ['Copy the selected victim.', 'Keep other entries queued.'], True),
        ('Flush through the victim', ['Copy earlier entries plus the victim.', 'Keep later entries queued.'], True)]
    for i, (name, body, active) in enumerate(choices):
        f.box(40+490*i, 980, 450, 140, name, body, active)
        f.arrow(755, 935, 265+490*i, 972)
    f.box(40, 1150, 1420, 105, 'Rules that apply across these decisions',
          ['Required restores remain allowed. Submitted transfers are not cancelled. GPU source data stays valid until copying completes.'])
    f.save(output/'hierarchy.png', renderer)


def queue_example(output, renderer):
    f = Figure(1500, 790)
    f.text(40, 45, 'Queue: [A, B, V, C, D] — V is the eviction victim to preserve', 29)
    f.text(40, 85, 'Entries are ordered from left to right. Choose how much pending data to copy to CPU memory.', 22)
    rows = [('Flush all', ['A', 'B', 'V', 'C', 'D'], [], True),
            ('Victim only', ['V'], ['A', 'B', 'C', 'D'], True),
            ('Through victim', ['A', 'B', 'V'], ['C', 'D'], True)]
    f.text(440, 140, 'Copy to CPU RAM', 24)
    f.text(1020, 140, 'Remain queued', 24)
    for i, (name, copied, pending, active) in enumerate(rows):
        y = 175+140*i
        f.text(40, y+42, name, 25)
        for j, item in enumerate(copied):
            f.box(440+j*95, y, 75, 80, item, [], active)
        if not pending:
            f.text(1020, y+42, 'None', 24)
        for j, item in enumerate(pending):
            f.box(1020+j*100, y, 75, 80, item, [], False)
    f.box(40, 615, 1420, 150, 'All three strategies preserve V while choosing different amounts of extra work',
          ['Flush all maximizes batching. Victim only minimizes immediate copy volume. Through victim preserves queue order.',
           'The copy must finish before the corresponding GPU storage is reused.',
           'Blue entries are copied; grey entries remain queued. Prefix dependencies and active-request locks still apply.'])
    f.save(output/'queue-options.png', renderer)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path,
                        default=Path(__file__).resolve().parents[1]/'results/backup-policy-hierarchy')
    args = parser.parse_args()
    renderer = shutil.which('rsvg-convert')
    if not renderer:
        raise RuntimeError('rsvg-convert is required')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    hierarchy(args.output_dir, renderer)
    queue_example(args.output_dir, renderer)
    (args.output_dir/'README.md').write_text('''# HiCache policy layers

![Policy hierarchy](hierarchy.png)

![Queue-selection example](queue-options.png)

These diagrams organize the design space rather than report implementation status:
three base write policies, three tool-activity modes, and three queue-selection
strategies when deferred backups must make allocation progress. Queue selection
is conditional on pending work and preservation requirements; it is not a fourth
activity mode. The diagrams do not certify that each combination has been tested.

All hierarchy options use equal styling. In the queue example, blue means copied
and grey means remaining queued.

GPU-space pressure can trigger action before the GPU is completely full.
Protect active-request KV and in-flight sources. Reusable, inactive cache data
can sometimes be discarded and recomputed; keeping every victim is a performance
goal, not a universal correctness requirement. Completed valid host backups do
not require duplicate transfers. Host capacity may prevent preserving a victim.

Source locations inspected:
- sglang/python/sglang/srt/mem_cache/unified_radix_cache.py:
  init_hicache, evict_for_alloc, _evict, _execute_and_commit_kv_backup.
- sglang/python/sglang/srt/managers/cache_controller.py: start_writing.
- sglang/python/sglang/srt/mem_cache/stream_activity.py: allow_optional, flush_reason.

Regenerate from ~/cs6222:
python3 scripts/memory-experiment/draw-backup-hierarchy.py
''')
    print(args.output_dir.resolve())


if __name__ == '__main__':
    main()
