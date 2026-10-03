import ctypes
import io
import sys
from ctypes import wintypes

import pytest
from PIL import Image
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QWidget

from ucpc.app import App
from ucpc.auth import Auth
from ucpc.capture import capture
from ucpc.config import Config
from ucpc.hotkeys import Hotkeys


@pytest.mark.skipif(sys.platform != "win32", reason="Real Windows keyboard dispatch")
@pytest.mark.stress
@pytest.mark.parametrize("win_first", [False, True])
def test_defaults_dispatch_from_real_input_without_alt_text_or_start_menu(
    win_first, foreground_test_window
):
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QPlainTextEdit

    from ucpc.actions import DEFAULT_HOTKEYS
    from ucpc.hotkeys import parse_hotkey
    from ucpc.mouse_bindings import MOUSE_KEYS

    class Keyboard(ctypes.Structure):
        _fields_ = [
            ("vk", wintypes.WORD),
            ("scan", wintypes.WORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("extra", ctypes.c_size_t),
        ]

    class Mouse(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("data", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("extra", ctypes.c_size_t),
        ]

    class Payload(ctypes.Union):
        _fields_ = [("keyboard", Keyboard), ("mouse", Mouse)]

    class Input(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("payload", Payload)]

    native = ctypes.WinDLL("user32", use_last_error=True)
    native.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
    native.SendInput.restype = wintypes.UINT
    native.GetForegroundWindow.restype = wintypes.HWND
    native.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    native.GetAsyncKeyState.argtypes = [ctypes.c_int]
    native.GetAsyncKeyState.restype = wintypes.SHORT

    def send(vks, up=False):
        inputs = (Input * len(vks))()
        for event, vk in zip(inputs, vks, strict=True):
            event.type = 1  # INPUT_KEYBOARD; no hooks or AI calls in this test.
            event.payload.keyboard = Keyboard(
                vk, 0, (2 if up else 0) | (1 if vk in (0x5B, 0x21, 0x22, 0x23, 0x24) else 0), 0, 0
            )
        assert native.SendInput(len(inputs), inputs, ctypes.sizeof(Input)) == len(inputs)

    qt = QApplication.instance() or QApplication([])
    qt.setQuitOnLastWindowClosed(False)
    editor = QPlainTextEdit("Synthetic editor: text must stay unchanged")
    editor.setWindowTitle("UCPC keyboard integration test")
    received = []
    keys = Hotkeys(qt, int(editor.winId()), DEFAULT_HOTKEYS, received.append)
    try:
        foreground_test_window(editor)
        editor.setFocus()
        QTest.qWait(100)
        hwnd = int(editor.winId())
        assert native.GetForegroundWindow() == hwnd, "The test editor must own keyboard focus"
        keyboard_bindings = {a: b for a, b in DEFAULT_HOTKEYS.items()
                             if parse_hotkey(b)[1] not in MOUSE_KEYS.values()}
        for action, binding in keyboard_bindings.items():
            if native.GetForegroundWindow() != hwnd:
                foreground_test_window(editor)
                editor.setFocus()
            assert not native.GetAsyncKeyState(0x12) & 0x8000  # VK_MENU / Alt
            _, vk = parse_hotkey(binding)
            modifiers = [0x5B, 0xA2] if win_first else [0xA2, 0x5B]
            send([*modifiers, vk])  # Left Ctrl + Left Win, either modifier order.
            QTest.qWait(20)
            send([vk, *reversed(modifiers)], up=True)
            QTest.qWait(30)
            assert received[-1:] == [action]
            foreground = native.GetForegroundWindow()
            foreground_class = ctypes.create_unicode_buffer(256)
            native.GetClassNameW(foreground, foreground_class, len(foreground_class))
            assert foreground == hwnd, {
                "action": action, "binding": binding, "foreground": foreground,
                "foreground_class": foreground_class.value,
            }  # Start or another application must never take focus.
        assert received == list(keyboard_bindings)
        assert editor.toPlainText() == "Synthetic editor: text must stay unchanged"
    finally:
        send([0x5B, 0xA2], up=True)
        keys.close()
        editor.hide()
        editor.deleteLater()
        qt.processEvents()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows messages")
def test_register_hotkey_dispatch_and_unregister():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    window = QWidget()
    hwnd = int(window.winId())
    received = []
    keys = Hotkeys(app, hwnd, {"pause": "ctrl+alt+shift+f23"}, received.append)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    assert user32.PostMessageW(hwnd, 0x312, 1, 0)
    QTimer.singleShot(100, app.quit)
    app.exec()
    keys.close()
    assert received == ["pause"]
    replacement = Hotkeys(app, hwnd, {"pause": "ctrl+alt+shift+f23"}, received.append)
    replacement.close()
    window.close()


@pytest.mark.stress
@pytest.mark.skipif(sys.platform != "win32", reason="Windows desktop capture")
def test_50_real_captures_encode_valid_png_in_memory():
    import base64

    # Small desktop region; images are never saved, printed or sent anywhere.
    config = Config(region={"left": 0, "top": 0, "width": 128, "height": 128})
    for _ in range(50):
        url = capture(config)
        with Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))) as image:
            assert image.format == "PNG" and image.size == (128, 128)
            image.verify()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows tray and hotkeys")
def test_full_tray_construction_controls_and_cleanup(tmp_path, monkeypatch):
    qt = QApplication.instance() or QApplication([])
    qt.setQuitOnLastWindowClosed(False)
    monkeypatch.setattr("ucpc.app.Auth", lambda: Auth(tmp_path))
    monkeypatch.setattr("ucpc.app.state_dir", lambda: tmp_path)
    app = App(qt, Config(hotkeys={"settings": "ctrl+alt+shift+f22"}))
    try:
        app.action("previous")
        app.action("next")
        app.action("cancel")
        app.show_panel()
        app.tick()
        assert app.panel.isVisible()
        assert app.overlay.protected and app.settings.protected
        app.panel.close()
        assert not app.panel.isVisible()
    finally:
        app.close()
        app.panel.deleteLater()
        qt.processEvents()
    assert not app.engine.thread.is_alive()
    assert not app.cleanup_errors and not app.hotkeys.registered
