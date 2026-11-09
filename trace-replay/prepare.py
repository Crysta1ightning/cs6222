"""Download a pinned TraceLab release or prepare a bounded session replay plan."""
import argparse
import collections
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import statistics
from pathlib import Path
import tempfile
import urllib.request

RELEASE = "v0.0.1"
URL = f"https://github.com/uw-syfi/TraceLab/releases/download/{RELEASE}/syfi_coding_trace.jsonl.gz"
SHA256 = "9d265eae69a31cae203848bea936f018148eed7ca8bf56050c5abe96da0b4e6b"
ATTRIBUTION = {
    "title": "TraceLab: Characterizing Coding Agent Workloads for LLM Serving",
    "authors": "Kan Zhu et al.; SyFI Lab, University of Washington",
    "paper": "https://arxiv.org/abs/2606.30560",
    "dataset": "https://github.com/uw-syfi/TraceLab",
    "license": "CC BY 4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
    "release": RELEASE, "url": URL,
}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        while block := f.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


def download(path):
    path = Path(path)
    if path.exists():
        if digest(path) != SHA256:
            raise ValueError(f"Existing file has wrong checksum: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    # Never publish a partial download as the dataset.
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        partial = Path(f.name)
        try:
            with urllib.request.urlopen(URL, timeout=60) as response:
                while block := response.read(1024 * 1024):
                    f.write(block)
            f.close()
            if digest(partial) != SHA256:
                raise ValueError("TraceLab download checksum mismatch")
            partial.replace(path)
        finally:
            partial.unlink(missing_ok=True)


def timestamp(value):
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() if value else None


def number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"Invalid {name}: {value!r}")
    return value


def integer(value, name):
    n = number(value, name)
    if int(n) != n:
        raise ValueError(f"Noninteger {name}: {value!r}")
    return int(n)


def convert(row, token_scale, time_scale, output_cap, max_tool_seconds):
    total = integer(row['input_tokens_total'], 'input_tokens_total')
    prefix = integer(row['prefix_tokens'], 'prefix_tokens')
    append = integer(row['newly_append_tokens'], 'newly_append_tokens')
    output = integer(row['output_tokens'], 'output_tokens')
    if total != prefix + append or total == 0:
        raise ValueError("Input total must equal prefix + append and be positive")
    prompt_len = max(1, round(total * token_scale))
    prefix_len = min(prompt_len, round(prefix * token_scale))
    output_len = max(1, round(output * token_scale))
    if output_cap:
        output_len = min(output_cap, output_len)
    source_tools = row.get('tools', [])
    starts = [timestamp(t.get('emitted_at')) for t in source_tools]
    origin = min((s for s in starts if s is not None), default=None)
    tools = []
    for index, (tool, start) in enumerate(zip(source_tools, starts)):
        end = timestamp(tool.get('result_at'))
        duration = (end - start) if start is not None and end is not None else None
        timing_source = 'timestamps'
        if duration is None:
            latency = tool.get('tool_wall_latency_ms')
            if latency is None:
                # Do not silently invent zero latency or ignore an un-timed tool.
                raise ValueError("Tool missing both timestamps and wall latency")
            duration = number(latency, 'tool_wall_latency_ms') / 1000
            timing_source = 'wall_latency; missing start assumed concurrent at zero'
        number(duration, 'tool duration')
        scaled = duration * time_scale
        actual = min(scaled, max_tool_seconds) if max_tool_seconds else scaled
        tools.append(dict(index=index, name=tool.get('tool_name', 'unknown'),
                          source_id=tool.get('tool_call_id'),
                          original_is_error=tool.get('is_error'),
                          offset_seconds=(start - origin) * time_scale if start is not None and origin is not None else 0,
                          duration_seconds=actual, original_duration_seconds=duration,
                          duration_capped=actual != scaled, timing_source=timing_source))
    events = row.get('timing_events', [])
    inputs = [timestamp(e.get('timestamp')) for e in events
              if e.get('event_type') in ('user_message', 'tool_result') and e.get('timestamp')]
    ends = [timestamp(t.get('result_at')) for t in source_tools if t.get('result_at')]
    return dict(round_index=integer(row['round_index'], 'round_index'),
                original=dict(input_tokens=total, prefix_tokens=prefix, append_tokens=append,
                              output_tokens=output),
                prefix_len=prefix_len, input_len=prompt_len - prefix_len, output_len=output_len,
                tools=tools, first_input_timestamp=min(inputs, default=None),
                last_event_timestamp=max([timestamp(e['timestamp']) for e in events if e.get('timestamp')] + ends, default=None),
                first_input_event_type=row.get('first_input_event_type'), human_wait_after_seconds=0)


