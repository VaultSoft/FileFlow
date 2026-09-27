from __future__ import annotations


APP_STYLESHEET = """
QMainWindow, QWidget {
    background: #0d1117;
    color: #e6edf3;
    font-family: "Segoe UI";
    font-size: 10pt;
}
QToolTip {
    background: #21262d;
    color: #e6edf3;
    border: 1px solid #3a424c;
    padding: 5px;
}
QFrame#sidebar {
    background: #161b22;
    border: 1px solid #262c33;
    border-radius: 6px;
}
QFrame#brandPanel {
    background: transparent;
    border: 0;
    border-bottom: 1px solid #2d333b;
}
QLabel#brandMark {
    background: #00d4aa;
    color: #061410;
    border-radius: 4px;
    font-size: 14pt;
    font-weight: 800;
    padding: 5px 8px;
}
QLabel#brandName {
    color: #f0f6fc;
    font-size: 16pt;
    font-weight: 750;
}
QLabel#brandMeta, QLabel#muted, QLabel#pageSubtitle, QLabel#fieldHint {
    color: #8b949e;
}
QLabel#eyebrow {
    color: #00d4aa;
    font-size: 9pt;
    font-weight: 700;
}
QLabel#headline, QLabel#pageTitle {
    color: #f0f6fc;
    font-size: 20pt;
    font-weight: 750;
}
QLabel#sectionTitle, QLabel#sectionHeading {
    color: #f0f6fc;
    font-size: 11pt;
    font-weight: 700;
}
QLabel#pathLabel {
    background: #11161c;
    border: 1px solid #30363d;
    border-radius: 5px;
    color: #c9d1d9;
    padding: 9px 11px;
}
QLabel#statusBanner {
    background: #151b22;
    border: 1px solid #30363d;
    border-left: 3px solid #58a6ff;
    border-radius: 5px;
    color: #c9d1d9;
    padding: 9px 11px;
}
QLabel#statusBanner[tone="success"] {
    background: #0d211d;
    border-left-color: #00d4aa;
    color: #b7f5e7;
}
QLabel#statusBanner[tone="warning"] {
    background: #261e0c;
    border-left-color: #e3b341;
    color: #f2d58a;
}
QLabel#statusBanner[tone="danger"] {
    background: #281316;
    border-left-color: #f85149;
    color: #ffb5af;
}
QLabel#recoveryWarning {
    color: #f2d58a;
    background: #261e0c;
    border: 1px solid #725b20;
    border-left: 3px solid #e3b341;
    border-radius: 5px;
    padding: 10px;
}
QFrame#summaryStrip, QFrame#panel, QFrame#rulesTester, QFrame#safetyPanel {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 6px;
}
QListWidget#navigation {
    background: transparent;
    border: 0;
    padding: 4px 0;
    outline: 0;
}
QListWidget#navigation::item {
    min-height: 36px;
    padding: 5px 11px;
    margin: 2px 0;
    border-radius: 4px;
    color: #9da7b3;
}
QListWidget#navigation::item:hover {
    background: #1f252c;
    color: #e6edf3;
}
QListWidget#navigation::item:selected {
    background: #123c34;
    color: #7ee8d1;
    font-weight: 700;
}
QPushButton {
    min-height: 20px;
    background: #00d4aa;
    color: #061410;
    border: 1px solid #00d4aa;
    border-radius: 5px;
    padding: 7px 13px;
    font-weight: 700;
}
QPushButton:hover {
    background: #21dfb9;
    border-color: #21dfb9;
}
QPushButton:pressed {
    background: #00a882;
    border-color: #00a882;
}
QPushButton:disabled {
    background: #252b32;
    border-color: #30363d;
    color: #68727d;
}
QPushButton#secondaryButton {
    background: #21262d;
    color: #d7dee7;
    border: 1px solid #3a424c;
}
QPushButton#secondaryButton:hover {
    background: #2a3139;
    border-color: #56606b;
}
QLineEdit, QComboBox {
    min-height: 22px;
    background: #0d1117;
    color: #e6edf3;
    border: 1px solid #3a424c;
    border-radius: 5px;
    padding: 6px 9px;
    selection-background-color: #007d67;
}
QLineEdit:focus, QComboBox:focus {
    border-color: #00d4aa;
}
QComboBox::drop-down {
    border: 0;
    width: 24px;
}
QComboBox QAbstractItemView {
    background: #161b22;
    color: #e6edf3;
    border: 1px solid #3a424c;
    selection-background-color: #123c34;
}
QTableWidget {
    background: #11161c;
    alternate-background-color: #141a20;
    color: #d7dee7;
    border: 1px solid #30363d;
    border-radius: 5px;
    gridline-color: #242a31;
    outline: 0;
    selection-background-color: #17483e;
    selection-color: #f0f6fc;
}
QTableWidget::item {
    padding: 5px;
}
QHeaderView::section {
    background: #1b2128;
    color: #aeb7c2;
    border: 0;
    border-right: 1px solid #30363d;
    border-bottom: 1px solid #30363d;
    padding: 7px;
    font-weight: 700;
}
QTextEdit {
    background: #11161c;
    color: #c9d1d9;
    border: 1px solid #30363d;
    border-radius: 5px;
    padding: 8px;
    selection-background-color: #007d67;
}
QProgressBar {
    min-height: 7px;
    max-height: 7px;
    background: #252b32;
    border: 0;
    border-radius: 3px;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk {
    background: #00d4aa;
    border-radius: 3px;
}
QScrollBar:vertical {
    background: #11161c;
    width: 11px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #3a424c;
    border-radius: 4px;
    min-height: 24px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
"""
