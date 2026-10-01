import json
from pathlib import Path

import pytest

from tools.audit_shortcuts import audit
from tools.build_release import ASSETS, MODULES, audit_distribution, stage_sources
from ucpc.actions import DEFAULT_HOTKEYS


def test_clean_staging_is_an_allowlist_even_when_source_contains_private_files(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    (source / "ucpc").mkdir(parents=True)
    for name in ASSETS:
        (source / name).parent.mkdir(parents=True, exist_ok=True)
        (source / name).write_text("safe template", encoding="utf8")
    for name in MODULES:
        (source / "ucpc" / name).write_text("# safe source", encoding="utf8")
    for name in (".env", "config.toml", "system_prompt.txt", "account.dat", "host.json"):
        (source / name).write_text("PRIVATE_SENTINEL", encoding="utf8")
    (source / "data").mkdir()
    (source / "data/response.txt").write_text("PRIVATE_SENTINEL", encoding="utf8")
    stage_sources(source, target)
    files = {p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()}
    assert files == set(ASSETS) | {f"ucpc/{name}" for name in MODULES} | {"launcher.py"}
    assert not any(b"PRIVATE_SENTINEL" in p.read_bytes() for p in target.rglob("*") if p.is_file())


def test_distribution_audit_rejects_runtime_credentials(tmp_path):
    (tmp_path / "UCPC.exe").write_bytes(b"synthetic executable")
    (tmp_path / "account.dat").write_bytes(b"private")
    with pytest.raises(RuntimeError, match="account.dat"):
        audit_distribution(tmp_path)


def test_audit_normalizes_modifier_order_and_checks_chord_prefixes():
    conflict = audit([{"key": "win+ctrl+f8 ctrl+k", "command": "test"}])
    assert conflict == {"capture": ["test"]}
    assert not audit([{"key": "ctrl+f8", "command": "unrelated"}])


def test_default_bindings_match_the_saved_microsoft_windows_list():
    path = Path(__file__).resolve().parent.parent / "data/vscode-default-keybindings.json"
    if not path.exists():
        pytest.skip("Run tools.audit_shortcuts to refresh Microsoft's list")
    entries = json.loads(path.read_bytes())
    assert len(entries) > 1000
    assert not audit(entries)
    assert len(DEFAULT_HOTKEYS) == len(set(DEFAULT_HOTKEYS.values()))
    assert all(len(binding.split("+")) <= 3 for binding in DEFAULT_HOTKEYS.values())
    assert all("alt" not in binding.split("+") for binding in DEFAULT_HOTKEYS.values())


def test_distribution_only_allows_the_exact_bundled_logo(tmp_path, monkeypatch):
    import tools.build_release as release

    monkeypatch.setattr(release, "ROOT", tmp_path)
    for name in ASSETS:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(b"safe template")
    (tmp_path / "UCPC.exe").write_bytes(b"synthetic executable")
    audit_distribution(tmp_path)
    (tmp_path / "logo/image.png").write_bytes(b"private screenshot")
    with pytest.raises(RuntimeError, match="image.png"):
        audit_distribution(tmp_path)
