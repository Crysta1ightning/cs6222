"""Allocate/reuse one PACE GPU node, verify NUMA placement, and save shell exports."""
import argparse
import contextlib
import csv
import datetime as dt
import json
import io
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys

from topology import PlacementError, choose, discover, parse_list, pci_path
from resources import snapshot, recommendation_text

HERE = Path(__file__).resolve().parent
BASE = 'e00930c5489053f26d86b179cee0d087f846acbb'


def run(command, timeout=30, **kwargs):
    result = subprocess.run(command, text=True, capture_output=True, timeout=timeout, **kwargs)
    if result.returncode:
        raise RuntimeError(f'{shlex.join(map(str,command))}:\n{result.stderr or result.stdout}')
    return result.stdout.strip()


def save(path, value):
    path.write_text(json.dumps(value, indent=2)+'\n')


def job_info(job):
    raw = run(['scontrol','show','job','--oneliner',str(job)])
    fields = dict(re.findall(r'\b([A-Za-z][A-Za-z0-9_/]*)=(\S+)', raw))
    if not re.search(r'\('+str(os.getuid())+r'\)', fields.get('UserId','')):
        raise RuntimeError('The allocation is not owned by this user')
    if fields.get('NumNodes') not in ('1','1-1'):
        raise RuntimeError('This skill uses one compute node; --cpus is a physical-core count')
    return fields, raw


def submission(args, output):
    return ['salloc','--job-name=pace-setup','--partition='+args.partition,
            '--nodes=1','--ntasks=1','--cpus-per-task='+str(args.cpus),
            '--hint=nomultithread','--gres=gpu:'+args.gpu+':1','--gres-flags=enforce-binding',
            '--mem='+args.memory,'--time='+args.time,'--chdir='+str(args.project),
            '--immediate='+str(args.wait_seconds)]


def step_command(job, cpus):
    return ['srun','--jobid='+str(job),'--overlap','--nodes=1','--ntasks=1',
            '--cpus-per-task='+str(cpus),'--cpu-bind=cores',
            '--immediate=30','--kill-on-bad-exit=1']


def terminal_command(job, cpus, output):
    return step_command(job,cpus)+['--pty','bash','--rcfile',str(output/'session.bashrc'),'-i']


def source_guard(values):
    # Shell exports cannot modify an unrelated parent terminal. Every agent step
    # must source this file on the same host/job with the full selected CPU set.
    guard = '''import os,socket,sys
from pathlib import Path
expected_job,expected_host,cpus,node=sys.argv[1:]
chosen=set(map(int,cpus.split(',')))
if os.environ.get('SLURM_JOB_ID')!=expected_job or socket.gethostname()!=expected_host:
    raise SystemExit('Environment belongs to another allocation/host; run pace-setup again')
if not chosen <= os.sched_getaffinity(0):
    raise SystemExit('This Slurm step does not expose all configured CPUs; enter with the recorded full CPU count')
if any(not Path(f'/sys/devices/system/cpu/cpu{cpu}/node{node}').exists() for cpu in chosen):
    raise SystemExit('CPU NUMA topology no longer matches the setup')
'''
    cpus = values['EXP_SERVER_CPUS']+','+values['EXP_TOOL_CPUS']
    command = shlex.join(['python3','-c',guard,values['EXP_JOB_ID'],values['EXP_HOSTNAME'],cpus,values['EXP_NUMA_NODE']])
    return '# Source in this allocation, never on a login node.\nif ! '+command+'; then\n  return 1 2>/dev/null || exit 1\nfi\n'


def gpu_properties():
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError('PyTorch cannot access CUDA inside this allocation')
    if torch.cuda.device_count() != 1:
        raise RuntimeError('Expected exactly one CUDA-visible GPU; do not run on an ambiguous multi-GPU step')
    props = torch.cuda.get_device_properties(0)
    uuid = str(getattr(props,'uuid',''))
    rows = list(csv.reader(run(['nvidia-smi','--query-gpu=index,uuid,pci.bus_id,name,memory.total',
                               '--format=csv,noheader,nounits']).splitlines()))
    rows = [[field.strip() for field in row] for row in rows]
    normalized = lambda value: re.sub('[^a-z0-9]','',value.lower().removeprefix('gpu-'))
    matches = [row for row in rows if uuid and normalized(row[1])==normalized(uuid)]
    if len(matches)!=1:
        raise RuntimeError(f'Cannot match CUDA logical GPU 0 UUID {uuid!r} to NVIDIA inventory; no physical GPU index was guessed')
    row = matches[0]
    return dict(cuda_logical_index=0,nvidia_index=int(row[0]),uuid=row[1],pci_bus_id=row[2],
                name=row[3],memory_MiB=float(row[4]),torch_name=props.name,
                cuda_visible_devices=os.environ.get('CUDA_VISIBLE_DEVICES'))


