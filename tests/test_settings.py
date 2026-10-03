"""Settings, native shortcut transactions and mouse-free reading regressions."""

import ctypes
import json
import time
from ctypes import wintypes
from dataclasses import replace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QMimeData, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from ucpc.actions import (
    ALT_DEFAULT_HOTKEYS,
    BATCH_DEFAULT_HOTKEYS,
    DEFAULT_HOTKEYS,
    PREVIOUS_DEFAULT_HOTKEYS,
)
from ucpc.auth import Auth
from ucpc.config import Config, load_config, save_config
from ucpc.history import Track
from ucpc.hotkeys import Hotkeys
from ucpc.overlay import Overlay
from ucpc.privacy import WDA_EXCLUDEFROMCAPTURE
from ucpc.settings import BindEdit, Settings


@pytest.fixture
def qt():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def test_solver_verification_setting_roundtrips_without_changing_model(qt):
    config = Config(vision_model="gpt-6-astra", verify_answer=True)
    settings = Settings(config)
    try:
        assert settings.verify_answer.isChecked()
        assert not settings.verify_answer.toolTip()  # No unprotected native tooltip window.
        settings.verify_answer.setChecked(False)
        draft = settings.draft(config)
        assert not draft.verify_answer and draft.vision_model == "gpt-6-astra"
        assert draft.reasoning_effort == "high"
    finally:
        settings.deleteLater()
        qt.processEvents()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_saved_accounts_are_keyboard_selectable_in_protected_settings(qt, theme):
    settings = Settings(Config(hotkeys={}, theme=theme))
    accounts = [
        {"id": "first", "label": "same@example.invalid · first", "active": True},
        {"id": "second", "label": "same@example.invalid · second", "active": False},
    ]
    settings.set_accounts(accounts)
    settings.tabs.setCurrentIndex(2)
    settings.resize(620, 500)
    try:
        settings.show_protected(activate=True)
        qt.processEvents()
        assert settings.protected and settings.account_list.isVisible()
        assert settings.selected_account_id() == "first"
        QTest.keyClick(settings.account_list, Qt.Key.Key_Down)
        assert settings.selected_account_id() == "second"
        assert settings.active_account_id == "first"
        assert settings.new_login_button.isVisible() and not settings.new_login_button.toolTip()
        assert settings.tabs.widget(2).verticalScrollBar().maximum() > 0
        settings.set_accounts([accounts[0]])
        assert not settings.account_list.isVisible()
    finally:
        settings.hide()
        settings.deleteLater()
        qt.processEvents()


def test_analysis_level_is_keyboard_operable_and_keeps_model_and_capture_settings(qt):
    config = Config(vision_model="gpt-5.6-sol", reasoning_effort="high")
    settings = Settings(config)
    try:
        assert settings.reasoning_levels[settings.reasoning.value()] == "high"
        assert not settings.reasoning.toolTip()
        QTest.keyClick(settings.reasoning, Qt.Key.Key_Left)
        draft = settings.draft(config)
        assert draft.reasoning_effort == "medium"
        assert "medium" in settings.reasoning_label.text()
        assert draft.vision_model == config.vision_model and draft.hotkeys == config.hotkeys
        assert draft.generation_timeout == 600 and not draft.verify_answer
    finally:
        settings.deleteLater()
        qt.processEvents()


@pytest.mark.parametrize("button, modifiers, expected", [
    (Qt.MouseButton.BackButton, Qt.KeyboardModifier.NoModifier, "mouse4"),
    (Qt.MouseButton.ForwardButton, Qt.KeyboardModifier.ControlModifier, "ctrl+mouse5"),
    (Qt.MouseButton.BackButton, Qt.KeyboardModifier.ShiftModifier, "shift+mouse4"),
])
def test_mouse_binding_recorder_and_config_roundtrip(qt, tmp_path, button, modifiers, expected):
    recorder = BindEdit("ctrl+win+f8")
    recording = []
    recorder.recording_changed.connect(recording.append)
    try:
        recorder.start_recording()
        QTest.mouseClick(recorder, button, modifiers)
        assert recorder.binding == expected and not recorder.recording
        assert recording == [True, False]
        assert "Mouse" in recorder.text()
        config = replace(Config(), hotkeys=Config().hotkeys | {"capture": expected})
        config.validate()
        path = tmp_path / "config.toml"
        save_config(config, path)
        import tomllib
        assert tomllib.loads(path.read_text(encoding="utf8"))["hotkeys"]["capture"] == expected
    finally:
        recorder.deleteLater()
        qt.processEvents()


