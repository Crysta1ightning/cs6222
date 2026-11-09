"""Closed-loop TraceLab shape replay against an already running SGLang server."""
import argparse
import concurrent.futures
import hashlib
import json
import math
import os
from pathlib import Path
import random
import signal
import socket
import statistics
import sys
import threading
import time

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS / 'memory-experiment'))
from activity import ActivityClient, run_command
from run import Server, command_output, counter_delta, source_version
from prepared_tools import PreparedTools
from timing import plan_timing, timing_fidelity


def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def validate_plan(plan):
    if plan.get('schema') != 'tracelab-plan-v1' or not 1 <= len(plan.get('sessions', [])) <= 64:
        raise ValueError('Expected bounded tracelab-plan-v1 plan')
    seen = set()
    for session in plan['sessions']:
        if session['session_id'] in seen:
            raise ValueError('Duplicate session')
        seen.add(session['session_id'])
        if not session['steps']:
            raise ValueError('Empty session')
        for step in session['steps']:
            for field in ['prefix_len', 'input_len', 'output_len']:
                value = step[field]
                if type(value) is not int or value < 0:
                    raise ValueError(f'Invalid {field}')
            if step['output_len'] < 1 or step['prefix_len'] + step['input_len'] < 1:
                raise ValueError('Empty prompt or generation')
            if len(step['tools']) > 32:
                raise ValueError('At most 32 overlapping tools per round are supported')
            for seconds in [session['arrival_seconds'], step['human_wait_after_seconds']] + [v for t in step['tools'] for v in [t['offset_seconds'],t['duration_seconds']]]:
                if not isinstance(seconds, (int,float)) or not math.isfinite(seconds) or not 0 <= seconds <= 3600:
                    raise ValueError('Replay times must be finite and within [0,3600]')


class PromptBuilder:
    """Preserve exact history IDs, filling unobserved history deterministically."""
    def __init__(self, pool, seed):
        self.pool = pool
        self.random = random.Random(seed)
        self.context = []

    def fresh(self, count):
        return self.random.choices(self.pool, k=count)

    def build(self, step):
        prefix = step['prefix_len']
        reusable = min(prefix, len(self.context))
        history = self.context[:prefix]
        missing = prefix - len(history)
        return history + self.fresh(missing + step['input_len']), reusable, missing

    def commit(self, ids, output_ids):
        self.context = ids + output_ids


def generate(url, ids, output_len, timeout):
    import requests
    start = time.monotonic_ns()
    response = requests.post(url.rstrip('/') + '/generate', json={
        'input_ids': ids, 'sampling_params': {'max_new_tokens': output_len,
                                            'temperature': 0, 'ignore_eos': True}}, timeout=timeout)
    response.raise_for_status()
    body = response.json()
    end = time.monotonic_ns()
    meta, output = body.get('meta_info', {}), body.get('output_ids')
    if not isinstance(output, list) or len(output) != output_len or any(type(t) is not int or t < 0 for t in output):
        raise RuntimeError('SGLang must return exact output_ids and complete the requested token count')
    if meta.get('completion_tokens') != output_len or meta.get('prompt_tokens') != len(ids):
        raise RuntimeError('Unexpected SGLang prompt/completion accounting')
    cached = meta.get('cached_tokens')
    if cached is not None and (not isinstance(cached, (int,float)) or not 0 <= cached <= len(ids)):
        raise RuntimeError('Invalid cached_tokens from server')
    record = dict(start_ns=start, end_ns=end, latency_seconds=(end-start)/1e9,
                  prompt_tokens=len(ids), output_tokens=output_len,
                  cached_tokens=cached, cached_tokens_details=meta.get('cached_tokens_details'),
                  prompt_sha256=sha(ids), output_sha256=sha(output), meta_info=meta,
                  ttft_seconds=None)
    return output, record


def union(intervals):
    result = []
    for start, end in sorted(intervals):
        if end < start:
            raise ValueError('Negative interval')
        if result and start <= result[-1][1]:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start,end])
    return result


