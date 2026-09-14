from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from ..models import ErrorCode, Severity, StructuredError
from ..preview_workflow import PreviewWorkflowService


class PreviewWorker(QObject):
    progress = pyqtSignal(str)
    finished = pyqtSignal(object)
    failed = pyqtSignal(object)

    def __init__(self, service: PreviewWorkflowService, selected_path: str):
        super().__init__()
        self.service = service
        self.selected_path = selected_path

    def run(self) -> None:
        try:
            self.progress.emit("Analysing immediate child files")
            result = self.service.analyse_folder(self.selected_path)
            self.finished.emit(result)
        except Exception as exc:
            self.failed.emit(
                StructuredError(
                    ErrorCode.UNKNOWN_IO_ERROR,
                    Severity.BLOCKING,
                    "Preview analysis failed.",
                    {"path": self.selected_path, "error": str(exc)},
                )
            )
