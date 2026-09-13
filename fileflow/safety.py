from __future__ import annotations

import ctypes
import ntpath
import os
import re
import stat
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Protocol

from .models import (
    ErrorCode,
    FileIdentity,
    IdentitySnapshot,
    MetadataSnapshot,
    SafetyDecision,
    SafetyReason,
    Severity,
    StructuredError,
)

RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

INVALID_NAME_CHARS = set('<>"|?*')
MAX_NORMAL_WIN32_PATH = 259


def _case_key(path: str) -> str:
    return path.replace("/", "\\").casefold()


class WindowsPathPolicy:
    def normalize(self, path: str) -> str:
        text = str(path)
        normalized = ntpath.normpath(text.replace("/", "\\"))
        drive, tail = ntpath.splitdrive(normalized)
        if drive:
            normalized = drive.upper() + tail
        return normalized

    def classify(self, path: str, approved_root: str | None = None) -> SafetyDecision:
        if path is None or str(path) == "":
            return SafetyDecision.block(SafetyReason.EMPTY_PATH, "Path is empty.")

        raw = str(path)
        if raw.startswith("\\\\?\\"):
            return SafetyDecision.unsupported(
                SafetyReason.EXTENDED_PATH,
                "Extended-length logical paths are not supported.",
            )
        if raw.startswith("\\\\"):
            return SafetyDecision.unsupported(
                SafetyReason.UNC_PATH,
                "UNC paths are not supported in Milestone 1.",
            )
        if re.match(r"^[A-Za-z]:(?![\\/])", raw):
            return SafetyDecision.unsupported(
                SafetyReason.DRIVE_RELATIVE,
                "Drive-relative paths are not supported.",
            )

        drive, tail = ntpath.splitdrive(raw)
        if not drive or not tail.startswith(("\\", "/")):
            return SafetyDecision.block(SafetyReason.RELATIVE_PATH, "Path must be absolute.")
        raw_parts = [part for part in re.split(r"[\\/]+", tail) if part]
        if any(part in (".", "..") for part in raw_parts):
            return SafetyDecision.unsupported(
                SafetyReason.AMBIGUOUS_NORMALIZATION,
                "Dot-segment normalization is not supported.",
            )

        normalized = self.normalize(raw)
        root = ntpath.abspath(drive + "\\")
        if _case_key(normalized) == _case_key(root):
            return SafetyDecision.block(
                SafetyReason.FILESYSTEM_ROOT,
                "Filesystem roots are not valid FileFlow roots.",
                normalized_path=normalized,
                code=ErrorCode.ROOT_UNSAFE,
                severity=Severity.BLOCKING,
            )
        if len(normalized) > MAX_NORMAL_WIN32_PATH:
            return SafetyDecision.unsupported(
                SafetyReason.UNSUPPORTED_LONG_PATH,
                "Path exceeds the supported normal Windows path length.",
                normalized_path=normalized,
                code=ErrorCode.PATH_TOO_LONG,
            )

        normalized_drive, _ = ntpath.splitdrive(normalized)
        parts = [part for part in PureWindowsPath(normalized).parts if part not in (normalized_drive + "\\", "\\")]
        for part in parts:
            decision = self._classify_component(part, normalized)
            if not decision.allowed:
                return decision

        protected = self._protected_root_reason(normalized)
        if protected is not None:
            return SafetyDecision.block(
                SafetyReason.PROTECTED_ROOT,
                f"{protected} is a protected Windows location.",
                normalized_path=normalized,
                code=ErrorCode.ROOT_UNSAFE,
                severity=Severity.BLOCKING,
            )

        if approved_root is not None:
            root_normalized = self.normalize(approved_root)
            if _case_key(normalized) != _case_key(root_normalized) and not _case_key(normalized).startswith(
                _case_key(root_normalized) + "\\"
            ):
                return SafetyDecision.block(
                    SafetyReason.PATH_ESCAPE,
                    "Path escapes the approved root.",
                    normalized_path=normalized,
                )

        return SafetyDecision.safe(normalized)

    def has_case_collision(self, candidate: str, existing_paths: list[str]) -> bool:
        candidate_key = _case_key(self.normalize(candidate))
        return any(_case_key(self.normalize(existing)) == candidate_key for existing in existing_paths)

    def relative_to_root(self, path: str, root: str) -> str:
        decision = self.classify(path, root)
        if not decision.allowed or decision.normalized_path is None:
            raise ValueError("Path is not beneath root.")
        return ntpath.relpath(decision.normalized_path, self.normalize(root))

    def join_under_root(self, root: str, *parts: str) -> SafetyDecision:
        root_decision = self.classify(root)
        if not root_decision.allowed or root_decision.normalized_path is None:
            return root_decision
        candidate = ntpath.join(root_decision.normalized_path, *parts)
        return self.classify(candidate, root_decision.normalized_path)

    def _classify_component(self, component: str, normalized: str) -> SafetyDecision:
        if component.endswith("."):
            return SafetyDecision.block(
                SafetyReason.TRAILING_DOT,
                "Path component has a trailing period.",
                normalized_path=normalized,
                code=ErrorCode.INVALID_NAME,
            )
        if component.endswith(" "):
            return SafetyDecision.block(
                SafetyReason.TRAILING_SPACE,
                "Path component has a trailing space.",
                normalized_path=normalized,
                code=ErrorCode.INVALID_NAME,
            )
        if ":" in component:
            return SafetyDecision.unsupported(
                SafetyReason.ADS_SYNTAX,
                "Alternate data stream syntax is not supported.",
                normalized_path=normalized,
                code=ErrorCode.INVALID_NAME,
            )
        if any(char in INVALID_NAME_CHARS for char in component):
            return SafetyDecision.block(
                SafetyReason.INVALID_NAME,
                "Path component contains an invalid Windows filename character.",
                normalized_path=normalized,
                code=ErrorCode.INVALID_NAME,
            )
        if unicodedata.normalize("NFC", component) != component:
            return SafetyDecision.unsupported(
                SafetyReason.AMBIGUOUS_NORMALIZATION,
                "Path component has ambiguous Unicode normalization.",
                normalized_path=normalized,
            )
        stem = component.split(".", 1)[0].upper()
        if stem in RESERVED_NAMES:
            return SafetyDecision.block(
                SafetyReason.RESERVED_DEVICE_NAME,
                "Path component uses a reserved Windows device name.",
                normalized_path=normalized,
                code=ErrorCode.RESERVED_NAME,
            )
        return SafetyDecision.safe(normalized)

    def _protected_root_reason(self, normalized: str) -> str | None:
        key = _case_key(normalized).rstrip("\\")
        protected_exact = {
            "C:\\Windows": "Windows directory",
            "C:\\Program Files": "Program Files",
            "C:\\Program Files (x86)": "Program Files (x86)",
            "C:\\ProgramData": "ProgramData",
            "C:\\System Volume Information": "system volume metadata",
            "C:\\$Recycle.Bin": "Recycle Bin",
        }
        for value, label in protected_exact.items():
            value_key = _case_key(value)
            if key == value_key or key.startswith(value_key + "\\"):
                return label

        profile_root = re.match(r"^[A-Za-z]:\\Users\\[^\\]+$", normalized.rstrip("\\"), re.IGNORECASE)
        if profile_root:
            return "user profile root"
        current_profile = os.environ.get("USERPROFILE")
        if current_profile:
            current_key = _case_key(self.normalize(current_profile)).rstrip("\\")
            users_match = re.match(r"^[A-Za-z]:\\Users\\[^\\]+(\\.*)?$", normalized.rstrip("\\"), re.IGNORECASE)
            if users_match and not (key == current_key or key.startswith(current_key + "\\")):
                return "other user profile"
        return None


