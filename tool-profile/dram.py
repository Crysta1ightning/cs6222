"""Read Intel IMC CAS counters using the kernel's sysfs event definitions.

Counters cover whole sockets, including other users; they are not per-process.
No sudo or changes to performance-monitoring permissions are attempted.
"""
import ctypes
import os
import platform
import struct
from pathlib import Path


def encode(pmu, event):
    config = 0
    for field in (pmu / "events" / event).read_text().strip().split(","):
        name, value = field.split("=")
        register, locations = (pmu / "format" / name).read_text().strip().split(":")
        if register != "config":
            raise ValueError(f"Unsupported event format: {register}")
        positions = []
        for span in locations.split(","):
            endpoints = [int(part) for part in span.split("-")]
            positions.extend(range(endpoints[0], endpoints[-1] + 1))
        number = int(value, 0)
        if number >> len(positions):
            raise ValueError("Event field exceeds its sysfs bit width")
        for source, destination in enumerate(positions):
            config |= ((number >> source) & 1) << destination
    return config


class Dram:
    def __init__(self):
        self.counters = []
        try:
            if platform.machine() != "x86_64":
                raise RuntimeError("Direct counter reader currently supports x86_64 only")
            libc = ctypes.CDLL(None, use_errno=True)
            libc.syscall.restype = ctypes.c_long
            root = Path("/sys/bus/event_source/devices")
            for pmu in sorted(root.glob("uncore_imc_*")):
                if not (pmu / "events/cas_count_read").exists():
                    continue
                cpus = (pmu / "cpumask").read_text().strip()
                # One representative CPU per socket, even outside our affinity.
                representatives = []
                for span in cpus.split(","):
                    ends = list(map(int, span.split("-")))
                    representatives.extend(range(ends[0], ends[-1] + 1))
                for event in ("cas_count_read", "cas_count_write"):
                    unit = (pmu / "events" / (event + ".unit")).read_text().strip()
                    if unit != "MiB":
                        raise ValueError(f"Unsupported counter unit: {unit}")
                    scale = float((pmu / "events" / (event + ".scale")).read_text()) * 2**20
                    for cpu in representatives:
                        attr = ctypes.create_string_buffer(64)
                        # PERF_FORMAT_TOTAL_TIME_ENABLED | TOTAL_TIME_RUNNING.
                        struct.pack_into("IIQQQQQ", attr, 0,
                                         int((pmu / "type").read_text()), 64,
                                         encode(pmu, event), 0, 0, 3, 0)
                        fd = libc.syscall(ctypes.c_long(298), ctypes.byref(attr),
                                          ctypes.c_int(-1), ctypes.c_int(cpu),
                                          ctypes.c_int(-1), ctypes.c_ulong(8))
                        if fd < 0:
                            number = ctypes.get_errno()
                            raise OSError(number, os.strerror(number))
                        self.counters.append((fd, scale, event, pmu.name, cpu))
            if not self.counters:
                raise RuntimeError("No supported IMC CAS counters exposed")
        except Exception:
            self.close()
            raise

    def read(self):
        return [struct.unpack("QQQ", os.read(fd, 24)) for fd, *_ in self.counters]

    def rates(self, before, after, seconds):
        totals = {"cas_count_read": 0.0, "cas_count_write": 0.0}
        for counter, old, new in zip(self.counters, before, after):
            count, enabled, running = [b - a for a, b in zip(old, new)]
            if running <= 0:
                return None, None
            totals[counter[2]] += count * enabled / running * counter[1] / seconds / 1e9
        return totals["cas_count_read"], totals["cas_count_write"]

    def close(self):
        for fd, *_ in self.counters:
            os.close(fd)
        self.counters.clear()
