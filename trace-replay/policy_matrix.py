"""Run the 15 backup-policy configurations inside one prepared allocation.

Owns each server process group. Never requests/cancels Slurm jobs or kills
unrelated servers. Results, journals, failure state and exact commands persist.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import random
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE=Path(__file__).resolve().parent
PROJECT=HERE.parents[1]
BASES=['write_through','write_through_selective','write_back']
VARIANTS=[('normal','all'),('skip','all'),('defer','all'),('defer','victim'),('defer','through_victim')]


def configurations():
    return [dict(base=base,mode=mode,queue=queue,key=f'{base}--{mode}--{queue}')
            for base in BASES for mode,queue in VARIANTS]


def save(path,data):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data,indent=2)+'\n')
    temporary.replace(path)


def parse_cpus(value):
    result=set()
    for part in value.split(','):
        ends=part.split('-');result.update(range(int(ends[0]),int(ends[-1])+1))
    return result


def validate_placement(args):
    server=parse_cpus(os.environ['EXP_SERVER_CPUS']);tools=parse_cpus(os.environ['EXP_TOOL_CPUS'])
    if not server or not tools or server & tools or not server|tools <= os.sched_getaffinity(0):
        raise RuntimeError('Require disjoint, allocated server/tool CPUs')
    if args.workers*args.threads>len(tools):
        raise RuntimeError('Not enough tool cores for the requested disjoint workers')
    identities=set()
    for cpu in server|tools:
        path=Path(f'/sys/devices/system/cpu/cpu{cpu}')
        if not (path/f'node{os.environ["EXP_NUMA_NODE"]}').exists():
            raise RuntimeError('All cores must share the chosen NUMA node')
        identity=tuple((path/'topology'/name).read_text().strip() for name in ['physical_package_id','core_id'])
        if identity in identities:raise RuntimeError('SMT siblings are not separate physical cores')
        identities.add(identity)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--trials',type=int,default=3)
    p.add_argument('--threads',type=int,default=3)
    p.add_argument('--workers',type=int,default=4)
    p.add_argument('--stream-worker',type=Path,default=HERE.parent/'memory-experiment/stream-worker')
    p.add_argument('--gpu-cache-tokens',type=int,default=4096)
    p.add_argument('--reserve-tokens',type=int,default=2048)
    p.add_argument('--max-pending-tokens',type=int,default=4096)
    p.add_argument('--max-seconds',type=int,default=1200)
    p.add_argument('--configs',help='Comma-separated configuration keys; default all 15')
    p.add_argument('--skip-warmup-trial',action='store_true',help='Pilot only: omit the excluded full delay replay')
    p.add_argument('--no-strict-timing',action='store_true',help='Diagnostic only; record timing failures rather than reject')
    args=p.parse_args()
    if not os.environ.get('SLURM_JOB_ID'):p.error('Enter the prepared compute allocation')
    if not 1<=args.trials<=10:p.error('trials must be 1..10')
    if not 0<=args.reserve_tokens<args.gpu_cache_tokens:p.error('reserve must be smaller than the GPU KV pool')
    if not 1<=args.max_pending_tokens<=args.gpu_cache_tokens:p.error('pending bound must fit in the GPU KV pool')
    validate_placement(args)
    args.output_dir=args.output_dir.resolve();args.output_dir.mkdir(parents=True,exist_ok=True)
    args.plan=args.plan.resolve()
    copied=args.output_dir/'plan.json'
    if copied!=args.plan:copied.write_bytes(args.plan.read_bytes())
    source_files=list(HERE.glob('*.py'))+[
        HERE.parent/'memory-experiment'/name for name in
        ['launch-stream-aware.sh','activity.py','stream-worker.c']]
    source_files += [HERE.parent/'backup-policy/persistent_stream.py']
    source_files += [PROJECT/'sglang/python/sglang/srt'/name for name in
        ['managers/cache_controller.py','mem_cache/unified_radix_cache.py','mem_cache/stream_activity.py']]
    source_manifest={}
    for source in source_files:
        relative=source.relative_to(PROJECT)
        target=args.output_dir/'sources'/relative
        target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(source.read_bytes())
        source_manifest[str(relative)]=hashlib.sha256(source.read_bytes()).hexdigest()
    save(args.output_dir/'source-manifest.json',source_manifest)
    for name,path in [('scripts',HERE.parent),('sglang',PROJECT/'sglang')]:
        (args.output_dir/f'{name}-working.diff').write_bytes(subprocess.check_output(['git','-C',str(path),'diff','--binary']))
    configs=configurations()
    if args.configs:
        keys=args.configs.split(',');configs=[c for c in configs if c['key'] in keys]
        if len(configs)!=len(keys):p.error('Unknown or repeated configuration key')
    random.Random(6222).shuffle(configs)
    manifest=dict(job_id=os.environ['SLURM_JOB_ID'],hostname=socket.gethostname(),
        settings={k:v for k,v in os.environ.items() if k.startswith('EXP_')},
        trials=args.trials,threads_per_worker=args.threads,workers=args.workers,
        gpu_cache_tokens=args.gpu_cache_tokens,
        controller_bounds=dict(reserve_tokens=args.reserve_tokens,max_pending_tokens=args.max_pending_tokens,max_defer_seconds=2),
        strict_timing=not args.no_strict_timing,configuration_order=configs,
        started=dt.datetime.now(dt.timezone.utc).isoformat(),completed=[],commands=[],status='running')
    status=args.output_dir/'status.json';save(status,manifest)
    server=None;log=None;journal=None;archive=None

    def stop_server():
        nonlocal server,log
        if server is not None:
            try:os.killpg(server.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                try:os.killpg(server.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                server.wait()
            server=None
        if log is not None:log.close();log=None
        if journal and journal.exists():archive.write_bytes(journal.read_bytes())

    def interrupt(signum,frame):raise KeyboardInterrupt(f'Interrupted by signal {signum}')
    signal.signal(signal.SIGINT,interrupt);signal.signal(signal.SIGTERM,interrupt)
    try:
        for index,c in enumerate(configs,1):
            directory=args.output_dir/c['key'];directory.mkdir(exist_ok=True)
            if list(directory.glob('*/trace-*.json')):raise RuntimeError('Use a fresh output directory; refusing to mix results')
            manifest['active_configuration']=c;save(status,manifest)
            print(f'CONFIG {index}/{len(configs)}: {c["key"]}',flush=True)
            try:
                with socket.create_connection(('127.0.0.1',30000),timeout=1):
                    raise RuntimeError('Port 30000 already occupied; will not stop that server')
            except ConnectionRefusedError:pass
            local=Path(tempfile.mkdtemp(prefix='matrix-',dir=os.environ['STREAM_CONTROL_ROOT']))
            journal=local/'controller.jsonl';archive=directory/'controller.jsonl'
            env=dict(os.environ,EXP_HICACHE_WRITE_POLICY=c['base'],EXP_QUEUE_POLICY=c['queue'],
                EXP_MAX_TOTAL_TOKENS=str(args.gpu_cache_tokens),
                STREAM_CONTROL_ROOT=str(local),STREAM_RESULT_ROOT=str(directory),
                SGLANG_STREAM_CONTROLLER_LOG=str(journal),OMP_PROC_BIND='FALSE',
                OMP_NUM_THREADS='1',
                SGLANG_STREAM_RESERVE_TOKENS=str(args.reserve_tokens),SGLANG_STREAM_MAX_PENDING_TOKENS=str(args.max_pending_tokens),
                SGLANG_STREAM_MAX_DEFER_SECONDS='2')
            launch=['bash',str(HERE.parent/'memory-experiment/launch-stream-aware.sh'),c['mode']]
            log=(directory/'server.log').open('w')
            server=subprocess.Popen(launch,cwd=PROJECT,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            deadline=time.monotonic()+600
            while True:
                if server.poll() is not None:raise RuntimeError(f'Server exited; inspect {directory}/server.log')
                try:
                    with urllib.request.urlopen('http://127.0.0.1:30000/health',timeout=2) as response:
                        if response.status==200:break
                except (OSError,urllib.error.HTTPError):pass
                if time.monotonic()>=deadline:raise TimeoutError('Server readiness timeout')
                time.sleep(2)
            phases=[]
            if not args.skip_warmup_trial:phases.append(('warmup','delay',1))
            phases.extend([('delay','delay',args.trials),('stream','stream',args.trials)])
            for phase,kind,trials in phases:
                command=['numactl','--physcpubind='+env['EXP_TOOL_CPUS'],'--membind='+env['EXP_NUMA_NODE'],
                    sys.executable,str(HERE/'replay.py'),'--plan',str(args.plan),
                    '--controller-mode',c['mode'],'--write-policy',c['base'],'--queue-policy',c['queue'],
                    '--control-socket',str(local/(c['mode']+'.sock')),'--tool-workload',kind,
                    '--tool-runtime','prepared','--threads',str(args.threads),
                    '--stream-worker',str(args.stream_worker.resolve()),
                    '--trials',str(trials),'--max-active-sessions','4',
                    '--max-stream-processes',str(args.workers),'--max-seconds',str(args.max_seconds),
                    '--output-dir',str(directory/phase)]
                if phase!='warmup' and not args.no_strict_timing:command.append('--require-timing-fidelity')
                manifest['commands'].append(dict(configuration=c['key'],phase=phase,command=command))
                save(status,manifest)
                print(f'  PHASE {phase}: {trials} trials',flush=True)
                subprocess.run(command,cwd=PROJECT,env=env,check=True)
            stop_server()
            manifest['completed'].append(c['key']);save(status,manifest)
        manifest.update(status='complete',ended=dt.datetime.now(dt.timezone.utc).isoformat())
    except BaseException as exc:
        manifest.update(status='failed',error=str(exc) or type(exc).__name__)
        raise
    finally:
        stop_server();save(status,manifest)
    print(f'All {len(configs)} configurations completed: {args.output_dir}',flush=True)


if __name__=='__main__':main()
