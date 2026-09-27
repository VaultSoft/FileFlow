"""FileFlow's QSS, in the shared VaultSoft look.

Colours are the VaultSoft palette used by PulseMonitor, WaveScout,
BatteryVault and VaultSoft Hub: near-black #0D1117 background, #131A23
cards with a thin blue-grey border, teal #00D4AA accent, amber for
attention and red for recovery/danger. Segoe UI throughout.

Only containers get a background. A blanket ``QWidget { background }``
paints every label inside a card as a dark box, so labels are transparent
unless an objectName below gives them a surface.
"""
from __future__ import annotations

BG_DEEP = "#060A10"
BG = "#0D1117"
SURFACE = "#131A23"
SURFACE_HOVER = "#1B2535"
BORDER = "#1C2B3A"
BORDER_HI = "#263848"
TEXT = "#E2EAF4"
TEXT_SUB = "#A0AEC0"
TEXT_MUTED = "#617080"
TITLE = "#FFFFFF"
ACCENT = "#00D4AA"
ACCENT_HOVER = "#00ECC0"
ACCENT_DIM = "#00A882"
ACCENT_DEEP = "#007A60"
AMBER = "#F0A500"
RED = "#E74C3C"
BLUE = "#7CC4FA"


def _tint(hex_colour: str, alpha: float) -> str:
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r}, {g}, {b}, {alpha})"