def window(intervals, requests, events):
    intervals = union(intervals)
    seconds = sum(b-a for a,b in intervals)/1e9
    selected = [r for r in requests if any(a <= r['start_ns'] and r['end_ns'] <= b for a,b in intervals)]
    scoped = [e for e in (events or []) if any(a <= e['monotonic_ns'] < b for a,b in intervals)]
    submitted = sum(e['bytes'] for e in scoped if e['event'] == 'BACKUP_SUBMITTED') if events is not None else None
    completed = sum(e['bytes'] for e in scoped if e['event'] == 'BACKUP_COMPLETED') if events is not None else None
    return dict(intervals_ns=intervals, seconds=seconds, fully_contained_requests=len(selected),
                requests_per_second=len(selected)/seconds if seconds else None,
                backup_submitted_bytes=submitted, backup_completed_bytes=completed,
                backup_submitted_GB_per_second=submitted/seconds/1e9 if submitted is not None and seconds else None,
                controller_event_counts=dict(__import__('collections').Counter(e['event'] for e in scoped)),
                cached_prompt_fraction=(sum(r['cached_tokens'] for r in selected)/sum(r['prompt_tokens'] for r in selected)
                                        if selected and all(r['cached_tokens'] is not None for r in selected) else None),
                latency_median_seconds=statistics.median(r['latency_seconds'] for r in selected) if selected else None)


class Replay:
    def __init__(self, args, plan, pool, prepared=None, controller_pid=None):
        self.args, self.plan, self.pool = args, plan, pool
        self.cancel = threading.Event()
        self.records, self.tools, self.clients = [], [], []
        self.builders = {}
        self.prepared = prepared
        self.controller_pid = controller_pid
        self.tool_executor = None
        self.stream_slots = threading.BoundedSemaphore(args.max_stream_processes)
        self.epoch = time.monotonic()
        self.deadline = self.epoch + args.max_seconds

    def wait_until(self, deadline):
        while time.monotonic() < deadline:
            self.check()
            self.cancel.wait(min(0.1, deadline-time.monotonic()))
        self.check()

    def check(self):
        if self.cancel.is_set() or time.monotonic() > self.deadline:
            self.cancel.set()
            raise RuntimeError('Replay canceled or exceeded --max-seconds')

    def tool(self, tool, epoch, sid, round_index):
        record = dict(session_id=sid, round_index=round_index, trace_tool=tool,
                      surrogate=self.args.tool_workload,
                      planned_start_ns=int((epoch+tool['offset_seconds'])*1e9))
        self.tools.append(record)
        self.wait_until(epoch + tool['offset_seconds'])
        queued=time.monotonic()
        acquired=False
        try:
            if self.args.tool_workload=='stream':
                while not self.stream_slots.acquire(timeout=0.1):
                    self.check()
                acquired=True
            record['surrogate_queue_seconds']=time.monotonic()-queued
            return self.execute_tool(tool,record)
        except BaseException as exc:
            record['error']=str(exc) or type(exc).__name__
            self.cancel.set()
            raise
        finally:
            if acquired:
                self.stream_slots.release()

    def execute_tool(self,tool,record):
        client = ActivityClient(self.args.control_socket, self.args.controller_mode,
                                timeout=self.args.control_timeout, expected_pid=self.controller_pid)
        self.clients.append(client)
        if self.prepared is not None:
            try:
                record['result'] = self.prepared.execute(tool, record, client, self.cancel,
                    min(self.args.tool_timeout, max(0.001, self.deadline-time.monotonic())))
                record['actual_seconds'] = (record['end_ns']-record['start_ns'])/1e9
                record['duration_overrun_seconds'] = max(0, record['actual_seconds']-tool['duration_seconds'])
                record['launch_lag_seconds'] = (record['start_ns']-record['planned_start_ns'])/1e9
                return
            finally:
                record['activity_events'] = client.events
        command = [sys.executable, str(HERE/'tool.py'), '--kind', self.args.tool_workload,
                   '--seconds', str(tool['duration_seconds'])]
        if self.args.tool_workload == 'stream':
            command += ['--binary', str(self.args.stream_binary.resolve())]
        env = dict(os.environ, OMP_NUM_THREADS=str(self.args.threads), OMP_DYNAMIC='FALSE',
                   OMP_PROC_BIND='close', OMP_PLACES='cores')
        try:
            result = run_command(command, env, client=client, timeout=min(self.args.tool_timeout, max(0.01,self.deadline-time.monotonic())),
                                 record=record, cancel=self.cancel)
            record['stderr']=result.stderr[-65536:]
            record['launch_lag_seconds']=(record['start_ns']-record['planned_start_ns'])/1e9
            if result.returncode:
                record['stdout']=result.stdout[-65536:]
            result.check_returncode()
            record['result'] = json.loads(result.stdout)
            record['actual_seconds'] = (record['end_ns']-record['start_ns'])/1e9
            record['duration_overrun_seconds'] = max(0, record['actual_seconds']-tool['duration_seconds'])
        except BaseException as exc:
            record['error'] = str(exc)
            self.cancel.set()
            raise
        finally:
            record['activity_events'] = client.events

    def request(self, sid, step, builder, phase='trace'):
        self.check()
        ids, reusable, missing = builder.build(step)
        output, record = generate(self.args.url, ids, step['output_len'],
                                  min(self.args.request_timeout,max(0.01,self.deadline-time.monotonic())))
        builder.commit(ids, output)
        record.update(session_id=sid, round_index=step['round_index'], phase=phase,
                      planned_prefix_tokens=step['prefix_len'], reusable_history_tokens=reusable,
                      synthetic_history_fill_tokens=missing)
        self.records.append(record)

    def session(self, session):
        sid = session['session_id']
        builder = PromptBuilder(self.pool, sha([self.plan['transforms']['seed'],sid]))
        self.builders[sid] = builder
        self.wait_until(self.epoch+session['arrival_seconds'])
        try:
            for step in session['steps']:
                self.request(sid, step, builder)
                epoch = time.monotonic()
                if step['tools']:
                    jobs = [self.tool_executor.submit(self.tool,tool,epoch,sid,step['round_index']) for tool in step['tools']]
                    for job in concurrent.futures.as_completed(jobs):
                        job.result()
                self.wait_until(time.monotonic()+step['human_wait_after_seconds'])
        except BaseException:
            self.cancel.set()
            raise

    def run(self):
        # Reuse tool threads across rounds; sleeping surrogates need no process.
        with concurrent.futures.ThreadPoolExecutor(max_workers=128) as executor:
            self.tool_executor = executor
            return self._run()

    def _run(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.args.max_active_sessions) as executor:
            jobs = [executor.submit(self.session,s) for s in self.plan['sessions']]
            for job in concurrent.futures.as_completed(jobs):
                job.result()
        trace_end = time.monotonic_ns()
        # Same deterministic, fixed-count inference tail in all modes, after all
        # tools end; include draining and this work in whole-run comparisons.
        for session in self.plan['sessions']:
            builder = self.builders[session['session_id']]
            for index in range(self.args.tail_requests):
                step = dict(prefix_len=min(len(builder.context),self.plan['transforms']['max_context']-2),
                            input_len=1, output_len=1, round_index=f'tail-{index}')
                self.request(session['session_id'], step, builder, phase='post_tool_tail')
        return trace_end


