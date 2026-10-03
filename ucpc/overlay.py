"""Keyboard-controlled reader; streaming never jumps away from the user's reading position."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetricsF, QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizeGrip,
    QVBoxLayout,
)

from .actions import DEFAULT_HOTKEYS, display_binding
from .privacy import exclude_from_capture
from .theme import ProtectedWindow


class Overlay(ProtectedWindow):
    def update_bindings(self, bindings):
        names = {action: display_binding(bindings.get(action, "")) or "не призначено"
                 for action in ("capture", "send", "help")}
        self.text.setPlaceholderText(
            f"Збери скріншоти: {names['capture']}.\nВідправ їх разом: {names['send']}.\n\n"
            f"Допомога: {names['help']}."
        )

    def __init__(self, font_size=18, opacity=94, width=660, height=580, theme="light"):
        super().__init__("UCPC · Відповідь", opacity, theme)
        self.setMinimumSize(440, 320)
        self.resize(width, height)
        self.font_size = font_size
        self._track = None
        self._text = ""
        self._status = ""
        self.shortcuts = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 12)
        layout.setSpacing(10)
        header = QHBoxLayout()
        self.heading = QLabel("UCPC")
        self.heading.setObjectName("heading")
        self.counter = QLabel("Поки немає відповідей")
        self.counter.setObjectName("muted")
        header.addWidget(self.heading)
        header.addWidget(self.counter)
        header.addStretch()
        self.settings_button = QPushButton("Налаштування")
        self.verify_button = QPushButton("Перевірити")
        self.hide_button = QPushButton("Сховати")
        self.hide_button.clicked.connect(self.hide)
        header.addWidget(self.verify_button)
        header.addWidget(self.settings_button)
        header.addWidget(self.hide_button)
        layout.addLayout(header)
        toolbar = QHBoxLayout()
        self.capture_button = QPushButton("+ Скріншот")
        self.send_button = QPushButton("Відправити · 0")
        self.clear_images_button = QPushButton("Очистити")
        self.capture_button.setObjectName("primary")
        self.previous_button = QPushButton("←")
        self.previous_button.setAccessibleName("Попередня відповідь")
        self.next_button = QPushButton("→")
        self.next_button.setAccessibleName("Наступна відповідь")
        self.smaller = QPushButton("A−")
        self.smaller.setAccessibleName("Зменшити шрифт")
        self.larger = QPushButton("A+")
        self.larger.setAccessibleName("Збільшити шрифт")
        for button in (
            self.capture_button,
            self.send_button,
            self.clear_images_button,
            self.previous_button,
            self.next_button,
        ):
            toolbar.addWidget(button)
        toolbar.addStretch()
        toolbar.addWidget(self.smaller)
        toolbar.addWidget(self.larger)
        layout.addLayout(toolbar)
        self.status = QLabel("Готово до нового скріншота")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.text = QPlainTextEdit()
        self.text.setObjectName("answer")
        self.text.setReadOnly(True)
        self.text.setAccessibleName("Текст відповіді нейромережі")
        self.text.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.update_bindings(DEFAULT_HOTKEYS)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.text, 1)
        bottom = QHBoxLayout()
        self.footer = QLabel("Виключення із захоплення ще не перевірено")
        self.footer.setObjectName("muted")
        self.footer.setWordWrap(True)
        bottom.addWidget(self.footer, 1)
        self.grip = QSizeGrip(self)
        bottom.addWidget(self.grip, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(bottom)
        shortcut = QShortcut(QKeySequence("Escape"), self)
        shortcut.activated.connect(self.hide)
        self.shortcuts.append(shortcut)
        self.zoom(0)
        self.center()

    def show_protected(self, activate=False):
        # Keep this boundary explicit so exclusion failure is testable independently.
        try:
            exclude_from_capture(int(self.winId()))
        except Exception:
            self.protected = False
            self.hide()
            raise
        self.protected = True
        self.footer.setText("Виключення із захоплення увімкнено")
        self.show()
        if activate:
            self.raise_()
            self.activateWindow()

    def zoom(self, delta):
        self.font_size = min(32, max(12, self.font_size + delta))
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPixelSize(self.font_size)
        self.text.setFont(font)
        self.text.setStyleSheet(
            f"QPlainTextEdit {{ font-family: 'Consolas'; font-size: {self.font_size}px; }}"
        )
        self.text.setTabStopDistance(QFontMetricsF(font).horizontalAdvance(" ") * 4)
        self.smaller.setEnabled(self.font_size > 12)
        self.larger.setEnabled(self.font_size < 32)

    def scroll(self, direction, lines=5, horizontal=False):
        bar = self.text.horizontalScrollBar() if horizontal else self.text.verticalScrollBar()
        amount = max(1, round(self.font_size * lines * 0.6)) if horizontal else lines
        bar.setValue(bar.value() + direction * amount)
        self.remember_position()

    def edge(self, end=False):
        bar = self.text.verticalScrollBar()
        bar.setValue(bar.maximum() if end else 0)
        self.remember_position()

    def remember_position(self):
        if self._track is not None and hasattr(self._track, "scroll_y"):
            self._track.scroll_y = self.text.verticalScrollBar().value()
            self._track.scroll_x = self.text.horizontalScrollBar().value()

    def update_response(self, track, text, status):
        if status != self._status:
            self.status.setText(status)
            self._status = status
        if track is not self._track:
            self.remember_position()
            self.text.setPlainText(text)
            self.text.verticalScrollBar().setValue(getattr(track, "scroll_y", 0))
            self.text.horizontalScrollBar().setValue(getattr(track, "scroll_x", 0))
        elif text != self._text:
            vertical = self.text.verticalScrollBar().value()
            horizontal = self.text.horizontalScrollBar().value()
            if text.startswith(self._text):
                cursor = QTextCursor(self.text.document())
                cursor.movePosition(QTextCursor.MoveOperation.End)
                cursor.insertText(text[len(self._text) :])
            else:
                selected = self.text.textCursor()
                points = []
                for position in (selected.anchor(), selected.position()):
                    probe = QTextCursor(self.text.document())
                    probe.setPosition(position)
                    points.append((probe.blockNumber(), probe.positionInBlock(), probe.block().text()))
                self.text.setPlainText(text)
                restored = QTextCursor(self.text.document())
                for index, (number, column, old_line) in enumerate(points):
                    block = self.text.document().findBlockByNumber(number)
                    if not block.isValid():
                        position = self.text.document().characterCount() - 1
                    else:
                        new_line = block.text()
                        old_indent = len(old_line) - len(old_line.lstrip(" \t"))
                        new_indent = len(new_line) - len(new_line.lstrip(" \t"))
                        if old_line.lstrip(" \t") == new_line.lstrip(" \t"):
                            column = (column - old_indent + new_indent
                                      if column >= old_indent else min(column, new_indent))
                        position = block.position() + max(0, min(column, block.length() - 1))
                    restored.setPosition(position, QTextCursor.MoveMode.KeepAnchor if index
                                         else QTextCursor.MoveMode.MoveAnchor)
                self.text.setTextCursor(restored)
            self.text.verticalScrollBar().setValue(vertical)
            self.text.horizontalScrollBar().setValue(horizontal)
        self._track, self._text = track, text