APP_STYLESHEET = f"""
QMainWindow, QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: "Segoe UI";
    font-size: 10pt;
}}
QLabel {{
    background: transparent;
}}
QToolTip {{
    background: {SURFACE_HOVER};
    color: {TEXT};
    border: 1px solid {BORDER_HI};
    border-radius: 4px;
    padding: 4px 8px;
}}

/* ---- sidebar and brand ------------------------------------------------ */
QFrame#sidebar {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 11px;
}}
QFrame#brandPanel {{
    background: transparent;
    border: 0;
    border-bottom: 1px solid {BORDER};
}}
QLabel#brandMark {{
    background: {_tint(ACCENT, 0.08)};
    border: 1px solid {_tint(ACCENT, 0.22)};
    border-radius: 10px;
}}
QLabel#brandName {{
    color: {TITLE};
    font-size: 13pt;
    font-weight: 800;
    letter-spacing: 2px;
}}
QLabel#brandMeta {{
    color: {TEXT_MUTED};
    font-size: 8.5pt;
}}
QLabel#versionPill {{
    color: {ACCENT};
    background: {_tint(ACCENT, 0.09)};
    border: 1px solid {_tint(ACCENT, 0.27)};
    border-radius: 4px;
    font-size: 8pt;
    font-weight: 600;
    padding: 1px 6px;
}}
QFrame#safetyNote {{
    background: {_tint(ACCENT, 0.05)};
    border: 1px solid {_tint(ACCENT, 0.18)};
    border-radius: 8px;
}}
QLabel#safetyNoteText {{
    color: {TEXT_SUB};
    font-size: 8.5pt;
}}

/* ---- text hierarchy ----------------------------------------------------- */
QLabel#muted, QLabel#pageSubtitle, QLabel#fieldHint {{
    color: {TEXT_SUB};
}}
QLabel#eyebrow {{
    color: {TEXT_MUTED};
    font-size: 8pt;
    font-weight: 700;
    letter-spacing: 1.5px;
}}
QLabel#groupTitle {{
    color: {TEXT_MUTED};
    font-size: 8pt;
    font-weight: 700;
    letter-spacing: 1.5px;
    padding-top: 8px;
}}
QLabel#pageTitle {{
    color: {TITLE};
    font-size: 18pt;
    font-weight: 700;
}}
QLabel#sectionTitle {{
    color: {TITLE};
    font-size: 13pt;
    font-weight: 700;
}}
QLabel#sectionHeading {{
    color: {TEXT};
    font-size: 10.5pt;
    font-weight: 700;
}}
QLabel#pathLabel {{
    background: {BG};
    border: 1px solid {BORDER_HI};
    border-radius: 8px;
    color: {TEXT};
    padding: 8px 11px;
}}

/* ---- status banners ----------------------------------------------------- */
QLabel#statusBanner {{
    background: {_tint(BLUE, 0.06)};
    border: 1px solid {BORDER};
    border-left: 3px solid {BLUE};
    border-radius: 8px;
    color: {TEXT};
    padding: 9px 12px;
}}
QLabel#statusBanner[tone="success"] {{
    background: {_tint(ACCENT, 0.07)};
    border-color: {_tint(ACCENT, 0.22)};
    border-left-color: {ACCENT};
    color: #B8F5E6;
}}
QLabel#statusBanner[tone="warning"] {{
    background: {_tint(AMBER, 0.08)};
    border-color: {_tint(AMBER, 0.25)};
    border-left-color: {AMBER};
    color: #F8D98A;
}}
QLabel#statusBanner[tone="danger"] {{
    background: {_tint(RED, 0.09)};
    border-color: {_tint(RED, 0.30)};
    border-left-color: {RED};
    color: #FFB8B0;
}}

/* ---- cards -------------------------------------------------------------- */
QFrame#summaryStrip, QFrame#panel, QFrame#rulesTester, QFrame#safetyPanel {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}

/* ---- navigation --------------------------------------------------------- */
QListWidget#navigation {{
    background: transparent;
    border: 0;
    padding: 4px 0;
    outline: 0;
}}
QListWidget#navigation::item {{
    min-height: 34px;
    padding: 4px 12px;
    margin: 2px 0;
    border-radius: 8px;
    border-left: 3px solid transparent;
    color: {TEXT_SUB};
}}
QListWidget#navigation::item:hover {{
    background: {SURFACE_HOVER};
    color: {TEXT};
}}
QListWidget#navigation::item:selected {{
    background: {_tint(ACCENT, 0.10)};
    border-left: 3px solid {ACCENT};
    color: {ACCENT};
    font-weight: 700;
}}

/* ---- buttons: primary (teal) and secondary (quiet) ----------------------- */
QPushButton {{
    min-height: 20px;
    background: {ACCENT};
    color: {BG};
    border: 1px solid {ACCENT};
    border-radius: 8px;
    padding: 7px 16px;
    font-weight: 700;
}}
QPushButton:hover {{
    background: {ACCENT_HOVER};
    border-color: {ACCENT_HOVER};
}}
QPushButton:pressed {{
    background: {ACCENT_DIM};
    border-color: {ACCENT_DIM};
}}
QPushButton:disabled {{
    background: {SURFACE_HOVER};
    border-color: {BORDER};
    color: {TEXT_MUTED};
}}
QPushButton#secondaryButton, QMessageBox QPushButton {{
    background: transparent;
    color: {TEXT};
    border: 1px solid {BORDER_HI};
    font-weight: 600;
}}
QPushButton#secondaryButton:hover, QMessageBox QPushButton:hover {{
    background: {SURFACE_HOVER};
    border-color: {ACCENT};
    color: {TITLE};
}}
QPushButton#secondaryButton:pressed, QMessageBox QPushButton:pressed {{
    background: {BORDER};
}}
QPushButton#secondaryButton:disabled {{
    background: transparent;
    border-color: {BORDER};
    color: {TEXT_MUTED};
}}
QMessageBox QPushButton {{
    min-width: 96px;
}}
QMessageBox QPushButton:default {{
    border-color: {ACCENT};
    color: {ACCENT};
}}
QMessageBox QLabel {{
    color: {TEXT};
}}

/* ---- inputs ------------------------------------------------------------- */
QLineEdit, QComboBox {{
    min-height: 22px;
    background: {BG};
    color: {TEXT};
    border: 1px solid {BORDER_HI};
    border-radius: 8px;
    padding: 5px 10px;
    selection-background-color: {ACCENT_DEEP};
}}
QLineEdit:hover, QComboBox:hover {{
    border-color: {_tint(ACCENT, 0.45)};
}}
QLineEdit:focus, QComboBox:focus {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{
    border: 0;
    width: 24px;
}}
QComboBox QAbstractItemView {{
    background: {SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER_HI};
    selection-background-color: {_tint(ACCENT, 0.18)};
    selection-color: {TITLE};
}}

/* ---- tables and detail panes -------------------------------------------- */
QTableWidget {{
    background: {SURFACE};
    alternate-background-color: {BG};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 10px;
    gridline-color: {BORDER};
    outline: 0;
    selection-background-color: {_tint(ACCENT, 0.16)};
    selection-color: {TITLE};
}}
QTableWidget::item {{
    padding: 5px 8px;
    border: 0;
}}
QTableWidget::item:selected {{
    background: {_tint(ACCENT, 0.16)};
    color: {TITLE};
    border: 0;
}}
QHeaderView {{
    background: transparent;
}}
QHeaderView::section {{
    background: {BG_DEEP};
    color: {TEXT_SUB};
    border: 0;
    border-bottom: 1px solid {BORDER_HI};
    padding: 8px;
    font-size: 8.5pt;
    font-weight: 700;
    letter-spacing: 0.6px;
}}
QTableCornerButton::section {{
    background: {BG_DEEP};
    border: 0;
}}
QTextEdit {{
    background: {SURFACE};
    color: {TEXT_SUB};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 8px 10px;
    selection-background-color: {ACCENT_DEEP};
}}

/* ---- progress and scrollbars -------------------------------------------- */
QProgressBar {{
    min-height: 6px;
    max-height: 6px;
    background: {SURFACE_HOVER};
    border: 0;
    border-radius: 3px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background: {ACCENT};
    border-radius: 3px;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
    border: 0;
}}
QScrollBar::handle:vertical {{
    background: {BORDER_HI};
    border-radius: 3px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
    background: {ACCENT_DEEP};
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
    border: 0;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER_HI};
    border-radius: 3px;
    min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {ACCENT_DEEP};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0;
    height: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}
"""