def gpu_matches(requested, actual):
    family=re.match(r'[a-z]+\d+[a-z]*',requested.lower())
    expected=family.group() if family else re.sub('[^a-z0-9]','',requested.lower())
    return expected in re.sub('[^a-z0-9]','',actual.lower())


PAGE_PROBE = '''import ctypes,json,mmap,os
size=32*1024*1024
memory=mmap.mmap(-1,size)
for offset in range(0,size,mmap.PAGESIZE): memory[offset]=1
address=ctypes.addressof(ctypes.c_char.from_buffer(memory))
line=next((line for line in open('/proc/self/numa_maps') if int(line.split()[0],16)==address),None)
if line is None: raise RuntimeError('Cannot locate probe mapping')
pages={part.split('=')[0]:int(part.split('=')[1]) for part in line.split() if part.startswith('N') and '=' in part}
print(json.dumps(dict(cpu_affinity=sorted(os.sched_getaffinity(0)),mapping=line.strip(),resident_pages_by_node=pages)))
'''

GPU_PROBE = '''import json
from importlib.metadata import version
import torch,sglang.srt
x=torch.ones((1024,1024),device='cuda'); y=x@x; torch.cuda.synchronize()
if not torch.allclose(y,torch.full_like(y,1024)): raise RuntimeError('GPU computation failed')
print(json.dumps(dict(gpu=torch.cuda.get_device_name(0),cuda_runtime=torch.version.cuda,
    versions={name:version(name) for name in ['sglang','torch','numpy','requests','psutil','pytest']},
    computation='PASS: 1024x1024 matrix multiply')))
'''


