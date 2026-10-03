import ctypes
import sys
from ctypes import wintypes

import mss
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFontMetricsF, QPalette, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from ucpc.config import Config
from ucpc.overlay import Overlay
from ucpc.privacy import WDA_EXCLUDEFROMCAPTURE, flush_desktop
from ucpc.settings import Settings


@pytest.fixture
def overlay():
    qt = QApplication.instance() or QApplication([])
    qt.setQuitOnLastWindowClosed(False)
    reader = Overlay()
    yield reader
    reader.hide()
    reader.deleteLater()
    qt.processEvents()


def test_exclusion_failure_never_displays_answer(overlay, monkeypatch):
    def fail(hwnd):
        raise RuntimeError("synthetic exclusion failure")

    monkeypatch.setattr("ucpc.overlay.exclude_from_capture", fail)
    overlay.update_response(object(), "private response", "")
    with pytest.raises(RuntimeError, match="exclusion failure"):
        overlay.show_protected()
    assert not overlay.isVisible() and not overlay.protected


def test_streaming_preserves_selection_and_scroll(overlay):
    track = object()
    initial = "\n".join(f"line {i}: value = {i}" for i in range(200))
    overlay.update_response(track, initial, "Generating")
    cursor = overlay.text.textCursor()
    cursor.setPosition(5)
    cursor.setPosition(12, QTextCursor.MoveMode.KeepAnchor)
    overlay.text.setTextCursor(cursor)
    bar = overlay.text.verticalScrollBar()
    bar.setValue(25)
    selected, position = cursor.selectedText(), bar.value()
    overlay.update_response(track, initial + "\nnew line", "Generating")
    assert overlay.text.textCursor().selectedText() == selected
    assert bar.value() == position
    new_track = object()
    overlay.update_response(new_track, "New answer", "Complete")
    assert overlay.text.toPlainText() == "New answer" and bar.value() == 0


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("statement", ["return x;", 'return "Привіт 👋";', "return 123;"])
def test_final_tab_formatting_preserves_code_selection(overlay, reverse, statement):
    from ucpc.code_format import code_tabs

    track = object()
    initial = "int main() {\n    int x = 2;\n    " + statement + "\n}\n"
    overlay.update_response(track, initial, "Streaming")
    cursor = overlay.text.textCursor()
    start = initial.index("return")
    size = len(statement.encode("utf-16-le")) // 2
    cursor.setPosition(start + size if reverse else start)
    cursor.setPosition(start if reverse else start + size, QTextCursor.MoveMode.KeepAnchor)
    overlay.text.setTextCursor(cursor)
    overlay.update_response(track, code_tabs(initial), "Ready")
    assert overlay.text.textCursor().selectedText() == statement


@pytest.mark.skipif(sys.platform != "win32", reason="Windows capture exclusion")
def test_real_window_affinity_is_confirmed_and_survives_show(overlay):
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    foreground = user32.GetForegroundWindow()
    overlay.show_protected()
    QTest.qWait(100)
    assert user32.GetForegroundWindow() == foreground
    user32.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    affinity = wintypes.DWORD()
    assert user32.GetWindowDisplayAffinity(int(overlay.winId()), ctypes.byref(affinity))
    assert affinity.value == WDA_EXCLUDEFROMCAPTURE and overlay.protected
    assert overlay.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    metrics = QFontMetricsF(overlay.text.font())
    assert metrics.horizontalAdvance("iiii") == metrics.horizontalAdvance("WWWW")
    overlay.close()
    assert not overlay.isVisible()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows compositor and MSS")
@pytest.mark.parametrize("kind", ["reader", "settings"])
@pytest.mark.parametrize("opacity", [75, 94, 100])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_excluded_window_is_absent_from_real_desktop_capture(kind, opacity, theme):
    qt = QApplication.instance() or QApplication([])
    qt.setQuitOnLastWindowClosed(False)
    backdrop = QWidget(
        None,
        Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint,
    )
    protected = (
        Overlay(opacity=opacity, theme=theme)
        if kind == "reader"
        else Settings(Config(overlay_opacity=opacity, theme=theme))
    )
    backdrop.setAutoFillBackground(True)
    palette = backdrop.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#236fab"))
    backdrop.setPalette(palette)
    area = qt.primaryScreen().availableGeometry()
    for window in (backdrop, protected):
        window.setGeometry(area.left() + 120, area.top() + 120, 600, 620)
    try:
        backdrop.show()
        QTest.qWait(150)
        region = {"left": backdrop.x() + 40, "top": backdrop.y() + 120, "width": 80, "height": 80}
        with mss.MSS() as screen:
            before = screen.grab(region).rgb
            assert before[:3] == bytes.fromhex("236fab")
            protected.show()
            protected.raise_()
            QTest.qWait(150)
            flush_desktop()
            assert screen.grab(region).rgb != before
            protected.show_protected()
            QTest.qWait(150)
            flush_desktop()
            after = screen.grab(region).rgb
            assert after == before  # underlying window, not a black rectangle
    finally:
        protected.hide()
        backdrop.hide()
        protected.deleteLater()
        backdrop.deleteLater()
        qt.processEvents()


@pytest.mark.stress
def test_one_hundred_theme_hide_resize_cycles_keep_capture_exclusion_and_reading_state(overlay):
    from ucpc.history import Track

    qt = QApplication.instance()
    track = Track(text="\n".join(f"line {i}: " + "code " * 80 for i in range(200)))
    overlay.update_response(track, track.text, "Ready")
    overlay.show_protected()
    native = ctypes.WinDLL("user32", use_last_error=True)
    native.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    for number in range(100):
        overlay.set_theme("dark" if number % 2 else "light")
        overlay.resize(540 + number % 200, 420 + number % 100)
        overlay.nudge(20 if number % 2 else -20, 10)
        overlay.scroll(1)
        overlay.remember_position()
        position = track.scroll_y
        overlay.hide()
        overlay.show_protected()
        qt.processEvents()
        actual = wintypes.DWORD()
        assert native.GetWindowDisplayAffinity(int(overlay.winId()), ctypes.byref(actual))
        assert actual.value == 0x11 and overlay.protected
        assert overlay.text.toPlainText() == track.text and track.scroll_y == position
        assert overlay.text.verticalScrollBar().value() == position
