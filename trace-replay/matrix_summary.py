"""Validate and summarize the 15-policy matrix without pooling configurations."""
import argparse
import collections
import csv
import html
import importlib.util
import json
import math
from pathlib import Path
import statistics
import subprocess
import shutil

from policy_matrix import BASES, VARIANTS, configurations

SPEC=importlib.util.spec_from_file_location('trace_compare',Path(__file__).with_name('compare.py'))
compare=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(compare)
NAMES={'write_through':'Write-through','write_through_selective':'Selective write-through','write_back':'Write-back'}
LABELS={('normal','all'):'Normal',('skip','all'):'Skip',('defer','all'):'Defer: all',
        ('defer','victim'):'Defer: victim',('defer','through_victim'):'Defer: through victim'}


def stats(values):
    values=[x for x in values if x is not None]
    return dict(n=len(values),median=statistics.median(values),min=min(values),max=max(values)) if values else dict(n=0,median=None,min=None,max=None)


def load(root,partial=False):
    groups={};reference=None;validation=[];shape_reference=None;stream_binary_reference=None
    status=json.loads((root/'status.json').read_text())
    if not partial and status.get('status')!='complete':raise ValueError('Matrix has not completed')
    for config in configurations():
        for kind in ['delay','stream']:
            paths=sorted((root/config['key']/kind).glob('trace-*.json'))
            if not paths:
                if partial:continue
                raise ValueError(f'Missing {config["key"]}/{kind}')
            compare.load(paths)  # Check within-cell sources, placement, runtime and completion.
            trials=[]
            for path in paths:
                data=json.loads(path.read_text())
                if (data['write_policy'],data['controller_mode'],data.get('queue_policy','all')) != (config['base'],config['mode'],config['queue']):
                    raise ValueError(f'Mislabeled configuration: {path}')
                if kind=='stream':
                    binary=data.get('stream_binary_sha256')
                    if not binary:raise ValueError(f'Missing STREAM binary identity: {path}')
                    if stream_binary_reference is not None and binary!=stream_binary_reference:
                        raise ValueError(f'STREAM binary changed across configurations: {path}')
                    stream_binary_reference=binary
                common=compare.signature(data)
                for key in ['write_policy','queue_policy','tool_workload','binary']:
                    common.pop(key,None)
                common['server'].pop('hicache_write_policy',None)
                if reference is not None and reference!=common:
                    raise ValueError(f'Uncontrolled configuration differences: {path}: {[k for k in common if common[k]!=reference[k]]}')
                reference=common
                for trial in data['trials']:
                    state=trial['controller_final'];events=trial['controller_events']
                    if state['active_tool_ids'] or state['pending_ops'] or state['in_flight_batches']:
                        raise ValueError('Trial did not fully drain')
                    starts=collections.Counter(e['tool_id'] for e in events if e['event']=='TOOL_START')
                    ends=collections.Counter(e['tool_id'] for e in events if e['event']=='TOOL_END')
                    expected=sum(len(step['tools']) for session in data['plan']['sessions'] for step in session['steps'])
                    expected_requests=sum(len(s['steps']) for s in data['plan']['sessions'])+len(data['plan']['sessions'])*data['replay_config']['tail_requests']
                    if len(trial['requests'])!=expected_requests or len(trial['tools'])!=expected:
                        raise ValueError('Unexpected number of requests or tools')
                    if starts!=ends or sum(starts.values())!=expected or any(e['event']=='TOOL_EXPIRED' for e in events):
                        raise ValueError('Tool activity accounting mismatch')
                    if not trial.get('timing_fidelity',{}).get('passed'):
                        raise ValueError('Trial failed timing fidelity')
                    counters=trial['controller_counter_deltas']
                    if counters['submitted_bytes']!=counters['completed_bytes']:
                        raise ValueError('Submitted/completed backup byte mismatch')
                    http=trial['transfer_counter_deltas']['sglang:hicache_backup_bytes_total']
                    if http is not None and http!=counters['completed_bytes']:
                        raise ValueError('HTTP backup counters disagree with scheduler')
                    shapes={(r['session_id'],str(r['round_index']),r['phase']):(r['prompt_tokens'],r['output_tokens']) for r in trial['requests']}
                    if shape_reference is not None and shapes!=shape_reference:raise ValueError('Request shapes changed')
                    shape_reference=shapes
                    values=compare.metrics(trial)
                    values['whole.execution_seconds']=(trial['accounting_settle_start_ns']-trial['start_ns'])/1e9
                    values['whole.partial_reclaim_selections']=sum(e['event']=='RECLAIM_SELECTION' for e in events)
                    values['whole.partial_reclaim_selected_bytes']=sum(e['selected_bytes'] for e in events if e['event']=='RECLAIM_SELECTION')
                    values['whole.no_eligible_pending_victim']=sum(e['event']=='RECLAIM_NO_ELIGIBLE_VICTIM' for e in events)
                    values['whole.peak_pending_tokens']=max((e['tokens'] for e in events if e['event']=='BACKUP_PENDING'),default=0)
                    values['whole.requests']=len(trial['requests'])
                    values['whole.tools']=len(trial['tools'])
                    trials.append(values)
                    validation.append(dict(configuration=config['key'],kind=kind,path=str(path),trial=trial['index'],
                        requests=len(trial['requests']),tools=expected,timing_passed=True,drained=True,
                        missing_backup_counter=http is None,missing_restore_counter=not trial['host_restore_measurement_available']))
            if not partial and len(trials)!=status['trials']:raise ValueError('Unexpected trial count in matrix cell')
            groups[(config['key'],kind)]={metric:stats([trial.get(metric) for trial in trials]) for metric in trials[0]}
    return groups,validation


