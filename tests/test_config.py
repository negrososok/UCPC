from dataclasses import replace

import pytest

from ucpc.config import Config, initialize, load_config


@pytest.mark.parametrize(
    "change",
    [
        {"history_limit": 0},
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
    (tmp_path / "config.example.toml").write_text("history_limit = 7", encoding="utf-8")
    (tmp_path / "system_prompt.example.txt").write_text("example", encoding="utf-8")
    (tmp_path / ".env.example").write_text("", encoding="utf-8")
    config = load_config()
    assert config.history_limit == 7
    (tmp_path / "system_prompt.txt").write_text("\ufeff Нова інструкція ", encoding="utf-8")
    (tmp_path / "config.toml").write_text("history_limit = 9", encoding="utf-8")
    initialize()
    assert load_config().history_limit == 9 and config.prompt() == "Нова інструкція"
