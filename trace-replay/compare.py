"""Compare trace replay modes without mixing plans, tool surrogates or placements."""
import argparse
import csv
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

SPEC=importlib.util.spec_from_file_location('memory_compare',Path(__file__).resolve().parents[1]/'memory-experiment/compare.py')
charts=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(charts)
ORDER={'normal':0,'defer':1,'skip':2}


def signature(data):
    config=data['replay_config']
    info=data['server_info'].get('server_args',data['server_info'])
    return dict(plan_sha256=data['plan_sha256'],tool_workload=data['tool_workload'],
                write_policy=data.get('write_policy','write_through'),queue_policy=data.get('queue_policy','all'),
                pool=data['token_pool_sha256'],binary=data.get('stream_binary_sha256'),
                replay={k:config.get(k) for k in ['threads','max_active_sessions','max_stream_processes','tail_requests','control_timeout','request_timeout','tool_timeout','drain_timeout','max_seconds','model','model_revision','tool_runtime','require_timing_fidelity']},
                hostname=data.get('hostname'),job_id=data.get('job_id'),affinity=data.get('cpu_affinity'),
                numa=data.get('numa',{}).get('stdout'),
                server={k:info.get(k) for k in ['model_path','revision','context_length','max_total_tokens','mem_fraction_static','hicache_size','hicache_ratio','hicache_write_policy','disable_cuda_graph','dtype','page_size','tp_size','attention_backend','sampling_backend','random_seed']},
                bounds={k:data['controller_initial'].get(k) for k in ['max_pending_tokens','max_pending_ops','max_defer_seconds','lease_seconds','reserve_tokens','scheduler_placement']},
                source_versions=data.get('source_versions'),runtime_source_sha256=data.get('runtime_source_sha256'))


def load(paths):
    groups={}
    reference=None
    for path in paths:
        data=json.loads(path.read_text())
        if data.get('schema')!='tracelab-replay-v1' or data.get('error') or not data.get('trials') or any(t.get('error') for t in data['trials']):
            raise ValueError(f'Failed, empty or unsupported result: {path}')
        if data.get('replay_config', {}).get('tool_runtime') == 'prepared':
            final = data.get('tool_runtime_final')
            expected = data['replay_config'].get('max_stream_processes', 1) if data.get('tool_workload') == 'stream' else 0
            if not final or final.get('protocol') != 'prepared-tools-v1' or len(final.get('workers', [])) != expected or not all(w.get('full_validation') and w.get('reaped') for w in final.get('workers', [])):
                raise ValueError(f'Prepared workers have not completed full validation and cleanup: {path}')
        current=signature(data)
        if reference is not None and reference!=current:
            differences=[k for k in reference if reference[k]!=current[k]]
            raise ValueError(f'Incompatible comparison ({path}): {differences}; keep these results separate')
        reference=current
        mode=data['controller_mode']
        if mode not in ORDER:
            raise ValueError(f'Unknown controller: {mode}')
        groups.setdefault(mode,[]).extend(data['trials'])
    return groups


