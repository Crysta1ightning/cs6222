"""Select disjoint physical cores using only the current Slurm step's affinity."""
from collections import defaultdict
from pathlib import Path
import re


class PlacementError(RuntimeError):
    pass


def parse_list(value):
    result = set()
    for part in value.strip().split(','):
        if not part:
            continue
        if not re.fullmatch(r'\d+(?:-\d+)?', part):
            raise ValueError(f'Invalid CPU/node list: {value!r}')
        ends = [int(x) for x in part.split('-')]
        if ends[0] > ends[-1]:
            raise ValueError('Descending CPU/node range')
        result.update(range(ends[0], ends[-1]+1))
    return result


def discover(allowed, sysroot=Path('/sys/devices/system/cpu')):
    rows = []
    for cpu in sorted(allowed):
        root = sysroot / f'cpu{cpu}'
        nodes = [int(p.name[4:]) for p in root.glob('node[0-9]*')]
        if len(nodes) != 1:
            raise PlacementError(f'Cannot identify one NUMA node for CPU {cpu}')
        rows.append(dict(cpu=cpu, node=nodes[0],
                         socket=int((root/'topology/physical_package_id').read_text()),
                         core=int((root/'topology/core_id').read_text())))
    return rows


def choose(rows, count, server_count, gpu_node, allowed_memory, requested_node=None, allow_remote=False):
    if not 1 <= server_count < count:
        raise PlacementError('Need at least one distinct physical core for both server and tools')
    by_node = defaultdict(dict)
    for row in sorted(rows, key=lambda r:r['cpu']):
        # Multiple logical CPUs on one physical core count only once.
        by_node[row['node']].setdefault((row['socket'], row['core']), row['cpu'])
    capacity = {node:len(cores) for node,cores in by_node.items()}
    usable = [node for node,cores in by_node.items() if len(cores) >= count and node in allowed_memory]
    if requested_node is not None:
        node = requested_node
        if gpu_node is not None and node != gpu_node and not allow_remote:
            raise PlacementError('Requested NUMA node is remote from GPU; use --allow-remote-numa explicitly')
    elif gpu_node is not None:
        node = gpu_node
        if node not in usable and allow_remote and usable:
            node = min(usable)
    else:
        raise PlacementError('GPU NUMA locality unknown; supply --numa-node explicitly after inspecting topology')
    if node not in usable:
        raise PlacementError(f'Need {count} physical cores on one permitted NUMA node (GPU node={gpu_node}); '
                             f'available physical cores by node={capacity}, allowed memory nodes={sorted(allowed_memory)}. '
                             'No cross-NUMA fallback or smaller CPU count was used.')
    selected = sorted(by_node[node].values())[:count]
    return dict(numa_node=node, gpu_numa_node=gpu_node,
                gpu_locality_verified=gpu_node is not None and node==gpu_node,
                physical_core_capacity_by_node=capacity,
                selected_cpus=selected, server_cpus=selected[:server_count],
                tool_cpus=selected[server_count:], tool_threads=count-server_count)


def pci_path(bus_id):
    domain, bus, slot = bus_id.lower().split(':')
    return f'{domain[-4:]:0>4}:{bus}:{slot}'
