"""Light/dark translucent material and protected top-level windows."""

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from .privacy import exclude_from_capture

STYLE = """
QWidget { color: #172537; font-family: 'Segoe UI'; font-size: 13px; background: transparent; }
QLabel#heading { font-size: 19px; font-weight: 600; }
QLabel#muted { color: #526176; }
QLabel#error { color: #9f2437; }
QPushButton { background: rgba(255,255,255,225); border: 1px solid #b7c4d3;
              border-radius: 7px; padding: 6px 10px; min-height: 22px; }
QPushButton:hover { background: #e8f0fa; border-color: #6e98c8; }
QPushButton:pressed { background: #d8e6f6; }
QPushButton:disabled { color: #8591a0; border-color: #d0d8e2; }
QPushButton#primary { background: #245ea2; color: white; border-color: #245ea2; }
QPushButton#primary:hover { background: #1b4e8b; }
QPushButton:focus, QLineEdit:focus, QSpinBox:focus, QPlainTextEdit:focus {
    border: 2px solid #245ea2;
}
QLineEdit, QSpinBox { background: rgba(255,255,255,225); border: 1px solid #b7c4d3;
                     border-radius: 5px; padding: 5px; min-height: 23px; }
QPlainTextEdit { border: 1px solid #bdcbdc; border-radius: 9px; padding: 12px;
                 selection-background-color: #c8dcf5; selection-color: #122a48; }
QPlainTextEdit#answer { font-family: 'Consolas'; }
QTabWidget::pane { border: 0; padding: 8px 0; }
QTabBar::tab { padding: 9px 16px; border-bottom: 2px solid transparent; color: #536278; }
QTabBar::tab:selected { border-bottom-color: #245ea2; color: #173c6a; font-weight: 600; }
QTabBar::tab:focus { background: #dceafb; }
QScrollBar:vertical { width: 12px; background: transparent; }
QScrollBar:horizontal { height: 12px; background: transparent; }
QScrollBar::handle { background: #a7b7cb; border-radius: 5px; min-height: 24px; min-width: 24px; }
QScrollBar::handle:hover { background: #768fae; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QCheckBox { spacing: 8px; }
QRadioButton { spacing: 8px; padding: 8px 0; }
QRadioButton::indicator { width: 17px; height: 17px; border-radius: 9px;
    border: 1px solid #6e98c8; background: #f9fcff; }
QRadioButton::indicator:checked { background: #245ea2; border: 3px solid #b8d4f4; }
QRadioButton:focus { color: #245ea2; }
QListWidget { border: 1px solid #bdcbdc; border-radius: 5px; }
QListWidget::item:selected { background: #c8dcf5; color: #122a48; }
"""

# Explicit pairs keep the hierarchy, selection and keyboard focus readable in both themes.
DARK_STYLE = STYLE
for light, dark in {
    "#172537": "#eef2f7",
    "#526176": "#abb8c9",
    "#9f2437": "#ff9cab",
    "rgba(255,255,255,225)": "rgba(37,43,54,230)",
    "#b7c4d3": "#526175",
    "#e8f0fa": "#354255",
    "#6e98c8": "#97b9e4",
    "#d8e6f6": "#465975",
    "#8591a0": "#7e8a9b",
    "#d0d8e2": "#394556",
    "#245ea2": "#347bcc",
    "#1b4e8b": "#448bd9",
    "#bdcbdc": "#526175",
    "#c8dcf5": "#365d8d",
    "#122a48": "#ffffff",
    "#536278": "#abb8c9",
    "#173c6a": "#e0edff",
    "#dceafb": "#354255",
    "#a7b7cb": "#667a94",
    "#768fae": "#8ba2bf",
    "#f9fcff": "#191e27",
    "#b8d4f4": "#a4c9f4",
}.items():
    DARK_STYLE = DARK_STYLE.replace(light, dark)


class ProtectedWindow(QWidget):
    moved = Signal()

    def __init__(self, title, opacity=94, theme="light"):
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint,
        )
        self.setWindowTitle(title)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.opacity = opacity
        self.set_theme(theme)
        self.protected = False
        self._drag = None

    def set_theme(self, theme):
        if theme not in ("light", "dark"):
            raise ValueError("Unknown theme")
        self.theme = theme
        self.setStyleSheet(DARK_STYLE if theme == "dark" else STYLE)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = self.theme == "dark"
        painter.setPen(QPen(QColor("#526175" if dark else "#b6c5d7"), 1))
        painter.setBrush(
            QColor(*((20, 24, 31) if dark else (249, 252, 255)), round(self.opacity * 2.55))
        )
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 15, 15)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.moved.emit()

    def show_protected(self, activate=False):
        try:
            exclude_from_capture(int(self.winId()))
        except Exception:
            self.protected = False
            self.hide()
            raise
        self.protected = True
        self.show()
        if activate:
            self.raise_()
            self.activateWindow()

    def clamp_to_screen(self):
        screen = QApplication.screenAt(self.geometry().center()) or QApplication.primaryScreen()
        if screen:
            area = screen.availableGeometry()
            self.resize(min(self.width(), area.width()), min(self.height(), area.height()))
            self.move(
                min(max(self.x(), area.left()), area.right() - self.width() + 1),
                min(max(self.y(), area.top()), area.bottom() - self.height() + 1),
            )

    def center(self, screen=None):
        screen = screen or self.screen() or QApplication.primaryScreen()
        if screen:
            area = screen.availableGeometry()
            self.resize(min(self.width(), area.width()), min(self.height(), area.height()))
            self.move(area.center() - self.rect().center())
            self.moved.emit()

    def nudge(self, dx, dy):
        self.move(self.pos() + QPoint(dx, dy))
        self.clamp_to_screen()
        self.moved.emit()

    def restore_geometry(self, coordinates):
        if (
            isinstance(coordinates, list)
            and len(coordinates) == 4
            and all(type(v) is int for v in coordinates)
        ):
            x, y, w, h = coordinates
            if w > 0 and h > 0:
                self.setGeometry(QRect(x, y, w, h))
                self.clamp_to_screen()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        if self._drag is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag)
            self.moved.emit()

    def mouseReleaseEvent(self, event):
        if self._drag is not None:
            self._drag = None
            self.clamp_to_screen()
            self.moved.emit()

    def closeEvent(self, event):
        event.ignore()
        self.hide()
