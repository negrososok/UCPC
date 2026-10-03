"""Build an unarchived Windows distribution from an explicit, clean source allowlist.

The ZIP is intentionally a separate step after the user's review.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = (
    "config.example.toml",
    "system_prompt.example.txt",
    ".env.example",
    "README.md",
    "START_HERE.txt",
    "logo/logo.png",
)
MODULES = (
    "__init__.py",
    "__main__.py",
    "actions.py",
    "app.py",
    "auth.py",
    "capture.py",
    "code_format.py",
    "config.py",
    "diagnostics.py",
    "engine.py",
    "history.py",
    "help_panel.py",
    "hotkeys.py",
    "mouse_bindings.py",
    "overlay.py",
    "privacy.py",
    "settings.py",
    "solver.py",
    "theme.py",
)


def stage_sources(source, target):
    """Never copy live configuration, account state, logs, tests or the developer's .env."""
    target.mkdir(parents=True, exist_ok=True)
    for name in ASSETS:
        (target / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target / name)
    (target / "ucpc").mkdir(exist_ok=True)
    for name in MODULES:
        shutil.copyfile(source / "ucpc" / name, target / "ucpc" / name)
    (target / "launcher.py").write_text(
        'import runpy\nrunpy.run_module("ucpc", run_name="__main__")\n', encoding="utf8"
    )


def audit_distribution(directory):
    forbidden = {
        "config.toml",
        "system_prompt.txt",
        ".env",
        "account.dat",
        "host.json",
        "ui.json",
        "app.lock",
        "coverage.json",
    }
    files = sorted(p for p in directory.rglob("*") if p.is_file())
    bad = [
        p.relative_to(directory).as_posix()
        for p in files
        if p.name in forbidden
        or (
            p.suffix.lower() in {".log", ".png", ".jpg", ".wav", ".mp3"}
            and p.relative_to(directory).as_posix() != "logo/logo.png"
        )
    ]
    if bad:
        raise RuntimeError("Personal/runtime files in distribution: " + ", ".join(bad))
    if not (directory / "UCPC.exe").exists():
        raise RuntimeError("UCPC.exe is missing")
    for name in ASSETS:
        if (directory / name).read_bytes() != (ROOT / name).read_bytes():
            raise RuntimeError("Unexpected distribution asset: " + name)
    # Hashes describe the review build without including local absolute paths.
    manifest = []
    for p in files:
        if p.name == "manifest.json":
            continue
        with p.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        manifest.append(
            {
                "file": p.relative_to(directory).as_posix(),
                "bytes": p.stat().st_size,
                "sha256": digest,
            }
        )
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf8")
    return len(files)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-only", action="store_true")
    args = parser.parse_args()
    output = ROOT / "dist/UCPC"
    if not args.audit_only:
        # A fixed clean staging area under this workspace; no credentials are ever copied in.
        (ROOT / "build").mkdir(exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix="clean-source-", dir=ROOT / "build"))
        stage_sources(ROOT, stage)
        env = os.environ.copy()
        windows = Path(env.get("SystemRoot", "C:/Windows"))
        # PATH from the Codex shell contains unrelated native tools (e.g. Poppler).
        # Their ICU DLLs share Windows DLL names but export a different ABI.
        env["PATH"] = os.pathsep.join(
            str(p) for p in (windows / "System32", windows, Path(sys.executable).parent)
        )
        for name in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "PYTHONPATH"):
            env.pop(name, None)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "PyInstaller",
                "--noconfirm",
                "--windowed",
                "--onedir",
                "--name",
                "UCPC",
                "--icon",
                str(stage / "logo/logo.png"),
                "--distpath",
                str(ROOT / "dist"),
                "--workpath",
                str(ROOT / "build/pyinstaller"),
                "--specpath",
                str(ROOT / "build"),
                "--paths",
                str(stage),
                "--collect-submodules",
                "ucpc",
                str(stage / "launcher.py"),
            ],
            cwd=stage,
            env=env,
            check=True,
        )
        for name in ASSETS:
            (output / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(stage / name, output / name)
    count = audit_distribution(output)
    print(f"Clean unarchived distribution: {output} ({count} files)")


if __name__ == "__main__":
    main()
