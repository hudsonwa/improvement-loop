"""Run the loop's work at background priority on whatever machine it is on.

Levels: "background" (default: efficiency cores where the OS has them, lowest CPU and I/O priority),
"low" (lowest CPU priority only), "normal" (no change). Everything is best effort: a missing tool or permission
leaves the process at a higher priority, never fails the run.

- macOS: the process and every child are marked Darwin background (`setpriority(PRIO_DARWIN_PROCESS, ...,
  PRIO_DARWIN_BG)`, and children are started under `taskpolicy -b`). On Apple silicon that keeps them on the
  efficiency cores and throttles their disk and network I/O.
- Linux: nice 19, the SCHED_IDLE scheduling class, idle I/O class (`ionice -c3` when present), and on hybrid
  Intel CPUs affinity to the efficiency cores listed in /sys/devices/cpu_atom/cpus.
- Windows: IDLE_PRIORITY_CLASS plus EcoQoS power throttling, which Windows schedules on efficiency cores.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Any, Callable, Dict, List, Optional, Set

LEVELS = ("background", "low", "normal")
_DARWIN_PRIO_PROCESS, _DARWIN_BG = 4, 0x1000
_WIN_IDLE_PRIORITY_CLASS = 0x40
_LINUX_ECORES = "/sys/devices/cpu_atom/cpus"


def _cpu_list(text: str) -> Set[int]:
    out: Set[int] = set()
    for part in text.strip().split(","):
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return out


def efficiency_cores() -> Set[int]:
    """Linux hybrid CPUs only; empty when unknown (other platforms let the OS pick)."""
    try:
        with open(_LINUX_ECORES, encoding="ascii") as fh:
            return _cpu_list(fh.read())
    except (OSError, ValueError):
        return set()


def _lower_self(level: str) -> None:
    """Lower the calling process (used in the driver and, through preexec, in each child)."""
    if level == "normal":
        return
    try:
        os.nice(19 - os.nice(0))
    except (OSError, AttributeError):
        pass
    if level != "background":
        return
    if sys.platform == "darwin":
        try:
            os.setpriority(_DARWIN_PRIO_PROCESS, 0, _DARWIN_BG)
        except (OSError, AttributeError):
            pass
    elif sys.platform.startswith("linux"):
        try:
            os.sched_setscheduler(0, os.SCHED_IDLE, os.sched_param(0))  # type: ignore[attr-defined]
        except (OSError, AttributeError):
            pass
        cores = efficiency_cores()
        if cores:
            try:
                os.sched_setaffinity(0, cores)  # type: ignore[attr-defined]
            except (OSError, AttributeError):
                pass


def lower_self(level: str = "background") -> None:
    if level not in LEVELS:
        raise ValueError(f"priority must be one of {', '.join(LEVELS)}")
    if sys.platform == "win32":
        _windows(None, level)
    else:
        _lower_self(level)


def wrap(cmd: List[str], level: str = "background") -> List[str]:
    """Prefix a command with the OS's background launcher where one exists."""
    if level != "background":
        return cmd
    if sys.platform == "darwin" and shutil.which("taskpolicy"):
        return ["taskpolicy", "-b", *cmd]
    if sys.platform.startswith("linux") and shutil.which("ionice"):
        return ["ionice", "-c3", *cmd]
    return cmd


def popen_kwargs(level: str = "background") -> Dict[str, Any]:
    """Keyword arguments for subprocess.Popen/run that start the child at the given priority."""
    if level == "normal":
        return {}
    if sys.platform == "win32":
        return {"creationflags": _WIN_IDLE_PRIORITY_CLASS}
    fn: Callable[[], None] = lambda: _lower_self(level)
    return {"preexec_fn": fn}


def run(cmd: List[str], level: str = "background", **kw: Any) -> subprocess.CompletedProcess:
    """subprocess.run at the given priority (Windows children get EcoQoS right after start)."""
    kw2 = dict(kw)
    timeout = kw2.pop("timeout", None)
    if kw2.pop("capture_output", False):
        kw2["stdout"] = kw2["stderr"] = subprocess.PIPE
    proc = subprocess.Popen(wrap(cmd, level), **popen_kwargs(level), **kw2)
    if sys.platform == "win32" and level == "background":
        _windows(proc.pid, level)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        raise subprocess.TimeoutExpired(cmd, timeout, out, err)
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def _windows(pid: Optional[int], level: str) -> None:
    """EcoQoS (+ idle class for this process) via ctypes; silently skipped where unsupported."""
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        h = k32.GetCurrentProcess() if pid is None else k32.OpenProcess(0x0200 | 0x1000, False, pid)
        if pid is None and level != "normal":
            k32.SetPriorityClass(h, _WIN_IDLE_PRIORITY_CLASS)
        if level == "background":
            class _State(ctypes.Structure):
                _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG),
                            ("StateMask", wintypes.ULONG)]
            s = _State(1, 0x1, 0x1)  # PROCESS_POWER_THROTTLING_EXECUTION_SPEED on = EcoQoS
            k32.SetProcessInformation(h, 4, ctypes.byref(s), ctypes.sizeof(s))  # ProcessPowerThrottling
        if pid is not None:
            k32.CloseHandle(h)
    except Exception:  # noqa: BLE001 - best effort by design
        pass
