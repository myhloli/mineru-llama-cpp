"""从 NVIDIA 固定版本清单安装最小 CUDA 构建组件，不安装显卡驱动。"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import os
import tarfile
import tempfile
import urllib.request
import zipfile

BASE = "https://developer.download.nvidia.com/compute/cuda/redist/"


def download(url: str, path: Path, checksum: str | None = None) -> None:
    """下载组件并校验官方 SHA256；成功缓存可重复使用。"""
    if path.exists() and checksum:
        with path.open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() == checksum:
                return
    # SDK CDN 拒绝默认 Python User-Agent；与既有 CI 一样使用系统 curl 下载。
    subprocess.run(["curl.exe" if os.name == "nt" else "curl", "--fail", "--location", "--retry", "3",
                    "--output", str(path), url], check=True)
    if checksum:
        with path.open("rb") as source:
            actual = hashlib.file_digest(source, "sha256").hexdigest()
        if actual != checksum:
            path.unlink()
            raise RuntimeError(f"SHA256 mismatch: {url}")


def install(version: str, destination: Path, target: str) -> None:
    """合并固定清单中的 nvcc、头文件、链接库及设备代码审计工具。"""
    with urllib.request.urlopen(BASE + f"redistrib_{version}.json", timeout=120) as response:
        metadata = json.load(response)
    destination.mkdir(parents=True, exist_ok=True)
    components = ["cuda_nvcc", "cuda_cudart", "libcublas", "cuda_cuobjdump"]
    components += [name for name in ("cuda_cccl", "cccl", "cuda_crt", "libnvvm") if name in metadata]
    for name in components:
        artifact = metadata[name][target]
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            archive = work / Path(artifact["relative_path"]).name
            print(f"Installing CUDA {version}: {name} for {target}", flush=True)
            download(BASE + artifact["relative_path"], archive, artifact["sha256"])
            extracted = work / "extracted"
            if archive.suffix == ".zip":
                with zipfile.ZipFile(archive) as source:
                    source.extractall(extracted)
            else:
                with tarfile.open(archive) as source:
                    source.extractall(extracted, filter="data")
            roots = list(extracted.iterdir())
            if len(roots) != 1 or not roots[0].is_dir():
                raise RuntimeError(f"Unexpected CUDA archive layout: {archive.name}")
            shutil.copytree(roots[0], destination, dirs_exist_ok=True, symlinks=True)
    if target.startswith("linux") and not (destination / "lib64").exists():
        (destination / "lib64").symlink_to("lib", target_is_directory=True)
    (destination / "mineru-cuda-version.json").write_text(json.dumps({"version": version, "target": target}))


def main() -> None:
    """选择主机原生平台；ARM Linux 必须使用 SBSA 而非 Jetson 组件。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    target = "windows-x86_64" if platform.system() == "Windows" else "linux-sbsa" if platform.machine() == "aarch64" else "linux-x86_64"
    install(args.version, args.destination.resolve(), target)


if __name__ == "__main__":
    main()
