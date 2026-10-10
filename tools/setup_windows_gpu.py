"""安装固定 Vulkan/oneAPI 构建工具，构建内置运行库的 SYCL 后端。"""
from __future__ import annotations
import argparse
import os
import re
from pathlib import Path
import subprocess
import sys
from download_sdk import download
from build_gpu_backends import stage_matches
from windows_pe import pe_machine

ROOT = Path(__file__).resolve().parents[1]


def vulkan_ready(prefix: Path, machine: int | None = None) -> bool:
    """仅复用固定版本 Vulkan SDK，旧版本缓存不能改变发布组件版本。"""
    header = prefix / "Include/vulkan/vulkan_core.h"
    compiler = prefix / "Bin/glslc.exe"
    return (compiler.is_file() and (prefix / "Lib/vulkan-1.lib").is_file() and header.is_file()
            and re.search(r"#define\s+VK_HEADER_VERSION\s+350\b", header.read_text()) is not None
            and (machine is None or pe_machine(compiler.read_bytes()) == machine))


def run_installer(command: list[str]) -> None:
    """接受 Windows 安装器成功和仅提示重启的退出码。"""
    result = subprocess.run(command)
    if result.returncode not in (0, 3010):
        raise subprocess.CalledProcessError(result.returncode, command)


def main() -> None:
    """按架构安装固定 SDK；ARM64 仅构建 Vulkan，AMD64 额外准备 SYCL。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=("AMD64", "ARM64"), default="AMD64")
    args = parser.parse_args()
    arm64 = args.arch == "ARM64"
    machine = 0xAA64 if arm64 else 0x8664
    work = Path(os.environ["RUNNER_TEMP"]) / ("mineru-gpu-build-arm64" if arm64 else "mineru-gpu-build")
    work.mkdir(parents=True, exist_ok=True)
    vulkan = Path(os.environ["VULKAN_SDK"])
    stage = None if arm64 else Path(os.environ["MINERU_EXTRA_BACKENDS_DIR"])
    if vulkan_ready(vulkan, machine) and (arm64 or stage_matches(stage, "sycl")):
        print("Using cached Vulkan SDK and independently built GPU MODULEs")
        return
    if not vulkan_ready(vulkan, machine):
        installer = work / "vulkan_sdk.exe"
        sdk_platform = "warm" if arm64 else "windows"
        download(f"https://sdk.lunarg.com/sdk/download/1.4.350.0/{sdk_platform}/vulkan_sdk.exe", installer)
        run_installer([str(installer), "-t", str(vulkan), "--accept-licenses", "--default-answer", "--confirm-command", "install"])
        installer.unlink()
    if not vulkan_ready(vulkan, machine):
        raise RuntimeError(f"Vulkan SDK 1.4.350.0 is missing or has the wrong architecture for {args.arch}")
    if arm64:
        print("Native ARM64 Vulkan SDK ready; no oneAPI runtime is required")
        return
    installer = work / "oneapi.exe"
    download("https://registrationcenter-download.intel.com/akdlm/IRC_NAS/0cb67a0d-67f6-410b-868b-f4a0a17ff0cf/intel-oneapi-toolkit-2026.1.1.32_offline.exe", installer)
    extracted = work / "oneapi-extracted"
    run_installer([str(installer), "-s", "-x", "-f", str(extracted)])
    components = "intel.oneapi.win.cpp-dpcpp-common:intel.oneapi.win.mkl.devel:intel.oneapi.win.dnnl:intel.oneapi.win.tbb.devel"
    run_installer([str(extracted / "bootstrapper.exe"), "-s", "--action", "install", "--eula=accept",
                   "--components=" + components, "-p=NEED_VS2022_INTEGRATION=0"])
    installer.unlink()
    level_zero = work / "level-zero-sdk"
    subprocess.run([sys.executable, str(ROOT / "tools/build_level_zero.py"), "--prefix", str(level_zero),
                    "--work", str(work / "level-zero"), "--stage", str(stage)], check=True)
    os.environ["LEVEL_ZERO_V1_SDK_PATH"] = str(level_zero)
    setvars = Path(os.environ["ONEAPI_ROOT"]) / "setvars.bat"
    if not setvars.is_file():
        raise FileNotFoundError(f"oneAPI environment script missing: {setvars}")
    # cmd.exe 不使用 Windows C argv 的反斜杠引号转义；通过脚本避免 list2cmdline 二次转义。
    batch = work / "build-sycl.cmd"
    batch.write_text(f'@echo off\ncall "{setvars}" intel64 --force\nif errorlevel 1 exit /b %errorlevel%\n'
                     f'"{sys.executable}" "{ROOT / "tools/build_gpu_backends.py"}" --backend sycl --stage "{stage}" --work "{work / "sycl"}"\n'
                     'exit /b %errorlevel%\n', newline="\r\n")
    subprocess.run(["cmd.exe", "/d", "/c", str(batch)], check=True)


if __name__ == "__main__":
    main()
