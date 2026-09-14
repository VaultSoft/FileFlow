from __future__ import annotations

from PyQt6.QtCore import Qt, QThread
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QMessageBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..apply_controller import ApplyController, ApplyResult, ApplyState, batch_source_folder
from ..app_metadata import APP_NAME
from ..models import StructuredError
from ..preview_workflow import PreviewAnalysis, PreviewWorkflowService
from ..storage import Database
from ..workers.apply_worker import ApplyWorker
from ..workers.preview_worker import PreviewWorker
from .presentation import PreviewPresentation, PreviewRow, format_bytes, present_analysis, present_revalidation, structured_error_text
from .styles import APP_STYLESHEET


class MainWindow(QMainWindow):
    def __init__(self, service: PreviewWorkflowService | None = None, database: Database | None = None):
        super().__init__()
        self.service = service or PreviewWorkflowService()
        self.database = database or Database(":memory:")
        self.database.migrate()
        self.apply_controller = ApplyController(self.database, preview_service=self.service)
        self.selected_folder: str | None = None
        self.current_analysis: PreviewAnalysis | None = None
        self.current_presentation: PreviewPresentation | None = None
        self.worker_thread: QThread | None = None
        self.worker: PreviewWorker | None = None
        self.apply_thread: QThread | None = None
        self.apply_worker: ApplyWorker | None = None
        self.apply_state = ApplyState.NO_PREVIEW

        self.setWindowTitle(APP_NAME)
        self.resize(1120, 720)
        self.setStyleSheet(APP_STYLESHEET)
        self._build_ui()
        self._show_home()

    def _build_ui(self) -> None:
        root = QWidget()
        shell = QHBoxLayout(root)
        shell.setContentsMargins(16, 16, 16, 16)
        shell.setSpacing(14)

        self.nav = QListWidget()
        self.nav.setFixedWidth(170)
        for label in ("Preview", "History", "Rules", "Settings"):
            QListWidgetItem(label, self.nav)
        self.nav.setCurrentRow(0)
        shell.addWidget(self.nav)

        self.pages = QStackedWidget()
        shell.addWidget(self.pages, 1)
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)

        self.preview_page = self._build_preview_page()
        self.history_page = self._build_history_page()
        self.pages.addWidget(self.preview_page)
        self.pages.addWidget(self.history_page)
        self.pages.addWidget(self._placeholder_page("Rules", "Built-in rules are active. Editing rules is deferred."))
        self.pages.addWidget(self._placeholder_page("Settings", "Settings are intentionally minimal while FileFlow remains preview-only."))

        self.setCentralWidget(root)

    def _build_preview_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        self.title = QLabel("FileFlow")
        self.title.setObjectName("headline")
        layout.addWidget(self.title)

        intro = QLabel("Select a folder, analyse immediate child files, and preview what FileFlow would do. Nothing is changed in this version.")
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        top = QHBoxLayout()
        self.folder_label = QLabel("No folder selected")
        self.folder_label.setObjectName("sectionTitle")
        self.folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.folder_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        top.addWidget(self.folder_label, 1)

        self.select_button = QPushButton("Select Folder")
        self.select_button.clicked.connect(self.select_folder)
        top.addWidget(self.select_button)

        self.analyse_button = QPushButton("Analyse")
        self.analyse_button.clicked.connect(self.start_analysis)
        self.analyse_button.setEnabled(False)
        top.addWidget(self.analyse_button)
        layout.addLayout(top)

        self.status_label = QLabel("")
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.summary_frame = QFrame()
        self.summary_frame.setObjectName("summaryStrip")
        summary_layout = QGridLayout(self.summary_frame)
        summary_layout.setContentsMargins(12, 10, 12, 10)
        self.summary_labels: dict[str, QLabel] = {}
        labels = (
            ("total", "Files"),
            ("ready", "Ready"),
            ("blocked", "Blocked"),
            ("unsupported", "Unsupported"),
            ("collisions", "Collisions"),
            ("dirs", "Skipped Folders"),
            ("bytes", "Data"),
        )
        for index, (key, label) in enumerate(labels):
            title = QLabel(label)
            title.setObjectName("muted")
            value = QLabel("0")
            value.setObjectName("sectionTitle")
            self.summary_labels[key] = value
            summary_layout.addWidget(title, 0, index)
            summary_layout.addWidget(value, 1, index)
        layout.addWidget(self.summary_frame)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(("Status", "Filename", "Category", "Source", "Destination", "Reason"))
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._show_selected_detail)
        layout.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMinimumHeight(120)
        bottom.addWidget(self.detail, 1)

        actions = QVBoxLayout()
        self.validate_button = QPushButton("Validate Preview")
        self.validate_button.setObjectName("secondaryButton")
        self.validate_button.clicked.connect(self.validate_preview)
        self.validate_button.setEnabled(False)
        actions.addWidget(self.validate_button)

        self.reanalyse_button = QPushButton("Re-analyse")
        self.reanalyse_button.setObjectName("secondaryButton")
        self.reanalyse_button.clicked.connect(self.start_analysis)
        self.reanalyse_button.setEnabled(False)
        actions.addWidget(self.reanalyse_button)

        self.apply_button = QPushButton("Apply - available in a later milestone")
        self.apply_button.clicked.connect(self.start_apply)
        self.apply_button.setEnabled(False)
        actions.addWidget(self.apply_button)
        actions.addStretch(1)
        bottom.addLayout(actions)
        layout.addLayout(bottom)
        return page

    def _build_history_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = QLabel("History")
        heading.setObjectName("headline")
        layout.addWidget(heading)
        self.recovery_banner = QLabel("")
        self.recovery_banner.setObjectName("muted")
        self.recovery_banner.setWordWrap(True)
        layout.addWidget(self.recovery_banner)
        self.history_table = QTableWidget(0, 7)
        self.history_table.setHorizontalHeaderLabels(("Time", "Source", "Ops", "Moved", "Failed", "Recovery", "Status"))
        self.history_table.horizontalHeader().setStretchLastSection(True)
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.history_table, 1)
        self.history_detail = QTextEdit()
        self.history_detail.setReadOnly(True)
        self.history_detail.setMinimumHeight(120)
        layout.addWidget(self.history_detail)
        return page

    def _placeholder_page(self, title: str, message: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = QLabel(title)
        heading.setObjectName("headline")
        body = QLabel(message)
        body.setObjectName("muted")
        body.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(body)
        layout.addStretch(1)
        return page

    def _show_home(self) -> None:
        self.status_label.setText("Start by selecting a folder. FileFlow will analyse only immediate child files.")
        self.detail.setPlainText("No preview yet.")
        self._refresh_history()
        self._refresh_apply_state()

    def select_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select folder to analyse")
        if not folder:
            return
        self.selected_folder = folder
        self.folder_label.setText(folder)
        validation = self.service.validate_folder(folder)
        self.status_label.setText(present_analysis(PreviewAnalysis(validation, (), None)).validation_message)
        self.analyse_button.setEnabled(validation.allowed)
        self.reanalyse_button.setEnabled(False)
        self.validate_button.setEnabled(False)
        self.current_analysis = None
        self.current_presentation = None
        self.apply_state = ApplyState.NO_PREVIEW
        self._refresh_apply_state()

    def start_analysis(self) -> None:
        if not self.selected_folder or self.worker_thread is not None:
            return
        self.status_label.setText("Analysing immediate child files...")
        self.analyse_button.setEnabled(False)
        self.reanalyse_button.setEnabled(False)
        self.validate_button.setEnabled(False)
        self.apply_button.setEnabled(False)

        self.worker_thread = QThread(self)
        self.worker = PreviewWorker(self.service, self.selected_folder)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.status_label.setText)
        self.worker.finished.connect(self._analysis_finished)
        self.worker.failed.connect(self._analysis_failed)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.failed.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self._worker_finished)
        self.worker_thread.start()

    def _worker_finished(self) -> None:
        if self.worker is not None:
            self.worker.deleteLater()
        if self.worker_thread is not None:
            self.worker_thread.deleteLater()
        self.worker = None
        self.worker_thread = None
        self.analyse_button.setEnabled(self.selected_folder is not None)
        self._refresh_apply_state()

    def _analysis_finished(self, analysis: PreviewAnalysis) -> None:
        self.current_analysis = analysis
        self.apply_state = ApplyState.PREVIEW_VALID if analysis.plan is not None else ApplyState.NO_PREVIEW
        presentation = present_analysis(analysis)
        self.current_presentation = presentation
        self._render_presentation(presentation)
        self.validate_button.setEnabled(analysis.plan is not None)
        self.reanalyse_button.setEnabled(self.selected_folder is not None)
        self._refresh_apply_state()

    def _analysis_failed(self, error: StructuredError) -> None:
        self.status_label.setText(structured_error_text(error))
        self.detail.setPlainText(error.message)
        self._refresh_apply_state()

    def _render_presentation(self, presentation: PreviewPresentation) -> None:
        summary = presentation.summary
        self.summary_labels["total"].setText(str(summary.total_files))
        self.summary_labels["ready"].setText(str(summary.ready))
        self.summary_labels["blocked"].setText(str(summary.blocked))
        self.summary_labels["unsupported"].setText(str(summary.unsupported))
        self.summary_labels["collisions"].setText(str(summary.collisions))
        self.summary_labels["dirs"].setText(str(summary.skipped_subdirectories))
        self.summary_labels["bytes"].setText(format_bytes(summary.total_bytes))

        self.table.setRowCount(0)
        for row_index, row in enumerate(presentation.rows):
            self.table.insertRow(row_index)
            values = (row.status, row.filename, row.category, row.source, row.destination, row.reason)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row_index, column, item)
        self.table.resizeColumnsToContents()
        self.status_label.setText(presentation.validation_message)
        self._refresh_apply_state()
        if presentation.rows:
            self.table.selectRow(0)
        else:
            self.detail.setPlainText("No preview rows to display.")

    def _show_selected_detail(self) -> None:
        if self.current_presentation is None:
            return
        selected = self.table.selectedItems()
        if not selected:
            return
        row_index = selected[0].row()
        if row_index >= len(self.current_presentation.rows):
            return
        self.detail.setPlainText(format_row_detail(self.current_presentation.rows[row_index]))

    def validate_preview(self) -> None:
        if self.current_analysis is None or self.current_analysis.plan is None:
            return
        result = self.service.revalidate_plan(self.current_analysis.plan)
        presentation = present_revalidation(result)
        lines = [presentation.title, presentation.message]
        if presentation.reasons:
            lines.append("")
            lines.extend(f"- {reason}" for reason in presentation.reasons)
        self.status_label.setText(presentation.title)
        self.detail.setPlainText("\n".join(lines))
        self.reanalyse_button.setEnabled(self.selected_folder is not None and presentation.status != "VALID")
        self.apply_state = ApplyState.PREVIEW_VALID if presentation.status == "VALID" else ApplyState.PREVIEW_STALE
        self._refresh_apply_state()

    def start_apply(self) -> None:
        if self.apply_thread is not None or self.current_analysis is None or self.current_analysis.plan is None:
            return
        plan = self.current_analysis.plan
        readiness = self.apply_controller.validate_before_confirmation(plan)
        if not readiness.can_apply:
            self.status_label.setText(readiness.message)
            self.apply_state = readiness.state
            self._refresh_apply_state()
            return
        summary = self.apply_controller.confirmation_summary(plan)
        if not self._confirm_apply(summary):
            self.status_label.setText("Move cancelled. No files were changed.")
            self._refresh_apply_state()
            return
        self.apply_state = ApplyState.APPLYING
        self.apply_button.setEnabled(False)
        self.status_label.setText("Moving files...")
        self.apply_thread = QThread(self)
        self.apply_worker = ApplyWorker(self.apply_controller, plan)
        self.apply_worker.moveToThread(self.apply_thread)
        self.apply_thread.started.connect(self.apply_worker.run)
        self.apply_worker.progress.connect(self._apply_progress)
        self.apply_worker.finished.connect(self._apply_finished)
        self.apply_worker.failed.connect(self._apply_failed)
        self.apply_worker.finished.connect(self.apply_thread.quit)
        self.apply_worker.failed.connect(self.apply_thread.quit)
        self.apply_thread.finished.connect(self._apply_worker_finished)
        self.apply_thread.start()

    def _confirm_apply(self, summary) -> bool:
        message = [
            summary.message,
            "",
            f"Files to move: {summary.operation_count}",
            f"Total data: {format_bytes(summary.total_bytes)}",
            f"Source folder: {summary.source_folder}",
            "Categories: " + ", ".join(f"{name}: {count}" for name, count in summary.category_summary),
        ]
        if summary.non_actionable_count:
            message.append(f"Non-actionable preview rows: {summary.non_actionable_count}")
        box = QMessageBox(self)
        box.setWindowTitle("Move files?")
        box.setText("\n".join(message))
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        move = box.addButton("Move Files", QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        return box.clickedButton() is move

    def _apply_progress(self, source: str, index: int, total: int) -> None:
        self.status_label.setText(f"Moving {index} of {total}: {source}")

    def _apply_finished(self, result: ApplyResult) -> None:
        self.apply_state = result.state
        self.status_label.setText(result.message)
        self.detail.setPlainText(
            f"Attempted: {result.attempted}\nMoved: {result.succeeded}\nFailed safely: {result.failed}\nBlocked: {result.blocked}\nRecovery required: {result.recovery_required}"
        )
        self._refresh_history()

    def _apply_failed(self, error: StructuredError) -> None:
        self.apply_state = ApplyState.RECOVERY_REQUIRED
        self.status_label.setText(structured_error_text(error))
        self.detail.setPlainText(error.message)
        self._refresh_history()

    def _apply_worker_finished(self) -> None:
        if self.apply_worker is not None:
            self.apply_worker.deleteLater()
        if self.apply_thread is not None:
            self.apply_thread.deleteLater()
        self.apply_worker = None
        self.apply_thread = None
        self._refresh_apply_state()

    def _refresh_apply_state(self) -> None:
        plan = self.current_analysis.plan if self.current_analysis and self.current_analysis.plan else None
        readiness = (
            self.apply_controller.readiness(plan, already_applying=True)
            if self.apply_thread is not None
            else self.apply_controller.validate_before_confirmation(plan) if plan is not None else self.apply_controller.readiness(None)
        )
        if self.apply_state in (ApplyState.COMPLETE, ApplyState.RECOVERY_REQUIRED):
            self.apply_button.setEnabled(False)
        else:
            self.apply_button.setEnabled(readiness.can_apply)
        if readiness.can_apply:
            self.apply_button.setText("Apply")
        else:
            self.apply_button.setText("Apply")

    def _refresh_history(self) -> None:
        rows = self.apply_controller.history_rows()
        self.history_table.setRowCount(0)
        for row_index, row in enumerate(rows):
            self.history_table.insertRow(row_index)
            values = (
                row["started_at"] or row["approved_at"],
                batch_source_folder(row),
                str(row["attempted_count"] or 0),
                str(row["succeeded_count"] or 0),
                str(row["failed_count"] or 0),
                str(row["recovery_count"] or 0),
                row["status"],
            )
            for column, value in enumerate(values):
                self.history_table.setItem(row_index, column, QTableWidgetItem(value))
        unresolved = self.apply_controller.operations_requiring_recovery()
        if unresolved:
            first = unresolved[0]
            self.recovery_banner.setText(
                f"A previous move needs recovery review. Source: {first['source_before']} Destination: {first['destination'] or ''}"
            )
        else:
            self.recovery_banner.setText("No recovery review is currently required.")


def format_row_detail(row: PreviewRow) -> str:
    parts = [
        f"Status: {row.status}",
        f"Safety code: {row.safety_code}",
        f"Collision: {row.collision_status}",
        f"Filename: {row.filename}",
        f"Source: {row.source}",
        f"Destination: {row.destination or 'None'}",
        f"Category: {row.category or 'None'}",
        f"Rule: {row.rule or 'None'}",
        f"Why: {row.reason}",
        f"Details: {row.detail}",
    ]
    return "\n".join(parts)