def compute(args, output):
    if os.environ.get('SLURM_JOB_ID') != str(args.job_id) or 'SLURM_STEP_ID' not in os.environ:
        raise RuntimeError('Compute setup must execute in the requested Slurm step')
    report = dict(status='checking',job_id=str(args.job_id),hostname=socket.gethostname(),
                  requested=requested(args),slurm_step_id=os.environ['SLURM_STEP_ID'])
    save(output/'settings.json',report)
    try:
        allowed = os.sched_getaffinity(0)
        status = Path('/proc/self/status').read_text()
        mems = re.search(r'^Mems_allowed_list:\s*(.+)$',status,re.MULTILINE)
        if not mems:
            raise PlacementError('Cannot read allowed memory nodes')
        memory_nodes = parse_list(mems.group(1))
        rows = discover(allowed)
        report.update(allowed_cpus=sorted(allowed),allowed_memory_nodes=sorted(memory_nodes),cpu_topology=rows)
        step_cpus = int(os.environ['SLURM_CPUS_PER_TASK'])
        if len({(row['socket'],row['core']) for row in rows}) > step_cpus:
            raise PlacementError('Step affinity exposes more physical cores than requested from Slurm; '
                                 'refusing to select potentially unallocated CPUs')
        for name, command in {'lscpu':['lscpu','-e=CPU,CORE,SOCKET,NODE'],
                              'numa':['numactl','--hardware'],'gpu_topology':['nvidia-smi','topo','-m']}.items():
            report[name] = run(command)
        gpu = gpu_properties()
        if not gpu_matches(args.gpu,gpu['name']):
            raise RuntimeError(f"Requested GPU type {args.gpu} does not match {gpu['name']}")
        gpu_sysfs = Path('/sys/bus/pci/devices')/pci_path(gpu['pci_bus_id'])/'numa_node'
        gpu_node = int(gpu_sysfs.read_text()) if gpu_sysfs.exists() else -1
        gpu_node = gpu_node if gpu_node>=0 else None
        report['gpu'] = gpu
        placement = choose(rows,args.cpus,args.server_cores,gpu_node,memory_nodes,
                           args.numa_node,args.allow_remote_numa)
        report['placement'] = placement
        node = placement['numa_node']
        binding = ['numactl','--physcpubind='+','.join(map(str,placement['server_cpus'])),f'--membind={node}']
        pages = json.loads(run(binding+[sys.executable,'-c',PAGE_PROBE],timeout=60))
        totals = pages['resident_pages_by_node']
        fraction = totals.get(f'N{node}',0)/sum(totals.values()) if sum(totals.values()) else 0
        if fraction < 0.95:
            raise PlacementError(f'Bound first-touch memory probe is not >=95% local: {totals}')
        report['memory_probe'] = dict(pages,local_fraction=fraction,scope='32 MiB probe mapping, not future KV buffers')
        report['gpu_check'] = json.loads(run(binding+[sys.executable,'-c',GPU_PROBE],timeout=180))
        report['sglang'] = dict(commit=run(['git','-C',str(args.project/'sglang'),'rev-parse','HEAD']),
                                status=run(['git','-C',str(args.project/'sglang'),'status','--short']),base_commit=BASE)
        report['pip_check'] = run([sys.executable,'-m','pip','check'],timeout=120)
        result_root = args.project/'scripts/results'/f"pace-{args.job_id}-{args.gpu}-numa{node}-t{placement['tool_threads']}"
        result_root.mkdir(parents=True,exist_ok=True)
        control = Path(os.environ.get('TMPDIR','/tmp'))/f'pace-{os.getuid()}-{args.job_id}'
        control.mkdir(mode=0o700,exist_ok=True)
        if control.stat().st_uid!=os.getuid():
            raise RuntimeError('Control directory is not owned by this user')
        control.chmod(0o700)
        values = dict(EXP_JOB_ID=str(args.job_id),EXP_HOSTNAME=socket.gethostname(),
                      EXP_TOTAL_CPUS=str(args.cpus),EXP_GPU_TYPE=args.gpu,EXP_GPU_UUID=gpu['uuid'],
                      EXP_SERVER_CPUS=','.join(map(str,placement['server_cpus'])),
                      EXP_TOOL_CPUS=','.join(map(str,placement['tool_cpus'])),
                      EXP_NUMA_NODE=str(node),EXP_THREADS=str(placement['tool_threads']),
                      EXP_RESULTS=str(result_root),EXP_ENV_FILE=str(output/'env.sh'),
                      EXP_SETUP_REPORT=str(output/'settings.json'),
                      STREAM_CONTROL_ROOT=str(control),STREAM_RESULT_ROOT=str(result_root/'controller'),
                      TRACE_RESULT_ROOT=str(result_root/'trace-delay'),
                      OMP_NUM_THREADS=str(placement['tool_threads']),OMP_DYNAMIC='FALSE',
                      OMP_PROC_BIND='close',OMP_PLACES='cores')
        env = source_guard(values)
        env += 'module load python/3.12.5 cuda/13.0.1 || { return 1 2>/dev/null || exit 1; }\n'
        env += 'source '+shlex.quote(str(args.project/'.venv/bin/activate'))+' || { return 1 2>/dev/null || exit 1; }\n'
        env += 'cd '+shlex.quote(str(args.project))+' || { return 1 2>/dev/null || exit 1; }\n'
        env += ''.join('export '+name+'='+shlex.quote(value)+'\n' for name,value in values.items())
        (output/'env.sh').write_text(env)
        report.update(status='ready',exports=values,environment_file=str(output/'env.sh'),
                      completed_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                      server_started=False,experiments_started=False)
        save(output/'settings.json',report)
    except BaseException as exc:
        (output/'env.sh').unlink(missing_ok=True)
        report.update(status='failed',error=str(exc))
        save(output/'settings.json',report)
        raise


def requested(args):
    return {key:str(getattr(args,key)) for key in ['project','cpus','server_cores','gpu','memory','time','partition','numa_node','allow_remote_numa']}


