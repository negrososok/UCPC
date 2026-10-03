"""CLI modes validate configuration and never accidentally submit a screenshot."""

import sys
from unittest.mock import Mock

import pytest

from ucpc import __main__ as entry
from ucpc.config import Config


@pytest.fixture
def cli(monkeypatch):
    config = Config(hotkeys={})
    monkeypatch.setattr(entry, "load_config", Mock(return_value=config))
    monkeypatch.setattr(entry, "load_dotenv", Mock())
    monkeypatch.setattr(Config, "prompt", lambda self: "Synthetic instruction")
    auth = Mock()
    auth.info.return_value = "ChatGPT: synthetic account"
    auth.models.return_value = [{"slug": "test-model", "display_name": "Synthetic model"}]
    auth.login.return_value = "synthetic@example.invalid"
    monkeypatch.setattr("ucpc.auth.Auth", Mock(return_value=auth))
    run = Mock(return_value=17)
    monkeypatch.setattr("ucpc.app.run", run)
    return config, auth, run


@pytest.mark.parametrize("argument", ["--check", "--models", "--login"])
def test_cli_diagnostic_modes_do_not_start_gui_or_generation(cli, monkeypatch, capsys, argument):
    _, auth, run = cli
    monkeypatch.setattr(sys, "argv", ["ucpc", argument])
    assert entry.main() == 0
    run.assert_not_called()
    assert capsys.readouterr().out
    assert auth.login.call_count == (argument == "--login")
    assert auth.models.call_count == (argument == "--models")


@pytest.mark.parametrize("arguments, smoke", [([], False), (["--smoke"], True)])
def test_cli_default_and_smoke_pass_validated_config_to_gui(cli, monkeypatch, arguments, smoke):
    config, auth, run = cli
    monkeypatch.setattr(sys, "argv", ["ucpc", *arguments])
    assert entry.main() == 17
    run.assert_called_once_with(config, smoke)
    auth.login.assert_not_called()
    auth.models.assert_not_called()


def test_cli_check_rejects_empty_prompt_before_login(cli, monkeypatch):
    _, auth, run = cli
    monkeypatch.setattr(sys, "argv", ["ucpc", "--check"])
    monkeypatch.setattr(Config, "prompt", lambda self: "")
    with pytest.raises(ValueError, match="порожній"):
        entry.main()
    assert not auth.mock_calls and not run.mock_calls


def test_cli_rejects_unsupported_os_before_initialization(cli, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ucpc"])
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(SystemExit) as result:
        entry.main()
    assert result.value.code == 2
    entry.load_config.assert_not_called()
