from __future__ import annotations


APP_STYLESHEET = """
QMainWindow {
    background: #f6f7f9;
}
QWidget {
    color: #1f2933;
    font-size: 10pt;
}
QListWidget {
    background: #ffffff;
    border: 1px solid #d8dee8;
    border-radius: 6px;
    padding: 6px;
}
QListWidget::item {
    min-height: 34px;
    padding: 6px 10px;
    border-radius: 4px;
}
QListWidget::item:selected {
    background: #e3edf8;
    color: #16324f;
}
QPushButton {
    background: #1f6feb;
    color: #ffffff;
    border: 0;
    border-radius: 5px;
    padding: 8px 12px;
}
QPushButton:disabled {
    background: #c8d0da;
    color: #5d6b7a;
}
QPushButton#secondaryButton {
    background: #ffffff;
    color: #1f2933;
    border: 1px solid #c8d0da;
}
QLabel#headline {
    font-size: 18pt;
    font-weight: 700;
}
QLabel#sectionTitle {
    font-size: 12pt;
    font-weight: 700;
}
QLabel#muted {
    color: #5d6b7a;
}
QFrame#summaryStrip {
    background: #ffffff;
    border: 1px solid #d8dee8;
    border-radius: 6px;
}
QTableWidget {
    background: #ffffff;
    border: 1px solid #d8dee8;
    border-radius: 6px;
    gridline-color: #edf0f4;
}
QHeaderView::section {
    background: #eef2f7;
    border: 0;
    border-right: 1px solid #d8dee8;
    padding: 6px;
    font-weight: 700;
}
QTextEdit {
    background: #ffffff;
    border: 1px solid #d8dee8;
    border-radius: 6px;
    padding: 8px;
}
"""
