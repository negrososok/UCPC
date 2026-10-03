"""Repeat the deterministic stress suite in fresh processes and save a local report."""

import argparse
import json
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycles", type=int, default=5)
    parser.add_argument("--full-cycles", type=int, default=0)
    args = parser.parse_args()
    if not 1 <= args.cycles <= 100:
        parser.error("cycles must be between 1 and 100")
    if not 0 <= args.full_cycles <= 20:
        parser.error("full-cycles must be between 0 and 20")
    root = Path(__file__).resolve().parent.parent
    output = root / "data" / "stress"
    output.mkdir(parents=True, exist_ok=True)
    results = []
    runs = [("full", number) for number in range(1, args.full_cycles + 1)]
    runs += [("stress", number) for number in range(1, args.cycles + 1)]
    for suite, number in runs:
        started = time.monotonic()
        report_path = output / f"{suite}-cycle-{number}.xml"
        command = [sys.executable, "-m", "pytest", "-q", "-W", "error",
                   f"--junitxml={report_path}"]
        if suite == "stress":
            command += ["-m", "stress"]
        result = subprocess.run(
            command,
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        elapsed = round(time.monotonic() - started, 3)
        (output / f"{suite}-cycle-{number}.txt").write_text(
            result.stdout + result.stderr, encoding="utf-8"
        )
        counts = {}
        if report_path.exists():
            reports = ET.parse(report_path).getroot().findall("testsuite")
            counts = {key: sum(int(report.get(key, 0)) for report in reports)
                      for key in ("tests", "failures", "errors", "skipped")}
        results.append({"suite": suite, "cycle": number, "seconds": elapsed,
                        "exit_code": result.returncode, **counts})
        (output / "summary.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(
            f"{suite.capitalize()} cycle {number}: "
            f"{'PASS' if result.returncode == 0 else 'FAIL'} ({elapsed}s, {counts.get('tests', 0)} tests)",
            flush=True,
        )
        if result.returncode:
            print(result.stdout + result.stderr)
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
