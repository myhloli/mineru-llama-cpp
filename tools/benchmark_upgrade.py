"""交替运行两个已安装包，各轮独立进程并保存诊断、输出和性能中位数。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys


def main() -> None:
    """使用同一 Python、模型和图像交替比较基线与候选安装目录。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-package", required=True, type=Path)
    parser.add_argument("--candidate-package", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--mmproj", required=True, type=Path)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("--rounds must be positive")
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    reports = {"baseline": [], "candidate": []}
    diagnostic = Path(__file__).with_name("diagnose_metal.py").resolve()
    for round_index in range(args.rounds):
        order = ("baseline", "candidate") if round_index % 2 == 0 else ("candidate", "baseline")
        for name in order:
            output = args.output / f"round-{round_index + 1}" / name
            package = getattr(args, name + "_package").resolve()
            env = os.environ.copy()
            env["PYTHONPATH"] = str(package)
            command = [sys.executable, str(diagnostic), "--model", str(args.model.resolve()),
                       "--mmproj", str(args.mmproj.resolve()), "--image", str(args.image.resolve()),
                       "--extract", "--output", str(output)]
            print(f"round {round_index + 1}: {name}", flush=True)
            completed = subprocess.run(command, env=env, cwd=args.output.parent, capture_output=True, text=True)
            output.mkdir(parents=True, exist_ok=True)
            (output / "driver.log").write_text(completed.stdout + completed.stderr)
            if completed.returncode:
                raise RuntimeError(f"{name} failed; see {output / 'native.log'}")
            report = json.loads((output / "result.json").read_text())
            assert Path(report["package_path"]).is_relative_to(package), report["package_path"]
            reports[name].append(report)
    fields = ["initialization_seconds", "generation_seconds", "layout_seconds", "extraction_seconds", "peak_rss_bytes"]
    medians = {name: {field: statistics.median(r[field] for r in runs) for field in fields}
               for name, runs in reports.items()}
    comparison = {
        "rounds": args.rounds,
        "medians": medians,
        "candidate_over_baseline": {field: medians["candidate"][field] / medians["baseline"][field] for field in fields},
        "extraction_identical": [reports["baseline"][i]["extraction"] == reports["candidate"][i]["extraction"]
                                 for i in range(args.rounds)],
        "generation_identical": [reports["baseline"][i]["generation"]["content"] == reports["candidate"][i]["generation"]["content"]
                                 for i in range(args.rounds)],
    }
    (args.output / "summary.json").write_text(json.dumps(comparison, ensure_ascii=False, indent=2))
    print(json.dumps(comparison, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
