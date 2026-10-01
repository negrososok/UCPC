import pytest

from ucpc.hotkeys import parse_hotkey


@pytest.mark.parametrize(
    "binding,key",
    [
        ("ctrl+alt+left", 0x25),
        ("CTRL+ALT+F8", 0x77),
        ("ctrl+alt+space", 0x20),
        ("win+shift+m", ord("M")),
    ],
)
def test_supported_hotkeys(binding, key):
    modifiers, vk = parse_hotkey(binding)
    assert modifiers & 0x4000
    assert vk == key


@pytest.mark.parametrize("binding", ["f8", "ctrl+f25", "ctrl+ctrl+m", "control+f8", "ctrl+😎"])
def test_invalid_hotkeys(binding):
    with pytest.raises(ValueError):
        parse_hotkey(binding)