def test_mouse_recorder_rejects_win_inline_without_committing_or_getting_stuck(qt):
    recorder = BindEdit("ctrl+win+f7")
    try:
        recorder.start_recording()
        QTest.mouseClick(recorder, Qt.MouseButton.BackButton, Qt.KeyboardModifier.MetaModifier)
        assert recorder.binding == "ctrl+win+f7" and recorder.recording
        assert "Пуск" in recorder.text()
        QTest.mouseClick(recorder, Qt.MouseButton.BackButton, Qt.KeyboardModifier.NoModifier)
        assert recorder.binding == "mouse4" and not recorder.recording
    finally:
        recorder.deleteLater()
        qt.processEvents()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_help_is_excluded_from_real_capture_and_shows_current_bindings(qt, theme):
    import mss
    from PySide6.QtGui import QColor, QPalette

    from ucpc.help_panel import HelpWindow
    from ucpc.privacy import flush_desktop

    config = replace(Config(theme=theme), hotkeys=DEFAULT_HOTKEYS | {
        "verify": "", "capture": "ctrl+win+f24"})
    help_window = HelpWindow(config)
    backdrop = QWidget(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                       | Qt.WindowType.WindowStaysOnTopHint)
    backdrop.setAutoFillBackground(True)
    palette = backdrop.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#236fab"))
    backdrop.setPalette(palette)
    backdrop.setGeometry(120, 120, 1100, 750)
    try:
        backdrop.show()
        QTest.qWait(100)
        help_window.move(140, 140)
        region = {"left": 240, "top": 240, "width": 60, "height": 60}
        with mss.MSS() as screen:
            before = screen.grab(region).rgb
            assert before[:3] == bytes.fromhex("236fab")
            help_window.show_protected()
            QTest.qWait(100)
            assert help_window.bind_labels["capture"].text() == "Ctrl+Win+F24"
            assert help_window.bind_labels["verify"].text() == "Не призначено"
            assert len(help_window.bind_labels) == len(DEFAULT_HOTKEYS) == 24
            affinity = wintypes.DWORD()
            user32 = ctypes.WinDLL("user32")
            user32.GetWindowDisplayAffinity.argtypes = [wintypes.HWND,
                                                       ctypes.POINTER(wintypes.DWORD)]
            assert user32.GetWindowDisplayAffinity(int(help_window.winId()), ctypes.byref(affinity))
            assert affinity.value == WDA_EXCLUDEFROMCAPTURE
            flush_desktop()
            assert screen.grab(region).rgb == before
            help_window.refresh(replace(config, hotkeys=config.hotkeys | {"capture": "ctrl+win+f23"}))
            assert help_window.bind_labels["capture"].text() == "Ctrl+Win+F23"
    finally:
        help_window.hide()
        backdrop.hide()
        help_window.deleteLater()
        backdrop.deleteLater()
        qt.processEvents()


def test_legacy_migration_preserves_model_prompt_and_custom_binding(tmp_path, monkeypatch):
    monkeypatch.setattr("ucpc.config.ROOT", tmp_path)
    save_config(Config(), tmp_path / "config.example.toml")
    (tmp_path / "system_prompt.example.txt").write_text("default", encoding="utf8")
    (tmp_path / ".env.example").write_text("", encoding="utf8")
    (tmp_path / "system_prompt.txt").write_text("personal instruction", encoding="utf8")
    (tmp_path / "config.toml").write_text(
        'vision_model="custom-model"\nspeech_enabled=false\nwindows_rate=-3\n[hotkeys]\ncapture="ctrl+alt+f8"\noverlay="ctrl+alt+shift+f24"\npause="ctrl+alt+space"',
        encoding="utf8",
    )
    config = load_config()
    assert config.vision_model == "custom-model" and config.prompt() == "personal instruction"
    assert config.hotkeys["capture"] == DEFAULT_HOTKEYS["capture"]
    assert config.hotkeys["overlay"] == "ctrl+alt+shift+f24"
    assert "pause" not in config.hotkeys
    text = (tmp_path / "config.toml").read_text(encoding="utf8")
    assert "speech" not in text and "windows_rate" not in text
    assert load_config() == config


