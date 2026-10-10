"""按 Windows 架构准备固定版本 Vulkan SDK。"""
from __future__ import annotations
import argparse
import hashlib
import os
import re
from pathlib import Path
import subprocess
import zipfile
from download_sdk import download
from windows_pe import pe_machine


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


def prepare_arm64_test_loader(prefix: Path, work: Path) -> None:
    """仅为无 GPU 驱动的 CI 安装检查准备官方 ARM64 loader，禁止合入 wheel。"""
    destination = prefix / "TestRuntime/vulkan-1.dll"
    if destination.is_file() and pe_machine(destination.read_bytes()) == 0xAA64:
        return
    archive_path = work / "vulkan-runtime-arm64.zip"
    download("https://sdk.lunarg.com/sdk/download/1.4.350.0/warm/VulkanRT-ARM64-1.4.350.0-Components.zip", archive_path)
    if hashlib.sha256(archive_path.read_bytes()).hexdigest() != "1845c336ca17180ca4e4f6f52542ff9998aa39a76bc6ed75961eef67aad1a811":
        raise RuntimeError("Unexpected checksum for ARM64 Vulkan test runtime")
    with zipfile.ZipFile(archive_path) as archive:
        name = "VulkanRT-ARM64-1.4.350.0-Components/vulkan-1.dll"
        data = archive.read(name)
    if pe_machine(data) != 0xAA64:
        raise RuntimeError("Vulkan test loader is not native ARM64")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    archive_path.unlink()


def main() -> None:
    """按架构安装固定 SDK；ARM64 额外准备仅供 CI 检查的 loader。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=("AMD64", "ARM64"), default="AMD64")
    args = parser.parse_args()
    arm64 = args.arch == "ARM64"
    machine = 0xAA64 if arm64 else 0x8664
    work = Path(os.environ["RUNNER_TEMP"]) / ("mineru-gpu-build-arm64" if arm64 else "mineru-gpu-build")
    work.mkdir(parents=True, exist_ok=True)
    vulkan = Path(os.environ["VULKAN_SDK"])
    if vulkan_ready(vulkan, machine):
        if arm64:
            prepare_arm64_test_loader(vulkan, work)
        print("Using cached Vulkan SDK")
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
        prepare_arm64_test_loader(vulkan, work)
    print(f"Native {args.arch} Vulkan SDK ready")


if __name__ == "__main__":
    main()
