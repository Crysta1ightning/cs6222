"""Small real-GPU check against an already-running isolated patched server.

This exercises tool signals around one request, not a STREAM benchmark.
No allocation or server is created by this script.
"""

import argparse
import json
import uuid

import requests

from activity import ActivityClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-socket", required=True)
    parser.add_argument("--controller-mode", choices=("normal", "defer", "skip"), required=True)
    parser.add_argument("--url", default="http://127.0.0.1:30000")
    args = parser.parse_args()
    client = ActivityClient(args.control_socket, args.controller_mode)
    if client.initial["active_tool_ids"]:
        raise RuntimeError("Another tool is active")

    def generate():
        # New first tokens create independent prefixes; keep the optional
        # operation well below the default 4096-token queue bound.
        seed = int(uuid.uuid4().hex[:8], 16)
        ids = [1000 + (seed + i * 71) % 18000 for i in range(512)]
        response = requests.post(args.url + "/generate", json={
            "input_ids": ids, "sampling_params": {"max_new_tokens": 1, "temperature": 0}}, timeout=120)
        response.raise_for_status()
        return response.json()

    generate()  # Kernel/model warmup and pre-existing writes outside the signal.
    before = client.wait_drained()
    tool_id = "smoke-" + uuid.uuid4().hex
    try:
        client.rpc("TOOL_START", tool_id=tool_id)
        result = generate()
        during = client.rpc("STATUS")
    finally:
        client.end(tool_id)
    after = client.wait_drained()
    delta = {k: after["counters"][k] - before["counters"][k] for k in before["counters"]}
    if args.controller_mode == "skip":
        assert delta["skipped_opportunities"] > 0, "No optional opportunity was skipped"
        assert delta["submitted_bytes"] == 0, "Unexpected backup during isolated skip check"
    elif args.controller_mode == "defer":
        assert delta["deferred_decisions"] > 0, "No deferral observed; inspect pressure/age overrides"
        assert delta["submitted_bytes"] > 0, "Deferred work did not drain"
    else:
        assert delta["submitted_bytes"] > 0, "No normal backup observed"
    assert not after["pending_ops"] and not after["in_flight_batches"]
    print(json.dumps(dict(passed=True, mode=args.controller_mode, counter_deltas=delta,
                          pending_during=during["pending_tokens"], response=result), indent=2))


if __name__ == "__main__":
    main()
