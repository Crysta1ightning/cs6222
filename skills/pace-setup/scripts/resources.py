"""Read scheduler-accounted capacity; never submit jobs or assume NUMA locality."""
import re

RECOMMEND = ('l40s','h100','h200','a100','a40')
UNUSABLE = ('DOWN','DRAIN','FAIL','RESERVED','MAINT','POWER','NOT_RESPONDING','UNKNOWN','COMPLETING','FUTURE')


def fields(line):
    return dict(re.findall(r'\b([A-Za-z][A-Za-z0-9_/]*)=(\S*)',line))


def memory_mib(value):
    match=re.fullmatch(r'(\d+(?:\.\d+)?)([KMGTPE]?)(?:i?B)?',value.upper())
    if not match:
        raise ValueError(f'Cannot interpret memory amount: {value}')
    number,unit=match.groups()
    return float(number)*{'':1,'K':1/1024,'M':1,'G':1024,'T':1024**2,'P':1024**3,'E':1024**4}[unit]


def tres(text):
    return dict(part.split('=',1) for part in text.split(',') if '=' in part)


def inspect_nodes(text,partition,cpus,memory,gpu):
    wanted_memory=memory_mib(memory)
    if wanted_memory<=0:
        raise ValueError('Availability checks require a positive explicit host-memory budget')
    nodes=[]
    for line in text.splitlines():
        f=fields(line)
        if partition not in f.get('Partitions','').split(','):
            continue
        configured=tres(f.get('CfgTRES',''))
        allocated=tres(f.get('AllocTRES',''))
        kinds={key.removeprefix('gres/gpu:'):int(value) for key,value in configured.items() if key.startswith('gres/gpu:')}
        if not kinds:
            continue
        state=f.get('State','UNKNOWN')
        physical_total=int(f.get('CoresPerSocket','0'))*int(f.get('Sockets','0'))
        cpu_total=int(f.get('CPUTot','0'))
        units=max(1,(cpu_total+physical_total-1)//physical_total) if physical_total else 1
        free_cpu=max(0,int(f.get('CPUEfctv',f.get('CPUTot','0')))-int(f.get('CPUAlloc','0')))
        free_cores=free_cpu//units
        # Use Slurm allocations, not FreeMem/CPULoad (which are OS observations).
        configured_memory=memory_mib(configured.get('mem',f.get('RealMemory','0')))
        allocated_memory=memory_mib(allocated.get('mem',str(f.get('AllocMem','0'))))
        free_memory=max(0,configured_memory-allocated_memory-float(f.get('MemSpecLimit','0')))
        for kind,total in kinds.items():
            key='gres/gpu:'+kind
            if key in allocated:
                used=int(allocated[key])
            elif len(kinds)==1 and 'gres/gpu' in allocated:
                used=int(allocated['gres/gpu'])
            elif 'AllocTRES' in f and not any(k.startswith('gres/gpu') for k in allocated):
                used=0
            else:
                continue  # Ambiguous mixed-GPU inventory is not reported as free.
            free_gpu=max(0,total-used)
            eligible=(not any(flag in state for flag in UNUSABLE) and free_gpu>0
                      and free_cores>=cpus and free_memory>=wanted_memory
                      and int(f.get('CoresPerSocket','0'))>=cpus)
            nodes.append(dict(node=f['NodeName'],gpu=kind,state=state,
                              free_gpus=free_gpu,free_scheduler_cpus=free_cpu,
                              estimated_free_physical_cores=free_cores,
                              free_host_memory_GiB=round(free_memory/1024,2),
                              cores_per_socket=int(f.get('CoresPerSocket','0')),
                              candidate=eligible,numa_placement_verified=False))
    candidates=[n for n in nodes if n['candidate']]
    alternatives=[]
    for kind in RECOMMEND:
        available=[n for n in candidates if n['gpu']==kind]
        if available and kind!=gpu:
            available.sort(key=lambda n:(-n['estimated_free_physical_cores'],-n['free_host_memory_GiB'],n['node']))
            alternatives.append(dict(gpu=kind,cpus=cpus,memory=memory,nodes=available))
    return dict(preferred_gpu=gpu,cpus=cpus,memory=memory,partition=partition,
                preferred_looks_available=any(n['gpu']==gpu for n in candidates),
                preferred_nodes=[n for n in nodes if n['gpu']==gpu],alternatives=alternatives,
                caveat='Scheduler snapshot, not a reservation or NUMA guarantee; priority, reservations and local core placement can still delay allocation.')


def snapshot(run,args):
    raw=run(['scontrol','show','nodes','--oneliner'],timeout=25)
    result=inspect_nodes(raw,args.partition,args.cpus,args.memory,args.gpu)
    result['smaller_cpu_alternatives']=[]
    if not result['preferred_looks_available'] and not result['alternatives']:
        for count in sorted({args.cpus//2,4,2},reverse=True):
            if not 2<=count<args.cpus:
                continue
            smaller=inspect_nodes(raw,args.partition,count,args.memory,args.gpu)
            choices=smaller['alternatives']
            if smaller['preferred_looks_available']:
                choices=[dict(gpu=args.gpu,cpus=count,memory=args.memory,
                              nodes=[n for n in smaller['preferred_nodes'] if n['candidate']])]+choices
            if choices:
                result['smaller_cpu_alternatives']=choices
                break
    return result


def recommendation_text(report):
    lines=[f"Preferred {report['preferred_gpu']} + {report['cpus']} cores + {report['memory']} has no matching free-capacity candidate in {report['partition']}."]
    if report['alternatives']:
        for item in report['alternatives'][:3]:
            node=item['nodes'][0]
            lines.append(f"Alternative: {item['gpu']} + {item['cpus']} cores + {item['memory']}; "
                         f"{node['node']} reports {node['free_gpus']} free GPU(s), "
                         f"{node['estimated_free_physical_cores']} free core equivalents, "
                         f"{node['free_host_memory_GiB']} GiB unallocated host RAM.")
    else:
        lines.append('No recommended NVIDIA alternative currently meets the same CPU/RAM request.')
        for item in report.get('smaller_cpu_alternatives',[])[:3]:
            node=item['nodes'][0]
            lines.append(f"Smaller pilot: {item['gpu']} + {item['cpus']} cores + {item['memory']}; "
                         f"{node['node']} reports {node['free_gpus']} free GPU(s) and "
                         f"{node['estimated_free_physical_cores']} free core equivalents. "
                         'This changes the server/tool split; do not pool with the original CPU-bandwidth experiment.')
    lines.append('No job was submitted and no GPU type or CPU count was silently changed. '+report['caveat'])
    return '\n'.join(lines)
