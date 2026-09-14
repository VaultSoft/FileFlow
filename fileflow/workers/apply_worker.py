from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from ..apply_controller import ApplyController
from ..models import ErrorCode, PreviewPlan, Severity, StructuredError


class ApplyWorker(QObject):
    progress = pyqtSignal(str, int, int)
    finished = pyqtSignal(object)
    failed = pyqtSignal(object)

    def __init__(self, controller: ApplyController, plan: PreviewPlan):
        super().__init__()
        self.controller = controller
        self.plan = plan

    def run(self) -> None:
        try:
            result = self.controller.apply_confirmed(self.plan, progress_callback=self.progress.emit)
            self.finished.emit(result)
        except Exception as exc:
            self.failed.emit(
                StructuredError(
                    ErrorCode.UNKNOWN_IO_ERROR,
                    Severity.BLOCKING,
                    "Apply failed before FileFlow could safely finish.",
                    {"error": str(exc)},
                )
            )