def summary(report, state):
    print(f"READY: job {report['job_id']} on {report['hostname']}")
    gpu = report['gpu']
    print(f"GPU: {gpu['name']} ({gpu['memory_MiB']:.0f} MiB), UUID {gpu['uuid']}")
    print(f"Placement: NUMA {report['placement']['numa_node']}; GPU locality verified={report['placement']['gpu_locality_verified']}")
    print('Python/CUDA modules: python/3.12.5 cuda/13.0.1')
    print('Packages: '+json.dumps(report['gpu_check']['versions'],sort_keys=True))
    print('SGLang commit: '+report['sglang']['commit'])
    print('SGLang local changes: '+(report['sglang']['status'] or 'clean'))
    actual = report['slurm_job']
    print(f"Slurm limits: CPUs={actual.get('NumCPUs')}, host RAM={actual.get('MinMemoryNode','see allocation.json')}, time={actual.get('TimeLimit')}")
    print('Exports (for this host/job only):')
    for key,value in report['exports'].items():
        print('export '+key+'='+shlex.quote(value))
    print('Report: '+str(state.parent/'settings.json'))
    print('No model server or experiments were started.')
    print('Open another ICE LOGIN terminal and run this exact command:')
    print(report['second_terminal_command'])
    print('Keep the original allocation shell open. Exiting it releases a new salloc allocation; exiting a second shell only ends that step.')
    print('For each agent command, use this Slurm step and source env.sh:')
    print(shlex.join(step_command(report['job_id'],int(json.loads(state.read_text())['allocated_step_cpus'])))+
          ' bash -lc '+shlex.quote('source '+shlex.quote(report['environment_file'])+' && YOUR_COMMAND'))


def forwarded(args, output):
    command=[str(HERE/'setup.py'),'--project',str(args.project),'--cpus',str(args.cpus),
             '--server-cores',str(args.server_cores),'--gpu',args.gpu,'--memory',args.memory,
             '--time',args.time,'--partition',args.partition,'--output-dir',str(output),
             '--setup-timeout',str(args.setup_timeout),'--wait-seconds',str(args.wait_seconds)]
    if args.numa_node is not None:
        command+=['--numa-node',str(args.numa_node)]
    if args.allow_remote_numa:
        command+=['--allow-remote-numa']
    if args.repair_install:
        command+=['--repair-install']
    if args.queue:
        command+=['--queue']
    if args.new_allocation:
        command+=['--new-allocation']
    return command


def manage(args, output):
    job = args.job_id or os.environ.get('SLURM_JOB_ID')
    if args.enter and not job:
        raise RuntimeError('salloc did not provide SLURM_JOB_ID; refusing another allocation')
    if not job:
        availability=snapshot(run,args)
        save(output/'availability.json',availability)
        if not availability['preferred_looks_available'] and not args.queue:
            print(recommendation_text(availability),flush=True)
            raise SystemExit(3)  # Availability recommendation, not completed setup.
        # salloc keeps ownership until its command (and the compute shell) exits.
        args.new_allocation=True
        command=submission(args,output)+['python3']+forwarded(args,output)+['--enter']
        save(output/'request.json',dict(requested=requested(args),command=command,status='allocating'))
        print('Requesting allocation, then entering its compute shell:',flush=True)
        print(shlex.join(command),flush=True)
        return os.execvp(command[0],command)
    fields,raw=job_info(job)
    if fields.get('JobState')!='RUNNING':
        raise RuntimeError('Existing allocation is not RUNNING: '+raw)
    cpus=int(fields['NumCPUs'])
    if cpus<args.cpus:
        raise RuntimeError(f'Allocation has {cpus} CPUs; requested {args.cpus}')
    save(output/'allocation.json',dict(job_id=str(job),created_by_setup=args.new_allocation,
         allocated_step_cpus=cpus,status='configuring',slurm_job=fields,requested=requested(args)))
    internal=['python']+forwarded(args,output)+['--compute','--job-id',str(job)]
    command=['bash','-l',str(HERE/'session.sh'),str(args.project),
             '1' if args.repair_install else '0',str(args.setup_timeout),str(output)]+internal
    if not (os.environ.get('SLURM_STEP_ID') and os.environ.get('SLURM_JOB_ID')==str(job)):
        command=step_command(job,cpus)+['--pty']+command
    print(f'Entering compute shell for job {job}; setup log: {output / "setup.log"}',flush=True)
    return os.execvp(command[0],command)


