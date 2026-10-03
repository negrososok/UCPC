"""Native desktop test setup; production UCPC never requests foreground this way."""

import ctypes
from ctypes import wintypes

import pytest


@pytest.fixture
def foreground_test_window():
    from PySide6.QtTest import QTest

    class Keyboard(ctypes.Structure):
        _fields_ = [
            ("vk", wintypes.WORD), ("scan", wintypes.WORD), ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD), ("extra", ctypes.c_size_t),
        ]

    class Mouse(ctypes.Structure):
        _fields_ = [
            ("x", wintypes.LONG), ("y", wintypes.LONG), ("data", wintypes.DWORD),
            ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("extra", ctypes.c_size_t),
        ]

    class Payload(ctypes.Union):
        _fields_ = [("keyboard", Keyboard), ("mouse", Mouse)]

    class Input(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("payload", Payload)]

    native = ctypes.WinDLL("user32", use_last_error=True)
    native.GetForegroundWindow.restype = wintypes.HWND
    native.SetForegroundWindow.argtypes = [wintypes.HWND]
    native.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
    native.SendInput.restype = wintypes.UINT

    def focus(window):
        window.show()
        window.activateWindow()
        QTest.qWait(80)
        hwnd = int(window.winId())
        if native.GetForegroundWindow() != hwnd:
            # Windows may reject activation by a background test runner. A neutral
            # Shift release gives this runner input eligibility without Alt/menu/text.
            assert not ctypes.windll.user32.GetAsyncKeyState(0xA0) & 0x8000
            event = Input()
            event.type = 1
            event.payload.keyboard = Keyboard(0xA0, 0, 2, 0, 0)
            assert native.SendInput(1, ctypes.byref(event), ctypes.sizeof(Input)) == 1
            native.SetForegroundWindow(hwnd)
            window.activateWindow()
            QTest.qWait(80)
        assert native.GetForegroundWindow() == hwnd, "Test window must actually own focus"

    return focus
