"""Filter assigned side buttons; queue actions to Qt without doing work in the hook."""

import ctypes
import threading
from ctypes import wintypes

MOUSE_KEYS = {"mouse4": 0x05, "mouse5": 0x06}
WM_XBUTTONDOWN, WM_XBUTTONUP = 0x020B, 0x020C


class MouseData(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("extra", ctypes.c_size_t)]


class SideButtonRouter:
    def __init__(self):
        self.routes = {}
        self.pressed = set()
        self.lock = threading.Lock()

    def set_routes(self, routes):
        with self.lock:
            self.routes = dict(routes)

    def event(self, vk, modifiers, down):
        """Return (suppress, action ID); retain ownership until the matching release."""
        with self.lock:
            if not down:
                owned = vk in self.pressed
                self.pressed.discard(vk)
                return owned, None
            if vk in self.pressed:
                return True, None
            action_id = self.routes.get((modifiers, vk))
            if action_id is None:
                return False, None
            self.pressed.add(vk)
            return True, action_id


class MouseHook:
    def __init__(self, hwnd):
        self.hwnd = hwnd
        self.router = SideButtonRouter()
        self.ready = threading.Event()
        self.error = None
        self.thread_id = 0
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
        )
        self.callback = self.callback_type(self._event)
        self.user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int, self.callback_type, wintypes.HINSTANCE, wintypes.DWORD
        ]
        self.user32.SetWindowsHookExW.restype = wintypes.HHOOK
        self.user32.CallNextHookEx.argtypes = [
            wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
        ]
        self.user32.CallNextHookEx.restype = ctypes.c_ssize_t
        self.user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
        self.user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        self.user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT
        ]
        self.user32.GetMessageW.restype = wintypes.BOOL
        self.user32.PeekMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG), wintypes.HWND,
            wintypes.UINT, wintypes.UINT, wintypes.UINT
        ]
        self.user32.PeekMessageW.restype = wintypes.BOOL
        self.user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        self.user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        self.user32.DispatchMessageW.restype = ctypes.c_ssize_t
        self.user32.PostMessageW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        ]
        self.user32.PostMessageW.restype = wintypes.BOOL
        self.user32.PostThreadMessageW.argtypes = [
            wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        ]
        self.user32.PostThreadMessageW.restype = wintypes.BOOL
        self.user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.user32.GetAsyncKeyState.restype = ctypes.c_short
        self.kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        self.kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        self.kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        self.thread = threading.Thread(target=self._run, name="UCPC-side-buttons", daemon=True)
        self.thread.start()
        if not self.ready.wait(3):
            self.close()
            raise RuntimeError("Не вдалося запустити бінди мишки")
        if self.error:
            self.close()
            raise RuntimeError("Не вдалося підключити бокові кнопки мишки") from self.error

    def set_routes(self, routes):
        self.router.set_routes(routes)

    def _modifiers(self):
        def down(vk):
            return bool(self.user32.GetAsyncKeyState(vk) & 0x8000)

        return (int(down(0x12)) | int(down(0x11)) * 2 | int(down(0x10)) * 4
                | int(down(0x5B) or down(0x5C)) * 8)

    def _event(self, code, message, address):
        if code >= 0 and message in (WM_XBUTTONDOWN, WM_XBUTTONUP):
            try:
                data = MouseData.from_address(address)
                button = (data.mouseData >> 16) & 0xFFFF
                if button in (1, 2):
                    vk = 0x04 + button
                    suppress, action_id = self.router.event(
                        vk, self._modifiers(), message == WM_XBUTTONDOWN
                    )
                    if action_id is not None and not self.user32.PostMessageW(
                        self.hwnd, 0x0312, action_id, 0
                    ):
                        self.router.event(vk, 0, False)
                        suppress = False
                    if suppress:
                        return 1
            except Exception as error:  # noqa: BLE001 — never throw through a native callback.
                # An input filter must fail open, never strand normal mouse input.
                self.error = error
        return self.user32.CallNextHookEx(None, code, message, address)

    def _run(self):
        hook = None
        try:
            self.thread_id = self.kernel32.GetCurrentThreadId()
            msg = wintypes.MSG()
            self.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
            hook = self.user32.SetWindowsHookExW(
                14, self.callback, self.kernel32.GetModuleHandleW(None), 0
            )
            if not hook:
                raise ctypes.WinError(ctypes.get_last_error())
            self.ready.set()
            while self.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                self.user32.TranslateMessage(ctypes.byref(msg))
                self.user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as error:  # noqa: BLE001 — report setup failure to the Qt thread.
            self.error = error
        finally:
            if hook:
                self.user32.UnhookWindowsHookEx(hook)
            self.ready.set()

    def close(self):
        self.set_routes({})
        if self.thread_id and self.thread.is_alive():
            self.user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
            self.thread.join(3)
