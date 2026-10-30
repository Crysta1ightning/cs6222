"""Bounded local tool-activity RPCs and STREAM subprocess lifetime signaling."""

import json
import os
import signal
import socket
import subprocess
import time
import uuid


class ActivityClient:
    def __init__(self, path, mode, timeout=2.0):
        self.path, self.mode, self.timeout = str(path), mode, timeout
        self.events = []
        self.pid = None
        self.initial = self.rpc("STATUS")

    def rpc(self, event, **fields):
        request = dict(event=event, request_id=uuid.uuid4().hex,
                       client_monotonic_ns=time.monotonic_ns(), **fields)
        record = dict(request=request, sent_ns=time.monotonic_ns())
        self.events.append(record)
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
                channel.settimeout(self.timeout)
                channel.connect(self.path)
                channel.sendall(json.dumps(request).encode() + b"\n")
                with channel.makefile("rb") as reader:
                    frame = reader.readline(1024 * 1024 + 1)
                if len(frame) > 1024 * 1024 or not frame.endswith(b"\n"):
                    raise RuntimeError("invalid control response")
                response = json.loads(frame)
            record.update(received_ns=time.monotonic_ns(), response=response)
            if not response.get("ok"):
                raise RuntimeError(response.get("error", "controller rejected event"))
            if response.get("request_id") != request["request_id"] or response.get("mode") != self.mode:
                raise RuntimeError("controller acknowledgment/mode mismatch")
            if event in ("TOOL_START", "TOOL_HEARTBEAT") and fields["tool_id"] not in response.get("active_tool_ids", []):
                raise RuntimeError("controller did not acknowledge an active tool lease")
            if self.pid is not None and self.pid != response["pid"]:
                raise RuntimeError("controller restarted during experiment")
            self.pid = response["pid"]
            return response
        except Exception as exc:
            record.update(error=str(exc), received_ns=time.monotonic_ns())
            raise

    def end(self, tool_id):
        error = None
        for _ in range(2):
            try:
                return self.rpc("TOOL_END", tool_id=tool_id)
            except Exception as exc:
                error = exc
        raise RuntimeError("TOOL_END failed; controller lease will expire") from error

    def wait_drained(self, timeout=30):
        deadline = time.monotonic() + timeout
        stable = 0
        previous = None
        while time.monotonic() < deadline:
            state = self.rpc("STATUS")
            totals = state["counters"]["submitted_batches"], state["counters"]["completed_batches"]
            idle = not state["pending_ops"] and not state["in_flight_batches"]
            stable = stable + 1 if idle and totals == previous else (1 if idle else 0)
            if stable >= 3:
                return state
            previous = totals
            time.sleep(0.05)
        raise RuntimeError("controller did not drain within bounded wait")


def run_stream(binary, env, *, client=None, timeout=180, warmup=False, record=None, before_launch=None):
    return run_command([str(binary)], env, client=client, timeout=timeout,
                       warmup=warmup, record=record, before_launch=before_launch)


def run_command(command, env, *, client=None, timeout=180, warmup=False,
                record=None, before_launch=None, cancel=None):
    """Acknowledge START before Popen; END follows exit/kill+wait on every path."""
    record = record if record is not None else {}
    tool_id = uuid.uuid4().hex
    record.update(tool_id=tool_id, warmup=warmup)
    process = None
    try:
        if client:
            record["start_ack"] = client.rpc("TOOL_START", tool_id=tool_id, warmup=warmup)
        if before_launch:
            before_launch()
        record["start_ns"] = time.monotonic_ns()
        process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, start_new_session=True)
        deadline = time.monotonic() + timeout
        lease = record.get("start_ack", {}).get("lease_seconds", 15)
        interval = min(2.0, lease / 3)
        heartbeat_at = time.monotonic() + interval
        while True:
            if cancel is not None and cancel.is_set():
                raise RuntimeError("Tool canceled after replay failure")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                stdout, stderr = process.communicate(timeout=min(interval, remaining, 0.25 if cancel else interval))
                record["end_ns"] = time.monotonic_ns()
                record["returncode"] = process.returncode
                return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                if client and time.monotonic() >= heartbeat_at:
                    client.rpc("TOOL_HEARTBEAT", tool_id=tool_id)
                    heartbeat_at = time.monotonic() + interval
    except BaseException as exc:
        record["error"] = str(exc) or type(exc).__name__
        raise
    finally:
        try:
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
                record.update(stdout=stdout, stderr=stderr, returncode=process.returncode)
            record.setdefault("end_ns", time.monotonic_ns())
        finally:
            # Even a lost START acknowledgment may have installed a lease.
            if client:
                record["end_ack"] = client.end(tool_id)
