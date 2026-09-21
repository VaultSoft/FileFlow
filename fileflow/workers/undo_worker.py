from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from ..models import ErrorCode, Severity, StructuredError
from ..undo import UndoController, UndoPlan


class UndoWorker(QObject):
    progress = pyqtSignal(str, int, int)
    finished = pyqtSignal(object)
    failed = pyqtSignal(object)

    def __init__(self, controller: UndoController, plan: UndoPlan):
        super().__init__()
        self.controller = controller
        self.plan = plan

    def run(self) -> None:
        try:
            result = self.controller.undo_confirmed(self.plan, progress_callback=self.progress.emit)
            self.finished.emit(result)
        except Exception as exc:
            self.failed.emit(
                StructuredError(
                    ErrorCode.UNKNOWN_IO_ERROR,
                    Severity.BLOCKING,
                    "Undo failed before FileFlow could safely finish.",
                    {"error": str(exc)},
                )
            )
