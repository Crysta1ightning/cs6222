"""Controlled surrogate subprocess; never execute commands from a trace."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'memory-experiment'))
from run import parse_stream


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kind', choices=['delay', 'stream'], required=True)
    p.add_argument('--seconds', type=float, required=True)
    p.add_argument('--binary', type=Path)
    args = p.parse_args()
    if not 0 <= args.seconds <= 3600:
        p.error('seconds must be in [0,3600]')
    start = time.monotonic()
    runs = []
    if args.kind == 'delay':
        time.sleep(args.seconds)
    else:
        if not args.binary or not args.binary.is_file():
            p.error('STREAM binary required')
        # Complete and validate every invocation; never truncate a kernel to fit
        # a trace duration. Whole process group is owned/killed by the parent.
        while not runs or time.monotonic() - start < args.seconds:
            result = subprocess.run([str(args.binary)], capture_output=True, text=True, timeout=300)
            result.check_returncode()
            runs.append(parse_stream(result.stdout))
    print(json.dumps(dict(kind=args.kind, target_seconds=args.seconds,
                          elapsed_seconds=time.monotonic()-start, stream_runs=runs)))


if __name__ == '__main__':
    main()
