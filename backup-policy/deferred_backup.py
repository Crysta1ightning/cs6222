"""Binary tool signals and bounded D2H gating (single Linux x86-64 node).

Writers lock only for tool transitions. The scheduler reads a persistent mmap.
Never unlink or resize the state file while clients are attached.
"""
import contextlib
import fcntl
import json
import logging
import math
import mmap
import os
import platform
import struct
import time
import uuid
from pathlib import Path

MAGIC = b"CS6222V3"
HEADER = struct.Struct("<8sQQ")  # magic, update sequence, active tools
RECORD = struct.Struct("<QQQ")  # token, pid, /proc process start time
MAX_TOOLS = 256
STATE_SIZE = HEADER.size + MAX_TOOLS * RECORD.size
REAP_INTERVAL = 1.0


def process_identity(pid):
    try:
        return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19])
    except (FileNotFoundError, ProcessLookupError):
        return None


class StateBusy(Exception):
    """A tool transition is being published; retry on the next poll."""


class ToolState:
    def __init__(self, path):
        self.fd = self.mapping = None
        if platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64"):
            raise ValueError("Binary tool state currently supports Linux x86-64 only")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            with self.lock():
                size = os.fstat(self.fd).st_size
                if size == 0:
                    os.ftruncate(self.fd, STATE_SIZE)
                    os.pwrite(self.fd, HEADER.pack(MAGIC, 0, 0), 0)
                elif size != STATE_SIZE:
                    raise ValueError("Invalid binary tool-state size; use a new state path")
                self.mapping = mmap.mmap(self.fd, STATE_SIZE)
                if HEADER.unpack_from(self.mapping)[0] != MAGIC:
                    raise ValueError("Invalid binary tool-state header")
        except BaseException:
            self.close()
            raise

    @contextlib.contextmanager
    def lock(self, blocking=True):
        fcntl.flock(self.fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        try:
            yield
        finally:
            fcntl.flock(self.fd, fcntl.LOCK_UN)

    def snapshot(self):
        # On Linux x86-64, ordered stores and an odd/even sequence protect
        # publication. Readers do not take flock or inspect process records.
        for _ in range(3):
            magic, sequence, count = HEADER.unpack_from(self.mapping)
            if magic != MAGIC or count > MAX_TOOLS:
                raise ValueError("Corrupt binary tool-state header")
            if not sequence & 1 and sequence == struct.unpack_from("<Q", self.mapping, 8)[0]:
                return sequence, count
        raise StateBusy()

    def records(self):
        records = {}
        for offset in range(HEADER.size, STATE_SIZE, RECORD.size):
            token, pid, identity = RECORD.unpack_from(self.mapping, offset)
            if token:
                records[f"{token:016x}"] = {"pid": pid, "identity": identity}
        return records

    def publish(self, records):
        if len(records) > MAX_TOOLS:
            raise ValueError(f"Tool registry capacity is {MAX_TOOLS}")
        payload = bytearray(STATE_SIZE - HEADER.size)
        for index, (token, record) in enumerate(records.items()):
            RECORD.pack_into(payload, index * RECORD.size, int(token, 16),
                             record["pid"], record["identity"])
        sequence = HEADER.unpack_from(self.mapping)[1] & ~1
        struct.pack_into("<Q", self.mapping, 8, sequence + 1)
        self.mapping[HEADER.size:] = payload
        struct.pack_into("<Q", self.mapping, 16, len(records))
        struct.pack_into("<Q", self.mapping, 8, sequence + 2)

    def reap(self):
        # Rare crash recovery must not block the scheduler behind a writer.
        try:
            with self.lock(blocking=False):
                before = self.records()
                after = live_records(before)
                if before != after or HEADER.unpack_from(self.mapping)[1] & 1:
                    self.publish(after)
        except BlockingIOError:
            pass

    def close(self):
        if self.mapping is not None:
            self.mapping.close()
            self.mapping = None
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def live_records(records):
    return {key: item for key, item in records.items()
            if process_identity(item["pid"]) == item["identity"]}


@contextlib.contextmanager
def registry(path):
    """Control-plane transaction for tool transitions and explicit inspection."""
    state = ToolState(path)
    try:
        with state.lock():
            before = state.records()
            records = live_records(before)
            try:
                yield records
            finally:
                if records != before or HEADER.unpack_from(state.mapping)[1] & 1:
                    state.publish(records)
    finally:
        state.close()


@contextlib.contextmanager
def active_tool(path):
    token = uuid.uuid4().hex[:16]
    with registry(path) as records:
        records[token] = {"pid": os.getpid(), "identity": process_identity(os.getpid())}
    try:
        yield token
    finally:
        with registry(path) as records:
            records.pop(token, None)


class BackupGate:
    def __init__(self, path, max_delay=0.1, max_bytes=268435456, log_path=None,
                 clock=time.monotonic):
        if not math.isfinite(max_delay) or max_delay < 0 or max_bytes < 0:
            raise ValueError("Backup limits must be finite and nonnegative; zero disables a limit")
        self.path, self.max_delay, self.max_bytes = path, max_delay, max_bytes
        self.log_path, self.clock = log_path, clock
        self.state = self.log_file = None
        self.next_reap = clock() + REAP_INTERVAL
        self.reset()

    @classmethod
    def from_env(cls):
        path = os.getenv("CS6222_TOOL_STATE")
        if not path:
            return None
        if not Path(path).is_absolute():
            raise ValueError("CS6222_TOOL_STATE must be an absolute node-local path")
        return cls(path, float(os.getenv("CS6222_BACKUP_MAX_DELAY", "0.1")),
                   int(os.getenv("CS6222_BACKUP_MAX_BYTES", "268435456")),
                   os.getenv("CS6222_BACKUP_LOG"))

    def reset(self):
        self.pending_since = None
        self.last_defer = None

    def decide(self, queued_bytes, force=False, enqueue=False):
        now = self.clock()
        if self.pending_since is None:
            self.pending_since = now
        age = now - self.pending_since
        tools, error = None, None
        # Mandatory progress does not need to consult tool state.
        if force:
            reason = "required-progress"
        elif self.max_bytes and queued_bytes >= self.max_bytes:
            reason = "queue-limit"
        elif self.max_delay and age >= self.max_delay:
            reason = "deadline"
        else:
            try:
                if self.state is None:
                    self.state = ToolState(self.path)
                due = now >= self.next_reap
                try:
                    sequence, tools = self.state.snapshot()
                except StateBusy:
                    if not due:
                        raise
                    self.state.reap()
                    sequence, tools = self.state.snapshot()
                if due:
                    if tools:
                        self.state.reap()
                        sequence, tools = self.state.snapshot()
                    self.next_reap = now + REAP_INTERVAL
                key = (queued_bytes, sequence)
                if not enqueue and key == self.last_defer:
                    return False
                reason = "tool-active" if tools else "tools-idle"
            except StateBusy:
                return False  # Retry later; the deadline is still checked first.
            except (OSError, ValueError, KeyError, TypeError) as exc:
                error = str(exc)
                reason = "signal-error"
        submit = reason != "tool-active"
        if submit or self.last_defer != key:
            self.emit({"event": "submit" if submit else "defer", "reason": reason,
                       "monotonic_ns": time.monotonic_ns(), "queued_bytes": queued_bytes,
                       "wait_seconds": age, "active_tools": tools, "signal_error": error})
        if submit:
            self.reset()
        else:
            self.last_defer = key
        return submit

    def emit(self, event):
        if self.log_path:
            try:
                if self.log_file is None:
                    self.log_file = open(self.log_path, "a")
                self.log_file.write(json.dumps(event) + "\n")
                self.log_file.flush()  # Verification reads decision events live.
            except OSError as exc:
                logging.getLogger(__name__).warning("Backup policy log unavailable: %s", exc)

    def close(self):
        if getattr(self, "state", None) is not None:
            self.state.close()
            self.state = None
        if getattr(self, "log_file", None) is not None:
            self.log_file.close()
            self.log_file = None

    def __del__(self):
        self.close()
