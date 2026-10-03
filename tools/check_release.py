"""Opt-in frozen launch check with an empty profile, no Python on PATH and a Unicode path."""

import ctypes
import json
import os
import shutil
import subprocess
import tempfile
import time
import tomllib
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def keyboard_probe():
    """The smoke exit code alone cannot detect App's recoverable binding conflict."""
    native = ctypes.WinDLL("user32", use_last_error=True)
    native.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    native.RegisterHotKey.restype = wintypes.BOOL
    native.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    native.UnregisterHotKey.restype = wintypes.BOOL

    def free():
        # Ctrl+Win+F1 is available before launch and owned by the frozen App after
        # successful binding setup. Mouse hook failure rolls keyboard setup back.
        if native.RegisterHotKey(None, 0xBFFE, 0x400A, 0x70):
            if not native.UnregisterHotKey(None, 0xBFFE):
                raise ctypes.WinError(ctypes.get_last_error())
            return True
        if ctypes.get_last_error() != 1409:  # ERROR_HOTKEY_ALREADY_REGISTERED
            raise ctypes.WinError(ctypes.get_last_error())
        return False

    return free


def main():
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="frozen-check-", dir=build)).resolve()
    if not scratch.is_relative_to(build.resolve()):
        raise RuntimeError("Release check must stay inside the build directory")
    # Keep the fixture for inspection. Never clean or copy the owner's live profile.
    target = scratch / "Друг без Python з пробілами" / "UCPC"
    shutil.copytree(ROOT / "dist/UCPC", target)
    env = os.environ.copy()
    windows = Path(env.get("SystemRoot", "C:/Windows"))
    env["PATH"] = os.pathsep.join(str(p) for p in (windows / "System32", windows))
    env["LOCALAPPDATA"] = str(scratch / "empty-profile")
    for name in ("PYTHONPATH", "PYTHONHOME", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH",
                 "OPENAI_API_KEY"):
        env.pop(name, None)
    rows = []
    available = keyboard_probe()
    for argument in ("--check", "--smoke", "--smoke", "--smoke"):
        started = time.monotonic()
        if not available():
            raise RuntimeError("Close other UCPC instances before the frozen launch check")
        registered = False
        with subprocess.Popen([str(target / "UCPC.exe"), argument], cwd=target, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              creationflags=subprocess.CREATE_NO_WINDOW) as process:
            try:
                deadline = time.monotonic() + 45
                while process.poll() is None:
                    registered = not available() or registered
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Frozen launch did not finish")
                    time.sleep(0.04)
                process.communicate(timeout=3)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=3)
        rows.append({"argument": argument, "exit_code": process.returncode,
                     "bindings_registered": registered if argument == "--smoke" else None,
                     "seconds": round(time.monotonic() - started, 2)})
        state = scratch / "empty-profile/UCPC"
        if (process.returncode or (state / "startup-error.log").exists()
                or (argument == "--smoke" and not registered) or not available()):
            raise RuntimeError("Frozen launch failed: " + json.dumps(rows[-1]))
    config = tomllib.loads((target / "config.toml").read_text(encoding="utf8"))
    assert config["hotkeys"]["capture"] == "mouse4"
    assert config["hotkeys"]["send"] == "mouse5"
    assert not (state / "account.dat").exists()
    # First launch creates an empty API-key template, never the owner's .env.
    assert (target / ".env").read_bytes() == (target / ".env.example").read_bytes()
    report = {"path_contains_unicode_and_spaces": True, "python_on_path": False,
              "fresh_profile": True, "capture": config["hotkeys"]["capture"],
              "send": config["hotkeys"]["send"], "checks": rows,
              "fixture": scratch.relative_to(ROOT).as_posix()}
    output = ROOT / "data"
    output.mkdir(exist_ok=True)
    (output / "release-check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                              encoding="utf8")
    print(json.dumps({k: v for k, v in report.items() if k != "fixture"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
