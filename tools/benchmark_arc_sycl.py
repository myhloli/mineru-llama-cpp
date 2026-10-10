"""Arc 130T 三轮交替实机验收：旧 SYCL、新 AOT/FP16 SYCL 和 Vulkan。"""
from __future__ import annotations
import argparse
import asyncio
import ctypes
from dataclasses import asdict, replace
import difflib
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import re
import statistics
import subprocess
import sys
import time


def write_json(path: Path, data) -> None:
    """拒绝 NaN/Infinity 后保存 UTF-8 证据，确保日志中的数值可供审阅。"""
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def sha256(path: Path) -> str:
    """分块计算大模型摘要，确认三条路径使用同一模型、投影和 PDF。"""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def peak_rss() -> int:
    """Windows 使用内核峰值工作集；本地脚本检查兼容 macOS/Linux 的 ru_maxrss。"""
    if sys.platform != "win32":
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return value if sys.platform == "darwin" else value * 1024
    class ProcessMemoryCounters(ctypes.Structure):
        """声明 Windows PSAPI 内存计数结构，避免依赖第三方监控进程。"""
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong)] + [
            (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return counters.PeakWorkingSetSize


def system_evidence() -> dict:
    """记录机器、GPU 驱动及电源方案；用户仍需固定插电状态和厂商性能模式。"""
    result = {"platform": platform.platform(), "python": sys.version}
    if sys.platform == "win32":
        for name, command in {
            "power_scheme": ["powercfg", "/getactivescheme"],
            "gpu_and_battery": ["powershell", "-NoProfile", "-Command",
                "@{gpu=@(Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,PNPDeviceID);"
                "battery=@(Get-CimInstance Win32_Battery | Select-Object BatteryStatus,EstimatedChargeRemaining)} | ConvertTo-Json -Depth 4"],
        }.items():
            completed = subprocess.run(command, capture_output=True, text=True, errors="replace")
            result[name] = {"exit_code": completed.returncode, "output": completed.stdout.strip()}
    return result


async def cancellation_check(engine, messages, sampling) -> dict:
    """取消异步等待后继续同步/异步流式调用，保持后台任务完成的既有语义。"""
    task = asyncio.create_task(engine.agenerate(messages, replace(sampling, n_predict=256)))
    await asyncio.sleep(0.01)
    task.cancel()
    cancelled = False
    try:
        await task
    except asyncio.CancelledError:
        cancelled = True
    assert cancelled, "Cancellation ran after the request had already finished"
    result = await engine.agenerate(messages, sampling)
    chunks = [chunk async for chunk in engine.astream(messages, sampling)]
    assert result.content and chunks and chunks[-1].finish_reason
    return {"waiter_cancelled": cancelled, "subsequent_generate": asdict(result),
            "async_stream_final": asdict(chunks[-1]), "async_stream_text": "".join(chunk.delta for chunk in chunks)}


def run_worker(args) -> None:
    """每次仅加载一个 wheel/后端，分别记录引擎任务或完整 MinerU PDF 解析。"""
    args.output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    import mineru_llama_cpp
    from mineru_llama_cpp import Engine, SamplingParams, LOG_LEVEL_DEBUG
    import_seconds = time.perf_counter() - started
    report = {"variant": args.variant, "mode": args.worker, "system": system_evidence(),
              "package_version": importlib.metadata.version("mineru-llama-cpp"),
              "package_path": str(Path(mineru_llama_cpp.__file__).resolve()),
              "import_seconds": import_seconds}
    report["dependency_versions"] = {name: importlib.metadata.version(name)
        for name in ("mineru", "mineru-vl-utils", "pillow", "pypdfium2")}
    report["python_version"] = platform.python_version()
    report["llama_cpp_commit"] = mineru_llama_cpp._mineru_llama_cpp._llama_cpp_commit
    package = Path(mineru_llama_cpp.__file__).resolve().parent
    for module in ("ggml-sycl.dll", "ggml-vulkan.dll"):
        if (package / "bin" / module).exists():
            report[module + "_sha256"] = sha256(package / "bin" / module)
    manifest = package / "bin/sycl-build.json"
    if manifest.exists():
        report["sycl_configuration"] = json.loads(manifest.read_text()).get("configuration", "legacy FP32/JIT")
    if sys.platform == "win32":
        from sycl_profile import WINDOWS_SYCL_PROFILE
        expected = "legacy FP32/JIT" if args.variant == "old-sycl" else WINDOWS_SYCL_PROFILE
        if report.get("sycl_configuration") != expected:
            raise RuntimeError(f"Wrong wheel installed for {args.variant}: {report.get('sycl_configuration')}")
    options = dict(n_ctx_seq=args.context, n_gpu_layers=99, n_parallel=args.parallel,
                   n_threads=args.threads, verbosity=LOG_LEVEL_DEBUG)
    report["engine_parameters"] = options
    report["precision_environment"] = {key: value for key, value in os.environ.items()
        if key.startswith("GGML_SYCL") or key in ("ONEAPI_DEVICE_SELECTOR", "SYCL_CACHE_PERSISTENT")}
    if args.worker == "mineru":
        # 固定 SDK 内部创建引擎的模型路径与参数；不改变 Engine 实现或推理输出。
        def configured_engine(*unused_paths, **unused_options):
            """为完整 MinerU 入口注入与阶段计时完全相同的模型、投影和推理参数。"""
            return Engine(args.model, args.mmproj, **options)
        mineru_llama_cpp.Engine = configured_engine
        started = time.perf_counter()
        from mineru.config import VlmConfig
        from mineru.parser import parse
        result = parse(args.pdf, tier="advanced", ocr_mode="ocr", image_analysis=False,
            page_range=args.pages or "", vlm_config=VlmConfig(engine="llama-cpp", model=str(args.model.parent),
                                                            max_concurrency=args.parallel))
        middle = result.to_dict(skip_defaults=False)
        markdown = result.markdown()
        write_json(args.output / "middle.json", middle)
        (args.output / "document.md").write_text(markdown, encoding="utf-8")
        report["full_mineru_seconds"] = time.perf_counter() - started
        report["mineru_version"] = importlib.metadata.version("mineru")
    else:
        from PIL import Image
        from mineru_vl_utils import MinerUClient
        started = time.perf_counter()
        engine = Engine(args.model, args.mmproj, **options)
        report["initialization_seconds"] = time.perf_counter() - started
        try:
            client = MinerUClient(backend="llama-cpp-engine", llama_cpp_engine=engine,
                                  use_tqdm=False, max_concurrency=args.parallel)
            with Image.open(args.image) as source:
                image = source.convert("RGB")
            started = time.perf_counter()
            first = client.two_step_extract(image)
            report["first_task_seconds"] = time.perf_counter() - started
            write_json(args.output / "first-page.json", first)
            durations = []
            for index in range(args.warm_tasks):
                started = time.perf_counter()
                output = client.two_step_extract(image)
                durations.append(time.perf_counter() - started)
                write_json(args.output / f"warm-page-{index + 1}.json", output)
            report["warm_task_seconds"] = durations
            report["peak_rss_before_lifecycle_bytes"] = peak_rss()
            messages = [{"role": "user", "content": "List three fruits."}]
            sampling = SamplingParams(temperature=0.0, top_k=1, seed=42, n_predict=32)
            result = engine.generate(messages, sampling)
            chunks = list(engine.stream(messages, sampling))
            assert chunks and chunks[-1].finish_reason and "".join(c.delta for c in chunks)
            report["lifecycle"] = {"generate": asdict(result), "stream_final": asdict(chunks[-1]),
                "stream_text": "".join(c.delta for c in chunks),
                "cancel": asyncio.run(cancellation_check(engine, messages, sampling))}
        finally:
            engine.close()
        started = time.perf_counter()
        with Engine(args.model, args.mmproj, **options) as rebuilt:
            assert rebuilt.generate(messages, sampling).content
        report["close_rebuild_seconds"] = time.perf_counter() - started
    report["peak_rss_bytes"] = peak_rss()
    write_json(args.output / "result.json", report)


def invalid_output(value, location="$") -> list[str]:
    """递归拒绝非有限值、非法 Unicode 和替换字符；语义退化另由逐页人工审阅裁决。"""
    errors = []
    if isinstance(value, float) and not math.isfinite(value):
        errors.append(location + ": non-finite number")
    if isinstance(value, str) and ("\ufffd" in value or any(0xd800 <= ord(c) <= 0xdfff for c in value)):
        errors.append(location + ": invalid/replacement Unicode")
    if isinstance(value, dict):
        for key, child in value.items():
            errors += invalid_output(child, f"{location}.{key}")
    if isinstance(value, list):
        for index, child in enumerate(value):
            errors += invalid_output(child, f"{location}[{index}]")
    return errors


def output_differences(before, after, location="$") -> dict:
    """按字段记录差异，允许数值/措辞改变；结构缺失和漏块直接列为拒绝项。"""
    result = {"reject": invalid_output(after, location), "review": []}
    if isinstance(before, dict) and isinstance(after, dict):
        for key in before.keys() - after.keys():
            result["reject"].append(f"{location}.{key}: missing field")
        for key in after.keys() - before.keys():
            result["review"].append({"path": f"{location}.{key}", "change": "added field"})
        for key in before.keys() & after.keys():
            child = output_differences(before[key], after[key], f"{location}.{key}")
            for group in result:
                result[group] += child[group]
    elif isinstance(before, list) and isinstance(after, list):
        if len(after) < len(before):
            result["reject"].append(f"{location}: list shrank {len(before)} -> {len(after)} (possible missing blocks)")
        elif len(after) != len(before):
            result["review"].append({"path": location, "change": "list grew", "before": len(before), "after": len(after)})
        for index, (old, new) in enumerate(zip(before, after)):
            child = output_differences(old, new, f"{location}[{index}]")
            for group in result:
                result[group] += child[group]
    elif type(before) != type(after) and not (type(before) in (int, float) and type(after) in (int, float)):
        result["reject"].append(f"{location}: type changed {type(before).__name__} -> {type(after).__name__}")
    elif before != after:
        change = {"path": location, "before": before, "after": after}
        if isinstance(before, str):
            change["similarity"] = difflib.SequenceMatcher(None, before, after, autojunk=False).ratio()
            if before.strip() and not after.strip():
                result["reject"].append(location + ": content disappeared")
        result["review"].append(change)
    return result


def run_process(python: Path, command: list[str], environment: dict, output: Path, backend: str) -> dict:
    """捕获每次独立进程原生日志，要求模型和投影都实际使用所请求的设备。"""
    output.mkdir(parents=True, exist_ok=True)
    with (output / "native.log").open("w", encoding="utf-8") as log:
        completed = subprocess.run([str(python), str(Path(__file__).resolve()), *command], env=environment,
                                   stdout=log, stderr=subprocess.STDOUT)
    path = output / "result.json"
    report = json.loads(path.read_text()) if path.exists() else {}
    log = (output / "native.log").read_text(encoding="utf-8", errors="replace")
    selected = re.findall(r"mineru-llama-cpp: selected (\S+).*backend=(\S+)", log)
    projectors = re.findall(r"CLIP using (\S+) backend", log)
    expected = "sycl" if backend == "sycl" else "vulkan"
    device_ok = bool(selected and projectors) and all(name.lower() == expected for _, name in selected)
    device_ok = device_ok and all(device in {item[0] for item in selected} for device in projectors)
    report.update(exit_code=completed.returncode, selected_devices=selected, projector_devices=projectors,
                  model_projector_device_consistency=device_ok, native_log=str(output / "native.log"))
    write_json(path, report)
    if completed.returncode or not device_ok:
        raise RuntimeError(f"{output}: execution/device consistency failed; inspect native.log")
    return report


def main() -> None:
    """三轮轮换执行两份独立环境，保存逐页差异并明确保留人工质量验收环节。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-python", type=Path)
    parser.add_argument("--candidate-python", type=Path)
    for key in ("model", "mmproj", "pdf", "output"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--power-mode", required=True, help="固定插电/Windows/厂商性能模式的文字记录")
    parser.add_argument("--pages", default="", help="完整 MinerU 的页码范围；默认全部页")
    parser.add_argument("--image", type=Path, help="阶段计时输入；默认 PDF 首页按 200 DPI 渲染")
    parser.add_argument("--context", type=int, default=8192)
    parser.add_argument("--parallel", type=int, default=1)
    parser.add_argument("--threads", type=int, default=-1)
    parser.add_argument("--warm-tasks", type=int, default=2)
    parser.add_argument("--worker", choices=("engine", "mineru"), help=argparse.SUPPRESS)
    parser.add_argument("--variant", default="", help=argparse.SUPPRESS)
    args = parser.parse_args()
    for name in ("model", "mmproj", "pdf", "output", "image", "baseline_python", "candidate_python"):
        value = getattr(args, name)
        if value is not None:
            setattr(args, name, value.resolve())
    if args.worker:
        run_worker(args)
        return
    if not args.baseline_python or not args.candidate_python or args.warm_tasks < 1:
        parser.error("Two installed Python environments and at least one warmed task are required")
    args.output.mkdir(parents=True, exist_ok=True)
    if not args.image:
        import pypdfium2 as pdfium
        args.image = args.output / "input-page-1.png"
        with pdfium.PdfDocument(args.pdf) as document:
            document[0].render(scale=200 / 72).to_pil().convert("RGB").save(args.image)
    evidence = {"system": system_evidence(), "power_mode": args.power_mode,
        "inputs": {name: {"path": str(getattr(args, name)), "sha256": sha256(getattr(args, name))}
                   for name in ("model", "mmproj", "pdf", "image")},
        "rounds": [], "quality_acceptance": "pending manual per-page review; no exact-text equality requirement"}
    evidence["quality_rejections"] = []
    write_json(args.output / "summary.json", evidence)
    variants = [("old-sycl", args.baseline_python, "sycl"), ("aot-f16-sycl", args.candidate_python, "sycl"),
                ("vulkan", args.candidate_python, "vulkan")]
    common = ["--model", str(args.model), "--mmproj", str(args.mmproj), "--pdf", str(args.pdf),
              "--image", str(args.image), "--power-mode", args.power_mode, "--pages", args.pages,
              "--context", str(args.context), "--parallel", str(args.parallel), "--threads", str(args.threads),
              "--warm-tasks", str(args.warm_tasks)]
    for round_index in range(3):
        order = variants[round_index:] + variants[:round_index]
        records = {}
        for variant, python, backend in order:
            environment = os.environ.copy()
            environment["MINERU_LLAMA_CPP_BACKEND"] = backend
            # 默认计算精度比较必须去掉外部精度覆盖；驱动缓存策略保留并写入报告。
            for key in ("GGML_SYCL_DYNAMIC_PRECISION", "GGML_SYCL_DYNAMIC_REQUIRED_PRECISION"):
                environment.pop(key, None)
            records[variant] = {}
            for mode in ("engine", "mineru"):
                directory = args.output / f"round-{round_index + 1}" / variant / mode
                print(f"Round {round_index + 1}: {variant} / {mode}", flush=True)
                records[variant][mode] = run_process(python, [*common, "--worker", mode, "--variant", variant,
                    "--output", str(directory)], environment, directory, backend)
                if records[variant][mode]["system"].get("power_scheme") != evidence["system"].get("power_scheme"):
                    raise RuntimeError("Power scheme changed during comparison")
                identity = {key: records[variant][mode][key] for key in
                            ("dependency_versions", "python_version", "llama_cpp_commit")}
                if evidence.setdefault("common_environment", identity) != identity:
                    raise RuntimeError("Python/SDK dependencies or llama.cpp commit differ between environments")
        # 不把 JSON 字节不相等视为失败；逐页列出结构和内容差异供人工裁决。
        for variant in ("aot-f16-sycl", "vulkan"):
            base = args.output / f"round-{round_index + 1}"
            differences = {"engine": {}, "mineru": {}}
            comparisons = [("engine", "first-page.json"), ("mineru", "middle.json")] + [
                ("engine", f"warm-page-{index + 1}.json") for index in range(args.warm_tasks)]
            for mode, filename in comparisons:
                before = json.loads((base / "old-sycl" / mode / filename).read_text(encoding="utf-8"))
                after = json.loads((base / variant / mode / filename).read_text(encoding="utf-8"))
                # PDF 文档比较保留页面树，避免时间戳/生产者元数据形成无关差异。
                if mode == "mineru":
                    before, after = before["pages"], after["pages"]
                differences[mode][filename] = output_differences(before, after)
                if differences[mode][filename]["reject"]:
                    evidence["quality_rejections"].append({"round": round_index + 1, "variant": variant,
                        "mode": mode, "file": filename, "reasons": differences[mode][filename]["reject"]})
            for mode, difference in differences.items():
                write_json(base / variant / mode / "quality-differences.json", difference)
        evidence["rounds"].append({"order": [item[0] for item in order], "records": records})
        write_json(args.output / "summary.json", evidence)
    medians = {}
    for variant, _, _ in variants:
        records = [entry["records"][variant] for entry in evidence["rounds"]]
        medians[variant] = {key: statistics.median(entry["engine"][key] for entry in records)
                           for key in ("initialization_seconds", "first_task_seconds", "peak_rss_before_lifecycle_bytes")}
        medians[variant]["warm_task_seconds"] = statistics.median(statistics.median(entry["engine"]["warm_task_seconds"]) for entry in records)
        medians[variant]["full_mineru_seconds"] = statistics.median(entry["mineru"]["full_mineru_seconds"] for entry in records)
        medians[variant]["full_mineru_peak_rss_bytes"] = statistics.median(entry["mineru"]["peak_rss_bytes"] for entry in records)
    evidence["medians"] = medians
    if evidence["quality_rejections"]:
        evidence["quality_acceptance"] = "rejected by structural/content checks; inspect per-page differences"
    write_json(args.output / "summary.json", evidence)
    print(json.dumps(medians, indent=2))
    if evidence["quality_rejections"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
