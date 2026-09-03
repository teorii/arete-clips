"""Children do not outlive the app.

The ring buffer runs ffmpeg for as long as the app does, and the tunnel runs
cloudflared the same way. Killed or crashed, the app used to leave both behind:
ffmpeg kept encoding on the GPU and kept writing segments, so the next launch
found its own ring buffer locked by a process it had no idea about, reported
capture as unavailable, and the hotkey did nothing.

A job object with KILL_ON_JOB_CLOSE is the Windows answer. Children inherit the
job, and the kernel closes it when the last handle goes, which happens however
this process ends. Nothing to clean up on the next start, because there is
nothing left over.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# Held for the process lifetime: the limit applies while a handle is open, so
# dropping this would defeat the whole thing.
_job: int | None = None


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _BASIC_LIMIT(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
        ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _EXTENDED_LIMIT(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BASIC_LIMIT),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def die_with_us() -> bool:
    """Put this process in a job its children inherit. True if it took."""
    global _job

    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    job = _kernel32.CreateJobObjectW(None, None)
    if not job:
        return False

    info = _EXTENDED_LIMIT()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not _kernel32.SetInformationJobObject(
        wintypes.HANDLE(job),
        JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        return False

    # Declared, because GetCurrentProcess returns the pseudo-handle -1 and
    # ctypes will not pass that correctly on its own.
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    if not _kernel32.AssignProcessToJobObject(job, _kernel32.GetCurrentProcess()):
        return False

    _job = job
    return True
