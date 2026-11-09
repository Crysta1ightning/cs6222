"""Prepare, resume, audit and assemble the fifteen-policy experiment matrix."""
import argparse
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import random
import signal
import subprocess
import sys
import tempfile
import time

import matrix_summary
import policy_matrix
import prepare

HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def valid_cell(directory, key, trials):
    """Validate an isolated configuration, even if a later cell failed."""
    with tempfile.TemporaryDirectory(prefix='policy-audit-') as tmp:
        view = Path(tmp)
        (view/key).symlink_to(directory.resolve(), target_is_directory=True)
        policy_matrix.save(view/'status.json', dict(status='running', trials=trials))
        _, checked = matrix_summary.load(view, partial=True)
        for kind in ('delay', 'stream'):
            count = sum(row['kind'] == kind for row in checked)
            if count != trials:
                raise ValueError(f'{key}/{kind}: expected {trials} valid trials, found {count}')


def discover(inputs, trials):
    accepted, rejected = {}, []
    for config in policy_matrix.configurations():
        key = config['key']
        candidates = { (root/key).resolve() for root in inputs if (root/key).is_dir() }
        good = []
        for candidate in sorted(candidates):
            try:
                valid_cell(candidate, key, trials)
                good.append(candidate)
            except (ValueError, KeyError, OSError) as exc:
                rejected.append(dict(configuration=key, path=str(candidate), reason=str(exc)))
        if len(good) > 1:
            raise ValueError(f'Ambiguous duplicate valid attempts for {key}: {good}; select input roots explicitly')
        if good:
            accepted[key] = good[0]
    return accepted, rejected


def assemble(output, accepted, trials):
    output.mkdir(parents=True, exist_ok=True)
    for config in policy_matrix.configurations():
        if (output/config['key']).exists() and config['key'] not in accepted:
            raise ValueError(f'Stale checkpoint {config["key"]}; use a fresh assembly directory')
    for key, source in accepted.items():
        target = output/key
        if target.is_symlink() and target.resolve() != source:
            raise ValueError(f'Refusing to replace checkpoint {target}')
        if not target.exists():
            target.symlink_to(source, target_is_directory=True)
    status = dict(status='validating', trials=trials,
                  completed=list(accepted), origins={k:str(v) for k,v in accepted.items()})
    policy_matrix.save(output/'status.json', status)
    # This also rejects cross-allocation, source, plan and request-shape mixing.
    try:
        if accepted:
            _,checked=matrix_summary.load(output, partial=True)
            for key in accepted:
                for kind in ('delay','stream'):
                    if sum(r['configuration']==key and r['kind']==kind for r in checked)!=trials:
                        raise ValueError(f'Incomplete assembled cell {key}/{kind}')
    except BaseException as exc:
        status.update(status='invalid',error=str(exc));policy_matrix.save(output/'status.json',status)
        raise
    status['status']='complete' if len(accepted)==15 else 'partial'
    policy_matrix.save(output/'status.json',status)
    return status


def prepare_plans(source, output):
    output.mkdir(parents=True, exist_ok=True)
    prepare.download(source)  # Verify an existing release before using it.
    baseline = prepare.prepare(source, sessions=4, rounds=4, min_rounds=4,
        token_scale=.08, time_scale=1, output_cap=32, max_tool_seconds=0,
        max_context=4096, human_wait_cap=5, min_tool_seconds=2, max_parallel_tools=1)
    larger = copy.deepcopy(baseline)
    larger['transforms'].update(token_scale=.32, max_context=16384,
        output_lengths='held at baseline values: 8% scaling, cap 32')
    larger['scale_intervention'] = ('Same sessions, output lengths and all tool/user timings; '
        '4x input/prefix token lengths; proportionally larger GPU pool/reserve/pending bounds; host cache fixed.')
    for session in larger['sessions']:
        for step in session['steps']:
            original = step['original']
            total = max(1, round(original['input_tokens']*.32))
            prefix = min(total, round(original['prefix_tokens']*.32))
            step.update(prefix_len=prefix, input_len=total-prefix)
            if total+step['output_len'] > 16384:
                raise ValueError('Scaled round exceeds context limit; no round was silently dropped')
    for name, plan in [('baseline', baseline), ('token4x', larger)]:
        path = output/f'{name}.json'
        if path.exists() and read(path) != plan:
            raise ValueError(f'Refusing to overwrite different plan {path}')
        policy_matrix.save(path, plan)