@dataclass(frozen=True)
class ReparseInfo:
    is_reparse_point: bool
    kind: str = "none"
    tag: int | None = None
    error: StructuredError | None = None


class ReparseInspector(Protocol):
    def inspect(self, path: str) -> ReparseInfo:
        ...


class WindowsReparseInspector:
    FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
    IO_REPARSE_TAG_SYMLINK = 0xA000000C
    IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003

    def inspect(self, path: str) -> ReparseInfo:
        try:
            path_obj = Path(path)
            info = path_obj.lstat()
        except OSError as exc:
            return ReparseInfo(
                False,
                "unknown",
                None,
                StructuredError(
                    ErrorCode.REPARSE_POINT,
                    Severity.OPERATION_BLOCKING,
                    "Could not inspect reparse state.",
                    {"path": path, "error": str(exc)},
                ),
            )
        if stat.S_ISLNK(info.st_mode):
            return ReparseInfo(True, "symlink", getattr(info, "st_reparse_tag", None))
        attributes = getattr(info, "st_file_attributes", 0)
        if attributes & self.FILE_ATTRIBUTE_REPARSE_POINT:
            tag = getattr(info, "st_reparse_tag", None)
            if tag == self.IO_REPARSE_TAG_SYMLINK:
                kind = "symlink"
            elif tag == self.IO_REPARSE_TAG_MOUNT_POINT:
                kind = "mount_point"
            else:
                kind = "reparse_point"
            return ReparseInfo(True, kind, tag)
        return ReparseInfo(False)