def chart(groups,base,metric,ylabel,destination,stream_only=False):
    width,height=1400,600;left,right,top,bottom=100,1370,65,460
    variants=[('normal','all'),('defer','all'),('defer','victim'),('defer','through_victim'),('skip','all')]
    kinds=['stream'] if stream_only else ['delay','stream']
    colors={'delay':'#60a5fa','stream':'#f59e0b'}
    def sample(mode,queue,kind):
        return groups.get((f'{base}--{mode}--{queue}',kind),{}).get(metric,stats([]))
    values=[sample(mode,queue,kind)['max'] for mode,queue in variants for kind in kinds]
    maximum=max([v for v in values if v is not None] or [1])
    upper=maximum*1.2 if maximum>0 else 1
    y=lambda v:bottom-v/upper*(bottom-top)
    svg=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"><rect width="100%" height="100%" fill="white"/><g font-family="Arial,sans-serif" fill="#172033">']
    def text(x,yy,value,size=18,anchor='middle'):
        svg.append(f'<text x="{x}" y="{yy}" text-anchor="{anchor}" font-size="{size}">{html.escape(str(value))}</text>')
    svg.append(f'<text transform="translate(27 260) rotate(-90)" text-anchor="middle" font-size="22">{html.escape(ylabel)}</text>')
    for tick in range(6):
        v=upper*tick/5;yy=y(v)
        svg.append(f'<line x1="{left}" x2="{right}" y1="{yy}" y2="{yy}" stroke="#e2e8f0"/>');text(left-12,yy+6,f'{v:.2f}',16,'end')
    for i,(mode,queue) in enumerate(variants):
        center=left+(i+0.5)*(right-left)/len(variants)
        for j,kind in enumerate(kinds):
            x=center+(j-(len(kinds)-1)/2)*80;s=sample(mode,queue,kind)
            if not s['n']:text(x,bottom-10,'N/A',17);continue
            svg.append(f'<rect x="{x-32}" y="{y(s["median"])}" width="64" height="{bottom-y(s["median"])}" fill="{colors[kind]}"/>')
            svg.append(f'<path d="M{x} {y(s["min"])}V{y(s["max"])} M{x-8} {y(s["min"])}H{x+8} M{x-8} {y(s["max"])}H{x+8}" stroke="#172033" stroke-width="2"/>')
            text(x,y(s['max'])-10,f'{s["median"]:.2f}',15)
        label=LABELS[(mode,queue)].replace(': ',':\n')
        for line,value in enumerate(label.splitlines()):text(center,bottom+36+line*24,value,18)
    for j,kind in enumerate(kinds):
        x=left+j*190;svg.append(f'<rect x="{x}" y="20" width="18" height="18" fill="{colors[kind]}"/>')
        text(x+28,36,'Waiting tools' if kind=='delay' else 'STREAM tools',17,'start')
    text(right,36,'Bars: median; whiskers: trial min–max',16,'end')
    svg.append('</g></svg>')
    destination.parent.mkdir(parents=True,exist_ok=True)
    source='\n'.join(svg)
    destination.with_suffix('.svg').write_text(source)
    if shutil.which('rsvg-convert'):
        subprocess.run(['rsvg-convert','--format=png','--output',str(destination)],input=source,text=True,check=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('results',type=Path)
    p.add_argument('--partial',action='store_true')
    p.add_argument('--plots',action='store_true',help='Also plot a partial matrix; missing cells are labeled N/A')
    args=p.parse_args();root=args.results.resolve()
    groups,validation=load(root,args.partial)
    if not groups:raise ValueError('No validated measured cells to summarize')
    rows=[]
    for (key,kind),metrics in groups.items():
        for metric,s in metrics.items():rows.append(dict(configuration=key,tool_workload=kind,metric=metric,**s))
    with (root/'summary.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    (root/'validation.json').write_text(json.dumps(validation,indent=2)+'\n')
    lines=['# Backup-policy experiment matrix','',
        'Values are trial medians. Waiting and STREAM tools are separate conditions. Missing measurements are N/A, not zero. Whole time includes inference, tools, reuse tail, drain and counter settling; warmup is excluded.','']
    def val(m,key,scale=1,digits=2):
        s=m[key]
        return f'{s["median"]*scale:.{digits}f}' if s['n'] else 'N/A'
    for kind in ['delay','stream']:
        lines += [f'## {"Waiting" if kind=="delay" else "STREAM"} tools','',
            '| Base policy | Controller | Whole time, s (min–max) | Request latency, ms | Input reuse, % | D2H, GB | H2D, GB | STREAM, GB/s |',
            '|---|---|---:|---:|---:|---:|---:|---:|']
        for c in configurations():
            if (c['key'],kind) not in groups:continue
            m=groups[(c['key'],kind)];s=m['whole.seconds']
            whole=f'{s["median"]:.2f} ({s["min"]:.2f}–{s["max"]:.2f})'
            lines.append(f'| {NAMES[c["base"]]} | {LABELS[(c["mode"],c["queue"])]} | {whole} | {val(m,"whole.latency_median_seconds",1000)} | {val(m,"whole.server_prefix_hit_rate",100,1)} | {val(m,"whole.backup_GB",digits=3)} | {val(m,"whole.restore_GB",digits=3)} | {val(m,"tool.stream_median_triad_GB_per_second")} |')
        lines += ['','| Base policy | Controller | Post-tool time, s | Deferred decisions | Skipped opportunities | Safety overrides | Partial reclaim selections |',
                  '|---|---|---:|---:|---:|---:|---:|']
        for c in configurations():
            if (c['key'],kind) not in groups:continue
            m=groups[(c['key'],kind)]
            lines.append(f'| {NAMES[c["base"]]} | {LABELS[(c["mode"],c["queue"])]} | {val(m,"post_tool.seconds")} | {val(m,"whole.controller.deferred_decisions",digits=0)} | {val(m,"whole.controller.skipped_opportunities",digits=0)} | {val(m,"whole.controller.pressure_overrides",digits=0)} | {val(m,"whole.partial_reclaim_selections",digits=0)} |')
        lines += ['','| Base policy | Controller | Tool-window D2H, GB | Post-tool D2H, GB | Tool-window request latency, ms | Post-tool request latency, ms |',
                  '|---|---|---:|---:|---:|---:|']
        for c in configurations():
            if (c['key'],kind) not in groups:continue
            m=groups[(c['key'],kind)]
            lines.append(f'| {NAMES[c["base"]]} | {LABELS[(c["mode"],c["queue"]) ]} | {val(m,"tool.backup_completed_bytes",1e-9,3)} | {val(m,"post_tool.backup_completed_bytes",1e-9,3)} | {val(m,"tool.latency_median_seconds",1000)} | {val(m,"post_tool.latency_median_seconds",1000)} |')
        lines += ['']
    lines += ['## How to read the comparison','',
        '- Compare each controller against normal within the same base policy and tool workload. Across-base differences also change which data is backed up.',
        '- Write-back has no eager optional backups in this implementation. Defer/skip and queue-selection variants may therefore be behaviorally identical; inspect policy activations rather than treating noise as an algorithmic gain.',
        '- Partial selection includes pending ancestor dependencies necessary to preserve a usable host prefix; victim-only is dependency-aware, not a promise of one tensor copy.',
        '- Deferred decisions count scheduler polls; skipped opportunities can revisit entries. Neither is a count of unique cache nodes.',
        '- Every configuration uses the same GPU-space reserve, queue bound and age limit (recorded in status.json). Victim-aware strategies also handle pressure that would otherwise prevent request admission.',
        '- The selected sessions are a narrow, timing-focused TraceLab subset; token lengths remain scaled. STREAM is synthetic memory stress, not original tool execution. No production-level benefit is assumed.','']
    (root/'TABLES.md').write_text('\n'.join(lines))
    if not args.partial or args.plots:
        for base in BASES:
            folder=root/'plots'/base
            for metric,label,name in [('whole.seconds','Whole workload time (s)','runtime.png'),
                ('whole.latency_median_seconds','Median model request latency (s)','latency.png'),
                ('whole.server_prefix_hit_rate','Fraction of input tokens reused','reuse.png'),
                ('whole.backup_GB','GPU-to-host backup payload (GB)','backups.png')]:
                chart(groups,base,metric,label,folder/name)
            chart(groups,base,'tool.stream_median_triad_GB_per_second','STREAM median Triad rate (GB/s)',folder/'stream-rate.png',True)
    print(f'Validated {len(validation)} trials; saved {root}/TABLES.md and summary.csv')


if __name__=='__main__':main()
