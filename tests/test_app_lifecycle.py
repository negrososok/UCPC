import queue
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication

from ucpc.app import App
from ucpc.config import Config
from ucpc.history import History
from ucpc.overlay import Overlay
from ucpc.settings import Settings


@pytest.fixture
def bare_app():
    qt = QApplication.instance() or QApplication([])
    qt.setQuitOnLastWindowClosed(False)
    app = App.__new__(App)
    app.qt = qt
    app.config = Config()
    app.events = queue.SimpleQueue()
    app.closed = False
    app.login_running = False
    app.login_id = 0
    app.login_cancel = None
    app.login_url = None
    app.last_reopen = 0
    app.logout_running = False
    app.account_connected = False
    app.last_capture = float("-inf")
    app.last_text = ""
    app.message = ""
    app.history = History()
    app.engine = SimpleNamespace(track=None, config=Config(), cancel=Mock(), close=Mock())
    app.settings = app.panel = Settings(Config(hotkeys={}))
    app.overlay = Overlay()
    app.tray = Mock()
    app.show_panel = Mock()
    app.timer = Mock()
    app.hotkeys = Mock()
    yield app
    app.cancel_login()
    app.panel.deleteLater()
    app.overlay.deleteLater()
    qt.processEvents()


def drain_until(app, predicate):
    deadline = time.monotonic() + 2
    while not predicate() and time.monotonic() < deadline:
        app.tick()
        time.sleep(0.001)
    assert predicate()


def test_second_click_reopens_closed_tab_without_new_worker(bare_app, monkeypatch):
    app = bare_app
    calls = []
    reopened = []

    def login(cancel, on_ready):
        calls.append(1)
        on_ready("https://auth.openai.com/test-only-placeholder")
        cancel.wait(2)
        raise RuntimeError("cancelled")

    app.auth = SimpleNamespace(login=login)
    monkeypatch.setattr("ucpc.app.web_open", lambda url: reopened.append(url) or True)
    app.login()
    drain_until(app, lambda: app.login_url is not None)
    app.login()
    assert len(calls) == 1
    assert len(reopened) == 1
    for _ in range(100):
        app.login()
    assert len(calls) == 1
    assert len(reopened) == 1  # repeated clicks are throttled


def test_cancel_then_retry_ignores_old_success_and_errors(bare_app):
    app = bare_app
    app.login_running = True
    app.login_cancel = threading.Event()
    old_event = app.login_cancel
    old_id = app.login_id
    app.cancel_login()
    assert old_event.is_set()
    assert not app.login_running
    app.login_id += 1
    app.login_running = True
    app.events.put(("login", old_id, "old@example.invalid"))
    app.events.put(("login_error", old_id, "old failure"))
    app.events.put(("login_ready", old_id, "old-url"))
    app.tick()
    assert app.login_running
    assert app.login_url is None
    assert not app.account_connected


def test_login_failure_resets_button_for_retry(bare_app):
    app = bare_app
    app.auth = SimpleNamespace(login=Mock(side_effect=RuntimeError("browser failure")))
    app.login()
    drain_until(app, lambda: not app.login_running)
    app.login()
    drain_until(app, lambda: not app.login_running)
    assert app.auth.login.call_count == 2


def test_capture_during_logout_cannot_send_image(bare_app, monkeypatch):
    app = bare_app
    app.logout_running = True
    grab = Mock()
    monkeypatch.setattr("ucpc.app.capture", grab)
    app.action("capture")
    grab.assert_not_called()


def test_logout_is_serialized_and_login_waits(bare_app):
    app = bare_app
    finish = threading.Event()
    logout = Mock(side_effect=lambda: finish.wait(2) or True)
    app.auth = SimpleNamespace(logout=logout)
    app.logout()
    app.logout()
    app.login()
    assert app.logout_running
    assert not app.login_running
    finish.set()
    drain_until(app, lambda: not app.logout_running)
    assert logout.call_count == 1
    assert not app.account_connected


def test_close_cancels_login_and_is_idempotent(bare_app):
    app = bare_app
    app.login_cancel = threading.Event()
    pending = app.login_cancel
    app.close()
    app.close()
    assert pending.is_set()
    app.hotkeys.close.assert_called_once()
    app.engine.close.assert_called_once()


def test_capture_hotkey_storm_is_throttled(bare_app, monkeypatch):
    app = bare_app
    app.account_connected = True
    monkeypatch.setattr(Config, "prompt", lambda self: "test prompt")
    grab = Mock(return_value="synthetic screenshot")
    monkeypatch.setattr("ucpc.app.capture", grab)
    app.engine.submit = Mock()
    for _ in range(10_000):
        app.action("capture")
    assert grab.call_count == 1
    assert app.engine.submit.call_count == 1


def test_partial_startup_is_cleaned_up():
    app = App.__new__(App)
    app.engine = Mock()
    app.tray = Mock()
    app.close()
    app.engine.close.assert_called_once()
    app.tray.hide.assert_called_once()


def test_capture_restores_helpers_after_capture_failure(bare_app, monkeypatch):
    app = bare_app
    app.panel.show()
    app.overlay.show_protected()
    geometry = app.overlay.geometry()

    def fail(config):
        assert not app.panel.isVisible() and not app.overlay.isVisible()
        raise RuntimeError("capture failed")

    monkeypatch.setattr("ucpc.app.capture", fail)
    with pytest.raises(RuntimeError, match="capture failed"):
        app.capture_screen()
    assert app.panel.isVisible() and app.overlay.isVisible()
    assert app.overlay.geometry() == geometry


def test_text_capture_submits_response(bare_app, monkeypatch):
    app = bare_app
    app.account_connected = True
    app.capture_screen = Mock(return_value="synthetic image")
    app.engine.submit = Mock()
    monkeypatch.setattr(Config, "prompt", lambda self: "test")
    app.action("capture")
    app.engine.submit.assert_called_once()
