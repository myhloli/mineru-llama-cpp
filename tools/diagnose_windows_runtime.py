"""在未安装 oneAPI SDK 的 Windows CI 中逐个加载 DLL，定位运行库缺失。"""
from __future__ import annotations
import argparse
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys


def main() -> None:
    """安装已有候选 wheel，记录每个 DLL 的加载结果与 PE 导入表。"""
    import pefile
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    wheels = list(args.directory.glob("*win_amd64.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"Expected one Windows AMD64 wheel, got {wheels}")
    subprocess.run([sys.executable, "-m", "pip", "install", str(wheels[0])], check=True)
    # 保持与安装测试相同的搜索路径，禁止构建工具链掩盖缺失 DLL。
    os.environ.pop("ONEAPI_ROOT", None)
    os.environ["PATH"] = os.pathsep.join(p for p in os.environ.get("PATH", "").split(os.pathsep)
                                         if "oneapi" not in p.lower())
    import mineru_llama_cpp
    package = Path(mineru_llama_cpp.__file__).parent
    report = {}
    handles = []
    for path in sorted((package / "bin").glob("*.dll")):
        with pefile.PE(str(path)) as binary:
            imports = getattr(binary, "DIRECTORY_ENTRY_IMPORT", []) + getattr(binary, "DIRECTORY_ENTRY_DELAY_IMPORT", [])
            needed = sorted({item.dll.decode() for item in imports})
        try:
            handles.append(ctypes.WinDLL(str(path), winmode=0x100 | 0x1000))
            error = None
        except OSError as exception:
            error = str(exception)
        report[path.name] = {"needed": needed, "load_error": error}
        print(json.dumps({path.name: report[path.name]}), flush=True)
    (args.directory / "runtime-diagnostics.json").write_text(json.dumps(report, indent=2))
    if any(item["load_error"] for item in report.values()):
        raise RuntimeError("Packaged DLL loading failed; see runtime-diagnostics.json")


if __name__ == "__main__":
    main()
