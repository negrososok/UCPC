"""Capture-protected, read-only help generated from the active configuration."""

from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .actions import ACTIONS, display_binding
from .theme import ProtectedWindow


class HelpWindow(ProtectedWindow):
    def __init__(self, config):
        super().__init__("UCPC · Допомога", config.overlay_opacity, config.theme)
        self.setMinimumSize(700, 510)
        self.resize(900, 620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        heading = QHBoxLayout()
        title = QLabel("Клавіші UCPC")
        title.setObjectName("heading")
        heading.addWidget(title)
        heading.addStretch()
        hide = QPushButton("Сховати · Esc")
        hide.clicked.connect(self.hide)
        heading.addWidget(hide)
        layout.addLayout(heading)
        self.workflow = QLabel()
        self.workflow.setWordWrap(True)
        layout.addWidget(self.workflow)
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(12)
        self.bind_labels = {}
        half = (len(ACTIONS) + 1) // 2
        for index, (action, name) in enumerate(ACTIONS.items()):
            row, column = index % half, (index // half) * 2
            label = QLabel(name)
            label.setWordWrap(True)
            key = QLabel()
            key.setObjectName("muted")
            key.setWordWrap(True)
            self.bind_labels[action] = key
            grid.addWidget(label, row, column)
            grid.addWidget(key, row, column + 1)
        layout.addLayout(grid)
        layout.addStretch()
        hint = QLabel("Приховування тексту не скасовує генерацію у фоні.\n"
                      "Перевірка звіряє видимий результат запуску; код і тести на сайт не надсилає.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.escape = QShortcut(QKeySequence("Escape"), self)
        self.escape.activated.connect(self.hide)
        self.refresh(config)
        self.center()

    def refresh(self, config):
        for action, label in self.bind_labels.items():
            binding = config.hotkeys.get(action, "")
            label.setText(display_binding(binding) if binding else "Не призначено")
        bindings = [self.bind_labels[action].text() for action in ("capture", "send", "verify")]
        self.workflow.setText(f"Скрін: {bindings[0]} → Відправка: {bindings[1]} → "
                              f"Запуск у редакторі → Перевірка: {bindings[2]}")
        self.opacity = config.overlay_opacity
        self.set_theme(config.theme)
