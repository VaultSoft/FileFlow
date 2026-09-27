"""The FileFlow mark: a document with an arrow flowing out of its corner.

Drawn with QPainter so it stays crisp at every size and needs no bundled
image. The geometry uses the same 24-unit grid as the line icons on the
VaultSoft website and in VaultSoft Hub (icons/fileflow.svg there), so the
app, its .exe and its Hub card all show the same shape.

``tile=True`` adds the dark rounded tile the other VaultSoft app icons use
(window, taskbar and .exe icon); ``tile=False`` is the bare glyph for use
inside the UI.
"""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from .styles import ACCENT, BG, TEXT

ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def _document_path() -> QPainterPath:
    # Page outline with a folded corner; the bottom-right is left open for the arrow.
    path = QPainterPath(QPointF(11, 21))
    path.lineTo(6.5, 21)
    path.arcTo(QRectF(4.5, 17, 4, 4), 270, -90)
    path.lineTo(4.5, 5)
    path.arcTo(QRectF(4.5, 3, 4, 4), 180, -90)
    path.lineTo(13, 3)
    path.lineTo(18, 8)
    path.lineTo(18, 11)
    path.moveTo(13, 3)
    path.lineTo(13, 8)
    path.lineTo(18, 8)
    return path


def _arrow_path() -> QPainterPath:
    path = QPainterPath(QPointF(13, 17))
    path.lineTo(21, 17)
    path.moveTo(18, 14)
    path.lineTo(21, 17)
    path.lineTo(18, 20)
    return path


def paint_mark(painter: QPainter, rect: QRectF, tile: bool = True) -> None:
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    side = min(rect.width(), rect.height())
    origin = QPointF(rect.x() + (rect.width() - side) / 2, rect.y() + (rect.height() - side) / 2)
    if tile:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(BG))
        painter.drawRoundedRect(QRectF(origin.x(), origin.y(), side, side), side * 0.22, side * 0.22)
        glyph = side * 0.74
    else:
        glyph = side
    painter.translate(origin.x() + (side - glyph) / 2, origin.y() + (side - glyph) / 2)
    painter.scale(glyph / 24, glyph / 24)
    # Small renders get a heavier stroke so the mark survives 16 px.
    width = 2.4 if glyph < 28 else 2.0
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for path, colour in ((_document_path(), TEXT), (_arrow_path(), ACCENT)):
        painter.setPen(QPen(QColor(colour), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(path)
    painter.restore()


def mark_pixmap(size: int, tile: bool = True, device_pixel_ratio: float = 1.0) -> QPixmap:
    pixmap = QPixmap(round(size * device_pixel_ratio), round(size * device_pixel_ratio))
    pixmap.setDevicePixelRatio(device_pixel_ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    paint_mark(painter, QRectF(0, 0, size, size), tile)
    painter.end()
    return pixmap


def app_icon() -> QIcon:
    """Window and taskbar icon; matches icon.ico, which make_icon.py renders from the same drawing."""
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(mark_pixmap(size))
    return icon