def test_config_atomic_failure_leaves_original(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    save_config(Config(), path)
    before = path.read_bytes()
    monkeypatch.setattr("ucpc.config.os.replace", Mock(side_effect=OSError("disk failure")))
    with pytest.raises(OSError):
        save_config(replace(Config(), overlay_font_size=22), path)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob(".ucpc-*.tmp"))


@pytest.mark.parametrize("old_defaults", [PREVIOUS_DEFAULT_HOTKEYS, ALT_DEFAULT_HOTKEYS,
                                        BATCH_DEFAULT_HOTKEYS])
def test_shorter_defaults_migrate_without_overwriting_custom_or_disabled_keys(
    tmp_path, monkeypatch, old_defaults
):
    monkeypatch.setattr("ucpc.config.ROOT", tmp_path)
    bindings = old_defaults | {"overlay": "ctrl+alt+shift+f24", "cancel": ""}
    save_config(replace(Config(), hotkeys=bindings), tmp_path / "config.toml")
    (tmp_path / "system_prompt.txt").write_text("my instruction", encoding="utf8")
    (tmp_path / ".env").write_text("", encoding="utf8")
    upgraded = load_config()
    assert upgraded.hotkeys == DEFAULT_HOTKEYS | {"overlay": "ctrl+alt+shift+f24", "cancel": ""}
    assert upgraded.prompt() == "my instruction"
    assert load_config() == upgraded
    # A custom shortcut may occupy a new default: keep the prior working shortcut then.
    occupied_action = "center" if old_defaults == BATCH_DEFAULT_HOTKEYS else "capture"
    bindings["overlay"] = DEFAULT_HOTKEYS[occupied_action]
    save_config(replace(Config(), hotkeys=bindings))
    upgraded = load_config()
    assert upgraded.hotkeys["overlay"] == bindings["overlay"]
    assert upgraded.hotkeys[occupied_action] == old_defaults[occupied_action]
    if occupied_action == "center":
        assert upgraded.hotkeys["verify"] == ""  # The preserved old center bind owns F10.


def test_recorder_is_operable_with_keyboard_and_cancel_does_not_change_binding(qt):
    editor = BindEdit("ctrl+alt+f23")
    events = []
    editor.recording_changed.connect(events.append)
    editor.show()
    editor.setFocus()
    QTest.keyClick(editor, Qt.Key.Key_Space)
    assert editor.recording
    QTest.keyClick(
        editor,
        Qt.Key.Key_F22,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
    )
    assert editor.binding == "ctrl+shift+f22" and not editor.recording
    editor.start_recording()
    QTest.keyClick(editor, Qt.Key.Key_Escape)
    assert editor.binding == "ctrl+shift+f22" and not editor.recording
    editor.start_recording()
    QTest.keyClick(editor, Qt.Key.Key_Backspace)
    assert editor.binding == "" and events == [True, False, True, False, True, False]
    editor.hide()
    editor.deleteLater()


