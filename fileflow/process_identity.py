from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_INVALID_PARAMETER = 87


@dataclass(frozen=True)
class ProcessIdentity:
    process_id: int
    process_started_at: str


class ProcessOwnerState(str, Enum):
    ALIVE = "ALIVE"
    DEAD = "DEAD"
    UNKNOWN = "UNKNOWN"


class ProcessIdentityProbe(Protocol):
    def current_identity(self) -> ProcessIdentity | None:
        ...

    def owner_state(self, process_id: int, process_started_at: str) -> ProcessOwnerState:
        ...


class WindowsProcessIdentityProbe:
    """Identifies a process by PID plus its Windows creation FILETIME.

    PID alone is never trusted because Windows can reuse it. Failure to open a
    process or read its creation time is treated as unknown unless Windows
    explicitly reports that the PID does not exist.
    """

    def current_identity(self) -> ProcessIdentity | None:
        if sys.platform != "win32":
            return None
        process_id = os.getpid()
        found, started_at = self._query_creation_time(process_id)
        if found is not True or started_at is None:
            return None
        return ProcessIdentity(process_id, started_at)

    def owner_state(self, process_id: int, process_started_at: str) -> ProcessOwnerState:
        if sys.platform != "win32" or process_id <= 0 or not process_started_at:
            return ProcessOwnerState.UNKNOWN
        found, current_started_at = self._query_creation_time(process_id)
        if found is False:
            return ProcessOwnerState.DEAD
        if found is not True or current_started_at is None:
            return ProcessOwnerState.UNKNOWN
        if current_started_at == process_started_at:
            return ProcessOwnerState.ALIVE
        return ProcessOwnerState.DEAD

    @staticmethod
    def _query_creation_time(process_id: int) -> tuple[bool | None, str | None]:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        open_process.restype = wintypes.HANDLE
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
                return False, None
            return None, None
        try:
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
                return None, None
            value = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
            return True, str(value)
        finally:
            close_handle(handle)
