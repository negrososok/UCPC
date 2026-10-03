import queue
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication

from ucpc.app import App
from ucpc.config import Config
from ucpc.help_panel import HelpWindow
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
    app.login_new_registration = False
    app.last_reopen = 0
    app.logout_running = False
    app.account_connected = False
    app.last_capture = float("-inf")
    app.last_text = ""
    app.message = ""
    app.history = History()
    app.pending_images = []
    app.retry_images = ()
    app.copy_id = 0
    app.engine = SimpleNamespace(track=None, config=Config(), cancel=Mock(), close=Mock())
    app.settings = app.panel = Settings(Config(hotkeys={}))
    app.overlay = Overlay()
    app.help = HelpWindow(app.config)
    app.tray = Mock()
    app.show_panel = Mock()
    app.timer = Mock()
    app.hotkeys = Mock()
    yield app
    app.cancel_login()
    app.panel.deleteLater()
    app.overlay.deleteLater()
    app.help.deleteLater()
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


def test_different_account_button_cancels_old_login_and_starts_new_registration(bare_app):
    app = bare_app
    old_cancel = app.login_cancel = threading.Event()
    app.login_running = True
    app.login_url = "old-placeholder"
    app.auth = SimpleNamespace(login=Mock(side_effect=RuntimeError("synthetic denial")))
    app.login(new_account=True)
    drain_until(app, lambda: not app.login_running)
    assert old_cancel.is_set()
    assert app.auth.login.call_args.kwargs["new_account"] is True
    assert app.auth.login.call_count == 1
    app.engine.cancel.assert_called_once()


def test_repeated_different_account_click_reopens_same_attempt(bare_app, monkeypatch):
    app = bare_app
    calls = []
    reopened = []

    def login(cancel, on_ready, **options):
        calls.append(options)
        on_ready("https://auth.openai.com/synthetic-new-account")
        cancel.wait(2)
        raise RuntimeError("cancelled")

    app.auth = SimpleNamespace(login=login)
    monkeypatch.setattr("ucpc.app.web_open", lambda url: reopened.append(url) or True)
    app.login(new_account=True)
    drain_until(app, lambda: app.login_url is not None)
    for _ in range(100):
        app.login(new_account=True)
    assert calls == [{"new_account": True}] and len(reopened) == 1


def test_successful_account_switch_clears_old_history_and_updates_saved_accounts(bare_app):
    from ucpc.history import Track

    app = bare_app
    app.history.add(Track(text="old private answer"))
    app.pending_images = ["old screenshot"]
    app.retry_images = ("old retry",)
    accounts = [{"id": "new", "label": "New registration", "active": True}]
    app.auth = SimpleNamespace(login=Mock(return_value="new@example.invalid"),
                               accounts=lambda: accounts)
    app.login(new_account=True)
    drain_until(app, lambda: not app.login_running)
    assert app.account_connected and not app.history.items and not app.pending_images
    assert not app.retry_images and app.settings.selected_account_id() == "new"


def test_failed_account_switch_keeps_current_history_and_account(bare_app):
    from ucpc.history import Track

    app = bare_app
    app.account_connected = True
    app.history.add(Track(text="existing answer"))
    app.pending_images = ["next screenshot"]
    app.auth = SimpleNamespace(login=Mock(side_effect=RuntimeError("synthetic denial")))
    app.login(new_account=True)
    drain_until(app, lambda: not app.login_running)
    assert app.account_connected and app.history.current.text == "existing answer"
    assert app.pending_images == ["next screenshot"]


@pytest.mark.parametrize("action", ["send", "verify"])
def test_request_cannot_start_during_account_login(bare_app, action):
    app = bare_app
    app.login_running = True
    app.account_connected = True
    app.pending_images = ["pending"]
    app.engine.submit = Mock()
    app.action(action)
    app.engine.submit.assert_not_called()
    assert "заверши або скасуй" in app.message
    assert app.pending_images == ["pending"]


def test_capture_during_logout_cannot_send_image(bare_app, monkeypatch):
    app = bare_app
    app.logout_running = True
    grab = Mock()
    monkeypatch.setattr("ucpc.app.capture", grab)
    app.action("capture")
    grab.assert_not_called()


@pytest.mark.parametrize("hide_with", ["hotkey", "window_close"])
def test_hiding_overlay_does_not_cancel_generation(bare_app, hide_with):
    from ucpc.history import Track

    app = bare_app
    track = Track()
    app.engine.track = track
    app.history.add(track)
    app.overlay.show_protected()
    if hide_with == "hotkey":
        app.action("overlay")
    else:
        app.overlay.close()
    assert not app.overlay.isVisible()
    app.engine.cancel.assert_not_called()
    app.engine.close.assert_not_called()
    track.append_text("Completed while hidden")
    track.finish()
    app.tick()
    assert not app.overlay.isVisible()
    app.action("overlay")
    assert app.overlay.isVisible() and app.overlay.text.toPlainText() == track.text


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
    assert app.engine.submit.call_count == 0
    assert len(app.pending_images) == 1


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


