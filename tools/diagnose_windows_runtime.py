"""在未安装 oneAPI SDK 的 Windows CI 中逐个加载 DLL，定位运行库缺失。"""
from __future__ import annotations
import argparse
import ctypes
import json
import importlib.metadata
import os
from pathlib import Path
import subprocess
import shutil
import sys


def main() -> None:
    """安装已有候选 wheel，记录每个 DLL 的加载结果与 PE 导入表。"""
    import pefile
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--use-current-bootstrap", action="store_true",
                        help="用当前 __init__.py 复核加载修复；默认检查原始 wheel")
    args = parser.parse_args()
    wheels = list(args.directory.glob("*win_amd64.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"Expected one Windows AMD64 wheel, got {wheels}")
    subprocess.run([sys.executable, "-m", "pip", "install", str(wheels[0])], check=True)
    if args.use_current_bootstrap:
        # 修复试验必须显式标记，不能把覆盖启动代码后的结果算作原始 wheel 验证。
        installed = importlib.metadata.distribution("mineru-llama-cpp").locate_file("mineru_llama_cpp/__init__.py")
        shutil.copy2(Path(__file__).resolve().parents[1] / "src/mineru_llama_cpp/__init__.py", installed)
        print("Diagnostic override: current source __init__.py replaces the installed wheel bootstrap", flush=True)
    # 保持与安装测试相同的搜索路径，禁止构建工具链掩盖缺失 DLL。
    os.environ.pop("ONEAPI_ROOT", None)
    os.environ["PATH"] = os.pathsep.join(p for p in os.environ.get("PATH", "").split(os.pathsep)
                                         if "oneapi" not in p.lower())
    import mineru_llama_cpp
    package = Path(mineru_llama_cpp.__file__).parent
    report = {}
    handles = []
    all_needed = set()
    for path in sorted((package / "bin").glob("*.dll")):
        with pefile.PE(str(path)) as binary:
            imports = getattr(binary, "DIRECTORY_ENTRY_IMPORT", []) + getattr(binary, "DIRECTORY_ENTRY_DELAY_IMPORT", [])
            needed = sorted({item.dll.decode() for item in imports})
            all_needed.update(needed)
        try:
            handles.append(ctypes.WinDLL(str(path), winmode=0x100 | 0x1000))
            error = None
        except OSError as exception:
            error = str(exception)
        report[path.name] = {"needed": needed, "load_error": error}
        print(json.dumps({path.name: report[path.name]}), flush=True)
    # 第二遍复核加载顺序；不能把首次失败被后续加载掩盖的情况算作原始通过。
    for name, result in report.items():
        if result["load_error"]:
            try:
                handles.append(ctypes.WinDLL(str(package / "bin" / name), winmode=0x100 | 0x1000))
                result["retry_error"] = None
            except OSError as exception:
                result["retry_error"] = str(exception)
            print(json.dumps({"retry": name, "error": result["retry_error"]}), flush=True)
    # 单独检查系统运行库，避免误把 VC++ 运行库当作始终存在的 Windows 组件。
    packaged = {path.name.lower() for path in (package / "bin").glob("*.dll")}
    for name in sorted(all_needed):
        if name.lower() in packaged or name.lower().startswith(("api-ms-", "ext-ms-")):
            continue
        try:
            handles.append(ctypes.WinDLL(name, winmode=0x1000))
            error = None
        except OSError as exception:
            error = str(exception)
        print(json.dumps({"system_dependency": name, "error": error}), flush=True)
    (args.directory / "runtime-diagnostics.json").write_text(json.dumps(report, indent=2))
    # Vulkan loader 由显卡驱动提供，无显卡驱动的诊断机器可跳过该可选 MODULE。
    if any(item["load_error"] for name, item in report.items() if name != "ggml-vulkan.dll"):
        raise RuntimeError("Packaged DLL loading failed; see runtime-diagnostics.json")


if __name__ == "__main__":
    main()