def read_events(path, offset, start):
    with Path(path).open() as f:
        f.seek(offset)
        lines=f.readlines()
    events=[]
    for line in lines:
        if not line.endswith('\n'):
            raise RuntimeError('Incomplete controller event journal')
        event=json.loads(line)
        if event['monotonic_ns'] >= start:
            events.append(event)
    return events


def summarize_trial(trial):
    records=trial['requests']
    prompt=sum(r['prompt_tokens'] for r in records)
    cached=[r['cached_tokens'] for r in records]
    trial['server_prefix_hit_rate']=sum(cached)/prompt if cached and all(v is not None for v in cached) else None
    trial['cached_tokens']=sum(cached) if cached and all(v is not None for v in cached) else None
    trial['output_tokens']=sum(r['output_tokens'] for r in records)
    trial['host_restore_observed']=(trial['transfer_counter_deltas'].get('sglang:load_back_bytes_total') or 0)>0
    trial['host_restore_measurement_available']=trial['transfer_counter_deltas'].get('sglang:load_back_bytes_total') is not None


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan',type=Path,required=True)
    p.add_argument('--controller-mode',choices=['normal','defer','skip'],required=True)
    p.add_argument('--write-policy',choices=['write_through','write_through_selective','write_back'],default='write_through')
    p.add_argument('--queue-policy',choices=['all','victim','through_victim'],default='all')
    p.add_argument('--control-socket',type=Path)
    p.add_argument('--control-timeout',type=float,default=10,
                   help='Bounded scheduler acknowledgment wait; must be below the tool lease')
    p.add_argument('--url',default='http://127.0.0.1:30000')
    p.add_argument('--model',default='Qwen/Qwen3-0.6B')
    p.add_argument('--tool-workload',choices=['delay','stream'],default='delay')
    p.add_argument('--stream-binary',type=Path,default=SCRIPTS/'memory-experiment/stream-5')
    p.add_argument('--tool-runtime', choices=['prepared', 'subprocess'], default='prepared',
                   help='prepared reuses workers/buffers; subprocess reproduces the legacy harness')
    p.add_argument('--stream-worker', type=Path, default=SCRIPTS/'memory-experiment/stream-worker')
    p.add_argument('--require-timing-fidelity', action='store_true',
                   help='Fail instead of accepting stretched/queued tool intervals')
    p.add_argument('--threads',type=int,default=1)
    p.add_argument('--max-stream-processes',type=int,default=1,
                   help='bound simultaneous STREAM processes; queue delays are recorded (stress intervention only)')
    p.add_argument('--max-active-sessions',type=int,default=4)
    p.add_argument('--trials',type=int,default=1)
    p.add_argument('--tail-requests',type=int,default=1)
    p.add_argument('--request-timeout',type=float,default=120)
    p.add_argument('--tool-timeout',type=float,default=300)
    p.add_argument('--drain-timeout',type=float,default=30)
    p.add_argument('--max-seconds',type=float,default=600)
    p.add_argument('--output-dir',type=Path,default=SCRIPTS/'results/tracelab')
    p.add_argument('--dry-run',action='store_true')
    args=p.parse_args()
    plan=json.loads(args.plan.read_text())
    validate_plan(plan)
    if not 1 <= args.trials <= 100 or not 1 <= args.max_active_sessions <= 8 or not 1 <= args.tail_requests <= 100 or not 1 <= args.threads <= len(os.sched_getaffinity(0)) or not 1 <= args.max_stream_processes <= 4:
        p.error('invalid trials, concurrency, tail count or thread count')
    maximum_tools=sorted([max(len(t['tools']) for t in s['steps']) for s in plan['sessions']], reverse=True)
    if sum(maximum_tools[:args.max_active_sessions])>128:
        p.error('Plan may exceed controller limit of 128 overlapping tool IDs; reduce concurrency')
    if any(not math.isfinite(t) or not 0 < t <= 3600 for t in [args.request_timeout,args.tool_timeout,args.drain_timeout,args.max_seconds]):
        p.error('timeouts must be finite and in (0,3600]')
    if args.tool_workload=='stream' and not args.dry_run:
        binary = args.stream_worker if args.tool_runtime == 'prepared' else args.stream_binary
        if not binary.is_file():
            p.error('Build STREAM: bash scripts/memory-experiment/build-worker.sh (prepared) or build.sh 5 (legacy)')
    plan_hash=sha(plan)
    if args.dry_run:
        print(json.dumps(dict(plan_sha256=plan_hash,sessions=len(plan['sessions']),
              rounds=sum(len(s['steps']) for s in plan['sessions']),
              tools=sum(len(t['tools']) for s in plan['sessions'] for t in s['steps']),
              max_context=max(t['prefix_len']+t['input_len']+t['output_len'] for s in plan['sessions'] for t in s['steps']),
              tool_runtime=args.tool_runtime,timing=plan_timing(plan),
              transforms=plan['transforms']),indent=2))
        return
    if not args.control_socket:
        p.error('--control-socket required except for --dry-run')
    server=Server(args.url)
    info=server.get('/server_info').json()
    settings=info.get('server_args',info)
    if not settings.get('enable_hierarchical_cache') or settings.get('hicache_write_policy')!=args.write_policy or not settings.get('enable_metrics'):
        raise RuntimeError('Require HiCache with the requested write policy and metrics')
    if settings.get('model_path') != args.model:
        raise RuntimeError('Server model_path differs from --model')
    limit=settings.get('context_length')
    if not isinstance(limit,int) or any(t['prefix_len']+t['input_len']+t['output_len']>limit for s in plan['sessions'] for t in s['steps']):
        raise RuntimeError('Cannot verify all rounds fit server context length')
    if not 0 < args.control_timeout < 15:
        p.error('--control-timeout must be positive and below the 15-second tool lease')
    client=ActivityClient(args.control_socket,args.controller_mode,timeout=args.control_timeout)
    if client.initial.get('queue_policy', 'all') != args.queue_policy:
        raise RuntimeError('Controller queue policy differs from the requested configuration')
    if client.initial.get('base_policy', 'write_through') != args.write_policy:
        raise RuntimeError('Controller base policy differs from the server/replay configuration')
    if client.initial['active_tool_ids'] or not client.initial.get('log_path'):
        raise RuntimeError('Use isolated controller with an event journal and no active tools')
    from transformers import AutoTokenizer
    tokenizer=AutoTokenizer.from_pretrained(args.model,local_files_only=True)
    pool=sorted(set(tokenizer.get_vocab().values())-set(tokenizer.all_special_ids))
    if len(pool)<256:
        raise RuntimeError('Tokenizer vocabulary unexpectedly small')
    args.output_dir.mkdir(parents=True,exist_ok=True)
    output=args.output_dir/f'trace-{args.controller_mode}-{args.tool_workload}-{time.time_ns()}.json'
    data=dict(schema='tracelab-replay-v1',plan=plan,plan_sha256=plan_hash,controller_mode=args.controller_mode,
              write_policy=args.write_policy,queue_policy=args.queue_policy,tool_workload=args.tool_workload,
              replay_config={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()},
              hostname=socket.gethostname(),job_id=os.environ.get('SLURM_JOB_ID'),
              cpu_affinity=sorted(os.sched_getaffinity(0)),numa=command_output(['numactl','--show']),
              gpu_topology=command_output(['nvidia-smi','topo','-m']),
              source_versions={'scripts':source_version(SCRIPTS),'sglang':source_version(SCRIPTS.parent/'sglang')},
              runtime_source_sha256={str(path.relative_to(SCRIPTS)): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in [HERE/'replay.py', HERE/'tool.py', HERE/'prepared_tools.py', HERE/'timing.py',
                               SCRIPTS/'memory-experiment/activity.py', SCRIPTS/'backup-policy/persistent_stream.py',
                               SCRIPTS/'memory-experiment/stream-worker.c']},
              token_pool_sha256=sha(pool),server_info=info,controller_initial=client.initial,
              stream_binary_sha256=hashlib.sha256((args.stream_worker if args.tool_runtime=='prepared' else args.stream_binary).read_bytes()).hexdigest() if args.tool_workload=='stream' else None,
              trials=[],limitations=[
                  'Sanitized trace shapes with synthetic token IDs; no original prompt, tool execution, semantic correctness, or original cache identity.',
                  'Reported prefix counts model potential reuse; actual hits and H2D restores must be measured independently.',
                  'Closed-loop local generation replaces recorded generation time; tools launch after full response with within-round relative offsets.',
                  'Delay surrogate consumes no intended bandwidth. STREAM is a synthetic stress intervention, not recorded tool bandwidth.',
                  'Concurrent STREAM processes are bounded; queuing and whole-invocation overruns alter trace timing and are recorded.',
                  'Scheduler events indicate submissions/acknowledgments, not physical DMA timings. Nonstreaming HTTP provides no TTFT.',
                  'Scaled/capped short-session subset with synthetic arrivals is not representative of the full trace distribution.'])
    def persist():
        output.write_text(json.dumps(data,indent=2)+'\n')
    prepared = None
    replay = None
    previous_handlers = {sig: signal.getsignal(sig) for sig in [signal.SIGINT, signal.SIGTERM]}
    def interrupted(signum, frame):
        if replay is not None:
            replay.cancel.set()
        raise KeyboardInterrupt(f'Replay interrupted by signal {signum}')
    for sig in previous_handlers:
        signal.signal(sig, interrupted)
    try:
        # Prefix probe and warmup outside every measured interval. Require cache
        # reporting instead of substituting a measured zero for missing fields.
        probe=random.Random(118).choices(pool,k=min(256,limit-2))
        generate(args.url,probe,1,args.request_timeout)
        _,second=generate(args.url,probe,1,args.request_timeout)
        if second['cached_tokens'] is None or second['cached_tokens']<=0:
            raise RuntimeError('Prefix-cache probe did not report a real cache hit')
        data['prefix_probe']=second
        largest=max((t for s in plan['sessions'] for t in s['steps']),key=lambda t:t['prefix_len']+t['input_len'])
        _,data['shape_warmup']=generate(args.url,random.Random(209).choices(pool,k=largest['prefix_len']+largest['input_len']),
                                       largest['output_len'],args.request_timeout)
        if args.tool_runtime == 'prepared':
            prepared = PreparedTools(args).__enter__()
            data['tool_runtime_initial'] = prepared.initial
            quantum = max((w['minimum_round_seconds'] for w in prepared.initial['workers']), default=None)
            data['timing_preflight'] = plan_timing(plan, quantum)
            if args.require_timing_fidelity and quantum is not None and any(
                    t['duration_seconds'] < quantum / 0.2 for s in plan['sessions'] for step in s['steps'] for t in step['tools']):
                raise RuntimeError('Trace intervals too short for the prepared STREAM quantum; select longer tools or use delay')
        for index in range(args.trials):
            client.wait_drained(args.drain_timeout)
            server.get('/flush_cache')
            client.wait_drained(args.drain_timeout)
            before=server.settle()
            initial_state=client.rpc('STATUS')
            offset=Path(client.initial['log_path']).stat().st_size
            replay=Replay(args,plan,pool,prepared=prepared,controller_pid=client.pid)
            trial=dict(index=index+1,requests=replay.records,tools=replay.tools)
            data['trials'].append(trial)
            start=time.monotonic_ns()
            try:
                trial['trace_end_ns']=replay.run()
                final=client.wait_drained(args.drain_timeout)
                accounting_start=time.monotonic_ns()
                after=server.settle()
                final=client.wait_drained(args.drain_timeout)
                end=time.monotonic_ns()
                events=read_events(client.initial['log_path'],offset,start)
                intervals=union([(t['start_ns'],t['end_ns']) for t in replay.tools])
                last_end=max((t['end_ns'] for t in replay.tools),default=start)
                trial.update(start_ns=start,end_ns=end,controller_events=events,controller_final=final,
                             controller_before=initial_state,
                             controller_counter_deltas={k:v-initial_state['counters'].get(k,0)
                                 for k,v in final['counters'].items() if not k.startswith('peak_')},
                             accounting_settle_start_ns=accounting_start,
                             accounting_settle_seconds=(end-accounting_start)/1e9,
                             transfer_counter_deltas=counter_delta(before,after),
                             windows={'tool':window(intervals,replay.records,events),
                                      'post_tool':window([(last_end,end)],replay.records,events),
                                      'whole':window([(start,end)],replay.records,events)})
                summarize_trial(trial)
                trial['timing_fidelity'] = timing_fidelity(trial['tools'])
                if args.require_timing_fidelity and not trial['timing_fidelity']['passed']:
                    raise RuntimeError('Tool timing fidelity failed; inspect duration overruns and launch/queue lags')
                hit=trial['server_prefix_hit_rate']
                print(f"Trial {index+1}: {len(replay.records)} requests, {len(replay.tools)} tools; whole {trial['windows']['whole']['seconds']:.2f}s; cached {f'{hit:.3f}' if hit is not None else 'N/A'}; host restore observed={trial['host_restore_observed']}; timing={'PASS' if trial['timing_fidelity']['passed'] else 'WARN'}",flush=True)
            except BaseException as exc:
                replay.cancel.set()
                trial['error']=str(exc) or type(exc).__name__
                raise
            finally:
                trial['activity_events']=[e for c in [client]+replay.clients for e in c.events if e['sent_ns']>=start]
                persist()
        if prepared is not None:
            prepared.close()
            data['tool_runtime_final'] = prepared.final()
            prepared = None
    except BaseException as exc:
        if prepared is not None:
            try:
                prepared.close(failed=True)
            except BaseException as cleanup:
                data['worker_cleanup_error'] = str(cleanup)
            data['tool_runtime_final'] = prepared.final()
        data['error']=str(exc) or type(exc).__name__
        persist()
        raise
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
    persist()
    print(f'Saved {output}')


if __name__=='__main__':
    main()