def test_capture_collects_multiple_images_and_send_submits_once_in_order(bare_app, monkeypatch):
    app = bare_app
    app.account_connected = True
    app.capture_screen = Mock(side_effect=["synthetic image 1", "synthetic image 2"])
    app.engine.submit = Mock()
    monkeypatch.setattr(Config, "prompt", lambda self: "test")
    app.action("capture")
    app.last_capture -= 1
    app.action("capture")
    app.engine.submit.assert_not_called()
    app.engine.cancel.assert_not_called()
    app.action("send")
    app.engine.submit.assert_called_once()
    assert app.engine.submit.call_args.args[:2] == (
        ("synthetic image 1", "synthetic image 2"),
        "test",
    )
    assert app.pending_images == [] and len(app.history.items) == 1
    app.action("send")
    app.engine.submit.assert_called_once()


def test_send_while_signed_out_preserves_collected_screenshots(bare_app):
    bare_app.pending_images = ["synthetic image"]
    bare_app.action("send")
    assert bare_app.pending_images == ["synthetic image"]
    assert not bare_app.history.items
    bare_app.show_panel.assert_called_once()


def test_failed_submit_preserves_batch_and_previous_history(bare_app, monkeypatch):
    bare_app.account_connected = True
    bare_app.pending_images = ["synthetic image"]
    bare_app.engine.submit = Mock(side_effect=RuntimeError("Synthetic submit failure"))
    monkeypatch.setattr(Config, "prompt", lambda self: "test")
    bare_app.action("send")
    assert bare_app.pending_images == ["synthetic image"]
    assert not bare_app.history.items


def test_send_can_retry_failed_batch_without_recapturing(bare_app, monkeypatch):
    from ucpc.history import Track

    bare_app.account_connected = True
    bare_app.retry_images = ("first image", "second image")
    prior = Track(text="partial response")
    prior.finish("Synthetic timeout")
    bare_app.engine.track = prior
    bare_app.history.add(prior)
    bare_app.engine.submit = Mock()
    monkeypatch.setattr(Config, "prompt", lambda self: "test")
    bare_app.action("send")
    assert bare_app.engine.submit.call_args.args[0] == bare_app.retry_images
    assert len(bare_app.history.items) == 2 and bare_app.history.items[0] is prior


def test_clear_images_keeps_running_response_and_history(bare_app):
    bare_app.pending_images = ["synthetic image"]
    bare_app.retry_images = ("synthetic prior image",)
    bare_app.action("clear_images")
    assert bare_app.pending_images == [] and bare_app.retry_images == ()
    bare_app.engine.cancel.assert_not_called()


def test_verify_uses_selected_task_and_fresh_result_without_consuming_next_batch(
    bare_app, monkeypatch
):
    from ucpc.history import Track

    app = bare_app
    app.account_connected = True
    source = Track(text="SELECTED_CODE", complete=True, task_images=("CONDITION_1", "CONDITION_2"))
    app.history.add(source)
    app.history.add(Track(text="OTHER_CODE", complete=True, task_images=("OTHER_CONDITION",)))
    app.history.move(-1)
    app.pending_images = ["NEXT_TASK_FRAME"]
    app.capture_screen = Mock(return_value="FRESH_RUN_RESULT")
    app.engine.submit = Mock()
    monkeypatch.setattr(Config, "prompt", lambda self: "personal prompt")
    app.action("verify")
    images, prompt, track = app.engine.submit.call_args.args
    assert images == ("CONDITION_1", "CONDITION_2", "FRESH_RUN_RESULT")
    assert prompt == "personal prompt" and track.kind == "verification"
    assert track.candidate == "SELECTED_CODE" and track.task_images == source.task_images
    assert app.pending_images == ["NEXT_TASK_FRAME"] and app.history.current is track


def test_failed_verification_is_retried_with_new_result_and_cannot_replay_as_new_question(
    bare_app, monkeypatch
):
    from ucpc.history import Track

    app = bare_app
    app.account_connected = True
    previous = Track(kind="verification", candidate="CODE", task_images=("CONDITION",))
    previous.finish("Synthetic API failure")
    app.history.add(previous)
    app.engine.track = previous
    app.retry_images = ("STALE_REGULAR_RETRY",)
    app.engine.submit = Mock()
    app.capture_screen = Mock(return_value="NEW_RESULT")
    monkeypatch.setattr(Config, "prompt", lambda self: "test")
    app.action("send")
    app.engine.submit.assert_not_called()
    assert not app.overlay.send_button.isEnabled()
    app.action("verify")
    assert app.engine.submit.call_args.args[0] == ("CONDITION", "NEW_RESULT")
    assert app.engine.submit.call_args.args[2].candidate == "CODE"


