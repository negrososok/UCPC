"""Opt-in live vision evals on synthetic tasks; compile/run outputs and save local evidence."""

import argparse
import asyncio
import base64
import json
import random
import re
import shutil
import subprocess
import sys
import time
from dataclasses import replace
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ucpc.auth import Auth
from ucpc.config import load_config
from ucpc.engine import Engine
from ucpc.history import Track

ROOT = Path(__file__).resolve().parent.parent

TASKS = {
    "cpp": (
        "Language: C++23. Time: 2 s. Memory: 256 MB.\n"
        "Input: n q, then n integers, then q lines l r (1-based inclusive).\n"
        "Print the sum of a[l..r] for each query on a separate line.\n"
        "1 <= n,q <= 200000. -1000000000 <= a[i] <= 1000000000.\n"
        "Example input:\n4 3\n1 -2 3 4\n1 4\n2 3\n4 4\n"
        "Example output:\n6\n1\n4"
    ),
    "python": (
        "Language: Python 3. Time: 3 s. Memory: 256 MB.\n"
        "Input: h w followed by h rows of a grid. '.' is free, '#' is blocked.\n"
        "Find the shortest path from (0,0) to (h-1,w-1), moving up/down/left/right.\n"
        "Print the number of moves, or -1 if unreachable. Both endpoints are free.\n"
        "1 <= h,w <= 500. Example input:\n3 3\n...\n##.\n...\nExample output: 4"
    ),
    "javascript": (
        "Language: JavaScript (Node.js). Time: 2 s. Memory: 256 MB.\n"
        "Input: n, followed by n integers (across arbitrary whitespace).\n"
        "Print the maximum length of a consecutive run of equal integers.\n"
        "1 <= n <= 200000. -1000000000 <= a[i] <= 1000000000.\n"
        "Example input: 8\n1 1 2 2 2 -3 -3 1\nExample output: 3"
    ),
    "csharp": (
        "Language: C# (.NET 8). Time: 2 s. Memory: 256 MB.\n"
        "Input: n, then n integers separated by arbitrary whitespace.\n"
        "Print the sum of the integers.\n"
        "1 <= n <= 200000. -1000000000 <= a[i] <= 1000000000.\n"
        "Example input: 4\n1 -2 3 4\nExample output: 6"
    ),
}


def screenshot(text):
    font = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 24)
    image = Image.new("RGB", (1240, 800), "white")
    ImageDraw.Draw(image).multiline_text((24, 24), text, font=font, fill="black", spacing=10)
    stream = BytesIO()
    image.save(stream, format="PNG")
    return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode("ascii")


def cases(language):
    rng = random.Random(7302)
    result = []
    if language == "cpp":
        for values in ([1, -2, 3, 4], [1000000000], [-7] * 37,
                       [rng.randrange(-1000000000, 1000000001) for _ in range(61)],
                       [1000000000] * 200000):
            n = len(values)
            queries = [(1, n), (1, 1), (n, n)]
            queries += [(rng.randrange(1, n + 1), n) for _ in range(199997 if n > 100 else 70)]
            prefix = [0]
            for value in values:
                prefix.append(prefix[-1] + value)
            data = f"{n} {len(queries)}\n" + " ".join(map(str, values)) + "\n"
            data += "\n".join(f"{l} {r}" for l, r in queries) + "\n"
            result.append((data, "\n".join(str(prefix[r] - prefix[l - 1]) for l, r in queries)))
    elif language == "python":
        for grid, expected in [(["."], 0), (["...", "##.", "..."], 4),
                               ([".#", "#."], -1), (["...", ".#.", "..."], 4),
                               (["." * 500] * 500, 998)]:
            result.append((f"{len(grid)} {len(grid[0])}\n" + "\n".join(grid) + "\n",
                           str(expected)))
    else:
        inputs = [[1, -2, 3, 4], [0], [-8] * 57,
                  [rng.randrange(-4, 5) for _ in range(321)], [1000000000] * 200000]
        for values in inputs:
            if language == "csharp":
                expected = sum(values)
            else:
                longest = current = 1
                for i in range(1, len(values)):
                    current = current + 1 if values[i] == values[i - 1] else 1
                    longest = max(longest, current)
                expected = longest
            # Include tabs/newlines and multiple spaces instead of assuming one input line.
            data = f"{len(values)}\n" + " \n\t".join(map(str, values)) + "\n"
            result.append((data, str(expected)))
    return result