class PathChainSafety:
    def __init__(self, path_policy: WindowsPathPolicy, reparse_inspector: ReparseInspector):
        self.path_policy = path_policy
        self.reparse_inspector = reparse_inspector

    def classify_chain(self, path: str, approved_root: str | None = None) -> SafetyDecision:
        decision = self.path_policy.classify(path, approved_root)
        if not decision.allowed or decision.normalized_path is None:
            return decision
        for component_path in self._component_paths(decision.normalized_path):
            info = self.reparse_inspector.inspect(component_path)
            if info.error is not None:
                return SafetyDecision.block(
                    SafetyReason.REPARSE_INSPECTION_FAILED,
                    "Could not prove the path chain is free of reparse points.",
                    normalized_path=decision.normalized_path,
                    code=ErrorCode.REPARSE_POINT,
                    details=info.error.details,
                )
            if info.is_reparse_point:
                return SafetyDecision.block(
                    SafetyReason.REPARSE_POINT,
                    "A participating path component is a reparse point.",
                    normalized_path=decision.normalized_path,
                    code=ErrorCode.REPARSE_POINT,
                    details={"path": component_path, "kind": info.kind, "tag": info.tag},
                )
        return decision

    def _component_paths(self, normalized_path: str) -> list[str]:
        drive, tail = ntpath.splitdrive(normalized_path)
        root = drive.upper() + "\\"
        pieces = [piece for piece in tail.strip("\\").split("\\") if piece]
        current = root
        paths: list[str] = []
        for piece in pieces:
            current = ntpath.join(current, piece)
            paths.append(current)
        return paths


@dataclass(frozen=True)
class IdentityResult:
    snapshot: IdentitySnapshot | None
    error: StructuredError | None = None

    @property
    def supported(self) -> bool:
        return self.snapshot is not None and self.error is None


class FileIdentityProvider(Protocol):
    def snapshot(self, logical_path: str) -> IdentityResult:
        ...


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", ctypes.c_uint32), ("dwHighDateTime", ctypes.c_uint32)]


class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", ctypes.c_uint32),
        ("ftCreationTime", _FILETIME),
        ("ftLastAccessTime", _FILETIME),
        ("ftLastWriteTime", _FILETIME),
        ("dwVolumeSerialNumber", ctypes.c_uint32),
        ("nFileSizeHigh", ctypes.c_uint32),
        ("nFileSizeLow", ctypes.c_uint32),
        ("nNumberOfLinks", ctypes.c_uint32),
        ("nFileIndexHigh", ctypes.c_uint32),
        ("nFileIndexLow", ctypes.c_uint32),
    ]


class WindowsFileIdentityProvider:
    GENERIC_METADATA_ACCESS = 0
    SHARE_READ = 0x00000001
    SHARE_WRITE = 0x00000002
    SHARE_DELETE = 0x00000004
    OPEN_EXISTING = 3
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    INVALID_HANDLE_VALUE = -1

    def snapshot(self, logical_path: str) -> IdentityResult:
        if sys.platform != "win32":
            return IdentityResult(
                None,
                StructuredError(
                    ErrorCode.UNSUPPORTED,
                    Severity.OPERATION_BLOCKING,
                    "Windows file identity is only available on Windows.",
                    {"path": logical_path},
                ),
            )
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        flags = self.FILE_FLAG_BACKUP_SEMANTICS | self.FILE_FLAG_OPEN_REPARSE_POINT
        handle = kernel32.CreateFileW(
            ctypes.c_wchar_p(logical_path),
            self.GENERIC_METADATA_ACCESS,
            self.SHARE_READ | self.SHARE_WRITE | self.SHARE_DELETE,
            None,
            self.OPEN_EXISTING,
            flags,
            None,
        )
        if handle == self.INVALID_HANDLE_VALUE:
            return self._error(logical_path, "Could not open path for identity inspection.")
        try:
            info = _BY_HANDLE_FILE_INFORMATION()
            if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
                return self._error(logical_path, "Could not read handle identity.")
            size = (int(info.nFileSizeHigh) << 32) | int(info.nFileSizeLow)
            file_index = (int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow)
            file_type = "directory" if info.dwFileAttributes & 0x10 else "file"
            metadata = MetadataSnapshot(
                logical_path=logical_path,
                size=size,
                mtime_ns=self._filetime_to_ns(info.ftLastWriteTime),
                ctime_ns=self._filetime_to_ns(info.ftCreationTime),
                attributes=int(info.dwFileAttributes),
                reparse_tag=None,
                reparse_kind=None,
            )
            identity = FileIdentity(
                volume_id=f"{int(info.dwVolumeSerialNumber):08X}",
                file_id=f"{file_index:016X}",
                file_type=file_type,
                link_count=int(info.nNumberOfLinks),
            )
            return IdentityResult(IdentitySnapshot(identity, metadata))
        finally:
            kernel32.CloseHandle(handle)

    def _error(self, path: str, message: str) -> IdentityResult:
        return IdentityResult(
            None,
            StructuredError(
                ErrorCode.UNSUPPORTED,
                Severity.OPERATION_BLOCKING,
                message,
                {"path": path, "win_error": ctypes.get_last_error()},
            ),
        )

    def _filetime_to_ns(self, filetime: _FILETIME) -> int:
        ticks = (int(filetime.dwHighDateTime) << 32) | int(filetime.dwLowDateTime)
        return (ticks - 116444736000000000) * 100


