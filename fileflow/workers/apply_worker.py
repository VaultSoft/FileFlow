from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal

from ..apply_controller import ApplyController
from ..models import ErrorCode, PreviewPlan, Severity, StructuredError
from ..storage import Database


class ApplyWorker(QObject):
    progress = pyqtSignal(str, int, int)
    finished = pyqtSignal(object)
    failed = pyqtSignal(object)

    def __init__(
        self,
        database_path: str,
        plan: PreviewPlan,
        *,
        database_factory: Callable[[str], Database] = Database,
        controller_factory: Callable[[Database], ApplyController] = ApplyController,
    ):
        super().__init__()
        self.database_path = database_path
        self.plan = plan
        self.database_factory = database_factory
        self.controller_factory = controller_factory

    def run(self) -> None:
        database: Database | None = None
        result = None
        error: StructuredError | None = None
        try:
            database = self.database_factory(self.database_path)
            database.migrate()
            controller = self.controller_factory(database)
            result = controller.apply_confirmed(self.plan, progress_callback=self.progress.emit)
        except Exception as exc:
            error = StructuredError(
                ErrorCode.UNKNOWN_IO_ERROR,
                Severity.BLOCKING,
                "Apply failed before FileFlow could safely finish.",
                {"error": str(exc)},
            )
        finally:
            if database is not None:
                try:
                    database.close()
                except Exception as exc:
                    if error is None:
                        error = StructuredError(
                            ErrorCode.UNKNOWN_IO_ERROR,
                            Severity.BLOCKING,
                            "Apply finished but its worker database could not be closed safely.",
                            {"error": str(exc)},
                        )

        if error is not None:
            self.failed.emit(error)
        else:
            self.finished.emit(result)
