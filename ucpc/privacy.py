"""Capture exclusion for this process's own top-level Windows windows."""

import ctypes
import sys
from ctypes import wintypes

WDA_EXCLUDEFROMCAPTURE = 0x11


def exclude_from_capture(hwnd: int) -> None:
    if sys.platform != "win32" or sys.getwindowsversion().build < 19041:
        raise RuntimeError("Виключення вікна із захоплення потребує Windows 10 2004 або Windows 11")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
    user32.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowDisplayAffinity.restype = wintypes.BOOL
    actual = wintypes.DWORD()
    if not user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE):
        raise RuntimeError(
            "Windows не дозволив виключити вікно із захоплення. Текстове вікно приховано."
        )
    if (
        not user32.GetWindowDisplayAffinity(hwnd, ctypes.byref(actual))
        or actual.value != WDA_EXCLUDEFROMCAPTURE
    ):
        raise RuntimeError("Виключення із захоплення не підтверджено. Текстове вікно приховано.")


def flush_desktop() -> None:
    """Wait for window hiding to reach the compositor before our own screenshot."""
    dwm = ctypes.WinDLL("dwmapi")
    dwm.DwmFlush.argtypes = []
    dwm.DwmFlush.restype = ctypes.c_long
    if dwm.DwmFlush() < 0:
        raise RuntimeError("Windows не завершив приховування вікон перед скріншотом")
