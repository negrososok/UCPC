"""Audit Windows defaults against Microsoft's complete generated shortcut list."""

import hashlib
import json
import re
import urllib.request
from pathlib import Path

from ucpc.actions import DEFAULT_HOTKEYS
from ucpc.mouse_bindings import MOUSE_KEYS

URL = "https://raw.githubusercontent.com/microsoft/vscode-docs/main/build/keybindings/doc.keybindings.win.json"


def canonical(chord):
    parts = chord.lower().split("+")
    return tuple(sorted(parts[:-1])), parts[-1]


def audit(entries):
    occupied = {}
    for rule in entries:
        # Include BOTH parts of multi-step shortcuts and ignore 'when' conditions.
        for chord in rule.get("win", rule.get("key", "")).split():
            occupied.setdefault(canonical(chord), []).append(rule.get("command", ""))
    return {
        action: occupied[canonical(binding)]
        for action, binding in DEFAULT_HOTKEYS.items()
        if canonical(binding) in occupied
    }


def main():
    raw = urllib.request.urlopen(URL, timeout=30).read()
    entries = json.loads(raw)
    conflicts = audit(entries)
    root = Path(__file__).resolve().parent.parent
    local = Path.home() / "AppData/Local/Programs/Microsoft VS Code"
    local_bindings = []
    versions = []
    # Bundled extension bindings are part of VS Code's defaults, too.
    for package in local.glob("*/resources/app/package.json"):
        versions.append(json.loads(package.read_text(encoding="utf8"))["version"])
        for manifest in (package.parent / "extensions").glob("*/package.json"):
            data = json.loads(manifest.read_text(encoding="utf8"))
            bindings = data.get("contributes", {}).get("keybindings", [])
            local_bindings.extend([bindings] if isinstance(bindings, dict) else bindings)
        bundle = package.parent / "out/vs/workbench/workbench.desktop.main.js"
        if bundle.exists():
            text = bundle.read_text(encoding="utf8")
            # Compiled numeric KeyMod constants: Ctrl=2048, Alt=512, Shift=1024, Win=256.
            numeric = set()
            for match in re.finditer(r"primary:(\d+)|secondary:\[([\d,]+)\]", text):
                numeric.update(map(int, (match[1] or match[2]).split(",")))
            key_codes = {
                "home": 14,
                "end": 13,
                "up": 16,
                "down": 18,
                "left": 15,
                "right": 17,
                "pageup": 11,
                "pagedown": 12,
            }
            key_codes.update({chr(65 + i).lower(): 31 + i for i in range(26)})
            key_codes.update({f"f{i}": 58 + i for i in range(1, 25)})
            for action, binding in DEFAULT_HOTKEYS.items():
                parts = binding.split("+")
                if parts[-1] in MOUSE_KEYS:
                    continue  # VS Code's numeric KeyCode table describes keyboard keys only.
                code = (
                    sum(
                        {"ctrl": 2048, "alt": 512, "shift": 1024, "win": 256}[p] for p in parts[:-1]
                    )
                    | key_codes[parts[-1]]
                )
                if code in numeric:
                    conflicts[action] = ["Compiled VS Code binding; inspect platform override"]
    conflicts.update(audit(local_bindings))
    out = {
        "source": URL,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "rules": len(entries),
        "bundled_extension_rules": len(local_bindings),
        "installed_versions": versions,
        "checked_bindings": DEFAULT_HOTKEYS,
        "conflicts": conflicts,
    }
    (root / "data").mkdir(exist_ok=True)
    (root / "data/vscode-default-keybindings.json").write_bytes(raw)
    (root / "data/shortcut-audit.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf8"
    )
    print(json.dumps({k: v for k, v in out.items() if k != "checked_bindings"}))
    if conflicts:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