def test_history_restores_both_axes_and_streaming_does_not_follow_bottom(qt):
    reader = Overlay()
    reader.show_protected()
    code = "\n".join(f"{i}: " + "long variable " * 100 for i in range(400))
    first, second = Track(text=code), Track(text=code)
    try:
        reader.update_response(first, first.text, "")
        reader.scroll(1, 12)
        reader.scroll(1, 10, horizontal=True)
        y, x = reader.text.verticalScrollBar().value(), reader.text.horizontalScrollBar().value()
        assert y > 0 and x > 0
        reader.update_response(second, second.text, "")
        assert reader.text.verticalScrollBar().value() == 0
        reader.scroll(1, 20)
        reader.update_response(first, first.text + "\nnew line", "")
        assert reader.text.verticalScrollBar().value() == y
        assert reader.text.horizontalScrollBar().value() == x
        reader.update_response(first, first.text + "\nnew line\nmore", "")
        assert reader.text.verticalScrollBar().value() == y
        reader.edge(True)
        assert reader.text.verticalScrollBar().value() == reader.text.verticalScrollBar().maximum()
        reader.edge()
        assert reader.text.verticalScrollBar().value() == 0
        previous_size = reader.text.font().pixelSize()
        reader.zoom(3)
        assert reader.text.font().pixelSize() == previous_size + 3
    finally:
        reader.hide()
        reader.deleteLater()


def test_center_and_movement_stay_in_visible_work_area(qt):
    reader = Overlay()
    area = reader.screen().availableGeometry()
    assert abs(reader.geometry().center().x() - area.center().x()) <= 1
    assert abs(reader.geometry().center().y() - area.center().y()) <= 1
    reader.nudge(100_000, -100_000)
    assert area.contains(reader.geometry())
    reader.restore_geometry([-100_000, -100_000, 660, 580])
    assert area.contains(reader.geometry())
    reader.deleteLater()


def test_native_rebind_occupied_key_restores_previous_and_obsolete_messages_are_ignored(qt):
    window, competitor = QWidget(), QWidget()
    received = []
    keys = Hotkeys(qt, int(window.winId()), {"overlay": "ctrl+alt+shift+f23"}, received.append)
    occupied = Hotkeys(qt, int(competitor.winId()), {"other": "ctrl+alt+shift+f24"}, lambda _: None)
    old_id = next(iter(keys.registered))
    try:
        with pytest.raises(RuntimeError, match="зайнятий"):
            keys.replace_bindings({"overlay": "ctrl+alt+shift+f24"})
        assert keys.bindings == {"overlay": "ctrl+alt+shift+f23"}
        assert len(keys.registered) == 1
        native = ctypes.WinDLL("user32", use_last_error=True)
        native.PostMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        native.PostMessageW(int(window.winId()), 0x312, old_id, 0)
        QTest.qWait(30)
        assert received == []
        native.PostMessageW(int(window.winId()), 0x312, next(iter(keys.registered)), 0)
        QTest.qWait(30)
        assert received == ["overlay"]
        keys.suspend()
        assert not keys.registered
        keys.resume()
        assert len(keys.registered) == 1
    finally:
        occupied.close()
        keys.close()
        window.deleteLater()
        competitor.deleteLater()


def make_app(tmp_path, monkeypatch, qt):
    from ucpc.app import App

    monkeypatch.setattr("ucpc.app.Auth", lambda: Auth(tmp_path / "auth"))
    monkeypatch.setattr("ucpc.app.state_dir", lambda: tmp_path / "auth")
    monkeypatch.setattr("ucpc.app.ROOT", tmp_path)
    monkeypatch.setattr("ucpc.config.ROOT", tmp_path)
    config = Config(hotkeys={"overlay": "ctrl+alt+shift+f22"})
    save_config(config)
    (tmp_path / ".env").write_text("", encoding="utf8")
    (tmp_path / "system_prompt.txt").write_text("synthetic instruction", encoding="utf8")
    app = App(qt, config)
    return app


def dispose(app, qt):
    app.close()
    for window in (app.overlay, app.settings, app.menu, app.help):
        window.deleteLater()
    qt.processEvents()


@pytest.fixture
def clipboard(qt):
    clipboard = qt.clipboard()
    saved = QMimeData()
    original = clipboard.mimeData()
    if original is not None:
        for format_name in original.formats():
            saved.setData(format_name, original.data(format_name))
    # Windows clipboard access can be held briefly by another desktop process.
    for _ in range(20):
        clipboard.setText("Synthetic clipboard sentinel")
        QTest.qWait(20)
        if clipboard.text() == "Synthetic clipboard sentinel":
            break
    assert clipboard.text() == "Synthetic clipboard sentinel"
    yield clipboard
    # Preserve every format, and do not overwrite a new external clipboard owner.
    if clipboard.ownsClipboard():
        clipboard.setMimeData(saved)


