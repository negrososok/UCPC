from dataclasses import replace

import pytest

from ucpc.config import Config, initialize, load_config, save_config


@pytest.mark.parametrize(
    "change",
    [
        {"theme": "blue"},
        {"theme": []},
        {"overlay_opacity": 74},
        {"overlay_font_size": 33},
        {"move_step": 0},
        {"scroll_lines": True},
        {"hotkeys": {"obsolete": "ctrl+f8"}},
        {"hotkeys": {"capture": "ctrl+f8", "cancel": "CTRL+F8"}},
        {"monitor": -1},
        {"max_image_size": 319},
        {"max_output_tokens": 0},
        {"auth_mode": "other"},
        {"request_prompt": 3},
        {"notifications": "false"},
        {"verify_answer": "true"},
        {"reasoning_effort": "none"},
        {"hotkeys": []},
        {"hotkeys": {"capture": 1}},
        {"region": []},
        {"region": {"left": 0}},
        {"region": {"left": 0, "top": 0, "width": 0, "height": 10}},
        {"region": {"left": 0, "top": 0, "width": True, "height": 10}},
    ],
)
def test_bad_settings_fail_before_application_start(change):
    with pytest.raises(ValueError):
        replace(Config(), **change).validate()


def test_first_run_preserves_user_files_and_reloads_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr("ucpc.config.ROOT", tmp_path)
    (tmp_path / "config.example.toml").write_text("monitor = 7", encoding="utf-8")
    (tmp_path / "system_prompt.example.txt").write_text("example", encoding="utf-8")
    (tmp_path / ".env.example").write_text("", encoding="utf-8")
    config = load_config()
    assert config.monitor == 7
    (tmp_path / "system_prompt.txt").write_text("\ufeff Нова інструкція ", encoding="utf-8")
    (tmp_path / "config.toml").write_text("monitor = 9", encoding="utf-8")
    initialize()
    assert load_config().monitor == 9 and config.prompt() == "Нова інструкція"


def test_old_history_limit_is_removed_without_resetting_custom_settings(tmp_path, monkeypatch):
    monkeypatch.setattr("ucpc.config.ROOT", tmp_path)
    (tmp_path / "config.toml").write_text(
        'history_limit=1\ntheme="dark"\n[hotkeys]\ncapture="ctrl+win+f24"', encoding="utf8"
    )
    (tmp_path / "system_prompt.txt").write_text("personal instruction", encoding="utf8")
    (tmp_path / ".env").write_text("", encoding="utf8")
    config = load_config()
    assert config.theme == "dark" and config.hotkeys["capture"] == "ctrl+win+f24"
    assert "history_limit" not in (tmp_path / "config.toml").read_text(encoding="utf8")
    assert load_config() == config


@pytest.mark.parametrize("custom", [{}, {"capture": "ctrl+f8", "send": ""},
                                  {"copy": "mouse4", "overlay": "mouse5"}])
def test_mouse_defaults_migrate_only_old_defaults_without_conflicts(tmp_path, monkeypatch, custom):
    from ucpc.actions import DEFAULT_HOTKEYS, KEYBOARD_DEFAULT_HOTKEYS

    monkeypatch.setattr("ucpc.config.ROOT", tmp_path)
    old = KEYBOARD_DEFAULT_HOTKEYS | custom
    save_config(replace(Config(), hotkeys=old), tmp_path / "config.toml")
    (tmp_path / "system_prompt.txt").write_text("personal", encoding="utf8")
    (tmp_path / ".env").write_text("", encoding="utf8")
    upgraded = load_config()
    if "copy" in custom:
        assert upgraded.hotkeys["capture"] == "ctrl+win+f8"
        assert upgraded.hotkeys["send"] == "ctrl+win+f9"
    else:
        assert upgraded.hotkeys["capture"] == custom.get("capture", DEFAULT_HOTKEYS["capture"])
        assert upgraded.hotkeys["send"] == custom.get("send", DEFAULT_HOTKEYS["send"])
    for action, binding in custom.items():
        assert upgraded.hotkeys[action] == binding
    assert upgraded.prompt() == "personal" and load_config() == upgraded


@pytest.mark.parametrize("old_network_timeout, expected", [(60, 180), (17, 17)])
def test_old_deadline_settings_upgrade_without_resetting_model_effort_or_custom_limits(
    tmp_path, monkeypatch, old_network_timeout, expected
):
    monkeypatch.setattr("ucpc.config.ROOT", tmp_path)
    (tmp_path / "config.toml").write_text(
        f'request_timeout={old_network_timeout}\nvision_model="custom-model"\n'
        'reasoning_effort="xhigh"\nverify_answer=false', encoding="utf8")
    (tmp_path / "system_prompt.txt").write_text("personal", encoding="utf8")
    (tmp_path / ".env").write_text("", encoding="utf8")
    upgraded = load_config()
    assert upgraded.generation_timeout == 600 and upgraded.request_timeout == expected
    assert upgraded.vision_model == "custom-model" and upgraded.reasoning_effort == "xhigh"
    assert not upgraded.verify_answer and upgraded.prompt() == "personal"
    assert load_config() == upgraded
    custom = replace(upgraded, generation_timeout=123, request_timeout=60)
    save_config(custom)
    assert load_config() == custom  # Never remigrate already configured deadlines.