@pytest.mark.parametrize("state", ["empty", "busy", "failed", "no_condition"])
def test_verification_requires_a_ready_answer_and_original_condition(bare_app, monkeypatch, state):
    from ucpc.history import Track

    app = bare_app
    if state != "empty":
        track = Track(text="CODE", task_images=() if state == "no_condition" else ("CONDITION",))
        if state != "busy":
            track.finish("failure" if state == "failed" else "")
        app.history.add(track)
    app.capture_screen = Mock()
    app.engine.submit = Mock()
    app.action("verify")
    app.capture_screen.assert_not_called()
    app.engine.submit.assert_not_called()


def test_help_toggle_uses_current_custom_and_disabled_shortcuts_without_cancelling(bare_app):
    from dataclasses import replace

    app = bare_app
    app.config = replace(app.config, hotkeys=app.config.hotkeys | {"send": "ctrl+win+f24", "verify": ""})
    app.action("help")
    assert app.help.isVisible() and app.help.protected
    assert app.help.bind_labels["send"].text() == "Ctrl+Win+F24"
    assert app.help.bind_labels["verify"].text() == "Не призначено"
    app.action("help")
    assert not app.help.isVisible()
    app.engine.cancel.assert_not_called()


@pytest.mark.parametrize("capture_fails", [False, True])
def test_first_new_capture_revokes_failed_old_batch(bare_app, monkeypatch, capture_fails):
    from ucpc.history import Track

    app = bare_app
    app.account_connected = True
    app.retry_images = ("OLD_PRIVATE_IMAGE",)
    app.engine.track = Track(text="OLD_RESPONSE")
    app.engine.track.finish("Synthetic API failure")
    app.engine.submit = Mock()
    app.capture_screen = Mock(
        side_effect=RuntimeError("capture failed") if capture_fails else None,
        return_value="NEW_IMAGE",
    )
    monkeypatch.setattr(Config, "prompt", lambda self: "test")
    app.action("capture")
    assert app.retry_images == ()
    app.action("send")
    if capture_fails:
        app.engine.submit.assert_not_called()
    else:
        assert app.engine.submit.call_args.args[0] == ("NEW_IMAGE",)


@pytest.mark.stress
def test_one_thousand_task_transitions_never_reuse_previous_batch(bare_app, monkeypatch):
    from ucpc.history import Track

    app = bare_app
    app.account_connected = True
    from dataclasses import replace

    app.config = replace(app.config, overlay_enabled=False)
    app.tick = Mock()
    monkeypatch.setattr(Config, "prompt", lambda self: "test")
    submitted = []

    def submit(images, prompt, track):
        submitted.append(images)
        app.engine.track = track

    app.engine.submit = Mock(side_effect=submit)
    for number in range(1000):
        prior = Track(text=f"OLD_RESPONSE_{number}")
        if number % 3 != 2:
            prior.finish("Synthetic API failure" if number % 3 else "")
        app.engine.track = prior
        app.history.add(prior)
        app.retry_images = (f"OLD_IMAGE_{number}",)
        for frame in range(3):
            app.last_capture = float("-inf")
            app.capture_screen = Mock(return_value=f"NEW_{number}_{frame}")
            app.action("capture")
        assert app.retry_images == ()
        app.action("send")
        assert submitted[-1] == tuple(f"NEW_{number}_{frame}" for frame in range(3))
        assert not app.pending_images
        app.action("send")  # Empty send must not replay a busy or completed request.
        assert len(submitted) == number + 1
    assert len(app.history.items) == 2000
    app.engine.cancel.assert_not_called()


@pytest.mark.parametrize("transition", ["login", "logout"])
def test_catalog_cannot_start_during_account_transition(bare_app, transition):
    app = bare_app
    app.account_connected = True
    app.login_running = transition == "login"
    app.logout_running = transition == "logout"
    app.auth = SimpleNamespace(models=Mock(return_value=[]))
    app.load_models()
    app.auth.models.assert_not_called()
    assert "завершення" in app.settings.error_label.text().lower()


@pytest.mark.parametrize("transition", ["login", "logout"])
def test_old_clipboard_retry_is_invalidated_when_changing_account(bare_app, monkeypatch, transition):
    app = bare_app
    app.account_connected = True
    app.auth = SimpleNamespace(login=Mock(side_effect=RuntimeError("synthetic denial")),
                               logout=Mock(return_value=True))
    old_copy_id = app.copy_id
    clipboard = Mock()
    clipboard.text.return_value = "OLD_PRIVATE_TEXT"
    clipboard_getter = Mock(return_value=clipboard)
    monkeypatch.setattr(app.qt, "clipboard", clipboard_getter)
    if transition == "login":
        app.login(new_account=True)
    else:
        app.logout()
    app.copy_text("OLD_PRIVATE_TEXT", old_copy_id)
    clipboard_getter.assert_not_called()
    drain_until(app, lambda: not app.login_running and not app.logout_running)