def assert_clipboard_text(clipboard, expected):
    # Reading can also collide with the Windows clipboard-history service.
    for _ in range(25):
        if clipboard.text() == expected:
            return
        QTest.qWait(20)
    assert clipboard.text() == expected


def test_global_copy_uses_entire_selected_answer_while_hidden_without_focus_change(
    tmp_path, monkeypatch, qt, clipboard, foreground_test_window
):
    app = make_app(tmp_path, monkeypatch, qt)
    editor = QWidget()
    native = ctypes.WinDLL("user32")
    native.GetForegroundWindow.restype = wintypes.HWND
    native.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    native.PostMessageW.restype = wintypes.BOOL
    text = (
        "  Український код 🙂\n"
        + "\n".join(f"\t{i}: return " + "very_long_variable " * 50 for i in range(30))
        + "\n  "
    )
    prior = Track(text=text, complete=True)
    app.history.add(prior)
    app.history.add(Track(text="Latest answer, which must not be copied"))
    app.history.move(-1)
    app.tick()
    cursor = app.overlay.text.textCursor()
    cursor.setPosition(4)
    cursor.setPosition(14, cursor.MoveMode.KeepAnchor)
    app.overlay.text.setTextCursor(cursor)
    app.overlay.scroll(1, 8)
    app.overlay.scroll(1, 4, horizontal=True)
    app.hotkeys.replace_bindings({"copy": DEFAULT_HOTKEYS["copy"]})
    try:
        app.overlay.hide()
        app.settings.hide()
        foreground_test_window(editor)
        foreground = native.GetForegroundWindow()
        assert foreground == int(editor.winId())
        before = (
            app.overlay.text.textCursor().selectionStart(),
            app.overlay.text.textCursor().selectionEnd(),
            app.overlay.text.verticalScrollBar().value(),
            app.overlay.text.horizontalScrollBar().value(),
        )
        assert native.PostMessageW(
            int(app.settings.winId()), 0x312, next(iter(app.hotkeys.registered)), 0
        )
        QTest.qWait(60)
        assert_clipboard_text(clipboard, text)
        assert app.history.current is prior
        assert not app.overlay.isVisible() and not app.settings.isVisible()
        assert native.GetForegroundWindow() == foreground
        assert before == (
            app.overlay.text.textCursor().selectionStart(),
            app.overlay.text.textCursor().selectionEnd(),
            app.overlay.text.verticalScrollBar().value(),
            app.overlay.text.horizontalScrollBar().value(),
        )
    finally:
        editor.hide()
        editor.deleteLater()
        dispose(app, qt)


def test_copy_streaming_answer_takes_snapshot_without_stopping_generation(
    tmp_path, monkeypatch, qt, clipboard
):
    app = make_app(tmp_path, monkeypatch, qt)
    track = Track(text="```python\n\treturn 1")
    app.history.add(track)
    try:
        app.action("copy")
        assert_clipboard_text(clipboard, track.text)
        track.append_text(" + 2\n```\n")
        assert_clipboard_text(clipboard, "```python\n\treturn 1")
        assert not track.complete
        app.action("copy")
        assert_clipboard_text(clipboard, track.text)
        track.finish()
        app.action("copy")
        assert_clipboard_text(clipboard, track.text)
    finally:
        dispose(app, qt)


@pytest.mark.parametrize("text", [None, "", " \n\t "])
def test_copy_empty_answer_preserves_clipboard_and_hidden_windows(
    tmp_path, monkeypatch, qt, clipboard, text
):
    app = make_app(tmp_path, monkeypatch, qt)
    app.overlay.hide()
    app.settings.hide()
    if text is not None:
        app.history.add(Track(text=text))
    try:
        app.action("copy")
        assert_clipboard_text(clipboard, "Synthetic clipboard sentinel")
        assert not app.overlay.isVisible() and not app.settings.isVisible()
        assert app.message == "Ще немає тексту для копіювання"
    finally:
        dispose(app, qt)


