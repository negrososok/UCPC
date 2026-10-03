"""Failure injection at the native privacy boundary; no unprotected fallback is allowed."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ucpc import privacy


@pytest.mark.parametrize("failure", ["set", "query", "wrong_affinity"])
def test_capture_exclusion_requires_both_native_success_and_exact_affinity(monkeypatch, failure):
    def affinity(hwnd, result):
        result._obj.value = 0 if failure == "wrong_affinity" else 0x11
        return failure != "query"

    native = SimpleNamespace(SetWindowDisplayAffinity=Mock(return_value=failure != "set"),
                             GetWindowDisplayAffinity=Mock(side_effect=affinity))
    monkeypatch.setattr(privacy.ctypes, "WinDLL", Mock(return_value=native))
    with pytest.raises(RuntimeError, match="приховано"):
        privacy.exclude_from_capture(123)
    native.SetWindowDisplayAffinity.assert_called_once_with(123, 0x11)


def test_desktop_flush_failure_is_reported_before_capture(monkeypatch):
    native = SimpleNamespace(DwmFlush=Mock(return_value=-1))
    monkeypatch.setattr(privacy.ctypes, "WinDLL", Mock(return_value=native))
    with pytest.raises(RuntimeError, match="приховування"):
        privacy.flush_desktop()