def run(args):
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('Enter the prepared compute allocation before running')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    lock = (args.output_dir/'run.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    # Separate studies must also avoid competing for the same server port.
    host_lock = Path(tempfile.gettempdir())/f'policy-server-{os.getuid()}.lock'
    resource_lock = host_lock.open('w')
    fcntl.flock(resource_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    binary = args.stream_worker.resolve()
    if not binary.is_file():raise ValueError('Build the STREAM worker first')
    plan = read(args.plan)
    context, pool, reserve, pending = ((4096,4096,2048,4096) if args.profile=='baseline'
                                      else (16384,16384,8192,16384))
    if plan['transforms']['max_context'] != context:
        raise ValueError('Plan context does not match selected profile')
    spec = dict(plan_sha256=digest(args.plan), binary_sha256=digest(binary),
        profile=args.profile, trials=args.trials, threads=args.threads, workers=args.workers,
        max_seconds=args.max_seconds, job_id=os.environ['SLURM_JOB_ID'],
        placement={k:os.environ.get(k) for k in ['EXP_SERVER_CPUS','EXP_TOOL_CPUS','EXP_NUMA_NODE','EXP_GPU_UUID','CUDA_VISIBLE_DEVICES']},
        sources={str(p.relative_to(HERE.parent)):digest(p) for p in list(HERE.glob('*.py')) +
                 [HERE.parent/'memory-experiment/launch-stream-aware.sh', HERE.parent/'memory-experiment/stream-worker.c',
                  HERE.parent/'memory-experiment/activity.py', HERE.parent/'memory-experiment/run.py',
                  HERE.parent/'memory-experiment/compare.py',
                  HERE.parent/'backup-policy/persistent_stream.py']},
        controller_sources={str(p):digest(p) for p in [HERE.parents[1]/'sglang/python/sglang/srt'/name for name in
            ['managers/cache_controller.py','mem_cache/unified_radix_cache.py','mem_cache/stream_activity.py']]})
    spec_file=args.output_dir/'run-spec.json'
    if spec_file.exists():
        if not args.resume:raise ValueError('Output exists; use --resume to validate and reuse checkpoints')
        if read(spec_file)!=spec:raise ValueError('Resume settings/sources/allocation changed; use a separate output directory')
    policy_matrix.save(spec_file,spec)
    attempts=args.output_dir/'attempts';attempts.mkdir(exist_ok=True)
    candidates=[p for p in attempts.iterdir() if p.is_dir() and (p/'status.json').exists() and read(p/'status.json').get('status')=='complete']
    accepted,rejected=discover(candidates,args.trials)
    assembled=args.output_dir/'matrix'
    assemble(assembled,accepted,args.trials)
    order=policy_matrix.configurations();random.Random(6222).shuffle(order)
    previous=read(args.output_dir/'progress.json') if (args.output_dir/'progress.json').exists() else {}
    state=dict(status='running',completed=list(accepted),commands=previous.get('commands',[]),rejected=rejected,plan=str(args.plan.resolve()))
    child=None
    def interrupted(signum,frame):raise KeyboardInterrupt(f'Interrupted by signal {signum}')
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,interrupted)
    try:
        for config in order:
            key=config['key']
            if key in accepted:continue
            attempt=attempts/f'{key}-{time.time_ns()}'
            command=[sys.executable,'-u',str(HERE/'policy_matrix.py'),'--plan',str(args.plan.resolve()),
                '--output-dir',str(attempt.resolve()),'--stream-worker',str(binary),'--configs',key,
                '--trials',str(args.trials),'--threads',str(args.threads),'--workers',str(args.workers),
                '--gpu-cache-tokens',str(pool),'--reserve-tokens',str(reserve),
                '--max-pending-tokens',str(pending),'--max-seconds',str(args.max_seconds)]
            state.update(active=key,heartbeat=time.time());state['commands'].append(command)
            policy_matrix.save(args.output_dir/'progress.json',state)
            env=dict(os.environ,EXP_CONTEXT_LENGTH=str(context),EXP_ATTENTION_BACKEND='triton',
                     EXP_SAMPLING_BACKEND='pytorch',EXP_RANDOM_SEED='6222')
            print(f'RUN {key}: {len(accepted)}/15 complete',flush=True)
            with (args.output_dir/'run.log').open('a') as log:
                child=subprocess.Popen(command,env=env,stdout=log,stderr=subprocess.STDOUT)
                code=child.wait();child=None
            if code:raise RuntimeError(f'Failed {key}; retain {attempt}, inspect run.log, then --resume')
            valid_cell(attempt/key,key,args.trials)
            accepted[key]=(attempt/key).resolve()
            assemble(assembled,accepted,args.trials)
            state['completed']=list(accepted)
            policy_matrix.save(args.output_dir/'progress.json',state)
        state['status']='complete'
    except BaseException as exc:
        state.update(status='stopped',error=str(exc) or type(exc).__name__)
        if child is not None:
            child.terminate()
            try:child.wait(timeout=30)
            except subprocess.TimeoutExpired:child.kill();child.wait()
        raise
    finally:
        policy_matrix.save(args.output_dir/'progress.json',state)
    subprocess.run([sys.executable,str(HERE/'matrix_summary.py'),str(assembled)],check=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('prepare');p.add_argument('--source',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True)
    for name in ['audit','assemble']:
        p=sub.add_parser(name);p.add_argument('inputs',nargs='+',type=Path);p.add_argument('--trials',type=int,default=3)
        p.add_argument('--output-dir',type=Path,required=True)
    p=sub.add_parser('run')
    p.add_argument('--plan',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--profile',choices=['baseline','token4x'],default='baseline');p.add_argument('--resume',action='store_true')
    p.add_argument('--stream-worker',type=Path,required=True);p.add_argument('--trials',type=int,default=3)
    p.add_argument('--threads',type=int,default=3);p.add_argument('--workers',type=int,default=4);p.add_argument('--max-seconds',type=int,default=1200)
    args=parser.parse_args()
    if args.command=='prepare':prepare_plans(args.source,args.output_dir)
    elif args.command=='run':run(args)
    else:
        accepted,rejected=discover(args.inputs,args.trials)
        args.output_dir.mkdir(parents=True,exist_ok=True)
        policy_matrix.save(args.output_dir/'audit.json',dict(accepted={k:str(v) for k,v in accepted.items()},rejected=rejected,
            missing=[c['key'] for c in policy_matrix.configurations() if c['key'] not in accepted]))
        if args.command=='assemble':assemble(args.output_dir,accepted,args.trials)
        print(f'{len(accepted)}/15 validated configurations; audit saved to {args.output_dir}/audit.json')


if __name__=='__main__':main()