@pytest.mark.parametrize(
    "failed_clipboard",
    [
        Mock(setText=Mock(side_effect=RuntimeError("Clipboard unavailable"))),
        Mock(text=Mock(return_value="Clipboard overwritten by another application")),
    ],
)
def test_copy_clipboard_failure_is_contained_and_keeps_answer(
    tmp_path, monkeypatch, qt, failed_clipboard
):
    app = make_app(tmp_path, monkeypatch, qt)
    track = Track(text="Synthetic answer")
    app.history.add(track)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(qt, "clipboard", lambda: failed_clipboard)
            app.action("copy")
            QTest.qWait(300)
        assert app.history.current is track and not track.complete
        assert app.message != "Поточну відповідь скопійовано"
        track.append_text(" still streaming")
        assert track.text.endswith("still streaming")
    finally:
        dispose(app, qt)


def test_copy_retries_transient_windows_clipboard_contention(tmp_path, monkeypatch, qt):
    app = make_app(tmp_path, monkeypatch, qt)
    track = Track(text="Synthetic answer")
    app.history.add(track)
    temporary_busy = Mock(text=Mock(side_effect=["", track.text]))
    try:
        with monkeypatch.context() as patch:
            patch.setattr(qt, "clipboard", lambda: temporary_busy)
            app.action("copy")
            # Wait for the retry, not an assumed native event-loop scheduling latency.
            deadline = time.monotonic() + 1
            while temporary_busy.setText.call_count < 2 and time.monotonic() < deadline:
                QTest.qWait(10)
        assert temporary_busy.setText.call_count == 2
        assert app.message == "Поточну відповідь скопійовано"
    finally:
        dispose(app, qt)


def test_new_copy_supersedes_pending_clipboard_retry(tmp_path, monkeypatch, qt):
    app = make_app(tmp_path, monkeypatch, qt)
    first, second = Track(text="First answer"), Track(text="Second answer")
    clipboard = Mock(text=Mock(side_effect=["", second.text]))
    try:
        with monkeypatch.context() as patch:
            patch.setattr(qt, "clipboard", lambda: clipboard)
            app.history.add(first)
            app.action("copy")
            app.history.add(second)
            app.action("copy")
            QTest.qWait(60)
        assert clipboard.setText.call_count == 2
        clipboard.setText.assert_called_with(second.text)
        assert app.message == "Поточну відповідь скопійовано"
    finally:
        dispose(app, qt)


@pytest.mark.parametrize("existing_copy", [None, "", "ctrl+win+f24"])
def test_copy_binding_migration_preserves_custom_and_disabled_binding(
    tmp_path, monkeypatch, existing_copy
):
    monkeypatch.setattr("ucpc.config.ROOT", tmp_path)
    bindings = {a: b for a, b in DEFAULT_HOTKEYS.items() if a != "copy"}
    if existing_copy is not None:
        bindings["copy"] = existing_copy
    save_config(replace(Config(), hotkeys=bindings, theme="dark"))
    (tmp_path / "system_prompt.txt").write_text("personal instruction", encoding="utf8")
    (tmp_path / ".env").write_text("", encoding="utf8")
    upgraded = load_config()
    assert upgraded.hotkeys == bindings | {
        "copy": DEFAULT_HOTKEYS["copy"] if existing_copy is None else existing_copy
    }
    assert upgraded.theme == "dark" and upgraded.prompt() == "personal instruction"
    assert load_config() == upgraded


def test_copy_binding_migration_does_not_take_existing_custom_shortcut(tmp_path, monkeypatch):
    monkeypatch.setattr("ucpc.config.ROOT", tmp_path)
    bindings = {a: b for a, b in DEFAULT_HOTKEYS.items() if a != "copy"}
    bindings["overlay"] = DEFAULT_HOTKEYS["copy"]
    save_config(replace(Config(), hotkeys=bindings))
    (tmp_path / "system_prompt.txt").write_text("personal instruction", encoding="utf8")
    (tmp_path / ".env").write_text("", encoding="utf8")
    assert load_config().hotkeys == bindings | {"copy": ""}


