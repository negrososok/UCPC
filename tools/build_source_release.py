"""Create a clean source ZIP, including the same setup and background launcher."""

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = (
    ".gitignore", "pyproject.toml", "uv.lock", "setup.ps1", "SETUP.cmd", "start.vbs",
    "config.example.toml", "system_prompt.example.txt", ".env.example",
    "SOURCE_START_HERE.txt", "logo/logo.png",
)
SOURCE_FILES = ("ucpc", "tests", "tools")
PYTHON_VERSION = "3.14.5"
FORBIDDEN = {".git", ".venv", "__pycache__", "data", "build", "dist"}
PRIVATE = {".env", "config.toml", "system_prompt.txt", "account.dat", "host.json", "ui.json", "app.lock"}


def source_readme():
    original = (ROOT / "README.md").read_text(encoding="utf8")
    start = original.index("## Запуск на іншому ПК:")
    end = original.index("## Основні клавіші", start)
    instructions = """## Запуск цього архіву з вихідним кодом

Потрібні Windows 11 x64 та інтернет. Вихідний код доступний для читання й
редагування у папці **ucpc**. Залежності встановлюються з **uv.lock**;
**.python-version** вибирає Python 3.14.5, як у перевіреній версії розробника.
Проста покрокова інструкція — **START_HERE.txt**.

1. **Видобути все** з архіву у свою папку «Документи».
2. Один раз установити [uv](https://docs.astral.sh/uv/getting-started/installation/).
3. Двічі натиснути **SETUP.cmd** й дочекатися **Setup complete**.
4. Двічі натиснути **start.vbs** — запуск у фоні без вікна термінала.
5. У налаштуваннях увійти у свій ChatGPT та застосувати доступну модель.

Наступного разу достатньо **start.vbs**. **setup.ps1** і **start.vbs** такі самі,
як у розробника; **SETUP.cmd** запускає setup.ps1 подвійним кліком.
Тести знаходяться у **tests**, інструменти — у **tools**. Особистих налаштувань,
акаунтів і готового середовища .venv в архіві немає.

"""
    return (original[:start] + instructions + original[end:]).encode("utf8")


def payload():
    files = {}
    for relative in ASSETS:
        path = ROOT / relative
        if not path.is_file() or path.is_symlink():
            raise RuntimeError("Missing or symlinked source asset: " + relative)
        files[relative] = path.read_bytes()
    for directory in SOURCE_FILES:
        for path in sorted((ROOT / directory).rglob("*.py")):
            relative = path.relative_to(ROOT)
            if any(part in FORBIDDEN for part in relative.parts):
                continue
            if path.is_symlink():
                raise RuntimeError("Symlinked source module: " + relative.as_posix())
            files[relative.as_posix()] = path.read_bytes()
    if "ucpc/__main__.py" not in files:
        raise RuntimeError("Source entry point is missing")
    files[".python-version"] = (PYTHON_VERSION + "\n").encode("ascii")
    files["START_HERE.txt"] = files["SOURCE_START_HERE.txt"]
    files["README.md"] = source_readme()
    if any(set(Path(name).parts) & FORBIDDEN or Path(name).name in PRIVATE for name in files):
        raise RuntimeError("Private files found in source archive")
    manifest = [{"file": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                for name, data in sorted(files.items())]
    files["manifest.json"] = json.dumps(manifest, indent=2).encode("utf8")
    return files


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="dist/UCPC-source-Windows11.zip")
    args = parser.parse_args()
    target = (ROOT / args.output).resolve()
    if target.exists():
        raise FileExistsError("Choose a new output name: " + str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    files = payload()
    with zipfile.ZipFile(target, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, data in sorted(files.items()):
            archive.writestr("UCPC/" + name, data)
    with zipfile.ZipFile(target) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("ZIP CRC validation failed")
        if set(archive.namelist()) != {"UCPC/" + name for name in files}:
            raise RuntimeError("ZIP file list mismatch")
        for name, data in files.items():
            if archive.read("UCPC/" + name) != data:
                raise RuntimeError("ZIP file mismatch: " + name)
    print(json.dumps({"archive": str(target), "bytes": target.stat().st_size,
                      "files": len(files), "python": PYTHON_VERSION,
                      "source_modules": sum(name.startswith("ucpc/") for name in files),
                      "clean_templates": True, "zip_verified": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
