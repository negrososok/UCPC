"""Real Windows side-button delivery against an isolated foreground test window."""

import ctypes
import sys
import time
from ctypes import wintypes
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from ucpc.hotkeys import Hotkeys


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows mouse filter")
@pytest.mark.stress
def test_real_side_buttons_work_in_background_and_only_swallow_assigned_combinations(
    foreground_test_window,
):
    class Mouse(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG), ("data", wintypes.DWORD),
                    ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("extra", ctypes.c_size_t)]

    class Keyboard(ctypes.Structure):
        _fields_ = [("vk", wintypes.WORD), ("scan", wintypes.WORD),
                    ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("extra", ctypes.c_size_t)]

    class Payload(ctypes.Union):
        _fields_ = [("mouse", Mouse), ("keyboard", Keyboard)]

    class Input(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("payload", Payload)]

    class Receiver(QWidget):
        def __init__(self):
            super().__init__()
            self.received = []

        def mousePressEvent(self, event):
            self.received.append(("down", event.button()))
            event.accept()

        def mouseReleaseEvent(self, event):
            self.received.append(("up", event.button()))
            event.accept()

    native = ctypes.WinDLL("user32", use_last_error=True)
    native.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
    native.SendInput.restype = wintypes.UINT
    native.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    native.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    native.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    native.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                  wintypes.LPARAM]
    native.GetForegroundWindow.restype = wintypes.HWND
    native.WindowFromPoint.argtypes = [wintypes.POINT]
    native.WindowFromPoint.restype = wintypes.HWND

    def expect_click(button):
        expected = [("down", button), ("up", button)]
        deadline = time.monotonic() + 0.5
        # SendInput queues input; a fixed 15 ms sleep is not a Qt delivery guarantee.
        while receiver.received != expected and time.monotonic() < deadline:
            QTest.qWait(5)
        assert receiver.received == expected, {
            "foreground": native.GetForegroundWindow(), "receiver": int(receiver.winId()),
            "routes": hook.router.routes, "pressed": hook.router.pressed,
            "hook_error": hook.error, "alive": hook.thread.is_alive(),
            "received": receiver.received,
        }

    def send(button=1, *, up=False, vk=None):
        event = Input()
        if vk is None:
            event.type = 0
            event.payload.mouse = Mouse(0, 0, button, 0x0100 if up else 0x0080, 0, 0)
        else:
            event.type = 1
            event.payload.keyboard = Keyboard(vk, 0, (2 if up else 0)
                                               | (1 if vk in (0x5B, 0x5C) else 0), 0, 0)
        assert native.SendInput(1, ctypes.byref(event), ctypes.sizeof(Input)) == 1
        QTest.qWait(15)

    def click(button=1, *, passthrough=False):
        if passthrough:
            # Unassigned input targets the window under the cursor. The live desktop
            # may have changed focus/cursor during the preceding background stress.
            foreground_test_window(receiver)
            assert native.SetCursorPos(point.x, point.y)
            QTest.qWait(15)
            assert native.WindowFromPoint(point) == int(receiver.winId())
        send(button)
        send(button, up=True)

    qt = QApplication.instance() or QApplication([])
    qt.setQuitOnLastWindowClosed(False)
    receiver = Receiver()
    receiver.setWindowTitle("UCPC side-button integration test")
    receiver.resize(400, 250)
    owner = QWidget()  # Global UCPC receiver stays hidden throughout.
    actions = []
    keys = Hotkeys(qt, int(owner.winId()), {"capture": "mouse4"}, actions.append)
    hook = keys.mouse_hook
    old_cursor = wintypes.POINT()
    native.GetCursorPos(ctypes.byref(old_cursor))
    try:
        foreground_test_window(receiver)
        point = wintypes.POINT(100, 100)
        assert native.ClientToScreen(int(receiver.winId()), ctypes.byref(point))
        assert native.SetCursorPos(point.x, point.y)
        QTest.qWait(40)
        assert not owner.isVisible() and hook.thread.is_alive() and not hook.error
        for vk in (0x05, 0x06, 0x11, 0x10, 0x12, 0x5B, 0x5C):
            assert not native.GetAsyncKeyState(vk) & 0x8000
        click()
        assert actions == ["capture"] and receiver.received == []
        click(2, passthrough=True)  # An unassigned side button still reaches the foreground app.
        expect_click(Qt.MouseButton.ForwardButton)
        receiver.received.clear()
        keys.replace_bindings({"copy": "ctrl+mouse4", "send": "mouse5"})
        click(passthrough=True)  # Bare Mouse4 is no longer assigned.
        expect_click(Qt.MouseButton.BackButton)
        receiver.received.clear()
        send(vk=0xA2)
        send()
        send(vk=0xA2, up=True)  # Release Ctrl before Mouse4: up must still be swallowed.
        send(up=True)
        assert actions == ["capture", "copy"] and receiver.received == []
        for _ in range(100):
            click(2)
        assert actions == ["capture", "copy"] + ["send"] * 100
        assert not receiver.received and not hook.router.pressed
        send(2)
        old_id = next(number for number, action in keys.registered.items() if action == "send")
        keys.suspend()
        send(2, up=True)  # Suspension cannot leak the release into the browser.
        assert not receiver.received
        click(2, passthrough=True)
        expect_click(Qt.MouseButton.ForwardButton)
        receiver.received.clear()
        keys.resume()
        before = len(actions)
        assert native.PostMessageW(int(owner.winId()), 0x0312, old_id, 0)
        QTest.qWait(25)
        assert len(actions) == before  # Ignore stale queued actions after resume.
        click(2)
        assert actions[-1] == "send" and len(actions) == before + 1
        keys.replace_bindings({})
        click(2, passthrough=True)
        expect_click(Qt.MouseButton.ForwardButton)
        receiver.received.clear()
        keys.replace_bindings({"copy": "shift+mouse4", "send": "ctrl+shift+mouse5"})
        send(vk=0xA0)
        click()
        send(vk=0xA2)
        click(2)
        send(vk=0xA2, up=True)
        send(vk=0xA0, up=True)
        assert actions[-2:] == ["copy", "send"] and not receiver.received
        before = keys.bindings.copy()
        with pytest.raises(ValueError, match="Win"):
            keys.replace_bindings({"copy": "win+mouse4"})
        assert keys.bindings == before
        assert native.GetForegroundWindow() == int(receiver.winId())
        assert not hook.error
    finally:
        send(1, up=True)
        send(2, up=True)
        send(vk=0xA2, up=True)
        send(vk=0xA0, up=True)
        send(vk=0x5B, up=True)
        keys.close()
        native.SetCursorPos(old_cursor.x, old_cursor.y)
        receiver.hide()
        receiver.deleteLater()
        owner.deleteLater()
        qt.processEvents()
    assert not hook.thread.is_alive()