def test_failed_settings_write_restores_native_keys_prompt_and_running_config(
    tmp_path, monkeypatch, qt
):
    app = make_app(tmp_path, monkeypatch, qt)
    try:
        before = (tmp_path / "config.toml").read_bytes()
        app.settings.bind_edits["overlay"].binding = "ctrl+alt+shift+f21"
        app.settings.prompt_editor.setPlainText("changed instruction")
        monkeypatch.setattr("ucpc.app.save_config", Mock(side_effect=OSError("disk failure")))
        app.apply_settings()
        assert app.config.hotkeys == {"overlay": "ctrl+alt+shift+f22"}
        assert app.hotkeys.bindings == app.config.hotkeys
        assert (tmp_path / "config.toml").read_bytes() == before
        assert (tmp_path / "system_prompt.txt").read_text(
            encoding="utf8"
        ) == "synthetic instruction"
        assert app.settings.error_label.text()
    finally:
        dispose(app, qt)


def test_successful_settings_apply_needs_no_restart_and_persists(tmp_path, monkeypatch, qt):
    app = make_app(tmp_path, monkeypatch, qt)
    try:
        for action in app.settings.bind_edits:
            app.settings.bind_edits[action].binding = ""
        app.settings.bind_edits["settings"].binding = "ctrl+alt+shift+f21"
        app.overlay.resize(530, 420)
        geometry = app.overlay.geometry()
        app.settings.theme_buttons["dark"].setChecked(True)
        app.settings.prompt_editor.setPlainText("new instruction")
        app.apply_settings()
        assert not app.settings.error_label.text()
        assert app.config == app.engine.config
        assert app.config.theme == app.overlay.theme == app.settings.theme == "dark"
        assert app.overlay.geometry() == geometry
        assert app.hotkeys.bindings["settings"] == "ctrl+alt+shift+f21"
        assert load_config() == app.config and app.config.prompt() == "new instruction"
        app.action("move_down")
        app.save_ui()
        assert json.loads(app.state_path.read_text())["overlay"][:2] == [
            app.overlay.x(),
            app.overlay.y(),
        ]
        user32 = ctypes.WinDLL("user32")
        user32.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        affinity = wintypes.DWORD()
        for window in (app.settings, app.menu, app.overlay):
            assert user32.GetWindowDisplayAffinity(int(window.winId()), ctypes.byref(affinity))
            assert affinity.value == WDA_EXCLUDEFROMCAPTURE
    finally:
        dispose(app, qt)


def test_global_movement_messages_move_reader_in_each_direction(tmp_path, monkeypatch, qt):
    app = make_app(tmp_path, monkeypatch, qt)
    try:
        bindings = {
            a: DEFAULT_HOTKEYS[a] for a in ("move_up", "move_down", "move_left", "move_right")
        }
        app.hotkeys.replace_bindings(bindings)
        app.overlay.center()
        initial = app.overlay.pos()
        native = ctypes.WinDLL("user32")
        native.PostMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        expected = {
            "move_up": (0, -24),
            "move_down": (0, 24),
            "move_left": (-24, 0),
            "move_right": (24, 0),
        }
        for number, action in app.hotkeys.registered.items():
            before = app.overlay.pos()
            assert native.PostMessageW(int(app.settings.winId()), 0x312, number, 0)
            QTest.qWait(30)
            dx, dy = expected[action]
            assert app.overlay.x() == before.x() + dx
            assert app.overlay.y() == before.y() + dy
        assert app.overlay.pos() == initial
    finally:
        dispose(app, qt)


def test_hotkey_conflict_at_startup_keeps_settings_accessible(tmp_path, monkeypatch, qt):
    blocker = QWidget()
    occupied = Hotkeys(qt, int(blocker.winId()), {"x": "ctrl+alt+shift+f22"}, lambda _: None)
    app = None
    try:
        app = make_app(tmp_path, monkeypatch, qt)
        assert app.settings.isVisible() and app.settings.protected
        assert "зайнятий" in app.settings.error_label.text()
        assert not app.hotkeys.registered
    finally:
        occupied.close()
        blocker.deleteLater()
        if app:
            dispose(app, qt)


