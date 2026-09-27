from __future__ import annotations

from datetime import datetime
import ntpath

from PyQt6.QtCore import Qt, QThread
from PyQt6.QtGui import QColor, QCloseEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFrame,
    QHeaderView,
    QMessageBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QProgressBar,
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
from ..app_metadata import APP_NAME, APP_VERSION
from ..models import StructuredError
from ..preview_workflow import PreviewAnalysis, PreviewWorkflowService
from ..rules import test_filename_against_rules
from ..storage import Database
from ..undo import UndoController, UndoControllerState, UndoPlan, UndoResult
from ..workers.apply_worker import ApplyWorker
from ..workers.preview_worker import PreviewWorker
from ..workers.undo_worker import UndoWorker
from .branding import app_icon, mark_pixmap
from .presentation import PreviewPresentation, PreviewRow, format_bytes, present_analysis, structured_error_text
from .styles import ACCENT, AMBER, APP_STYLESHEET, RED, TEXT_SUB


class MainWindow(QMainWindow):
    def __init__(self, service: PreviewWorkflowService | None = None, database: Database | None = None):
        super().__init__()
        self.service = service or PreviewWorkflowService()
        self.database = database or Database(":memory:")
        self.database.migrate()
        self.apply_controller = ApplyController(self.database, preview_service=self.service)
        self.undo_controller = UndoController(self.database)
        self.selected_folder: str | None = None
        self.current_analysis: PreviewAnalysis | None = None
        self.current_presentation: PreviewPresentation | None = None
        self.worker_thread: QThread | None = None
        self.worker: PreviewWorker | None = None
        self.apply_thread: QThread | None = None
        self.apply_worker: ApplyWorker | None = None
        self.apply_state = ApplyState.NO_PREVIEW
        self.history_rows_by_id = {}
        self.current_undo_plan: UndoPlan | None = None
        self.undo_thread: QThread | None = None
        self.undo_worker: UndoWorker | None = None

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1280, 800)
        self.setMinimumSize(1040, 680)
        self.setStyleSheet(APP_STYLESHEET)
        self._build_ui()
        self._show_home()

    def _build_ui(self) -> None:
        root = QWidget()
        shell = QHBoxLayout(root)
        shell.setContentsMargins(14, 14, 14, 14)
        shell.setSpacing(16)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(202)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(12, 12, 12, 12)
        sidebar_layout.setSpacing(10)

        brand = QFrame()
        brand.setObjectName("brandPanel")
        brand_layout = QGridLayout(brand)
        brand_layout.setContentsMargins(2, 4, 2, 14)
        brand_layout.setHorizontalSpacing(10)
        brand_layout.setVerticalSpacing(3)
        brand_layout.addWidget(self._brand_mark(40), 0, 0, 2, 1)
        brand_name = QLabel(brand_wordmark())
        brand_name.setObjectName("brandName")
        brand_name.setTextFormat(Qt.TextFormat.RichText)
        brand_layout.addWidget(brand_name, 0, 1, 1, 2)
        brand_meta = QLabel("by VaultSoft")
        brand_meta.setObjectName("brandMeta")
        brand_layout.addWidget(brand_meta, 1, 1)
        brand_layout.addWidget(self._version_pill(), 1, 2, Qt.AlignmentFlag.AlignLeft)
        brand_layout.setColumnStretch(2, 1)
        sidebar_layout.addWidget(brand)

        self.nav = QListWidget()
        self.nav.setObjectName("navigation")
        for label in ("Preview", "History", "Rules", "Settings"):
            QListWidgetItem(label, self.nav)
        self.nav.setCurrentRow(0)
        sidebar_layout.addWidget(self.nav, 1)
        safety_note = QFrame()
        safety_note.setObjectName("safetyNote")
        safety_note_layout = QVBoxLayout(safety_note)
        safety_note_layout.setContentsMargins(10, 8, 10, 9)
        safety_note_layout.setSpacing(3)
        safety_title = QLabel("SAFETY")
        safety_title.setObjectName("eyebrow")
        safety_note_layout.addWidget(safety_title)
        safety_text = QLabel("Same-volume moves only\nNo overwrite  \u00b7  No delete")
        safety_text.setObjectName("safetyNoteText")
        safety_text.setWordWrap(True)
        safety_note_layout.addWidget(safety_text)
        sidebar_layout.addWidget(safety_note)
        shell.addWidget(sidebar)

        self.pages = QStackedWidget()
        shell.addWidget(self.pages, 1)
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.currentRowChanged.connect(self._page_changed)

        self.preview_page = self._build_preview_page()
        self.history_page = self._build_history_page()
        self.rules_page = self._build_rules_page()
        self.settings_page = self._build_settings_page()
        self.pages.addWidget(self.preview_page)
        self.pages.addWidget(self.history_page)
        self.pages.addWidget(self.rules_page)
        self.pages.addWidget(self.settings_page)

        self.setCentralWidget(root)

    def _brand_mark(self, size: int) -> QLabel:
        mark = QLabel()
        mark.setObjectName("brandMark")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setFixedSize(size, size)
        mark.setPixmap(mark_pixmap(round(size * 0.62), tile=False, device_pixel_ratio=self.devicePixelRatioF()))
        return mark

    def _version_pill(self) -> QLabel:
        pill = QLabel(f"v{APP_VERSION}")
        pill.setObjectName("versionPill")
        pill.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        return pill

    def _page_heading(self, title: str, subtitle: str) -> tuple[QLabel, QLabel]:
        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        description = QLabel(subtitle)
        description.setObjectName("pageSubtitle")
        description.setWordWrap(True)
        return heading, description

    def _configure_table(self, table: QTableWidget) -> None:
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(32)
        table.horizontalHeader().setHighlightSections(False)
        table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        # Paths are the long values here; keep the drive and the filename visible.
        table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        table.setWordWrap(False)

    def _page_changed(self, index: int) -> None:
        if index == 1:
            self._refresh_history()

    def _build_preview_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 2, 4, 4)
        layout.setSpacing(10)

        self.title, intro = self._page_heading(
            "Preview",
            "Choose a folder, inspect every proposed move, then explicitly confirm only the files marked Ready.",
        )
        layout.addWidget(self.title)
        layout.addWidget(intro)

        folder_panel = QFrame()
        folder_panel.setObjectName("panel")
        top = QHBoxLayout(folder_panel)
        top.setContentsMargins(12, 10, 12, 10)
        self.folder_label = QLabel("No folder selected")
        self.folder_label.setObjectName("pathLabel")
        self.folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.folder_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        top.addWidget(self.folder_label, 1)

        self.select_button = QPushButton("Choose Folder")
        self.select_button.setObjectName("secondaryButton")
        self.select_button.clicked.connect(self.select_folder)
        top.addWidget(self.select_button)

        self.analyse_button = QPushButton("Analyse")
        self.analyse_button.clicked.connect(self.start_analysis)
        self.analyse_button.setEnabled(False)
        top.addWidget(self.analyse_button)
        layout.addWidget(folder_panel)

        self.status_label = QLabel("")
        self.status_label.setObjectName("statusBanner")
        self.status_label.setProperty("tone", "neutral")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.missing_folders_banner = QLabel("")
        self.missing_folders_banner.setObjectName("statusBanner")
        self.missing_folders_banner.setProperty("tone", "warning")
        self.missing_folders_banner.setWordWrap(True)
        self.missing_folders_banner.setVisible(False)
        layout.addWidget(self.missing_folders_banner)

        self.preview_progress = QProgressBar()
        self.preview_progress.setRange(0, 1)
        self.preview_progress.setValue(0)
        self.preview_progress.setVisible(False)
        layout.addWidget(self.preview_progress)

        self.summary_frame = QFrame()
        self.summary_frame.setObjectName("summaryStrip")
        summary_layout = QGridLayout(self.summary_frame)
        summary_layout.setContentsMargins(16, 11, 16, 12)
        summary_layout.setHorizontalSpacing(18)
        summary_layout.setVerticalSpacing(2)
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
            title = QLabel(label.upper())
            title.setObjectName("eyebrow")
            value = QLabel("0")
            value.setObjectName("sectionTitle")
            if key == "ready":
                value.setStyleSheet(f"color: {ACCENT};")
            self.summary_labels[key] = value
            summary_layout.addWidget(title, 0, index)
            summary_layout.addWidget(value, 1, index)
        layout.addWidget(self.summary_frame)

        filters = QHBoxLayout()
        filter_title = QLabel("Preview rows")
        filter_title.setObjectName("sectionHeading")
        filters.addWidget(filter_title)
        filters.addStretch(1)
        self.preview_filter = QComboBox()
        self.preview_filter.addItems(("All statuses", "Ready", "Blocked", "Collisions", "Unsupported"))
        self.preview_filter.setToolTip("Filter preview rows by safety status")
        self.preview_filter.currentIndexChanged.connect(self._filter_preview_rows)
        filters.addWidget(self.preview_filter)
        self.preview_search = QLineEdit()
        self.preview_search.setPlaceholderText("Search filename or category")
        self.preview_search.setClearButtonEnabled(True)
        self.preview_search.setMaximumWidth(280)
        self.preview_search.textChanged.connect(self._filter_preview_rows)
        filters.addWidget(self.preview_search)
        self.preview_filter_count = QLabel("0 shown")
        self.preview_filter_count.setObjectName("muted")
        filters.addWidget(self.preview_filter_count)
        layout.addLayout(filters)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(("Status", "Filename", "Category", "Source", "Destination", "Reason"))
        self._configure_table(self.table)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._show_selected_detail)
        layout.addWidget(self.table, 1)

        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMinimumHeight(100)
        self.detail.setMaximumHeight(145)
        layout.addWidget(self.detail)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.validate_button = QPushButton("Validate Preview")
        self.validate_button.setObjectName("secondaryButton")
        self.validate_button.clicked.connect(self.validate_preview)
        self.validate_button.setEnabled(False)
        actions.addWidget(self.validate_button)

        self.reanalyse_button = QPushButton("Analyse Again")
        self.reanalyse_button.setObjectName("secondaryButton")
        self.reanalyse_button.clicked.connect(self.start_analysis)
        self.reanalyse_button.setEnabled(False)
        actions.addWidget(self.reanalyse_button)

        actions.addStretch(1)
        self.apply_hint_label = QLabel("Apply unlocks after a valid preview.")
        self.apply_hint_label.setObjectName("muted")
        actions.addWidget(self.apply_hint_label)

        self.apply_button = QPushButton("Move Ready Files")
        self.apply_button.clicked.connect(self.start_apply)
        self.apply_button.setEnabled(False)
        actions.addWidget(self.apply_button)
        layout.addLayout(actions)
        return page

    def _build_history_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 2, 4, 4)
        layout.setSpacing(10)
        heading, subtitle = self._page_heading(
            "History & Undo",
            "Review persisted Apply results, inspect recovery state, and preview safe same-volume Undo operations.",
        )
        layout.addWidget(heading)
        layout.addWidget(subtitle)

        self.recovery_banner = QLabel("")
        self.recovery_banner.setObjectName("statusBanner")
        self.recovery_banner.setProperty("tone", "success")
        self.recovery_banner.setWordWrap(True)
        layout.addWidget(self.recovery_banner)

        history_bar = QHBoxLayout()
        history_heading = QLabel("Apply batches")
        history_heading.setObjectName("sectionHeading")
        history_bar.addWidget(history_heading)
        history_bar.addStretch(1)
        self.refresh_history_button = QPushButton("Refresh")
        self.refresh_history_button.setObjectName("secondaryButton")
        self.refresh_history_button.clicked.connect(self._refresh_history)
        history_bar.addWidget(self.refresh_history_button)
        layout.addLayout(history_bar)

        self.history_table = QTableWidget(0, 7)
        self.history_table.setHorizontalHeaderLabels(("Date / Time", "Source Folder", "Operations", "Moved", "Failed", "Recovery", "Status"))
        self._configure_table(self.history_table)
        history_header = self.history_table.horizontalHeader()
        history_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        history_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in range(2, 7):
            history_header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.history_table.itemSelectionChanged.connect(self._show_history_detail)
        layout.addWidget(self.history_table, 1)

        self.history_detail = QTextEdit()
        self.history_detail.setReadOnly(True)
        self.history_detail.setMinimumHeight(100)
        self.history_detail.setMaximumHeight(150)
        self.history_detail.setPlainText("Select a history row to view its read-only details.")
        layout.addWidget(self.history_detail)

        undo_actions = QHBoxLayout()
        undo_title = QLabel("Undo preview")
        undo_title.setObjectName("sectionHeading")
        undo_actions.addWidget(undo_title)
        undo_actions.addStretch(1)
        self.preview_undo_button = QPushButton("Preview Undo")
        self.preview_undo_button.setObjectName("secondaryButton")
        self.preview_undo_button.setEnabled(False)
        self.preview_undo_button.clicked.connect(self.preview_undo)
        undo_actions.addWidget(self.preview_undo_button)
        self.confirm_undo_button = QPushButton("Undo Ready Files")
        self.confirm_undo_button.setEnabled(False)
        self.confirm_undo_button.clicked.connect(self.start_undo)
        undo_actions.addWidget(self.confirm_undo_button)
        layout.addLayout(undo_actions)

        self.undo_summary_label = QLabel("Select an eligible Apply batch, then choose Preview Undo. No files move during preview.")
        self.undo_summary_label.setObjectName("statusBanner")
        self.undo_summary_label.setProperty("tone", "neutral")
        self.undo_summary_label.setWordWrap(True)
        layout.addWidget(self.undo_summary_label)

        self.undo_progress = QProgressBar()
        self.undo_progress.setRange(0, 1)
        self.undo_progress.setValue(0)
        self.undo_progress.setVisible(False)
        layout.addWidget(self.undo_progress)

        self.undo_table = QTableWidget(0, 5)
        self.undo_table.setHorizontalHeaderLabels(("Status", "Filename", "Current Location", "Restore Location", "Reason"))
        self._configure_table(self.undo_table)
        undo_header = self.undo_table.horizontalHeader()
        undo_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        undo_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        undo_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        undo_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        undo_header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.undo_table.setMinimumHeight(155)
        layout.addWidget(self.undo_table)
        return page

    def _build_rules_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 2, 4, 4)
        layout.setSpacing(10)
        heading, body = self._page_heading(
            "Rules",
            "Built-in extension rules are deterministic and read-only. Preview always shows the exact result before Apply.",
        )
        layout.addWidget(heading)
        layout.addWidget(body)

        overview = QHBoxLayout()
        active_categories = sum(1 for category in self.service.categories if category.enabled and category.extensions)
        overview_label = QLabel(f"{active_categories} active categories")
        overview_label.setObjectName("sectionHeading")
        overview.addWidget(overview_label)
        overview.addStretch(1)
        scope = QLabel("First matching active rule wins")
        scope.setObjectName("muted")
        overview.addWidget(scope)
        layout.addLayout(overview)

        self.rules_table = QTableWidget(0, 4)
        self.rules_table.setHorizontalHeaderLabels(("State", "Category", "Destination folder", "Extensions"))
        self._configure_table(self.rules_table)
        rules_header = self.rules_table.horizontalHeader()
        rules_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        rules_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        rules_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        rules_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        categories = tuple(sorted(self.service.categories, key=lambda category: (category.sort_order, category.id)))
        self.rules_table.setRowCount(len(categories))
        for row_index, category in enumerate(categories):
            state = "Active" if category.enabled and category.extensions else "Inactive"
            values = (state, category.name, category.destination_folder, ", ".join(category.extensions) or "No automatic match")
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setForeground(QColor(ACCENT if state == "Active" else TEXT_SUB))
                self.rules_table.setItem(row_index, column, item)
        layout.addWidget(self.rules_table, 1)

        tester = QFrame()
        tester.setObjectName("rulesTester")
        tester_layout = QGridLayout(tester)
        tester_layout.setContentsMargins(12, 10, 12, 10)
        tester_title = QLabel("Test a filename")
        tester_title.setObjectName("sectionHeading")
        tester_layout.addWidget(tester_title, 0, 0, 1, 3)
        tester_hint = QLabel("This checks rule matching only. It does not inspect your disk or bypass Preview safety checks.")
        tester_hint.setObjectName("fieldHint")
        tester_hint.setWordWrap(True)
        tester_layout.addWidget(tester_hint, 1, 0, 1, 3)
        self.rule_test_input = QLineEdit()
        self.rule_test_input.setPlaceholderText("Example: holiday-photo.jpg")
        self.rule_test_input.setClearButtonEnabled(True)
        self.rule_test_input.returnPressed.connect(self._test_rule_filename)
        tester_layout.addWidget(self.rule_test_input, 2, 0, 1, 2)
        self.rule_test_button = QPushButton("Test")
        self.rule_test_button.clicked.connect(self._test_rule_filename)
        tester_layout.addWidget(self.rule_test_button, 2, 2)
        self.rule_test_result = QLabel("Enter a filename to see which built-in rule would match.")
        self.rule_test_result.setObjectName("statusBanner")
        self.rule_test_result.setProperty("tone", "neutral")
        self.rule_test_result.setWordWrap(True)
        tester_layout.addWidget(self.rule_test_result, 3, 0, 1, 3)
        tester_layout.setColumnStretch(0, 1)
        layout.addWidget(tester)
        return page

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 2, 4, 4)
        layout.setSpacing(10)
        heading, body = self._page_heading(
            "Settings & Safety",
            "FileFlow keeps its behavior intentionally narrow. These safety boundaries are fixed, not hidden preferences.",
        )
        layout.addWidget(heading)
        layout.addWidget(body)

        boundaries_title = QLabel("SAFETY BOUNDARIES")
        boundaries_title.setObjectName("groupTitle")
        layout.addWidget(boundaries_title)
        safety_panel = QFrame()
        safety_panel.setObjectName("safetyPanel")
        safety_layout = QGridLayout(safety_panel)
        safety_layout.setContentsMargins(14, 12, 14, 12)
        safety_layout.setHorizontalSpacing(18)
        safety_layout.setVerticalSpacing(9)
        boundaries = (
            ("Scan scope", "Immediate child files only; folders are never traversed."),
            ("Move scope", "Same-volume moves only, using the exact Preview destination."),
            ("Destination folders", "Category folders such as Documents and Images must already exist. FileFlow never creates them."),
            ("Collision policy", "Existing or case-equivalent destinations are blocked; no overwrite or auto-rename."),
            ("Undo", "Location-only restore after identity and original-path checks."),
            ("Recovery", "Read-only evidence; FileFlow never retries or repairs automatically."),
            ("Batch limit", "FileFlow supports up to 100 real operations per Apply or Undo. Larger plans are blocked, never truncated."),
        )
        for row_index, (name, detail) in enumerate(boundaries):
            name_label = QLabel(name)
            name_label.setObjectName("sectionHeading")
            detail_label = QLabel(detail)
            detail_label.setObjectName("muted")
            detail_label.setWordWrap(True)
            safety_layout.addWidget(name_label, row_index, 0, Qt.AlignmentFlag.AlignTop)
            safety_layout.addWidget(detail_label, row_index, 1)
        safety_layout.setColumnStretch(1, 1)
        layout.addWidget(safety_panel)

        about_title = QLabel("ABOUT")
        about_title.setObjectName("groupTitle")
        layout.addWidget(about_title)
        app_panel = QFrame()
        app_panel.setObjectName("panel")
        app_layout = QGridLayout(app_panel)
        app_layout.setContentsMargins(14, 12, 14, 12)
        app_layout.setHorizontalSpacing(12)
        app_layout.setVerticalSpacing(6)
        app_layout.addWidget(self._brand_mark(44), 0, 0, 2, 1, Qt.AlignmentFlag.AlignTop)
        about_name = QLabel(brand_wordmark())
        about_name.setObjectName("brandName")
        about_name.setTextFormat(Qt.TextFormat.RichText)
        app_layout.addWidget(about_name, 0, 1, 1, 2)
        about_meta = QLabel("by VaultSoft  \u00b7  Preview, organise and safely undo file moves.")
        about_meta.setObjectName("muted")
        about_meta.setWordWrap(True)
        app_layout.addWidget(about_meta, 1, 1, 1, 2)
        version_title = QLabel("Version")
        version_title.setObjectName("fieldHint")
        app_layout.addWidget(version_title, 2, 1)
        app_layout.addWidget(self._version_pill(), 2, 2, Qt.AlignmentFlag.AlignLeft)
        database_title = QLabel("History database")
        database_title.setObjectName("fieldHint")
        app_layout.addWidget(database_title, 3, 1, Qt.AlignmentFlag.AlignTop)
        database_path = QLabel(self.database.path)
        database_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        database_path.setWordWrap(True)
        app_layout.addWidget(database_path, 3, 2)
        app_layout.setColumnStretch(2, 1)
        layout.addWidget(app_panel)
        layout.addStretch(1)
        return page

    def _test_rule_filename(self) -> None:
        result = test_filename_against_rules(self.rule_test_input.text(), self.service.rules, self.service.categories)
        tone = "success" if result.matched else "neutral" if result.valid else "warning"
        self._set_banner(self.rule_test_result, result.message, tone)

    def _set_banner(self, label: QLabel, text: str, tone: str = "neutral") -> None:
        label.setText(text)
        label.setProperty("tone", tone)
        label.style().unpolish(label)
        label.style().polish(label)

    def _show_home(self) -> None:
        self._set_banner(
            self.status_label,
            "Choose a folder to begin. FileFlow analyses immediate child files only and makes no changes during Preview.",
        )
        self.detail.setPlainText("No preview yet. Select a folder to inspect the exact rule matches, destinations, and safety decisions.")
        self._refresh_history()
        self._refresh_apply_state()

    def select_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select folder to analyse")
        if not folder:
            return
        self.selected_folder = folder
        self.folder_label.setText(folder)
        validation = self.service.validate_folder(folder)
        tone = "success" if validation.allowed else "danger"
        self._set_banner(self.status_label, present_analysis(PreviewAnalysis(validation, (), None)).validation_message, tone)
        self.analyse_button.setEnabled(validation.allowed)
        self.reanalyse_button.setEnabled(False)
        self.validate_button.setEnabled(False)
        self.current_analysis = None
        self.current_presentation = None
        self.missing_folders_banner.setVisible(False)
        self.apply_state = ApplyState.NO_PREVIEW
        self._refresh_apply_state()

    def start_analysis(self) -> None:
        if not self.selected_folder or self.worker_thread is not None:
            return
        self._set_banner(self.status_label, "Analysing immediate child files. No files are being moved.")
        self.preview_progress.setRange(0, 0)
        self.preview_progress.setVisible(True)
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
        self.preview_progress.setRange(0, 1)
        self.preview_progress.setValue(1)
        self.preview_progress.setVisible(False)
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
        self._set_banner(self.status_label, structured_error_text(error), "danger")
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
                    item.setForeground(status_color(row.status))
                self.table.setItem(row_index, column, item)
        tone = "success" if summary.ready else "warning" if presentation.rows else "neutral"
        self._set_banner(self.status_label, presentation.validation_message, tone)
        if presentation.missing_destination_folders:
            folder_list = ", ".join(presentation.missing_destination_folders)
            self._set_banner(
                self.missing_folders_banner,
                f"Some destination folders are missing: {folder_list}.\n"
                "Create these folders inside the selected folder, then Analyse Again.",
                "warning",
            )
            self.missing_folders_banner.setVisible(True)
        else:
            self.missing_folders_banner.setVisible(False)
        self._filter_preview_rows()
        self._refresh_apply_state()
        if presentation.rows:
            self.table.selectRow(0)
        else:
            self.detail.setPlainText("No preview rows to display.")

    def _filter_preview_rows(self) -> None:
        selected_filter = self.preview_filter.currentText() if hasattr(self, "preview_filter") else "All statuses"
        status_filter = {
            "Ready": "READY",
            "Blocked": "BLOCKED",
            "Collisions": "COLLISION",
            "Unsupported": "UNSUPPORTED",
        }.get(selected_filter)
        search = self.preview_search.text().strip().casefold() if hasattr(self, "preview_search") else ""
        shown = 0
        for row_index in range(self.table.rowCount()):
            status_item = self.table.item(row_index, 0)
            values = tuple(self.table.item(row_index, column).text() for column in (1, 2) if self.table.item(row_index, column))
            matches_status = status_filter is None or (status_item is not None and status_item.text() == status_filter)
            matches_search = not search or any(search in value.casefold() for value in values)
            visible = matches_status and matches_search
            self.table.setRowHidden(row_index, not visible)
            shown += int(visible)
        if hasattr(self, "preview_filter_count"):
            self.preview_filter_count.setText(f"{shown} shown")

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
        readiness = self.apply_controller.validate_before_confirmation(self.current_analysis.plan)
        if readiness.can_apply:
            blocked = readiness.blocked_count + readiness.unsupported_count
            message = f"Preview is current. {readiness.ready_count} ready, {blocked} blocked."
            detail = "Only Ready rows are actionable. Blocked and unsupported rows will not be moved."
            tone = "success"
        else:
            message = readiness.message
            detail = "This preview cannot be applied. Review the message above, then Analyse Again if the folder changed."
            tone = "warning"
        self._set_banner(self.status_label, message, tone)
        self.detail.setPlainText(detail)
        self.reanalyse_button.setEnabled(self.selected_folder is not None)
        self.apply_state = readiness.state
        self._refresh_apply_state()

    def start_apply(self) -> None:
        if self.apply_thread is not None or self.undo_thread is not None or self.current_analysis is None or self.current_analysis.plan is None:
            return
        plan = self.current_analysis.plan
        readiness = self.apply_controller.validate_before_confirmation(plan)
        if not readiness.can_apply:
            self._set_banner(self.status_label, readiness.message, "warning")
            self.apply_state = readiness.state
            self._refresh_apply_state()
            return
        summary = self.apply_controller.confirmation_summary(plan)
        if not self._confirm_apply(summary):
            self._set_banner(self.status_label, "Move cancelled. No files were changed.")
            self._refresh_apply_state()
            return
        self.apply_state = ApplyState.APPLYING
        self.apply_button.setEnabled(False)
        self._set_banner(self.status_label, "Applying the confirmed plan. FileFlow is verifying every move.")
        self.preview_progress.setRange(0, summary.operation_count)
        self.preview_progress.setValue(0)
        self.preview_progress.setVisible(True)
        self.apply_thread = QThread(self)
        self.apply_worker = ApplyWorker(self.database.path, plan)
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
            "Files will be moved, not copied.",
            "FileFlow will not overwrite existing destination files.",
        ]
        if summary.blocked_count:
            message.append(f"Blocked preview rows not included: {summary.blocked_count}")
        if summary.unsupported_count:
            message.append(f"Unsupported preview rows not included: {summary.unsupported_count}")
        other_non_actionable = summary.non_actionable_count - summary.blocked_count - summary.unsupported_count
        if other_non_actionable:
            message.append(f"Other non-actionable preview rows not included: {other_non_actionable}")
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
        self.preview_progress.setRange(0, total)
        self.preview_progress.setValue(index)
        self._set_banner(self.status_label, f"Moving {index} of {total}: {source}")

    def _apply_finished(self, result: ApplyResult) -> None:
        self.apply_state = result.state
        tone = "danger" if result.recovery_required else "success" if result.succeeded else "warning"
        self._set_banner(self.status_label, result.message, tone)
        self.detail.setPlainText(
            "Apply result\n"
            f"Moved and verified: {result.succeeded}\n"
            f"Failed safely: {result.failed}\n"
            f"Blocked or excluded: {result.blocked}\n"
            f"Recovery review required: {result.recovery_required}\n\n"
            "This preview has been consumed. Choose Analyse Again for a new plan, or open History for persisted details and Undo eligibility."
        )
        self._refresh_history()

    def _apply_failed(self, error: StructuredError) -> None:
        self.apply_state = ApplyState.RECOVERY_REQUIRED
        self._set_banner(self.status_label, structured_error_text(error), "danger")
        self.detail.setPlainText(error.message)
        self._refresh_history()

    def _apply_worker_finished(self) -> None:
        if self.apply_worker is not None:
            self.apply_worker.deleteLater()
        if self.apply_thread is not None:
            self.apply_thread.deleteLater()
        self.apply_worker = None
        self.apply_thread = None
        self.preview_progress.setVisible(False)
        self._refresh_apply_state()

    def _refresh_apply_state(self) -> None:
        plan = self.current_analysis.plan if self.current_analysis and self.current_analysis.plan else None
        readiness = (
            self.apply_controller.readiness(plan, already_applying=True)
            if self.apply_thread is not None
            else self.apply_controller.validate_before_confirmation(plan) if plan is not None else self.apply_controller.readiness(None)
        )
        if self.apply_state in (ApplyState.COMPLETE, ApplyState.RECOVERY_REQUIRED) or self.undo_thread is not None:
            self.apply_button.setEnabled(False)
        else:
            self.apply_button.setEnabled(readiness.can_apply)
        if readiness.can_apply:
            count = readiness.ready_count
            self.apply_button.setText(f"Move {count} File{'s' if count != 1 else ''}")
            self.apply_hint_label.setText(f"{format_bytes(readiness.total_bytes)} across {count} ready move{'s' if count != 1 else ''}")
        else:
            self.apply_button.setText("Move Ready Files")
            self.apply_hint_label.setText(readiness.message)
        if plan is None and readiness.state in (ApplyState.APPLYING, ApplyState.RECOVERY_REQUIRED):
            self._set_banner(self.status_label, readiness.message, "danger")

    def _refresh_history(self) -> None:
        rows = self.apply_controller.history_rows()
        self.history_rows_by_id = {row["id"]: row for row in rows}
        self.history_table.setRowCount(0)
        for row_index, row in enumerate(rows):
            self.history_table.insertRow(row_index)
            values = (
                format_history_time(row["started_at"] or row["approved_at"]),
                batch_source_folder(row),
                str(row["attempted_count"] or 0),
                str(row["succeeded_count"] or 0),
                str(row["failed_count"] or 0),
                str(row["recovery_count"] or 0),
                row["status"],
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row["id"])
                if column == 6:
                    item.setForeground(history_status_color(row["status"]))
                self.history_table.setItem(row_index, column, item)
        unresolved = self.apply_controller.operations_requiring_recovery()
        undo_recovery = self.undo_controller.recovery_inspections()
        lock_status = self.apply_controller.execution_lock_status()
        self.mutation_globally_blocked = bool(unresolved or undo_recovery or lock_status.blocks_apply)
        if unresolved:
            first = unresolved[0]
            self._set_banner(
                self.recovery_banner,
                "Recovery review required. A previous move could not be fully verified, so Apply and Undo are locked.\n"
                f"Source: {first['source_before']}\nDestination: {first['destination'] or ''}\nState: {first['result']}",
                "danger",
            )
        elif undo_recovery:
            first, inspection = undo_recovery[0]
            self._set_banner(
                self.recovery_banner,
                "Recovery review required. A previous Undo could not be fully verified, so Apply and Undo are locked.\n"
                f"Current location: {first['source_before']}\nRestore location: {first['restore_destination']}\n"
                f"State: {first['state']}  Assessment: {inspection.classification.value}",
                "danger",
            )
        else:
            if lock_status.blocks_apply:
                self._set_banner(self.recovery_banner, lock_status.message, "warning")
            else:
                self._set_banner(
                    self.recovery_banner,
                    "Recovery status is clear. History is read-only until you explicitly preview an eligible Undo.",
                    "success",
                )
        if not rows:
            self.history_detail.setPlainText("No Apply history yet. Completed and safely failed operations will appear here.")
            self.preview_undo_button.setEnabled(False)
        elif not self.history_table.selectedItems():
            self.history_table.selectRow(0)

    def _show_history_detail(self) -> None:
        selected = self.history_table.selectedItems()
        if not selected:
            return
        first_item = self.history_table.item(selected[0].row(), 0)
        batch_id = first_item.data(Qt.ItemDataRole.UserRole) if first_item is not None else None
        row = self.history_rows_by_id.get(batch_id)
        if row is None:
            return
        self.current_undo_plan = None
        self.undo_table.setRowCount(0)
        self.confirm_undo_button.setEnabled(False)
        self.confirm_undo_button.setText("Undo Ready Files")
        self._set_banner(
            self.undo_summary_label,
            "Choose Preview Undo to re-check exact file identities and restore paths. No files move during preview.",
        )
        operations = self.apply_controller.history_operations(batch_id)
        lines = [
            f"Date / time: {format_history_time(row['started_at'] or row['approved_at'])}",
            f"Source folder: {batch_source_folder(row)}",
            f"Status: {row['status']}",
            f"Operations: {row['attempted_count'] or 0}",
            f"Moved: {row['succeeded_count'] or 0}",
            f"Failed safely: {row['failed_count'] or 0}",
            f"Recovery required: {row['recovery_count'] or 0}",
        ]
        if operations:
            lines.append("")
            lines.extend(
                f"{operation['result']}: {operation['source_before']} -> {operation['destination'] or ''}"
                for operation in operations
            )
        undo_batches = tuple(batch for batch in self.undo_controller.list_batches() if batch["original_batch_id"] == batch_id)
        if undo_batches:
            lines.append("")
            lines.append("Undo history:")
            for undo_batch in undo_batches:
                lines.append(
                    f"{undo_batch['status']}: restored {undo_batch['succeeded_count'] or 0} of {undo_batch['attempted_count'] or 0}; "
                    f"failed {undo_batch['failed_count'] or 0}; recovery {undo_batch['recovery_count'] or 0}"
                )
        self.history_detail.setPlainText("\n".join(lines))
        self.preview_undo_button.setEnabled(
            int(row["succeeded_count"] or 0) > 0
            and self.apply_thread is None
            and self.undo_thread is None
            and not self.mutation_globally_blocked
        )

    def preview_undo(self) -> None:
        batch_id = self._selected_history_batch_id()
        if not batch_id or self.apply_thread is not None or self.undo_thread is not None:
            return
        try:
            plan = self.undo_controller.create_plan(batch_id)
        except Exception as exc:
            self._set_banner(self.undo_summary_label, f"Undo Preview could not be created safely: {exc}", "danger")
            self.confirm_undo_button.setEnabled(False)
            return
        self.current_undo_plan = plan
        self._render_undo_plan(plan)

    def _render_undo_plan(self, plan: UndoPlan) -> None:
        self.undo_table.setRowCount(0)
        for row_index, operation in enumerate(plan.operations):
            self.undo_table.insertRow(row_index)
            if operation.reason is not None:
                reason = operation.reason.message
            elif operation.metadata_changed:
                reason = "File changed after Apply. Undo restores location only; current contents move with it."
            else:
                reason = "Identity and exact restore path verified."
            values = (
                operation.status.value,
                ntpath.basename(operation.source_path),
                operation.source_path,
                operation.restore_path,
                reason,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setForeground(status_color(operation.status.value))
                self.undo_table.setItem(row_index, column, item)
        readiness = self.undo_controller.readiness(plan)
        tone = "success" if readiness.can_undo else "warning"
        self._set_banner(
            self.undo_summary_label,
            f"Ready: {readiness.ready_count}. Blocked successful moves: {readiness.blocked_count}. "
            f"Apply operations that never moved successfully: {readiness.excluded_apply_count}. {readiness.message}",
            tone,
        )
        self.confirm_undo_button.setText(f"Undo {readiness.ready_count} File{'s' if readiness.ready_count != 1 else ''}")
        self.confirm_undo_button.setEnabled(readiness.can_undo and self.apply_thread is None and self.undo_thread is None)

    def start_undo(self) -> None:
        if self.current_undo_plan is None or self.undo_thread is not None or self.apply_thread is not None:
            return
        readiness = self.undo_controller.validate_before_confirmation(self.current_undo_plan)
        if not readiness.can_undo:
            self._set_banner(self.undo_summary_label, readiness.message + " Create a fresh Undo Preview.", "warning")
            self.confirm_undo_button.setEnabled(False)
            return
        summary = self.undo_controller.confirmation_summary(self.current_undo_plan)
        if not self._confirm_undo(summary):
            self._set_banner(self.undo_summary_label, "Undo cancelled. No files were changed.")
            return
        self.confirm_undo_button.setEnabled(False)
        self.preview_undo_button.setEnabled(False)
        self.apply_button.setEnabled(False)
        self._set_banner(self.undo_summary_label, "Restoring files to their exact original locations.")
        self.undo_progress.setRange(0, summary.operation_count)
        self.undo_progress.setValue(0)
        self.undo_progress.setVisible(True)
        self.undo_thread = QThread(self)
        self.undo_worker = UndoWorker(self.database.path, self.current_undo_plan)
        self.undo_worker.moveToThread(self.undo_thread)
        self.undo_thread.started.connect(self.undo_worker.run)
        self.undo_worker.progress.connect(self._undo_progress)
        self.undo_worker.finished.connect(self._undo_finished)
        self.undo_worker.failed.connect(self._undo_failed)
        self.undo_worker.finished.connect(self.undo_thread.quit)
        self.undo_worker.failed.connect(self.undo_thread.quit)
        self.undo_thread.finished.connect(self._undo_worker_finished)
        self.undo_thread.start()

    def _confirm_undo(self, summary) -> bool:
        message = [
            summary.message,
            "",
            f"Files to restore: {summary.operation_count}",
            f"Blocked successful moves not included: {summary.blocked_count}",
            f"Apply operations that did not succeed: {summary.excluded_apply_count}",
            "FileFlow will never overwrite or auto-rename an occupied original path.",
        ]
        if summary.edited_count:
            message.append(
                f"Files changed after Apply: {summary.edited_count}. Their current contents and metadata will move with them."
            )
        box = QMessageBox(self)
        box.setWindowTitle("Move files back?")
        box.setText("\n".join(message))
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        restore = box.addButton("Move Files Back", QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        return box.clickedButton() is restore

    def _undo_progress(self, source: str, index: int, total: int) -> None:
        self.undo_progress.setRange(0, total)
        self.undo_progress.setValue(index)
        self._set_banner(self.undo_summary_label, f"Restoring {index} of {total}: {source}")

    def _undo_finished(self, result: UndoResult) -> None:
        tone = "danger" if result.recovery_required else "success" if result.succeeded else "warning"
        self.confirm_undo_button.setEnabled(False)
        self.current_undo_plan = None
        # Refresh first: reselecting the history row resets the Undo banner.
        self._refresh_history()
        self._set_banner(self.undo_summary_label, result.message, tone)

    def _undo_failed(self, error: StructuredError) -> None:
        self.confirm_undo_button.setEnabled(False)
        self._refresh_history()
        self._set_banner(self.undo_summary_label, structured_error_text(error), "danger")

    def _undo_worker_finished(self) -> None:
        if self.undo_worker is not None:
            self.undo_worker.deleteLater()
        if self.undo_thread is not None:
            self.undo_thread.deleteLater()
        self.undo_worker = None
        self.undo_thread = None
        self.undo_progress.setVisible(False)
        self._refresh_apply_state()
        # Refreshing the detail resets the Undo banner; keep the result the user just got.
        result_text = self.undo_summary_label.text()
        result_tone = self.undo_summary_label.property("tone") or "neutral"
        self._show_history_detail()
        self._set_banner(self.undo_summary_label, result_text, result_tone)

    def _selected_history_batch_id(self) -> str | None:
        selected = self.history_table.selectedItems()
        if not selected:
            return None
        first = self.history_table.item(selected[0].row(), 0)
        return first.data(Qt.ItemDataRole.UserRole) if first is not None else None

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.worker_thread is not None or self.apply_thread is not None or self.undo_thread is not None:
            QMessageBox.information(
                self,
                "FileFlow is still working",
                "Wait for the current analysis or file operation to finish before closing FileFlow.",
            )
            event.ignore()
            return
        event.accept()


def status_color(status: str) -> QColor:
    normalized = status.upper()
    if normalized in {"READY", "VALID", "SUCCEEDED", "COMPLETE"}:
        return QColor(ACCENT)
    if normalized in {"BLOCKED", "RECOVERY_REQUIRED"}:
        return QColor(RED)
    if normalized in {"COLLISION", "UNSUPPORTED", "STALE", "INTERRUPTED"}:
        return QColor(AMBER)
    return QColor(TEXT_SUB)


def brand_wordmark() -> str:
    return f"FILE<span style='color:{ACCENT}'>FLOW</span>"


def history_status_color(status: str) -> QColor:
    return status_color(status)


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


def format_history_time(value: str | None) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return value
