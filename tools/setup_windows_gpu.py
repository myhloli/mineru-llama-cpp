"""安装固定 GPU 构建工具，分别构建 CUDA 与内置运行库的 SYCL 后端。"""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import sys
from install_cuda import download, install

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
    installer = work / "vulkan_sdk.exe"
    download("https://sdk.lunarg.com/sdk/download/1.4.350.0/windows/vulkan_sdk.exe", installer)
    run_installer([str(installer), "-t", str(vulkan), "--accept-licenses", "--default-answer", "--confirm-command", "install"])
    installer.unlink()
    cuda = Path(os.environ["CUDA_PATH"])
    install("12.8.1", cuda, "windows-x86_64")
    subprocess.run([sys.executable, str(ROOT / "tools/build_gpu_backends.py"), "--backend", "cuda",
                    "--stage", str(stage), "--work", str(work / "cuda")], check=True)
    installer = work / "oneapi.exe"
    download("https://registrationcenter-download.intel.com/akdlm/IRC_NAS/0cb67a0d-67f6-410b-868b-f4a0a17ff0cf/intel-oneapi-toolkit-2026.1.1.32_offline.exe", installer)
    extracted = work / "oneapi-extracted"
    run_installer([str(installer), "-s", "-x", "-f", str(extracted)])
    components = "intel.oneapi.win.cpp-dpcpp-common:intel.oneapi.win.mkl.devel:intel.oneapi.win.dnnl:intel.oneapi.win.tbb.devel"
    run_installer([str(extracted / "bootstrapper.exe"), "-s", "--action", "install", "--eula=accept",
                   "--components=" + components, "-p=NEED_VS2022_INTEGRATION=0"])
    installer.unlink()
    build_command = ["cmd.exe", "/d", "/c", f'call "{os.environ["ONEAPI_ROOT"]}\\setvars.bat" intel64 --force && '
                      f'"{sys.executable}" "{ROOT / "tools/build_gpu_backends.py"}" --backend sycl --stage "{stage}" --work "{work / "sycl"}"']
    subprocess.run(build_command, check=True)


if __name__ == "__main__":
    main()