def finish_compute(args, output):
    compute(args,output)
    report=json.loads((output/'settings.json').read_text())
    fields,_=job_info(args.job_id)
    if fields.get('JobState')!='RUNNING':
        raise RuntimeError('Allocation ended during setup')
    report.update(slurm_job=fields,allocation_created_by_setup=args.new_allocation,
                  virtualenv=str(args.project/'.venv'),
                  second_terminal_command=shlex.join(terminal_command(args.job_id,int(fields['NumCPUs']),output)))
    save(output/'settings.json',report)
    state=output/'allocation.json'
    save(state,dict(status='ready',job_id=str(args.job_id),allocated_step_cpus=int(fields['NumCPUs']),
                    created_by_setup=args.new_allocation,slurm_job=fields))
    # Both the first shell and every second-terminal entry load this file.
    rc='if [[ -f "$HOME/.bashrc" ]]; then source "$HOME/.bashrc"; fi\n'
    rc+='source '+shlex.quote(str(output/'env.sh'))+' || exit 1\n'
    rc+='printf \'PACE environment loaded: job %s, host %s, NUMA %s\\n\' "$EXP_JOB_ID" "$EXP_HOSTNAME" "$EXP_NUMA_NODE"\n'
    (output/'session.bashrc').write_text(rc)
    stream=io.StringIO()
    with contextlib.redirect_stdout(stream):
        summary(report,state)
    (output/'settings-summary.txt').write_text(stream.getvalue())
    print(stream.getvalue(),end='',flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project',type=Path,default=Path.home()/'cs6222')
    p.add_argument('--cpus',type=int,default=16,help='physical CPU cores on one compute node')
    p.add_argument('--gpu',default='l40s',help='Slurm GPU type, e.g. l40s or h100')
    p.add_argument('--server-cores',type=int)
    p.add_argument('--memory',default='32G')
    p.add_argument('--time',default='01:00:00')
    p.add_argument('--partition',default='ice-gpu')
    p.add_argument('--job-id',help='reuse this allocation; never canceled by setup')
    p.add_argument('--numa-node',type=int)
    p.add_argument('--allow-remote-numa',action='store_true')
    p.add_argument('--repair-install',action='store_true',help='explicitly reinstall the manual\'s pinned dependencies')
    p.add_argument('--wait-seconds',type=int,default=30)
    p.add_argument('--queue',action='store_true',help='explicitly wait even when preferred resources look busy')
    p.add_argument('--check-resources',action='store_true',help='read availability and recommendations without allocating')
    p.add_argument('--setup-timeout',type=int,default=1800)
    p.add_argument('--output-dir',type=Path)
    p.add_argument('--dry-run',action='store_true')
    p.add_argument('--compute',action='store_true',help=argparse.SUPPRESS)
    p.add_argument('--enter',action='store_true',help=argparse.SUPPRESS)
    p.add_argument('--new-allocation',action='store_true',help=argparse.SUPPRESS)
    args=p.parse_args()
    args.project=args.project.expanduser().resolve()
    args.gpu=args.gpu.lower()
    args.server_cores=args.server_cores if args.server_cores is not None else min(4,args.cpus-1)
    if args.cpus<2 or not 1<=args.server_cores<args.cpus or args.wait_seconds<1 or args.setup_timeout<1:
        p.error('Need >=2 physical cores, nonempty server/tool splits and positive timeouts')
    if not re.fullmatch(r'[a-z0-9_-]+',args.gpu) or (args.job_id and not args.job_id.isdigit()):
        p.error('Invalid GPU type or job ID')
    if args.numa_node is not None and args.numa_node<0:
        p.error('NUMA node must be nonnegative')
    output=args.output_dir or args.project/'scripts/results/pace-setup'/dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    output=output.expanduser().resolve()
    if args.dry_run:
        print(json.dumps(dict(requested=requested(args),allocation_command=submission(args,output),
                              reuses_job=args.job_id or os.environ.get('SLURM_JOB_ID'),output_dir=str(output)),indent=2))
        return
    if args.check_resources:
        print(json.dumps(snapshot(run,args),indent=2))
        return
    if not args.project.is_dir():
        p.error('Project directory does not exist')
    output.mkdir(parents=True,exist_ok=True)
    # Stale env.sh must never survive a failed rerun in a user-specified directory.
    (output/'env.sh').unlink(missing_ok=True)
    if args.compute:
        try:
            finish_compute(args,output)
        except BaseException as exc:
            (output/'env.sh').unlink(missing_ok=True)
            if (output/'settings.json').exists():
                report=json.loads((output/'settings.json').read_text())
                report.update(status='failed',error=str(exc));save(output/'settings.json',report)
            raise
    else:
        manage(args,output)


if __name__=='__main__':
    main()
