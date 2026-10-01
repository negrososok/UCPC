"""Settings, native shortcut transactions and mouse-free reading regressions."""

import ctypes
import json
from ctypes import wintypes
from dataclasses import replace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from ucpc.actions import ALT_DEFAULT_HOTKEYS, DEFAULT_HOTKEYS, PREVIOUS_DEFAULT_HOTKEYS
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


@pytest.mark.parametrize("old_defaults", [PREVIOUS_DEFAULT_HOTKEYS, ALT_DEFAULT_HOTKEYS])
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
    bindings["overlay"] = DEFAULT_HOTKEYS["capture"]
    save_config(replace(Config(), hotkeys=bindings))
    upgraded = load_config()
    assert upgraded.hotkeys["overlay"] == bindings["overlay"]
    assert upgraded.hotkeys["capture"] == old_defaults["capture"]


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
    for window in (app.overlay, app.settings, app.menu):
        window.deleteLater()
    qt.processEvents()


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
