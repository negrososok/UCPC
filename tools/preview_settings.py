"""Render only synthetic settings; never include the user's account or instruction."""

import argparse
from pathlib import Path

from PySide6.QtWidgets import QApplication

from ucpc.config import Config
from ucpc.settings import Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--theme", choices=("light", "dark"), default="light")
    parser.add_argument("--tab", type=int, choices=range(1, 5), default=1)
    args = parser.parse_args()
    qt = QApplication([])
    qt.setQuitOnLastWindowClosed(False)
    settings = Settings(Config(theme=args.theme))
    settings.tabs.setCurrentIndex(args.tab - 1)
    settings.account.setText("ChatGPT: synthetic@example.test")
    settings.prompt_editor.setPlainText("Synthetic preview instruction")
    settings.status.setText("Готово · налаштування для наступного запиту")
    output = (
        Path(__file__).resolve().parent.parent
        / "data"
        / f"settings-preview-{args.theme}-{args.tab}.png"
    )
    output.parent.mkdir(exist_ok=True)
    try:
        settings.show_protected()
        qt.processEvents()
        if not settings.grab().save(str(output)):
            raise RuntimeError("Could not save preview")
        print(output)
    finally:
        settings.hide()


if __name__ == "__main__":
    main()
