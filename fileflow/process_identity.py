from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_INVALID_PARAMETER = 87
STILL_ACTIVE = 259


@dataclass(frozen=True)
class ProcessIdentity:
    process_id: int
    process_started_at: str


class ProcessOwnerState(str, Enum):
    ALIVE = "ALIVE"
    DEAD = "DEAD"
    UNKNOWN = "UNKNOWN"


class ProcessQueryState(str, Enum):
    ACTIVE = "ACTIVE"
    EXITED = "EXITED"
    MISSING = "MISSING"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ProcessQueryResult:
    state: ProcessQueryState
    process_started_at: str | None = None
    reason: str | None = None


class ProcessIdentityProbe(Protocol):
    def current_identity(self) -> ProcessIdentity | None:
        ...

    def owner_state(self, process_id: int, process_started_at: str) -> ProcessOwnerState:
        ...


class WindowsProcessIdentityProbe:
    """Identifies a process by PID plus its Windows creation FILETIME.

    PID alone is never trusted because Windows can reuse it. A process must
    report STILL_ACTIVE and have the expected creation time to count as live.
    Query failures remain unknown unless Windows proves the process exited or
    the PID does not exist.
    """

    def __init__(self, process_query: Callable[[int], ProcessQueryResult] | None = None):
        self.process_query = process_query or self._query_process

    def current_identity(self) -> ProcessIdentity | None:
        if sys.platform != "win32":
            return None
        process_id = os.getpid()
        result = self.process_query(process_id)
        if result.state != ProcessQueryState.ACTIVE or result.process_started_at is None:
            return None
        return ProcessIdentity(process_id, result.process_started_at)

    def owner_state(self, process_id: int, process_started_at: str) -> ProcessOwnerState:
        if sys.platform != "win32" or process_id <= 0 or not process_started_at:
            return ProcessOwnerState.UNKNOWN
        result = self.process_query(process_id)
        if result.state in (ProcessQueryState.EXITED, ProcessQueryState.MISSING):
            return ProcessOwnerState.DEAD
        if result.state != ProcessQueryState.ACTIVE or result.process_started_at is None:
            return ProcessOwnerState.UNKNOWN
        if result.process_started_at == process_started_at:
            return ProcessOwnerState.ALIVE
        return ProcessOwnerState.DEAD

    @staticmethod
    def _query_process(process_id: int) -> ProcessQueryResult:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        open_process.restype = wintypes.HANDLE
        get_exit_code_process = kernel32.GetExitCodeProcess
        get_exit_code_process.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        get_exit_code_process.restype = wintypes.BOOL
        get_process_times = kernel32.GetProcessTimes
        get_process_times.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        )
        get_process_times.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL

        handle = open_process(PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
        if not handle:
            error = ctypes.get_last_error()
            if error == ERROR_INVALID_PARAMETER:
                return ProcessQueryResult(ProcessQueryState.MISSING, reason="pid_not_found")
            return ProcessQueryResult(ProcessQueryState.UNKNOWN, reason=f"open_process_failed:{error}")
        try:
            exit_code = wintypes.DWORD()
            if not get_exit_code_process(handle, ctypes.byref(exit_code)):
                return ProcessQueryResult(ProcessQueryState.UNKNOWN, reason="exit_code_query_failed")
            if int(exit_code.value) != STILL_ACTIVE:
                return ProcessQueryResult(ProcessQueryState.EXITED, reason=f"exit_code:{int(exit_code.value)}")

            creation = wintypes.FILETIME()
            exit_time = wintypes.FILETIME()
            kernel_time = wintypes.FILETIME()
            user_time = wintypes.FILETIME()
            if not get_process_times(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            ):
                return ProcessQueryResult(ProcessQueryState.UNKNOWN, reason="creation_time_query_failed")
            value = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
            return ProcessQueryResult(ProcessQueryState.ACTIVE, str(value))
        finally:
            close_handle(handle)
