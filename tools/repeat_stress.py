"""Repeat the deterministic stress suite in fresh processes and save a local report."""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycles", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.cycles <= 100:
        parser.error("cycles must be between 1 and 100")
    root = Path(__file__).resolve().parent.parent
    output = root / "data" / "stress"
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for number in range(1, args.cycles + 1):
        started = time.monotonic()
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-m",
                "stress",
                "-q",
                "-W",
                "error",
                f"--junitxml={output / f'cycle-{number}.xml'}",
            ],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        elapsed = round(time.monotonic() - started, 3)
        (output / f"cycle-{number}.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
        results.append({"cycle": number, "seconds": elapsed, "exit_code": result.returncode})
        (output / "summary.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(
            f"Cycle {number}/{args.cycles}: {'PASS' if result.returncode == 0 else 'FAIL'} ({elapsed}s)",
            flush=True,
        )
        if result.returncode:
            print(result.stdout + result.stderr)
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
