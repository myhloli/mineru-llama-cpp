"""在独立进程中诊断实际安装包的 Metal、模型生成和文档提取。"""

from __future__ import annotations

import argparse
import base64
from dataclasses import asdict
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import resource
import subprocess
import sys
import time


def parse_args() -> argparse.Namespace:
    """收集模型路径、运行模式和原始日志保存目录。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--mmproj", required=True, type=Path)
    parser.add_argument("--image", type=Path)
    parser.add_argument("--extract", action="store_true", help="运行 MinerU 两阶段提取")
    parser.add_argument("--mode", choices=["default", "disable", "enable", "cpu"], default="default")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def run_worker(args: argparse.Namespace) -> None:
    """加载安装包并保存预热后的生成、图像和可选两阶段提取结果。"""
    import mineru_llama_cpp
    from mineru_llama_cpp import Engine, LOG_LEVEL_DEBUG, SamplingParams
    from mineru_llama_cpp import _mineru_llama_cpp as native

    report = {
        "platform": platform.platform(),
        "python": sys.version,
        "package_version": importlib.metadata.version("mineru-llama-cpp"),
        "package_path": str(Path(mineru_llama_cpp.__file__).resolve()),
        "llama_cpp_commit": getattr(native, "_llama_cpp_commit", "unavailable (older wheel)"),
        "mode": args.mode,
    }
    if sys.platform == "darwin":
        report["chip"] = subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], text=True).strip()
        report["macos_build"] = subprocess.check_output(["sw_vers", "-buildVersion"], text=True).strip()
    (args.output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    sp = SamplingParams(temperature=0.0, top_k=1, seed=42, n_predict=32)
    messages = [{"role": "user", "content": "Say hello in exactly one word."}]
    started = time.perf_counter()
    with Engine(args.model, args.mmproj, n_ctx_seq=8192, n_parallel=1,
                n_gpu_layers=0 if args.mode == "cpu" else 99, verbosity=LOG_LEVEL_DEBUG) as engine:
        report["initialization_seconds"] = time.perf_counter() - started
        engine.generate(messages, sp)
        started = time.perf_counter()
        result = engine.generate(messages, sp)
        report["generation_seconds"] = time.perf_counter() - started
        report["generation"] = asdict(result)
        if args.image:
            uri = "data:image/png;base64," + base64.b64encode(args.image.read_bytes()).decode()
            image_messages = [{"role": "system", "content": "You are a helpful assistant."},
                              {"role": "user", "content": [
                                  {"type": "image_url", "image_url": {"url": uri}},
                                  {"type": "text", "text": "\nLayout Detection:"}]}]
            started = time.perf_counter()
            report["layout"] = asdict(engine.generate(
                image_messages, SamplingParams(temperature=0.0, top_p=0.01, top_k=1,
                                              repeat_penalty=1.0, n_predict=1024)))
            report["layout_seconds"] = time.perf_counter() - started
        if args.extract:
            if not args.image:
                raise ValueError("--extract requires --image")
            from PIL import Image
            from mineru_vl_utils import MinerUClient

            client = MinerUClient(backend="llama-cpp-engine", llama_cpp_engine=engine,
                                  use_tqdm=False, max_concurrency=1)
            with Image.open(args.image) as source:
                image = source.convert("RGB")
            client.two_step_extract(image)
            started = time.perf_counter()
            report["extraction"] = client.two_step_extract(image)
            report["extraction_seconds"] = time.perf_counter() - started
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report["peak_rss_bytes"] = rss if sys.platform == "darwin" else rss * 1024
    (args.output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))


def main() -> None:
    """隔离环境开关、捕获原生日志，并区分 Tensor 能力和实际 GPU 卸载。"""
    args = parse_args()
    args.model = args.model.resolve()
    args.mmproj = args.mmproj.resolve()
    args.image = args.image.resolve() if args.image else None
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.worker:
        run_worker(args)
        return
    command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--model", str(args.model),
               "--mmproj", str(args.mmproj), "--mode", args.mode, "--output", str(args.output)]
    if args.image:
        command += ["--image", str(args.image)]
    if args.extract:
        command.append("--extract")
    env = os.environ.copy()
    env.pop("GGML_METAL_TENSOR_ENABLE", None)
    env.pop("GGML_METAL_TENSOR_DISABLE", None)
    if args.mode in ("enable", "disable"):
        env["GGML_METAL_TENSOR_" + args.mode.upper()] = "1"
    result_path = args.output / "result.json"
    result_path.unlink(missing_ok=True)
    with (args.output / "native.log").open("w") as log:
        completed = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT)
    log = (args.output / "native.log").read_text(errors="replace")
    report = json.loads(result_path.read_text()) if result_path.exists() else {}
    tensor = re.findall(r"has tensor\s*=\s*(true|false)", log)
    report.update({"exit_code": completed.returncode, "tensor_supported": tensor[-1] == "true" if tensor else None,
                   "gpu_offload": re.findall(r"offloaded (\d+)/(\d+) layers to GPU", log),
                   "native_log": str(args.output / "native.log")})
    result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0 if completed.returncode == 0 else 1)


if __name__ == "__main__":
    main()