def runner(language, code, directory):
    paths = {"cpp": "main.cpp", "python": "main.py", "javascript": "main.js",
             "csharp": "Program.cs"}
    path = directory / paths[language]
    path.write_text(code, encoding="utf8")
    if language == "cpp":
        compiler = shutil.which("g++")
        if not compiler:
            raise RuntimeError("g++ unavailable")
        executable = directory / "main.exe"
        build = subprocess.run([compiler, "-std=gnu++23", "-O2", str(path), "-o", str(executable)],
                               capture_output=True, text=True, timeout=30, check=False)
        if build.returncode:
            raise RuntimeError("Compilation failed: " + build.stderr[:1600])
        return [str(executable)]
    if language == "python":
        compile(code, str(path), "exec")
        return [sys.executable, str(path)]
    if language == "javascript":
        node = shutil.which("node")
        if not node:
            raise RuntimeError("Node.js unavailable")
        return [node, str(path)]
    dotnet = shutil.which("dotnet")
    if not dotnet:
        raise RuntimeError(".NET SDK unavailable")
    project = directory / "Eval.csproj"
    project.write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
                       '<OutputType>Exe</OutputType><TargetFramework>net8.0</TargetFramework>'
                       '<ImplicitUsings>enable</ImplicitUsings></PropertyGroup></Project>',
                       encoding="utf8")
    nuget = directory / "NuGet.Config"
    nuget.write_text('<configuration><packageSources><clear /></packageSources></configuration>',
                     encoding="utf8")
    build = subprocess.run([dotnet, "build", str(project), "--configuration", "Release",
                            "--configfile", str(nuget), "--nologo", "--verbosity", "quiet"],
                           capture_output=True, text=True, timeout=45, check=False)
    if build.returncode:
        raise RuntimeError("Compilation failed: " + (build.stdout + build.stderr)[:1600])
    return [dotnet, str(directory / "bin/Release/net8.0/Eval.dll")]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--languages", nargs="+", choices=TASKS, default=list(TASKS))
    args = parser.parse_args()
    output = ROOT / "data/style-eval"
    output.mkdir(parents=True, exist_ok=True)
    config = replace(load_config(), verify_answer=False)
    job = Engine.__new__(Engine)
    job.config, job.auth = config, Auth()
    job.on_error = lambda *_: None
    report = []
    for language in args.languages:
        started = time.monotonic()
        frame = screenshot(TASKS[language])
        track = Track(task_images=(frame,))
        await job._generate(frame, config.prompt(), track)
        code, complete, error = track.snapshot()
        directory = output / language
        directory.mkdir(exist_ok=True)
        result = {"language": language, "model": track.model_name(),
                  "reasoning_effort": config.reasoning_effort,
                  "generation_seconds": round(time.monotonic() - started, 2),
                  "complete": complete, "error": error, "cases": []}
        try:
            if error or not complete or not code:
                raise RuntimeError(error or "No complete answer")
            if "```" in code or re.search(r"(?:import os|subprocess|child_process|DllImport)", code):
                raise RuntimeError("Output outside this synthetic eval's code-only contract")
            command = await asyncio.to_thread(runner, language, code, directory)
            for number, (data, expected) in enumerate(cases(language), 1):
                before = time.monotonic()
                execution = await asyncio.to_thread(
                    subprocess.run, command, input=data, capture_output=True, text=True,
                    encoding="utf8", timeout=3, cwd=directory, check=False
                )
                passed = execution.returncode == 0 and execution.stdout.split() == expected.split()
                result["cases"].append({"case": number, "passed": passed,
                                         "seconds": round(time.monotonic() - before, 3)})
                if not passed:
                    result["execution_error"] = execution.stderr[:1600]
            result["unnecessary_class_or_template"] = bool(
                re.search(r"\b(?:struct|template)\b|#define", code) if language == "cpp"
                else re.search(r"\b(?:class|lambda)\b", code) if language == "python"
                else re.search(r"\bclass\b", code) if language == "javascript" else False
            )
            result["has_tabs"] = "\t" in code
            result["style_passed"] = (
                not result["unnecessary_class_or_template"]
                and ("\t" not in code if language == "python" else "\t" in code)
                and ("NextLong" not in code and "FastScanner" not in code
                     if language == "csharp" else True)
            )
            result["passed"] = all(item["passed"] for item in result["cases"]) and result["style_passed"]
        except (RuntimeError, SyntaxError, subprocess.TimeoutExpired) as exc:
            result["evaluation_error"] = str(exc)[:1800]
            result["passed"] = False
        report.append(result)
        (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                             encoding="utf8")
        print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if all(row["passed"] for row in report) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