def metrics(trial):
    result={}
    for name, w in trial['windows'].items():
        for key in ['seconds','requests_per_second','latency_median_seconds','cached_prompt_fraction',
                    'backup_submitted_bytes','backup_completed_bytes']:
            result[name+'.'+key]=w.get(key)
        for event in ['BACKUP_OVERRIDE','BACKUP_DEFERRED','BACKUP_SKIPPED']:
            result[name+'.'+event+'_events']=w.get('controller_event_counts',{}).get(event,0)
    result['whole.restore_GB']=(trial['transfer_counter_deltas'].get('sglang:load_back_bytes_total')/1e9
        if trial['transfer_counter_deltas'].get('sglang:load_back_bytes_total') is not None else None)
    result['whole.backup_GB']=(trial['transfer_counter_deltas'].get('sglang:hicache_backup_bytes_total')/1e9
        if trial['transfer_counter_deltas'].get('sglang:hicache_backup_bytes_total') is not None else None)
    result['whole.server_prefix_hit_rate']=trial.get('server_prefix_hit_rate')
    result['whole.accounting_settle_seconds']=trial.get('accounting_settle_seconds')
    for key,value in trial.get('controller_counter_deltas',{}).items():
        result['whole.controller.'+key]=value
    stream_runs=[r for tool in trial.get('tools',[]) for r in tool.get('result',{}).get('stream_runs',[])]
    result['tool.stream_invocations']=len(stream_runs) if stream_runs else None
    result['tool.stream_median_triad_GB_per_second']=charts.stats([r['kernels']['Triad']['average_GB_per_second'] for r in stream_runs])[0]
    result['tool.surrogate_queue_seconds_total']=sum(t.get('surrogate_queue_seconds',0) for t in trial.get('tools',[]))
    result['tool.duration_overrun_seconds_total']=sum(t.get('duration_overrun_seconds',0) for t in trial.get('tools',[]))
    for key in ['excessive_duration_overruns', 'excessive_start_lags', 'max_overrun_seconds', 'passed']:
        result['tool.timing_fidelity.'+key] = trial.get('timing_fidelity', {}).get(key)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('inputs',nargs='+',type=Path)
    p.add_argument('--output-dir',type=Path,required=True)
    args=p.parse_args()
    paths=sorted({p.resolve() for item in args.inputs for p in (item.rglob('trace-*.json') if item.is_dir() else [item])})
    if not paths:
        p.error('No trace-*.json results found')
    groups=load(paths)
    modes=sorted(groups,key=ORDER.get)
    args.output_dir.mkdir(parents=True,exist_ok=True)
    runs=[dict(label=f'Trace replay + HiCache ({mode})',color=charts.COLORS[i]) for i,mode in enumerate(modes)]
    samples=[]
    rows=[]
    for mode in modes:
        per_trial=[metrics(t) for t in groups[mode]]
        series={k:[t.get(k) for t in per_trial] for k in per_trial[0]}
        samples.append(series)
        for metric,values in series.items():
            median,low,high,n=charts.stats(values)
            rows.append(dict(controller_mode=mode,metric=metric,n=n,median=median,min=low,max=high))
    with (args.output_dir/'summary.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    renderer=shutil.which('rsvg-convert')
    if not renderer:
        raise RuntimeError('CSV saved; PNG rendering requires rsvg-convert')
    for metric,label,filename in [
        ('whole.seconds','Whole replay + drain + accounting (s)','whole-time.png'),
        ('whole.latency_median_seconds','Median request latency (s)','request-latency.png'),
        ('whole.server_prefix_hit_rate','Server cached prompt fraction','prefix-hit-rate.png'),
        ('whole.restore_GB','Measured host-to-GPU restore payload (GB)','host-restore.png')]:
        figure=charts.render_metric(runs,samples,metric,label,'summary')
        target=args.output_dir/filename
        subprocess.run([renderer,'-o',str(target)],input=figure.encode(),check=True)
        print('Saved',target)
    for mode in modes:
        trials=groups[mode]
        median=charts.stats([t['windows']['whole']['seconds'] for t in trials])[0]
        restores=[t['transfer_counter_deltas'].get('sglang:load_back_bytes_total') for t in trials]
        print(f'{mode}: {len(trials)} trials, median whole {median:.3f}s; host restore bytes {restores}')
        timing = [t['timing_fidelity'] for t in trials if 'timing_fidelity' in t]
        if timing:
            print(f'  Timing warnings: {sum(not t["passed"] for t in timing)}/{len(timing)} audited trials; do not treat functional completion as faithful trace timing.')
    print('Saved',args.output_dir/'summary.csv')
    print('Inspect tool and post_tool rows in summary.csv alongside whole-run time; cache hits alone do not prove host-cache reuse.')


if __name__=='__main__':
    main()
