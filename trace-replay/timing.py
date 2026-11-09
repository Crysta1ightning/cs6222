"""Describe trace timing distortion independently of functional success."""
import argparse
import json
from pathlib import Path
import statistics


def plan_timing(plan, quantum=None):
    durations = [t['duration_seconds'] for s in plan['sessions'] for step in s['steps'] for t in step['tools']]
    positive = [v for v in durations if v > 0]
    return dict(tools=len(durations), zero_duration_tools=len(durations)-len(positive),
        min_positive_seconds=min(positive) if positive else None,
        median_seconds=statistics.median(durations) if durations else None,
        max_seconds=max(durations) if durations else None,
        complete_stream_round_seconds=quantum,
        tools_shorter_than_one_round=sum(v < quantum for v in durations) if quantum is not None else None,
        note='No durations are floored, stretched, or removed by this audit.')


def timing_fidelity(tools, tolerance=0.2, absolute_tolerance=0.002):
    errors = [max(0, t['actual_seconds']-t['trace_tool']['duration_seconds']) for t in tools]
    bad = [error > max(absolute_tolerance, tolerance*t['trace_tool']['duration_seconds'])
           for t, error in zip(tools, errors)]
    lags = [max(0, t.get('launch_lag_seconds', 0)) for t in tools]
    queued = [t.get('surrogate_queue_seconds', 0) for t in tools]
    late = [lag > max(absolute_tolerance, tolerance*t['trace_tool']['duration_seconds'])
            for t, lag in zip(tools, lags)]
    return dict(duration_tolerance_fraction=tolerance, absolute_tolerance_seconds=absolute_tolerance,
        tools=len(tools), excessive_duration_overruns=sum(bad), excessive_start_lags=sum(late),
        duration_fidelity_passed=not any(bad), schedule_fidelity_passed=not any(late),
        passed=not any(bad) and not any(late),
        median_overrun_seconds=statistics.median(errors) if errors else None,
        max_overrun_seconds=max(errors) if errors else None,
        max_start_lag_seconds=max(lags) if lags else None,
        summed_queue_seconds=sum(queued),
        note='Functional correctness and faithful trace timing are separate checks; overlapping queue waits are not wall time.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--result', type=Path)
    p.add_argument('--output', type=Path)
    args = p.parse_args()
    report = dict(plan=plan_timing(json.loads(args.plan.read_text())))
    if args.result:
        data = json.loads(args.result.read_text())
        report['trials'] = [timing_fidelity(t['tools']) for t in data['trials']]
    text = json.dumps(report, indent=2)+'\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    print(text, end='')


if __name__ == '__main__':
    main()
