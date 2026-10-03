"""Protected settings with keyboard recording, a theme switch and inline errors."""

from dataclasses import replace

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .actions import ACTIONS, DEFAULT_HOTKEYS, display_binding
from .hotkeys import KEYS, parse_hotkey
from .theme import ProtectedWindow


class BindEdit(QPushButton):
    recording_changed = Signal(bool)

    def __init__(self, binding, parent=None):
        super().__init__(parent)
        self.binding = binding
        self.recording = False
        self.clicked.connect(self.start_recording)
        self.refresh()

    def refresh(self):
        self.setText(display_binding(self.binding) if self.binding else "Не призначено")

    def start_recording(self):
        if self.recording:
            return
        self.recording = True
        self.setText("Клавіші або Mouse4/Mouse5…")
        self.recording_changed.emit(True)

    def stop_recording(self):
        if self.recording:
            self.recording = False
            self.refresh()
            self.recording_changed.emit(False)

    def focusOutEvent(self, event):
        self.stop_recording()
        super().focusOutEvent(event)

    def event(self, event):
        # Let the recorder receive Ctrl+S/Ctrl+1 too, rather than triggering the
        # settings window's local shortcuts while capturing a custom binding.
        if getattr(self, "recording", False) and event.type() == QEvent.Type.ShortcutOverride:
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        if not self.recording:
            return super().keyPressEvent(event)
        key = event.key()
        mods = event.modifiers()
        if key == Qt.Key.Key_Escape or key in {Qt.Key.Key_Tab, Qt.Key.Key_Backtab}:
            self.stop_recording()
            if key != Qt.Key.Key_Escape:
                super().keyPressEvent(event)
            return
        if (
            key in {Qt.Key.Key_Backspace, Qt.Key.Key_Delete}
            and mods == Qt.KeyboardModifier.NoModifier
        ):
            self.binding = ""
            self.stop_recording()
            return
        if key in {Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta}:
            return
        parts = []
        for flag, name in (
            (Qt.KeyboardModifier.ControlModifier, "ctrl"),
            (Qt.KeyboardModifier.AltModifier, "alt"),
            (Qt.KeyboardModifier.ShiftModifier, "shift"),
            (Qt.KeyboardModifier.MetaModifier, "win"),
        ):
            if mods & flag:
                parts.append(name)
        vk = event.nativeVirtualKey()
        name = next((name for name, code in KEYS.items() if code == vk), None)
        if name is None and 0x70 <= vk <= 0x87:
            name = f"f{vk - 0x70 + 1}"
        if name is None and (0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A):
            name = chr(vk).lower()
        if name is None:  # Qt synthetic events and non-Windows test environments.
            special = {
                Qt.Key.Key_Up: "up",
                Qt.Key.Key_Down: "down",
                Qt.Key.Key_Left: "left",
                Qt.Key.Key_Right: "right",
                Qt.Key.Key_Home: "home",
                Qt.Key.Key_End: "end",
                Qt.Key.Key_PageUp: "pageup",
                Qt.Key.Key_PageDown: "pagedown",
                Qt.Key.Key_Space: "space",
                Qt.Key.Key_Return: "enter",
            }
            name = special.get(key)
            if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F24:
                name = f"f{key - Qt.Key.Key_F1 + 1}"
            elif 0x30 <= key <= 0x39 or 0x41 <= key <= 0x5A:
                name = chr(key).lower()
        if parts and name:
            binding = "+".join(parts + [name])
            parse_hotkey(binding)
            self.binding = binding
            self.stop_recording()
        event.accept()

    def mousePressEvent(self, event):
        name = {
            Qt.MouseButton.BackButton: "mouse4",
            Qt.MouseButton.ForwardButton: "mouse5",
        }.get(event.button())
        if name is None:
            return super().mousePressEvent(event)
        if self.recording:
            parts = [modifier for flag, modifier in (
                (Qt.KeyboardModifier.ControlModifier, "ctrl"),
                (Qt.KeyboardModifier.AltModifier, "alt"),
                (Qt.KeyboardModifier.ShiftModifier, "shift"),
                (Qt.KeyboardModifier.MetaModifier, "win"),
            ) if event.modifiers() & flag]
            binding = "+".join(parts + [name])
            try:
                parse_hotkey(binding)
            except ValueError as error:
                self.setText(str(error))
            else:
                self.binding = binding
                self.stop_recording()
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() in (Qt.MouseButton.BackButton, Qt.MouseButton.ForwardButton):
            event.accept()
            return
        super().mouseReleaseEvent(event)