@dataclass(frozen=True)
class CloudInfo:
    safe: bool
    reason: SafetyReason = SafetyReason.OK
    error: StructuredError | None = None


class CloudClassifier(Protocol):
    def classify(self, path: str, reparse_info: ReparseInfo | None = None) -> CloudInfo:
        ...


class ConservativeCloudClassifier:
    def classify(self, path: str, reparse_info: ReparseInfo | None = None) -> CloudInfo:
        if reparse_info is not None and reparse_info.is_reparse_point:
            return CloudInfo(
                False,
                SafetyReason.CLOUD_UNKNOWN,
                StructuredError(
                    ErrorCode.CLOUD_PLACEHOLDER,
                    Severity.OPERATION_BLOCKING,
                    "Cloud or reparse-backed files are unsupported in Milestone 1.",
                    {"path": path, "kind": reparse_info.kind, "tag": reparse_info.tag},
                ),
            )
        try:
            attrs = getattr(Path(path).lstat(), "st_file_attributes", 0)
        except OSError as exc:
            return CloudInfo(
                False,
                SafetyReason.CLOUD_UNKNOWN,
                StructuredError(
                    ErrorCode.CLOUD_PLACEHOLDER,
                    Severity.OPERATION_BLOCKING,
                    "Could not classify cloud placeholder state.",
                    {"path": path, "error": str(exc)},
                ),
            )
        if attrs & 0x00400000 or attrs & 0x00001000:
            return CloudInfo(
                False,
                SafetyReason.CLOUD_PLACEHOLDER,
                StructuredError(
                    ErrorCode.CLOUD_PLACEHOLDER,
                    Severity.OPERATION_BLOCKING,
                    "Cloud placeholder files are unsupported in Milestone 1.",
                    {"path": path},
                ),
            )
        return CloudInfo(True)


class FakeReparseInspector:
    def __init__(self, reparse_paths: set[str] | None = None, failing_paths: set[str] | None = None):
        self.reparse_paths = {_case_key(path) for path in (reparse_paths or set())}
        self.failing_paths = {_case_key(path) for path in (failing_paths or set())}

    def inspect(self, path: str) -> ReparseInfo:
        key = _case_key(path)
        if key in self.failing_paths:
            return ReparseInfo(
                False,
                "unknown",
                None,
                StructuredError(
                    ErrorCode.REPARSE_POINT,
                    Severity.OPERATION_BLOCKING,
                    "Injected reparse inspection failure.",
                    {"path": path},
                ),
            )
        if key in self.reparse_paths:
            return ReparseInfo(True, "test_reparse", None)
        return ReparseInfo(False)


class FakeIdentityProvider:
    def __init__(self, snapshots: dict[str, IdentitySnapshot] | None = None, failing_paths: set[str] | None = None):
        self.snapshots = {_case_key(path): snapshot for path, snapshot in (snapshots or {}).items()}
        self.failing_paths = {_case_key(path) for path in (failing_paths or set())}

    def snapshot(self, logical_path: str) -> IdentityResult:
        key = _case_key(logical_path)
        if key in self.failing_paths or key not in self.snapshots:
            return IdentityResult(
                None,
                StructuredError(
                    ErrorCode.UNSUPPORTED,
                    Severity.OPERATION_BLOCKING,
                    "Injected identity failure.",
                    {"path": logical_path},
                ),
            )
        return IdentityResult(self.snapshots[key])
