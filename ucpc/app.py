import json
import queue
import threading
import time
from dataclasses import replace

from PySide6.QtCore import QLockFile, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QMenu,
    QSystemTrayIcon,
)

from .actions import ACTIONS, display_binding
from .auth import USAGE_URL, Auth, state_dir
from .capture import capture
from .config import ROOT, Config, atomic_write, save_config
from .diagnostics import configure as configure_diagnostics
from .engine import Engine, friendly_error
from .help_panel import HelpWindow
from .history import History, Track
from .hotkeys import Hotkeys
from .overlay import Overlay
from .privacy import exclude_from_capture, flush_desktop
from .settings import Settings


def tray_icon() -> QIcon:
    icon = QIcon(str(ROOT / "logo" / "logo.png"))
    if not icon.isNull():
        return icon
    # Keep startup usable if an incomplete source checkout is missing the asset.
    pixmap = QPixmap(32, 32)
    pixmap.fill(QColor("#173a50"))
    painter = QPainter(pixmap)
    painter.setPen(QColor("white"))
    font = painter.font()
    font.setPixelSize(24)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), 0x84, "U")
    painter.end()
    return QIcon(pixmap)


class App:
    def __init__(self, qt: QApplication, config: Config, smoke: bool = False):
        self.qt, self.config = qt, config
        self.events = queue.SimpleQueue()
        self.auth = Auth()
        self.history = History()
        self.pending_images = []
        self.retry_images = ()
        self.copy_id = 0
        self.engine = Engine(
            config, self.auth, lambda track, msg: self.events.put(("error", track, msg))
        )
        self.login_running = False
        self.login_id = 0
        self.login_cancel = None
        self.login_url = None
        self.login_new_registration = False
        self.last_reopen = 0.0
        self.logout_running = False
        self.closed = False
        self.last_capture = float("-inf")
        self.message = "Готово. Скріншот робиться лише за хоткеєм."
        self.settings = Settings(config)
        self.help = HelpWindow(config)
        self.panel = self.settings  # Account/login events share this protected window.
        self.overlay = Overlay(
            config.overlay_font_size,
            config.overlay_opacity,
            config.overlay_width,
            config.overlay_height,
            config.theme,
        )
        self.state_path = state_dir() / "ui.json"
        self.overlay.update_bindings(config.hotkeys)
        try:
            state = json.loads(self.state_path.read_text(encoding="utf8"))
            self.overlay.restore_geometry(state.get("overlay"))
        except (OSError, ValueError, AttributeError, TypeError):
            pass
        self.ui_timer = QTimer(qt)
        self.ui_timer.setSingleShot(True)
        self.ui_timer.timeout.connect(self.save_ui)
        self.overlay.moved.connect(lambda: self.ui_timer.start(350))
        self.overlay.capture_button.clicked.connect(lambda: self.action("capture"))
        self.overlay.send_button.clicked.connect(lambda: self.action("send"))
        self.overlay.verify_button.clicked.connect(lambda: self.action("verify"))
        self.overlay.clear_images_button.clicked.connect(lambda: self.action("clear_images"))
        self.overlay.settings_button.clicked.connect(self.show_panel)
        self.overlay.previous_button.clicked.connect(lambda: self.action("previous"))
        self.overlay.next_button.clicked.connect(lambda: self.action("next"))
        self.overlay.smaller.clicked.connect(lambda: self.action("font_smaller"))
        self.overlay.larger.clicked.connect(lambda: self.action("font_larger"))
        self.settings.apply_requested.connect(self.apply_settings)
        self.settings.recording_changed.connect(self.recording_changed)
        self.settings.login_button.clicked.connect(lambda: self.login())
        self.settings.new_login_button.clicked.connect(lambda: self.login(new_account=True))
        self.settings.cancel_login_button.clicked.connect(self.cancel_login)
        self.settings.logout_button.clicked.connect(self.logout)
        self.settings.usage_button.clicked.connect(lambda: web_open(USAGE_URL))
        self.settings.models_button.clicked.connect(self.load_models)
        try:
            self.settings.prompt_editor.setPlainText(config.prompt())
        except OSError as exc:
            self.settings.set_error(friendly_error(exc))
        self.panel.account.setText(
            self.auth.info() if config.auth_mode == "chatgpt" else "Окремий OpenAI API"
        )
        self.account_connected = "вхід потрібен" not in self.panel.account.text()
        self.settings.set_accounts(self.auth.accounts())
        icon = tray_icon()
        qt.setWindowIcon(icon)
        self.overlay.setWindowIcon(icon)
        self.help.setWindowIcon(icon)
        self.settings.setWindowIcon(icon)
        self.tray = QSystemTrayIcon(icon, qt)
        self.menu = QMenu()
        self.menu.aboutToShow.connect(self.protect_menu)
        self.build_menu()
        # The tray menu is a separate native window and needs its own exclusion.
        self.protect_menu()
        self.tray.activated.connect(self.activated)
        self.tray.show()
        self.hotkeys = Hotkeys(qt, int(self.settings.winId()), {}, self.action)
        conflict = False
        try:
            self.hotkeys.replace_bindings(config.hotkeys)
        except (ValueError, RuntimeError) as exc:
            self.settings.set_error(str(exc))
            self.message = str(exc)
            conflict = True
        self.timer = QTimer(qt)
        self.timer.timeout.connect(self.tick)
        self.timer.start(100)
        qt.aboutToQuit.connect(self.close)
        if smoke:
            QTimer.singleShot(1500, qt.quit)
        else:
            if config.overlay_enabled:
                self.show_overlay()
            if conflict or not self.account_connected:
                self.show_panel()
                if not self.account_connected:
                    self.settings.tabs.setCurrentIndex(2)
        self.tick()

    def build_menu(self):
        self.menu.clear()
        for action in (
            "capture",
            "send",
            "verify",
            "help",
            "clear_images",
            "overlay",
            "settings",
            "copy",
            "previous",
            "next",
            "cancel",
            "center",
        ):
            binding = display_binding(self.config.hotkeys.get(action, ""))
            self.menu.addAction(
                f"{ACTIONS[action]}    {binding}", lambda checked=False, a=action: self.action(a)
            )
        self.menu.addSeparator()
        self.menu.addAction("Закрити UCPC", self.qt.quit)

    def protect_menu(self):
        try:
            exclude_from_capture(int(self.menu.winId()))
            self.tray.setContextMenu(self.menu)
        except (RuntimeError, OSError) as exc:
            self.tray.setContextMenu(None)
            self.error(friendly_error(exc))

    def recording_changed(self, recording):
        if self.closed:
            return
        try:
            self.hotkeys.suspend() if recording else self.hotkeys.resume()
        except (ValueError, RuntimeError) as exc:
            self.settings.set_error(str(exc))

    def load_models(self):
        if self.closed:
            return
        if self.login_running or self.logout_running:
            self.settings.set_error("Дочекайся завершення входу або виходу з акаунта")
            return
        if self.config.auth_mode != "chatgpt" or not self.account_connected:
            self.settings.set_error("Спочатку увійди через ChatGPT")
            return
        if not self.settings.models_button.isEnabled():
            return
        self.settings.models_button.setEnabled(False)
        self.settings.set_error("")
        attempt = self.login_id

        def run():
            try:
                self.events.put(("models", attempt, self.auth.models()))
            except Exception as exc:  # noqa: BLE001 — worker reports through Qt's event queue.
                self.events.put(("models_error", attempt, friendly_error(exc)))

        threading.Thread(target=run, daemon=True, name="UCPC-models").start()

    def apply_settings(self):
        old = self.config
        prompt_path = ROOT / old.system_prompt_file
        changed_prompt = False
        rebound = False
        original = ""
        try:
            candidate = self.settings.draft(old)
            prompt = self.settings.prompt_editor.toPlainText().strip()
            if not prompt:
                raise ValueError("Інструкція не може бути порожньою")
            original = prompt_path.read_text(encoding="utf-8-sig")
            if candidate.hotkeys != self.hotkeys.bindings:
                self.hotkeys.replace_bindings(candidate.hotkeys)
                rebound = True
            if prompt != original.strip():
                atomic_write(prompt_path, prompt + "\n")
                changed_prompt = True
            save_config(candidate)
        except Exception as exc:  # noqa: BLE001 — preserve the working settings on any failure.
            rollback_errors = []
            if changed_prompt:
                try:
                    atomic_write(prompt_path, original)
                except OSError:
                    rollback_errors.append("Не вдалося відновити попередню інструкцію")
            if rebound:
                try:
                    self.hotkeys.replace_bindings(old.hotkeys)
                except (ValueError, RuntimeError):
                    rollback_errors.append("Перевір попередні бінди: одна з клавіш уже зайнята")
            self.settings.set_error(" · ".join([friendly_error(exc)] + rollback_errors))
            return
        self.config = self.engine.config = candidate
        self.settings.config = candidate
        self.overlay.font_size = candidate.overlay_font_size
        self.overlay.zoom(0)
        self.overlay.opacity = self.settings.opacity = candidate.overlay_opacity
        # Applying a theme must preserve the size/position chosen by dragging.
        self.overlay.set_theme(candidate.theme)
        self.settings.set_theme(candidate.theme)
        self.help.refresh(candidate)
        self.overlay.update_bindings(candidate.hotkeys)
        self.settings.set_error("")
        self.message = "Налаштування збережено"
        self.build_menu()
        self.save_ui()
        self.tick()

    def save_ui(self):
        if not hasattr(self, "state_path"):
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            g = self.overlay.geometry()
            atomic_write(
                self.state_path, json.dumps({"overlay": [g.x(), g.y(), g.width(), g.height()]})
            )
        except OSError as exc:
            self.error("Не вдалося зберегти позицію вікна: " + friendly_error(exc))

    def show_panel(self):
        try:
            self.panel.show_protected(activate=True)
        except (RuntimeError, OSError) as exc:
            self.panel.hide()
            self.error(friendly_error(exc))

    def show_overlay(self):
        try:
            self.overlay.show_protected()
        except (RuntimeError, OSError) as exc:
            self.error(friendly_error(exc))

    def capture_screen(self):
        visible = [window for window in (self.panel, self.overlay, getattr(self, "help", None))
                   if window is not None and window.isVisible()]
        try:
            for window in visible:
                window.hide()
            if visible:
                flush_desktop()
            return capture(self.config)
        finally:
            for window in visible:
                if window is self.overlay:
                    window.show_protected()
                else:
                    exclude_from_capture(int(window.winId()))
                    window.show()

    def activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_panel()

    def action(self, action):
        try:
            if action == "capture":
                if self.closed or self.logout_running:
                    raise RuntimeError("UCPC завершує роботу або виходить з акаунта")
                if time.monotonic() - self.last_capture < 0.35:
                    return
                self.last_capture = time.monotonic()
                if not self.pending_images:
                    self.retry_images = ()
                image = self.capture_screen()
                self.pending_images.append(image)
                self.message = (
                    f"Зібрано скріншотів: {len(self.pending_images)}. Відправ їх окремим біндом."
                )
            elif action == "clear_images":
                self.pending_images.clear()
                self.retry_images = ()
                self.message = "Зібрані скріншоти очищено"
            elif action == "send":
                if self.closed or self.logout_running:
                    raise RuntimeError("UCPC завершує роботу або виходить з акаунта")
                if self.login_running:
                    raise RuntimeError("Спочатку заверши або скасуй вхід у ChatGPT")
                if self.config.auth_mode == "chatgpt" and not self.account_connected:
                    self.show_panel()
                    self.settings.tabs.setCurrentIndex(2)
                    raise RuntimeError("Спочатку увійди через ChatGPT у налаштуваннях UCPC")
                prompt = self.config.prompt()
                if not prompt:
                    raise ValueError("Системний промпт порожній")
                previous = self.engine.track
                failed = (previous and previous.kind == "answer"
                          and previous.snapshot()[2] not in {"", "Скасовано"})
                images = tuple(self.pending_images) or (self.retry_images if failed else ())
                if not images:
                    self.message = "Спочатку додай скріншот біндом зйомки"
                    self.tick()
                    return
                self.overlay.remember_position()
                track = Track(task_images=images)
                self.engine.submit(images, prompt, track)
                self.history.add(track)
                self.retry_images = images
                self.pending_images.clear()
                if self.config.overlay_enabled:
                    self.show_overlay()
                self.message = f"Відправлено скріншотів: {len(images)}"
            elif action == "verify":
                if self.closed or self.logout_running:
                    raise RuntimeError("UCPC завершує роботу або виходить з акаунта")
                if self.login_running:
                    raise RuntimeError("Спочатку заверши або скасуй вхід у ChatGPT")
                source = self.history.current
                text, complete, error = source.snapshot() if source else ("", False, "")
                candidate = source.candidate if source and source.kind == "verification" else text
                if (not source or not complete or not candidate.strip() or not source.task_images
                        or (error and source.kind != "verification")):
                    self.message = "Спочатку отримай відповідь до повної умови задачі"
                    self.tick()
                    return
                if self.config.auth_mode == "chatgpt" and not self.account_connected:
                    raise RuntimeError("Спочатку увійди через ChatGPT у налаштуваннях UCPC")
                prompt = self.config.prompt()
                if not prompt:
                    raise ValueError("Системний промпт порожній")
                result_image = self.capture_screen()
                track = Track(task_images=source.task_images, kind="verification", candidate=candidate)
                self.overlay.remember_position()
                self.engine.submit((*source.task_images, result_image), prompt, track)
                self.history.add(track)
                if self.config.overlay_enabled:
                    self.show_overlay()
                self.message = "Перевіряємо вибрану відповідь за свіжим результатом запуску"
            elif action in {"previous", "next"}:
                self.overlay.remember_position()
                if self.history.move(-1 if action == "previous" else 1) is not None:
                    self.show_overlay()
                    self.message = "Перемкнуто відповідь"
            elif action == "cancel":
                self.engine.cancel()
                self.message = "Генерацію скасовано. Отриманий текст залишився в історії."
            elif action == "overlay":
                self.overlay.hide() if self.overlay.isVisible() else self.show_overlay()
            elif action == "settings":
                self.panel.hide() if self.panel.isVisible() else self.show_panel()
            elif action == "help":
                if self.help.isVisible():
                    self.help.hide()
                else:
                    self.help.refresh(self.config)
                    self.help.show_protected()
            elif action == "copy":
                self.copy_id += 1
                track = self.history.current
                text = track.snapshot()[0] if track else ""
                if text.strip():
                    self.copy_text(text, self.copy_id)
                else:
                    self.message = "Ще немає тексту для копіювання"
            elif action in {"overlay_up", "overlay_down", "scroll_left", "scroll_right"}:
                self.tick()  # Select the current response before acting on its scrollbars.
                self.overlay.scroll(
                    -1 if action in {"overlay_up", "scroll_left"} else 1,
                    self.config.scroll_lines,
                    action.startswith("scroll_"),
                )
            elif action in {"top", "bottom"}:
                self.tick()
                self.overlay.edge(action == "bottom")
            elif action.startswith("move_"):
                step = self.config.move_step
                dx, dy = {
                    "move_up": (0, -step),
                    "move_down": (0, step),
                    "move_left": (-step, 0),
                    "move_right": (step, 0),
                }[action]
                self.overlay.nudge(dx, dy)
            elif action == "center":
                self.overlay.center()
            elif action in {"font_smaller", "font_larger"}:
                self.overlay.zoom(-1 if action == "font_smaller" else 1)
                candidate = replace(self.config, overlay_font_size=self.overlay.font_size)
                save_config(candidate)
                self.config = self.engine.config = candidate
            else:
                raise ValueError("Невідома дія: " + action)
            self.tick()
        except Exception as exc:  # noqa: BLE001 — isolate errors from the Qt event loop.
            self.error(friendly_error(exc))

    def copy_text(self, text, copy_id, attempt=0):
        if self.closed or copy_id != self.copy_id:
            return
        try:
            clipboard = self.qt.clipboard()
            clipboard.setText(text)
            if clipboard.text() == text:
                self.message = "Поточну відповідь скопійовано"
            elif attempt < 10:
                # Let Qt service Windows' delayed-rendering messages between retries.
                self.message = "Копіюємо текст…"
                QTimer.singleShot(20, lambda: self.copy_text(text, copy_id, attempt + 1))
            else:
                raise RuntimeError("Не вдалося скопіювати текст. Спробуй бінд ще раз.")
        except Exception as exc:  # noqa: BLE001 — timer callbacks need the same error boundary.
            self.error(friendly_error(exc))
        self.tick()

    def error(self, message):
        self.message = message
        if self.config.notifications:
            self.tray.showMessage("UCPC", message, QSystemTrayIcon.MessageIcon.Warning)

    def login(self, *, new_account=False):
        if self.closed:
            return
        if self.logout_running:
            self.message = "Дочекайся завершення виходу з акаунта"
            return
        if self.login_running:
            if new_account and not self.login_new_registration:
                self.cancel_login()
            else:
                self.reopen_login()
                return
        selected = self.settings.selected_account_id()
        self.login_account_change = new_account or selected != self.settings.active_account_id
        if self.login_account_change:
            self.copy_id += 1  # Cancel delayed clipboard writes belonging to the old account.
            self.engine.cancel()
        self.login_running = True
        self.login_new_registration = new_account
        self.login_id += 1
        self.settings.models_button.setEnabled(True)
        attempt = self.login_id
        cancel = self.login_cancel = threading.Event()
        self.login_url = None
        self.last_reopen = 0.0
        self.message = "Заверши вхід у браузері. UCPC використовуватиме ліміти ChatGPT."
        options = {"new_account": True} if new_account else (
            {"account_id": selected} if selected is not None else {}
        )

        def run():
            try:
                email = self.auth.login(
                    cancel=cancel,
                    on_ready=lambda url: self.events.put(("login_ready", attempt, url)),
                    **options,
                )
                self.events.put(("login", attempt, email))
            except Exception as exc:  # noqa: BLE001 — worker reports failures to the tray.
                self.events.put(("login_error", attempt, friendly_error(exc)))

        threading.Thread(target=run, daemon=True, name="UCPC-login").start()

    def reopen_login(self):
        if self.login_running:
            if self.login_url and time.monotonic() - self.last_reopen >= 0.75:
                self.last_reopen = time.monotonic()
                if web_open(self.login_url):
                    self.message = "Вкладку входу відкрито повторно. Заверши вхід у браузері."
                else:
                    self.error("Браузер не відкрився. Скасуй вхід і спробуй знову.")
            elif not self.login_url:
                self.message = "Готуємо вхід. Зачекай мить або натисни «Скасувати вхід»."
            return

    def cancel_login(self):
        if self.login_cancel is not None:
            self.login_cancel.set()
        self.login_id += 1  # delayed events from this attempt are now obsolete
        self.login_running = False
        self.login_cancel = None
        self.login_url = None
        self.message = "Вхід скасовано. Continue with ChatGPT почне нову спробу."
        self.login_new_registration = False

    def logout(self):
        if self.closed or self.logout_running:
            return
        if self.login_running:
            self.error("Дочекайся завершення поточного входу")
            return
        self.engine.cancel()
        self.logout_running = True
        self.copy_id += 1
        self.login_id += 1
        self.settings.models_button.setEnabled(True)
        self.settings.model_list.clear()
        self.settings.model_list.hide()
        self.history.clear()
        self.pending_images.clear()
        self.retry_images = ()
        self.message = "Вихід з акаунта…"

        def run():
            try:
                confirmed = self.auth.logout()
                self.events.put(("logout", None, confirmed))
            except Exception as exc:  # noqa: BLE001 — worker reports failures to the tray.
                self.events.put(("logout_error", None, friendly_error(exc)))

        threading.Thread(target=run, daemon=True, name="UCPC-logout").start()

    def tick(self):
        if self.closed:
            return
        while not self.events.empty():
            kind, track, value = self.events.get()
            if kind.startswith("login") and track != self.login_id:
                continue
            if kind.startswith("models"):
                if track != self.login_id:
                    continue
                self.settings.models_button.setEnabled(True)
                if kind == "models":
                    self.settings.set_models(value)
                else:
                    self.settings.set_error(value)
                continue
            if kind == "error":
                if track is None or track is self.engine.track:
                    self.error(value)
            elif kind == "login":
                self.login_running = False
                self.login_url = None
                self.login_cancel = None
                self.account_connected = True
                self.settings.set_accounts(self.auth.accounts())
                self.settings.model_list.clear()
                self.settings.model_list.hide()
                if self.login_account_change:
                    self.history.clear()
                    self.pending_images.clear()
                    self.retry_images = ()
                self.panel.account.setText("ChatGPT: " + value + " · використовується підписка")
                self.message = "Вхід завершено. Запити UCPC використовуватимуть ліміти ChatGPT."
                self.settings.set_error("")
                self.show_panel()
            elif kind == "login_error":
                self.login_running = False
                self.login_url = None
                self.login_cancel = None
                self.error(value)
            elif kind == "login_ready":
                self.login_url = value
                self.message = (
                    "Заверши вхід у браузері. Якщо закрив вкладку, натисни "
                    "Continue with ChatGPT повторно."
                )
            elif kind == "logout":
                self.logout_running = False
                self.account_connected = False
                self.panel.account.setText("ChatGPT: вхід потрібен")
                self.message = (
                    "Вихід завершено"
                    if value
                    else "Локальний вихід завершено; відкликання на сервері не підтверджено. "
                    "Відключи UCPC у налаштуваннях ChatGPT."
                )
            elif kind == "logout_error":
                self.logout_running = False
                self.error(value)
        track = self.history.current
        text, complete, error = track.snapshot() if track else ("", False, "")
        progress = (
            "Помилка запиту"
            if error
            else "Текст готовий"
            if complete
            else f"{track.current_phase()}… {int(time.time() - track.created_at)} с"
            if track
            else "Текстовий режим"
        )
        if track and (model := track.model_name()):
            progress += " · " + model
        detail = error or self.message
        detail += f" · Зібрано скріншотів: {len(self.pending_images)}"
        self.panel.status.setText(detail + "\n" + progress)
        self.settings.cancel_login_button.setEnabled(self.login_running)
        self.settings.new_login_button.setEnabled(not self.logout_running)
        self.settings.account_list.setEnabled(not self.login_running and not self.logout_running)
        self.settings.logout_button.setEnabled(self.account_connected and not self.logout_running)
        self.overlay.update_response(track, text, detail + "\n" + progress)
        self.overlay.counter.setText(
            f"{'Перевірка' if track.kind == 'verification' else 'Відповідь'} "
            f"{self.history.index + 1} / {len(self.history.items)}"
            if track
            else "Поки немає відповідей"
        )
        self.overlay.previous_button.setEnabled(self.history.index > 0)
        self.overlay.next_button.setEnabled(self.history.index + 1 < len(self.history.items))
        retryable = (
            self.engine.track
            and self.engine.track.kind == "answer"
            and self.retry_images
            and self.engine.track.snapshot()[2] not in {"", "Скасовано"}
        )
        self.overlay.send_button.setText(
            f"Відправити · {len(self.pending_images)}"
            if self.pending_images
            else "Повторити"
            if retryable
            else "Відправити · 0"
        )
        self.overlay.send_button.setEnabled(bool(self.pending_images or retryable))
        self.overlay.clear_images_button.setEnabled(bool(self.pending_images or self.retry_images))
        self.overlay.verify_button.setEnabled(bool(
            track and complete and track.task_images
            and (track.candidate if track.kind == "verification" else text)
            and (not error or track.kind == "verification")
        ))
        self.tray.setToolTip("UCPC · Текстовий помічник")

    def close(self):
        if getattr(self, "closed", False):
            return
        self.closed = True
        if hasattr(self, "pending_images"):
            self.pending_images.clear()
            self.retry_images = ()
        self.cleanup_errors = []
        if hasattr(self, "overlay"):
            self.save_ui()
        if hasattr(self, "login_id"):
            self.cancel_login()
        # Startup may fail halfway through construction. Release everything that exists.
        for name, method in (
            ("timer", "stop"),
            ("hotkeys", "close"),
            ("engine", "close"),
            ("ui_timer", "stop"),
            ("tray", "hide"),
            ("menu", "hide"),
            ("overlay", "hide"),
            ("panel", "hide"),
            ("help", "hide"),
        ):
            resource = getattr(self, name, None)
            if resource is not None:
                try:
                    getattr(resource, method)()
                except Exception as exc:  # noqa: BLE001 — finish all cleanup after device failures
                    self.cleanup_errors.append(friendly_error(exc))


def web_open(url: str):
    return QDesktopServices.openUrl(QUrl(url))


def run(config: Config, smoke: bool = False):
    qt = QApplication([])
    qt.setApplicationName("UCPC")
    qt.setQuitOnLastWindowClosed(False)
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    configure_diagnostics(directory)
    lock = QLockFile(str(directory / "app.lock"))
    if not lock.tryLock(0):
        raise RuntimeError("UCPC уже працює. Знайди значок U у системному треї.")
    if not QSystemTrayIcon.isSystemTrayAvailable():
        raise RuntimeError("Системний трей недоступний")
    assistant = App.__new__(App)
    try:
        assistant.__init__(qt, config, smoke)
        return qt.exec()
    finally:
        assistant.close()
        lock.unlock()
