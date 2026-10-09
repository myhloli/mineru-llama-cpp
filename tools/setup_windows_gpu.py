"""安装固定 Vulkan/oneAPI 构建工具，构建内置运行库的 SYCL 后端。"""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import sys
from download_sdk import download

ROOT = Path(__file__).resolve().parents[1]


def run_installer(command: list[str]) -> None:
    """接受 Windows 安装器成功和仅提示重启的退出码。"""
    result = subprocess.run(command)
    if result.returncode not in (0, 3010):
        raise subprocess.CalledProcessError(result.returncode, command)


def main() -> None:
    """为 AMD64 发布构建准备 SDK 和独立的后端暂存目录。"""
    work = Path(os.environ["RUNNER_TEMP"]) / "mineru-gpu-build"
    stage = Path(os.environ["MINERU_EXTRA_BACKENDS_DIR"])
    work.mkdir(parents=True, exist_ok=True)
    vulkan = Path(os.environ["VULKAN_SDK"])
    if (vulkan / "Bin/glslc.exe").exists() and (stage / "sycl-build.json").exists():
        print("Using cached Vulkan SDK and independently built GPU MODULEs")
        return
    installer = work / "vulkan_sdk.exe"
    download("https://sdk.lunarg.com/sdk/download/1.4.350.0/windows/vulkan_sdk.exe", installer)
    run_installer([str(installer), "-t", str(vulkan), "--accept-licenses", "--default-answer", "--confirm-command", "install"])
    installer.unlink()
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
