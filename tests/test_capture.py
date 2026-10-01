import base64
from io import BytesIO
from types import SimpleNamespace
from typing import ClassVar

import pytest
from PIL import Image

from ucpc.capture import capture
from ucpc.config import Config


class Screen:
    monitors: ClassVar[list[dict[str, int]]] = [
        {"left": 0, "top": 0, "width": 1280, "height": 360},
        {"left": 0, "top": 0, "width": 640, "height": 360},
    ]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def grab(self, area):
        self.area = area
        size = (area["width"], area["height"])
        return SimpleNamespace(size=size, rgb=bytes(size[0] * size[1] * 3))


def test_capture_encodes_png_and_resizes_without_saving(monkeypatch):
    screen = Screen()
    monkeypatch.setattr("ucpc.capture.mss.MSS", lambda: screen)
    uri = capture(Config(monitor=1, max_image_size=320))
    prefix, encoded = uri.split(",", 1)
    assert prefix == "data:image/png;base64"
    img = Image.open(BytesIO(base64.b64decode(encoded)))
    assert img.size == (320, 180)
    assert img.format == "PNG"
    assert screen.area is screen.monitors[1]


def test_capture_region_overrides_monitor(monkeypatch):
    screen = Screen()
    monkeypatch.setattr("ucpc.capture.mss.MSS", lambda: screen)
    region = {"left": -100, "top": 0, "width": 100, "height": 50}
    capture(Config(monitor=99, region=region))
    assert screen.area == region


def test_capture_missing_monitor_fails_before_grab(monkeypatch):
    screen = Screen()
    monkeypatch.setattr("ucpc.capture.mss.MSS", lambda: screen)
    with pytest.raises(ValueError, match="не знайдений"):
        capture(Config(monitor=99))