def test_failed_mouse_setup_restores_previous_keyboard_binding(monkeypatch):
    qt = QApplication.instance() or QApplication([])
    owner = QWidget()
    received = []
    keys = Hotkeys(qt, int(owner.winId()), {"copy": "ctrl+shift+f24"}, received.append)
    monkeypatch.setattr("ucpc.hotkeys.MouseHook", Mock(side_effect=RuntimeError("setup failed")))
    try:
        with pytest.raises(RuntimeError, match="setup failed"):
            keys.replace_bindings({"copy": "mouse4"})
        assert keys.bindings == {"copy": "ctrl+shift+f24"}
        assert list(keys.registered.values()) == ["copy"]
        assert len(keys.keyboard_ids) == 1 and keys.mouse_hook is None
        keys.suspend()
        keys.resume()
        assert list(keys.registered.values()) == ["copy"]
    finally:
        keys.close()
        owner.deleteLater()
        qt.processEvents()


@pytest.mark.parametrize("down", [True, False])
def test_native_mouse_callback_fails_open_when_action_window_has_gone(down):
    from ucpc.mouse_bindings import MouseData, MouseHook, SideButtonRouter

    hook = MouseHook.__new__(MouseHook)
    hook.hwnd = 123
    hook.error = None
    hook.router = SideButtonRouter()
    hook.router.set_routes({(0, 5): 17})
    hook._modifiers = lambda: 0
    hook.user32 = Mock()
    hook.user32.PostMessageW.return_value = False
    hook.user32.CallNextHookEx.return_value = 321
    data = MouseData()
    data.mouseData = 1 << 16
    result = hook._event(0, 0x020B if down else 0x020C, ctypes.addressof(data))
    assert result == 321 and not hook.router.pressed


def test_native_mouse_callback_never_reads_other_events_or_negative_hook_codes():
    from ucpc.mouse_bindings import MouseHook

    hook = MouseHook.__new__(MouseHook)
    hook.user32 = Mock()
    hook.user32.CallNextHookEx.return_value = 987
    assert hook._event(-1, 0x020B, 0) == 987  # Null address must never be read.
    assert hook._event(0, 0x0200, 0) == 987  # Mouse movement is completely ignored.


@pytest.mark.stress
def test_fifty_native_mouse_hook_restarts_leave_no_worker_threads():
    import threading

    qt = QApplication.instance() or QApplication([])
    owner = QWidget()
    existing = {thread.ident for thread in threading.enumerate()}
    try:
        for _ in range(50):
            keys = Hotkeys(qt, int(owner.winId()), {"capture": "mouse4", "send": "mouse5"}, Mock())
            hook = keys.mouse_hook
            assert hook.thread.is_alive() and not hook.error
            keys.close()
            keys.close()
            assert not hook.thread.is_alive()
            qt.processEvents()
        assert {thread.ident for thread in threading.enumerate()} <= existing
    finally:
        owner.deleteLater()
        qt.processEvents()
