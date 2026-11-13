"""Host and process measurements for the live view. Real counters only.

Everything here is read from the operating system: nothing is estimated. A
value that cannot be read is reported as ``None`` and shown as "Not available".
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import re
import subprocess
import time

GIB = 1 << 30
_libproc = None


class _RusageInfoV2(ctypes.Structure):
    _fields_ = [
        ("ri_uuid", ctypes.c_uint8 * 16),
        ("ri_user_time", ctypes.c_uint64),
        ("ri_system_time", ctypes.c_uint64),
        ("ri_pkg_idle_wkups", ctypes.c_uint64),
        ("ri_interrupt_wkups", ctypes.c_uint64),
        ("ri_pageins", ctypes.c_uint64),
        ("ri_wired_size", ctypes.c_uint64),
        ("ri_resident_size", ctypes.c_uint64),
        ("ri_phys_footprint", ctypes.c_uint64),
        ("ri_proc_start_abstime", ctypes.c_uint64),
        ("ri_proc_exit_abstime", ctypes.c_uint64),
        ("ri_child_user_time", ctypes.c_uint64),
        ("ri_child_system_time", ctypes.c_uint64),
        ("ri_child_pkg_idle_wkups", ctypes.c_uint64),
        ("ri_child_interrupt_wkups", ctypes.c_uint64),
        ("ri_child_pageins", ctypes.c_uint64),
        ("ri_child_elapsed_abstime", ctypes.c_uint64),
        ("ri_diskio_bytesread", ctypes.c_uint64),
        ("ri_diskio_byteswritten", ctypes.c_uint64),
    ]


def process_usage(pid: int) -> dict | None:
    """Footprint, CPU time and cumulative disk bytes of one process (libproc)."""
    global _libproc
    try:
        if _libproc is None:
            _libproc = ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib")
        info = _RusageInfoV2()
        if _libproc.proc_pid_rusage(int(pid), 2, ctypes.byref(info)) != 0:
            return None
        return {
            "footprint_bytes": info.ri_phys_footprint,
            "resident_bytes": info.ri_resident_size,
            "cpu_time_ns": info.ri_user_time + info.ri_system_time,
            "disk_read_bytes": info.ri_diskio_bytesread,
            "disk_write_bytes": info.ri_diskio_byteswritten,
        }
    except (OSError, AttributeError):
        return None


def _run(args: list[str], timeout: float = 2.0) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def memory() -> dict:
    text = _run(["/usr/bin/vm_stat"])
    page_match = re.search(r"page size of (\d+)", text)
    page = int(page_match[1]) if page_match else 16384

    def pages(name: str) -> int | None:
        match = re.search(name + r":\s*(\d+)", text)
        return int(match[1]) * page if match else None

    total = int(_run(["/usr/sbin/sysctl", "-n", "hw.memsize"]) or 0) or None
    level = _run(["/usr/sbin/sysctl", "-n", "kern.memorystatus_level"]).strip()
    swap = re.search(r"used = ([\d.]+)([MG])", _run(["/usr/sbin/sysctl", "vm.swapusage"]))
    return {
        "total_bytes": total,
        "wired_bytes": pages("Pages wired down"),
        "active_bytes": pages("Pages active"),
        "inactive_bytes": pages("Pages inactive"),
        "free_bytes": pages("Pages free"),
        "compressed_bytes": pages("Pages occupied by compressor"),
        "available_bytes": int(total * int(level) / 100) if total and level.isdigit() else None,
        "pressure_level_pct": int(level) if level.isdigit() else None,
        "swap_used_bytes": int(float(swap[1]) * (1 << 20 if swap[2] == "M" else GIB)) if swap else None,
    }


def gpu() -> dict:
    """GPU utilisation and memory as published by the graphics driver's performance statistics."""
    text = _run(["/usr/sbin/ioreg", "-r", "-d", "1", "-w", "0", "-c", "IOAccelerator"])

    def number(key: str) -> int | None:
        match = re.search(r'"' + re.escape(key) + r'"\s*=\s*(\d+)', text)
        return int(match[1]) if match else None

    return {
        "utilization_pct": number("Device Utilization %"),
        "renderer_pct": number("Renderer Utilization %"),
        "in_use_bytes": number("In use system memory"),
        "allocated_bytes": number("Alloc system memory"),
    }


def cpu_count() -> int:
    return os.cpu_count() or 1


class Sampler:
    """Keeps the previous sample so rates (CPU, disk) are differences of real counters."""

    def __init__(self):
        self.previous: dict | None = None
        self.previous_time = 0.0
        self.previous_pid: int | None = None

    def sample(self, pid: int | None) -> dict:
        now = time.time()
        result = {"t": now, "memory": memory(), "gpu": gpu(), "process": None}
        usage = process_usage(pid) if pid else None
        if usage:
            process = {"pid": pid, "footprint_bytes": usage["footprint_bytes"], "resident_bytes": usage["resident_bytes"],
                       "cpu_pct": None, "disk_read_bytes_per_s": None, "disk_read_bytes": usage["disk_read_bytes"]}
            if self.previous and self.previous_pid == pid and now > self.previous_time:
                span = now - self.previous_time
                process["cpu_pct"] = round((usage["cpu_time_ns"] - self.previous["cpu_time_ns"]) / 1e9 / span * 100, 1)
                process["disk_read_bytes_per_s"] = max(0, usage["disk_read_bytes"] - self.previous["disk_read_bytes"]) / span
            result["process"] = process
            self.previous, self.previous_time, self.previous_pid = usage, now, pid
        else:
            self.previous = None
        return result