@pytest.mark.stress
def test_10_000_reader_navigation_operations_keep_memory_and_cursor_bounded(qt):
    reader = Overlay()
    reader.show_protected()
    first = Track(text="\n".join(f"line {i} " + "long " * 200 for i in range(300)))
    second = Track(text=first.text)
    try:
        for i in range(10_000):
            track = first if i % 50 < 25 else second
            reader.update_response(track, track.text, "test")
            reader.scroll(-1 if i % 3 == 0 else 1, 5)
            reader.scroll(-1 if i % 5 == 0 else 1, 5, horizontal=True)
            if i % 300 == 0:
                reader.edge(i % 600 == 0)
            if i % 1000 == 0:
                qt.processEvents()
            v, h = reader.text.verticalScrollBar(), reader.text.horizontalScrollBar()
            assert 0 <= v.value() <= v.maximum() and 0 <= h.value() <= h.maximum()
        assert reader.text.toPlainText() == first.text
    finally:
        reader.hide()
        reader.deleteLater()


def test_closing_during_bind_recording_does_not_reregister_hotkeys(tmp_path, monkeypatch, qt):
    app = make_app(tmp_path, monkeypatch, qt)
    edit = app.settings.bind_edits["overlay"]
    edit.start_recording()
    assert app.hotkeys.suspended and not app.hotkeys.registered
    app.close()
    assert not app.hotkeys.registered and not app.hotkeys.suspended
    dispose(app, qt)


def test_logout_ignores_catalog_result_from_previous_account(tmp_path, monkeypatch, qt):
    app = make_app(tmp_path, monkeypatch, qt)
    try:
        old = app.login_id
        app.login_id += 1
        app.events.put(("models", old, [{"slug": "stale-model"}]))
        app.tick()
        assert app.settings.model_list.count() == 0
    finally:
        dispose(app, qt)


def test_recording_overrides_settings_local_save_shortcut(qt):
    settings = Settings(Config(hotkeys={}))
    applied = []
    settings.apply_requested.connect(lambda: applied.append(1))
    editor = settings.bind_edits["capture"]
    settings.show_protected(activate=True)
    QTest.qWait(100)
    editor.setFocus()
    try:
        editor.start_recording()
        QTest.keyClick(editor, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
        assert editor.binding == "ctrl+s" and not applied and not editor.recording
        QTest.keyClick(editor, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
        assert applied == [1]
    finally:
        settings.hide()
        settings.deleteLater()


def test_tray_menu_remains_excluded_when_displayed(tmp_path, monkeypatch, qt):
    import mss
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QColor, QPalette

    from ucpc.privacy import flush_desktop

    app = make_app(tmp_path, monkeypatch, qt)
    app.settings.hide()
    app.overlay.hide()
    backdrop = QWidget(
        None,
        Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint,
    )
    backdrop.setAutoFillBackground(True)
    palette = backdrop.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#236fab"))
    backdrop.setPalette(palette)
    area = qt.primaryScreen().availableGeometry()
    backdrop.setGeometry(area.left() + 120, area.top() + 120, 640, 500)
    try:
        backdrop.show()
        QTest.qWait(100)
        region = {"left": backdrop.x() + 30, "top": backdrop.y() + 30, "width": 60, "height": 60}
        with mss.MSS() as screen:
            before = screen.grab(region).rgb
            assert before[:3] == bytes.fromhex("236fab")
            app.menu.popup(QPoint(backdrop.x() + 10, backdrop.y() + 10))
            QTest.qWait(100)
            assert app.menu.isVisible()
            user32 = ctypes.WinDLL("user32")
            user32.GetWindowDisplayAffinity.argtypes = [
                wintypes.HWND,
                ctypes.POINTER(wintypes.DWORD),
            ]
            affinity = wintypes.DWORD()
            assert user32.GetWindowDisplayAffinity(int(app.menu.winId()), ctypes.byref(affinity))
            assert affinity.value == WDA_EXCLUDEFROMCAPTURE
            flush_desktop()
            assert screen.grab(region).rgb == before
    finally:
        app.menu.hide()
        backdrop.hide()
        backdrop.deleteLater()
        dispose(app, qt)