def rows(path):
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt') as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def prepare(path, *, sessions=4, rounds=8, min_rounds=4, provider='any', seed=6222,
            token_scale=0.05, time_scale=0.1, output_cap=32, max_tool_seconds=5,
            max_context=4096, human_wait_cap=5, session_spacing=0,
            min_median_tool_seconds=0, min_tool_seconds=0, max_parallel_tools=0):
    """Keep the first consecutive rounds of each candidate, then hash-select sessions.

    Selection is independent of controller mode. Ineligible sessions are reported;
    no oversized round is silently dropped from inside a selected session.
    """
    candidates = collections.defaultdict(list)
    source_count = 0
    for row in rows(path):
        source_count += 1
        if provider != 'any' and row['provider'] != provider:
            continue
        key = (row['provider'], row['session_id'])
        # The public release is session ordered. Bound memory to each session's
        # lowest round indices, including unordered custom input.
        current = candidates[key]
        current.append(row)
        current.sort(key=lambda r: r['round_index'])
        del current[rounds:]
    eligible, rejected = [], collections.Counter()
    for (source_provider, sid), source in candidates.items():
        if len(source) < min_rounds:
            rejected['too_short'] += 1
            continue
        indices = [r['round_index'] for r in source]
        if indices != list(range(indices[0], indices[0] + len(indices))):
            rejected['nonconsecutive_or_duplicate_rounds'] += 1
            continue
        try:
            steps = [convert(r, token_scale, time_scale, output_cap, max_tool_seconds) for r in source]
        except (KeyError, TypeError, ValueError) as exc:
            rejected['invalid_or_missing_fields'] += 1
            continue
        if any(s['prefix_len'] + s['input_len'] + s['output_len'] > max_context for s in steps):
            rejected['context_overflow'] += 1
            continue
        if not any(s['tools'] for s in steps) or not any(s['prefix_len'] for s in steps[1:]):
            rejected['no_tools_or_reuse'] += 1
            continue
        durations = sorted(t['duration_seconds'] for s in steps for t in s['tools'])
        if statistics.median(durations) < min_median_tool_seconds:
            rejected['short_median_tool_duration'] += 1
            continue
        if min(durations) < min_tool_seconds:
            rejected['short_individual_tool_duration'] += 1
            continue
        peak = 0
        for step in steps:
            events = [(t['offset_seconds'], 1) for t in step['tools'] if t['duration_seconds'] > 0]
            events += [(t['offset_seconds']+t['duration_seconds'], -1)
                       for t in step['tools'] if t['duration_seconds'] > 0]
            active = 0
            for _, delta in sorted(events, key=lambda e: (e[0], e[1])):
                active += delta
                peak = max(peak, active)
        if max_parallel_tools and peak > max_parallel_tools:
            rejected['excess_parallel_tools'] += 1
            continue
        for step, next_step in zip(steps, steps[1:]):
            # Only user-driven gaps are human waits; never label them tool activity.
            if next_step['first_input_event_type'] == 'user_message':
                end, start = step['last_event_timestamp'], next_step['first_input_timestamp']
                if end is None or start is None:
                    rejected['missing_human_gap'] += 1
                    break
                raw = max(0, start - end)
                scaled = raw * time_scale
                step['human_wait_after_seconds'] = min(scaled, human_wait_cap) if human_wait_cap else scaled
                step['original_human_wait_after_seconds'] = raw
        else:
            eligible.append(dict(session_id=sid, provider=source_provider,
                                 original_model=source[0].get('model'), steps=steps))
    eligible.sort(key=lambda s: hashlib.sha256(f"{seed}:{s['provider']}:{s['session_id']}".encode()).digest())
    if len(eligible) < sessions:
        raise ValueError(f"Only {len(eligible)} eligible sessions for {sessions} requested: {dict(rejected)}")
    selected = eligible[:sessions]
    for index, session in enumerate(selected):
        session['arrival_seconds'] = index * session_spacing
    source_hash=digest(path)
    return dict(schema='tracelab-plan-v1', attribution=ATTRIBUTION, source_sha256=source_hash,
                matches_pinned_release=source_hash==SHA256,
                source_rows=source_count, eligible_sessions=len(eligible), rejected_sessions=dict(rejected),
                transforms=dict(seed=seed, provider=provider, requested_sessions=sessions, max_rounds=rounds,
                                min_rounds=min_rounds, token_scale=token_scale, time_scale=time_scale,
                                output_cap=output_cap, max_tool_seconds=max_tool_seconds,
                                human_wait_cap=human_wait_cap, max_context=max_context,
                                session_spacing_seconds=session_spacing),
                prefix_model='reported cached-prefix length reconstructed with synthetic token IDs; not original token identity or guaranteed hits',
                timing_model='closed-loop local generation; tool offsets relative to first emitted tool after full response; synthetic session arrivals',
                selection=dict(min_median_tool_seconds=min_median_tool_seconds,
                    min_tool_seconds=min_tool_seconds, max_parallel_tools=max_parallel_tools,
                    changes_workload_distribution=bool(min_median_tool_seconds or min_tool_seconds or max_parallel_tools)), sessions=selected)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    fetch = sub.add_parser('fetch')
    fetch.add_argument('--output', type=Path, default=Path(os.environ.get('TMPDIR', '/tmp')) / 'syfi_coding_trace-v0.0.1.jsonl.gz')
    q = sub.add_parser('prepare')
    q.add_argument('source', type=Path)
    q.add_argument('--output', type=Path, required=True)
    for name, default in [('sessions',4),('rounds',8),('min-rounds',4),('seed',6222),('output-cap',32),('max-context',4096)]:
        q.add_argument('--'+name, type=int, default=default)
    for name, default in [('token-scale',0.05),('time-scale',0.1),('max-tool-seconds',5),('human-wait-cap',5),('session-spacing',0)]:
        q.add_argument('--'+name, type=float, default=default)
    q.add_argument('--provider', choices=['any','claude','codex'], default='any')
    q.add_argument('--min-median-tool-seconds', type=float, default=0,
                   help='Select complete sessions with longer scaled tools; never floor individual durations')
    q.add_argument('--min-tool-seconds', type=float, default=0,
                   help='Reject complete sessions containing any shorter scaled tool')
    q.add_argument('--max-parallel-tools', type=int, default=0,
                   help='Maximum intrinsic within-round tool overlap; zero disables this session filter')
    args = p.parse_args()
    if args.command == 'fetch':
        download(args.output)
        print(f"Verified {args.output}: {SHA256}")
    else:
        if not 1 <= args.sessions <= 64 or not 2 <= args.min_rounds <= args.rounds <= 10000:
            p.error('sessions 1..64; 2 <= min-rounds <= rounds <= 10000')
        if not 0 < args.token_scale <= 1 or not 0 < args.time_scale <= 100:
            p.error('token-scale must be in (0,1]; time-scale in (0,100]')
        if any(not math.isfinite(v) or v < 0 for v in [args.max_tool_seconds,args.human_wait_cap,args.session_spacing,args.min_median_tool_seconds,args.min_tool_seconds]) or args.max_parallel_tools < 0 or args.output_cap < 0 or args.max_context < 2:
            p.error('invalid cap, spacing, or context')
        options = vars(args).copy()
        for key in ['command','source','output']:
            options.pop(key)
        plan = prepare(args.source, **options)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(plan, indent=2)+'\n')
        print(f"Saved {args.output}: {len(plan['sessions'])} sessions, {sum(len(s['steps']) for s in plan['sessions'])} rounds")
        print(f"Eligible: {plan['eligible_sessions']}; excluded: {plan['rejected_sessions']}")


if __name__ == '__main__':
    main()
