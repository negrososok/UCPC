"""RegisterHotKey integration: no keyboard hooks, no admin rights, no key logging."""

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter

MODIFIERS = {"alt": 0x1, "ctrl": 0x2, "shift": 0x4, "win": 0x8}
KEYS = {
    "space": 0x20,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "pageup": 0x21,
    "pagedown": 0x22,
    "home": 0x24,
    "end": 0x23,
    "escape": 0x1B,
    "enter": 0x0D,
}


def parse_hotkey(value: str) -> tuple[int, int]:
    parts = [part.strip().lower() for part in value.split("+")]
    if len(parts) < 2 or any(p not in MODIFIERS for p in parts[:-1]):
        raise ValueError(f"Хоткей {value}: потрібні модифікатори ctrl/alt/shift/win і клавіша")
    if len(set(parts[:-1])) != len(parts[:-1]):
        raise ValueError(f"Повторений модифікатор: {value}")
    mods = 0x4000  # MOD_NOREPEAT
    for part in parts[:-1]:
        mods |= MODIFIERS[part]
    key = parts[-1]
    if key in KEYS:
        vk = KEYS[key]
    elif len(key) == 1 and key.isascii() and key.isalnum():
        vk = ord(key.upper())
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    else:
        raise ValueError(f"Невідома клавіша хоткея: {value}")
    return mods, vk


class Hotkeys(QAbstractNativeEventFilter):
    def __init__(self, app, hwnd: int, bindings: dict, callback):
        super().__init__()
        self.app, self.hwnd, self.callback = app, hwnd, callback
        self.registered = {}
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.RegisterHotKey.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self.user32.RegisterHotKey.restype = wintypes.BOOL
        self.user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.UnregisterHotKey.restype = wintypes.BOOL
        self.bindings = {}
        self.suspended = False
        self.next_id = 1
        app.installNativeEventFilter(self)
        try:
            self.replace_bindings(bindings)
        except Exception:
            self.close()
            raise

    def _unregister(self):
        for number in self.registered:
            self.user32.UnregisterHotKey(self.hwnd, number)
        self.registered.clear()

    def _register(self, bindings):
        try:
            seen = set()
            for action, binding in bindings.items():
                if not binding:
                    continue
                mods, vk = parse_hotkey(binding)
                if (mods, vk) in seen:
                    raise ValueError(f"Повторений хоткей: {binding}")
                seen.add((mods, vk))
                number = self.next_id
                self.next_id += 1
                if self.next_id > 0xBFFF:
                    raise RuntimeError("Перезапусти UCPC перед наступною зміною біндів")
                if action.startswith(("move_", "scroll_", "overlay_")):
                    mods &= ~0x4000  # Holding a scroll/movement key repeats naturally.
                if not self.user32.RegisterHotKey(self.hwnd, number, mods, vk):
                    raise RuntimeError(
                        f"Хоткей {binding} зайнятий або недоступний. Зміни його у налаштуваннях."
                    )
                self.registered[number] = action
        except Exception:
            self._unregister()
            raise

    def replace_bindings(self, bindings):
        old = self.bindings.copy()
        self._unregister()
        self.suspended = False
        try:
            self._register(bindings)
        except Exception:
            self._register(old)
            raise
        self.bindings = bindings.copy()

    def suspend(self):
        self._unregister()
        self.suspended = True

    def resume(self):
        if self.suspended:
            self._register(self.bindings)
            self.suspended = False

    def nativeEventFilter(self, event_type, message):
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == 0x312 and msg.hWnd == self.hwnd:  # WM_HOTKEY
            action = self.registered.get(int(msg.wParam))
            if action:
                self.callback(action)
                return True, 0
        return False, 0

    def close(self):
        self._unregister()
        self.suspended = False
        self.app.removeNativeEventFilter(self)
