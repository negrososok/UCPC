import pytest

from ucpc.hotkeys import parse_hotkey


@pytest.mark.parametrize(
    "binding,key",
    [
        ("ctrl+alt+left", 0x25),
        ("CTRL+ALT+F8", 0x77),
        ("ctrl+alt+space", 0x20),
        ("win+shift+m", ord("M")),
        ("mouse4", 0x05),
        ("MOUSE5", 0x06),
        ("ctrl+shift+mouse4", 0x05),
    ],
)
def test_supported_hotkeys(binding, key):
    modifiers, vk = parse_hotkey(binding)
    assert modifiers & 0x4000
    assert vk == key


@pytest.mark.parametrize("binding", ["f8", "ctrl+f25", "ctrl+ctrl+m", "control+f8", "ctrl+😎",
                                     "mouse3", "mouse6", "ctrl+ctrl+mouse4", "mouse4+ctrl",
                                     "win+mouse4", "ctrl+win+mouse5"])
def test_invalid_hotkeys(binding):
    with pytest.raises(ValueError):
        parse_hotkey(binding)


def test_side_button_router_keeps_release_ownership_across_modifiers_and_rebinds():
    from ucpc.mouse_bindings import SideButtonRouter

    router = SideButtonRouter()
    router.set_routes({(2, 5): 17, (0, 6): 18})
    assert router.event(5, 0, True) == (False, None)
    assert router.event(5, 2, True) == (True, 17)
    assert router.event(5, 2, True) == (True, None)  # Held button never repeats.
    router.set_routes({})  # Recorder suspension or removal while still held.
    assert router.event(5, 0, False) == (True, None)
    assert router.event(5, 2, True) == (False, None)
    assert router.event(5, 2, False) == (False, None)
    router.set_routes({(0, 6): 21})
    assert router.event(6, 4, True) == (False, None)  # Extra modifiers must match exactly.
    assert router.event(6, 0, True) == (True, 21)
    assert router.event(6, 8, False) == (True, None)


@pytest.mark.stress
def test_side_button_routes_do_not_leak_across_ten_thousand_batches():
    from ucpc.mouse_bindings import SideButtonRouter

    router = SideButtonRouter()
    for number in range(10000):
        router.set_routes({(number % 16, 5): number})
        assert router.event(5, number % 16, True) == (True, number)
        router.set_routes({})
        assert router.event(5, 0, False) == (True, None)
        assert not router.pressed