class Settings(ProtectedWindow):
    apply_requested = Signal()
    recording_changed = Signal(bool)

    def __init__(self, config):
        super().__init__("UCPC · Налаштування", config.overlay_opacity, config.theme)
        self.config = config
        self.setMinimumSize(620, 500)
        self.resize(760, 650)
        self.shortcuts = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        heading = QHBoxLayout()
        title = QLabel("Налаштування UCPC")
        title.setObjectName("heading")
        heading.addWidget(title)
        heading.addStretch()
        hide = QPushButton("Сховати · Esc")
        hide.clicked.connect(self.hide)
        heading.addWidget(hide)
        layout.addLayout(heading)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.bind_edits = {}
        control = QWidget()
        controls = QVBoxLayout(control)
        hint = QLabel(
            "Mouse4 — скрін, Mouse5 — відправка. Інші стандартні бінди: Ctrl+Win + клавіша.\n"
            "Win — клавіша з логотипом Windows.\n"
            "Tab — перейти до кнопки; Enter або Space — записати бінд.\n"
            "Під час запису: Esc — скасувати; Backspace — прибрати бінд.\n"
            "Бокові кнопки: Mouse4/Mouse5, окремо або з Ctrl/Shift. Win із мишкою відкриває «Пуск».\n"
            "Зміни діятимуть після «Застосувати». Ctrl+1…4 — перемикати вкладки."
        )
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        controls.addWidget(hint)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        rows = QWidget()
        grid = QGridLayout(rows)
        for row, (action, label) in enumerate(ACTIONS.items()):
            button = BindEdit(config.hotkeys.get(action, ""))
            button.setAccessibleName(label)
            button.recording_changed.connect(self.recording_changed.emit)
            self.bind_edits[action] = button
            grid.addWidget(QLabel(label), row, 0)
            grid.addWidget(button, row, 1)
        grid.setColumnStretch(1, 1)
        scroll.setWidget(rows)
        controls.addWidget(scroll, 1)
        self.reset_bindings = QPushButton("Стандартні бінди")
        self.reset_bindings.clicked.connect(self.defaults)
        controls.addWidget(self.reset_bindings, 0, Qt.AlignmentFlag.AlignLeft)
        self.tabs.addTab(control, "1 · Клавіші")

        appearance = QWidget()
        appearance_layout = QVBoxLayout(appearance)
        appearance_layout.setSpacing(14)
        appearance_layout.addWidget(QLabel("Тема вікна відповіді та налаштувань"))
        self.theme_group = QButtonGroup(self)
        self.theme_buttons = {}
        for theme, label in (("light", "Біла"), ("dark", "Чорна")):
            button = QRadioButton(label)
            button.setChecked(config.theme == theme)
            self.theme_buttons[theme] = button
            self.theme_group.addButton(button)
            appearance_layout.addWidget(button)
        appearance_hint = QLabel(
            "Вибери тему й натисни «Застосувати» або Ctrl+S.\n\n"
            "Розмір відповіді змінюй, потягнувши нижній правий кут.\n"
            "Вікно можна перетягнути за заголовок або посунути біндами.\n"
            "Позиція та розмір зберігаються на цьому ПК."
        )
        appearance_hint.setObjectName("muted")
        appearance_hint.setWordWrap(True)
        appearance_layout.addWidget(appearance_hint)
        appearance_layout.addStretch()
        self.tabs.addTab(appearance, "2 · Вигляд")

        account = QWidget()
        account_layout = QVBoxLayout(account)
        self.account = QLabel()
        self.account.setWordWrap(True)
        account_layout.addWidget(self.account)
        self.login_button = QPushButton("Увійти через ChatGPT / повторно відкрити вкладку")
        self.new_login_button = QPushButton("Інший акаунт / робочий простір")
        self.account_list = QListWidget()
        self.account_list.setAccessibleName("Збережені підключення ChatGPT")
        self.account_list.setMaximumHeight(80)
        self.account_list.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.account_list.hide()
        self.active_account_id = None
        account_layout.addWidget(self.account_list)
        self.cancel_login_button = QPushButton("Скасувати вхід")
        self.logout_button = QPushButton("Вийти з акаунта")
        self.usage_button = QPushButton("Перевірити ліміти в браузері")
        for button in (
            self.login_button,
            self.new_login_button,
            self.cancel_login_button,
            self.logout_button,
            self.usage_button,
        ):
            account_layout.addWidget(button)
        account_form = QFormLayout()
        self.model = QLineEdit(config.vision_model)
        self.model.setAccessibleName("Модель ChatGPT")
        self.model.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        account_form.addRow("Модель ChatGPT", self.model)
        self.models_button = QPushButton("Показати доступні моделі")
        account_form.addRow(self.models_button)
        self.model_list = QListWidget()
        self.model_list.setAccessibleName("Доступні моделі ChatGPT")
        self.model_list.setMaximumHeight(120)
        self.model_list.hide()
        self.model_list.itemActivated.connect(self.choose_model)
        self.model_list.itemClicked.connect(self.choose_model)
        account_form.addRow(self.model_list)
        self.reasoning_levels = ("low", "medium", "high", "xhigh", "max")
        self.reasoning = QSlider(Qt.Orientation.Horizontal)
        self.reasoning.setRange(0, len(self.reasoning_levels) - 1)
        self.reasoning.setValue(self.reasoning_levels.index(config.reasoning_effort))
        self.reasoning.setAccessibleName("Рівень аналізу: стрілки змінюють значення")
        self.reasoning_label = QLabel()
        self.reasoning.valueChanged.connect(self.update_reasoning_label)
        self.update_reasoning_label()
        reasoning_row = QWidget()
        reasoning_layout = QHBoxLayout(reasoning_row)
        reasoning_layout.setContentsMargins(0, 0, 0, 0)
        reasoning_layout.addWidget(self.reasoning, 1)
        reasoning_layout.addWidget(self.reasoning_label)
        account_form.addRow("Рівень аналізу", reasoning_row)
        self.monitor = self.spin(
            account_form, "Монітор: 1 — перший; 0 — усі", 0, 32, config.monitor
        )
        account_layout.addLayout(account_form)
        self.verify_answer = QCheckBox("Перевіряти відповідь перед показом (два запити)")
        self.verify_answer.setChecked(config.verify_answer)
        self.verify_answer.setAccessibleDescription(
            "Повільніше й використовує більше ліміту. Код не запускається."
        )
        account_layout.addWidget(self.verify_answer)
        info = QLabel(
            "Вхід відкривається у звичайному браузері.\n"
            "Для зміни акаунта або workspace натисни «Інший акаунт / робочий простір».\n"
            "Збережене підключення: вибери його у списку й натисни кнопку входу.\n"
            "Налаштування застосовуються до наступного запиту.\n"
            "Історія текстів зберігається лише до закриття UCPC."
        )
        info.setWordWrap(True)
        info.setObjectName("muted")
        account_layout.addWidget(info)
        account_layout.addStretch()
        account_scroll = QScrollArea()
        account_scroll.setWidgetResizable(True)
        account_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        account_scroll.setWidget(account)
        self.tabs.addTab(account_scroll, "3 · Акаунт")

        prompt_tab = QWidget()
        prompt_layout = QVBoxLayout(prompt_tab)
        prompt_layout.addWidget(QLabel("Інструкція, з якою модель аналізує кожен скріншот:"))
        self.prompt_editor = QPlainTextEdit()
        self.prompt_editor.setAccessibleName("Системна інструкція")
        self.prompt_editor.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        prompt_layout.addWidget(self.prompt_editor, 1)
        self.tabs.addTab(prompt_tab, "4 · Інструкція")
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setObjectName("muted")
        layout.addWidget(self.status)
        self.error_label = QLabel()
        self.error_label.setObjectName("error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)
        bottom = QHBoxLayout()
        privacy = QLabel("Вікно виключене із захоплення Windows")
        privacy.setObjectName("muted")
        bottom.addWidget(privacy)
        bottom.addStretch()
        self.apply_button = QPushButton("Застосувати · Ctrl+S")
        self.apply_button.setObjectName("primary")
        self.apply_button.clicked.connect(self.apply_requested.emit)
        bottom.addWidget(self.apply_button)
        layout.addLayout(bottom)
        for sequence, callback in (("Escape", self.hide), ("Ctrl+S", self.apply_requested.emit)):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(callback)
            self.shortcuts.append(shortcut)
        for i in range(4):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self)
            shortcut.activated.connect(lambda i=i: self.tabs.setCurrentIndex(i))
            self.shortcuts.append(shortcut)
        self.center()

    def set_accounts(self, accounts):
        self.account_list.clear()
        self.active_account_id = None
        for account in accounts:
            item = QListWidgetItem(account["label"])
            item.setData(Qt.ItemDataRole.UserRole, account["id"])
            self.account_list.addItem(item)
            if account["active"]:
                self.active_account_id = account["id"]
                self.account_list.setCurrentItem(item)
        self.account_list.setVisible(len(accounts) > 1)

    def selected_account_id(self):
        item = self.account_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    @staticmethod
    def spin(form, label, lo, hi, value):
        spin = QSpinBox()
        spin.setRange(lo, hi)
        spin.setValue(value)
        spin.setAccessibleName(label)
        spin.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        form.addRow(label, spin)
        return spin

    def choose_model(self, item):
        self.model.setText(item.data(Qt.ItemDataRole.UserRole))

    def update_reasoning_label(self):
        level = self.reasoning_levels[self.reasoning.value()]
        names = {"low": "Швидкий", "medium": "Збалансований", "high": "Ретельний",
                 "xhigh": "Глибокий", "max": "Максимальний"}
        self.reasoning_label.setText(f"{names[level]} · {level}")
        self.reasoning.setAccessibleDescription(
            f"{names[level]}: {level}. Вищий рівень може потребувати більше часу."
        )

    def set_models(self, models):
        self.model_list.clear()
        for model in models:
            slug = model["slug"]
            item = QListWidgetItem(f"{model.get('display_name', slug)} · {slug}")
            item.setData(Qt.ItemDataRole.UserRole, slug)
            self.model_list.addItem(item)
        self.model_list.show()
        self.models_button.setEnabled(True)
        if not models:
            self.set_error("Акаунт не повернув доступних моделей")

    def defaults(self):
        for action, edit in self.bind_edits.items():
            edit.binding = DEFAULT_HOTKEYS[action]
            edit.refresh()
        self.set_error("")

    def draft(self, base):
        config = replace(
            base,
            hotkeys={a: e.binding for a, e in self.bind_edits.items()},
            theme=next(name for name, button in self.theme_buttons.items() if button.isChecked()),
            vision_model=self.model.text().strip(),
            monitor=self.monitor.value(),
            verify_answer=self.verify_answer.isChecked(),
            reasoning_effort=self.reasoning_levels[self.reasoning.value()],
        )
        config.validate()
        return config

    def set_error(self, message):
        self.error_label.setText(message)
        self.error_label.setVisible(bool(message))

    def hideEvent(self, event):
        for edit in self.bind_edits.values():
            edit.stop_recording()
        super().hideEvent(event)
