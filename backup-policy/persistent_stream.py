"""Pipe protocol for a preinitialized, NUMA-bound STREAM worker."""
import json
import os
import select
import signal
import subprocess
import time


class PersistentStream:
    def __init__(self, command, env, timeout=300):
        self.command, self.env, self.timeout = command, env, timeout
        self.process = None
        self.full_validation = False
        self.buffer = b''

    def receive(self, expected):
        deadline = time.monotonic() + self.timeout
        while b'\n' not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.process.stdout], [], [], remaining)[0]:
                raise RuntimeError(f"STREAM worker timed out waiting for {expected}")
            chunk = os.read(self.process.stdout.fileno(), 4096)
            if not chunk:
                raise RuntimeError(f"STREAM worker exited waiting for {expected}")
            self.buffer += chunk
        line, self.buffer = self.buffer.split(b'\n', 1)
        message = json.loads(line)
        if message.get('event') != expected:
            raise RuntimeError(f"STREAM protocol expected {expected}, got {message}")
        return message

    def send(self, command):
        self.process.stdin.write((command + '\n').encode())
        self.process.stdin.flush()

    def __enter__(self):
        self.process = subprocess.Popen(self.command, env=self.env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, bufsize=0, start_new_session=True)
        self.launched_ns = time.monotonic_ns()
        try:
            self.ready = self.receive('ready')
        except BaseException:
            self.terminate()
            raise
        return self

    def start(self):
        self.send('GO')
        return self.receive('started')

    def finish(self):
        result = self.receive('done')
        if not result['sample_valid'] or result['end_ns'] <= result['start_ns']:
            raise RuntimeError('STREAM loop validation or timing failed')
        return result

    def terminate(self):
        if self.process is not None:
            if self.process.poll() is None:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            self.process.wait()
            self.process.stdin.close()
            self.process.stdout.close()

    def __exit__(self, exc_type, exc, traceback):
        try:
            if exc_type is None:
                self.send('QUIT')
                self.full_validation = self.receive('validated')['valid']
                if self.process.wait(timeout=self.timeout) or not self.full_validation:
                    raise RuntimeError('STREAM full-array validation failed')
        finally:
            self.terminate()
