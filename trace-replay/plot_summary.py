"""Redraw an exported policy summary without GPU access or raw trial files.

This validates aggregate table structure, not the original measurements.
"""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

from matrix_summary import chart
from policy_matrix import BASES, configurations

METRICS=[('whole.seconds','Replay + drain + counter settling (s)','runtime',False),
         ('whole.execution_seconds','Replay + drain before counter settling (s)','execution',False),
         ('whole.latency_median_seconds','Median model request latency (s)','latency',False),
         ('whole.server_prefix_hit_rate','Fraction of input tokens reused','reuse',False),
         ('whole.backup_GB','GPU-to-host payload (GB)','backups',False),
         ('whole.restore_GB','Host-to-GPU payload (GB)','restores',False),
         ('tool.stream_median_triad_GB_per_second','Median per-tool STREAM Triad rate (GB/s)','stream-rate',True)]


def number(value):
    value={'True':'1','False':'0'}.get(value,value)
    result=float(value)
    if not math.isfinite(result):raise ValueError('Statistics must be finite; missing data uses empty cells')
    return result


def load_csv(path):
    groups={};seen=set();keys={c['key'] for c in configurations()}
    with path.open(newline='') as stream:
        reader=csv.DictReader(stream)
        required={'configuration','tool_workload','metric','n','median','min','max'}
        if not required<=set(reader.fieldnames or []):raise ValueError('Not a matrix summary CSV')
        for row in reader:
            key,kind,metric=row['configuration'],row['tool_workload'],row['metric']
            if key not in keys or kind not in ('delay','stream') or not metric:
                raise ValueError('Unknown configuration, condition or empty metric')
            identity=(key,kind,metric)
            if identity in seen:raise ValueError(f'Duplicate aggregate row: {identity}')
            seen.add(identity)
            count=number(row['n'])
            if count<0 or count!=int(count):raise ValueError('Sample count must be a nonnegative integer')
            values=[row[k] for k in ('min','median','max')]
            if count==0:
                if any(values):raise ValueError('Missing measurements must have empty statistics')
                sample=dict(n=0,min=None,median=None,max=None)
            else:
                if not all(values):raise ValueError('Measured cells require all three statistics')
                low,median,high=map(number,values)
                if not low<=median<=high:raise ValueError('Require min <= median <= max')
                sample=dict(n=int(count),min=low,median=median,max=high)
            groups.setdefault((key,kind),{})[metric]=sample
    if not any(m in data and data[m]['n'] for data in groups.values() for m,_,_,_ in METRICS):
        raise ValueError('No measured plotting metrics in this CSV')
    return groups


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('summary',type=Path)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();groups=load_csv(args.summary)
    for base in BASES:
        for metric,label,name,stream_only in METRICS:
            chart(groups,base,metric,label,args.output_dir/base/f'{name}.png',stream_only)
    manifest=dict(source_csv=str(args.summary.resolve()),
        source_sha256=hashlib.sha256(args.summary.read_bytes()).hexdigest(),
        validation='Aggregate structure only. Use study.py audit or matrix_summary.py to validate raw trials.',
        uncertainty='Medians with trial min-max whiskers; not confidence intervals. Missing cells are N/A.')
    (args.output_dir/'plot-provenance.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(f'Saved SVG figures (and PNG when rsvg-convert is available) to {args.output_dir}')


if __name__=='__main__':main()
