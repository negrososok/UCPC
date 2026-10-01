"""Render a synthetic answer to inspect the overlay without capturing personal desktop data."""

import argparse
from pathlib import Path

from PySide6.QtWidgets import QApplication

from ucpc.history import Track
from ucpc.overlay import Overlay


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--theme", choices=("light", "dark"), default="light")
    args = parser.parse_args()
    qt = QApplication([])
    qt.setQuitOnLastWindowClosed(False)
    reader = Overlay(theme=args.theme)
    code = """class Solution:
    def twoSum(self, nums, target):
        seen = {}

        for i, value in enumerate(nums):
            complement = target - value

            if complement in seen:
                return [seen[complement], i]

            seen[value] = i
"""
    track = Track(text=code, model="gpt-5.6-sol", complete=True)
    reader.update_response(track, code, "Текст готовий · модель: gpt-5.6-sol")
    reader.counter.setText("Відповідь 1 / 3")
    output = Path(__file__).resolve().parent.parent / "data" / f"overlay-preview-{args.theme}.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        reader.show_protected()
        qt.processEvents()
        if not reader.grab().save(str(output)):
            raise RuntimeError("Could not save preview")
        print(output)
    finally:
        reader.hide()


if __name__ == "__main__":
    main()
