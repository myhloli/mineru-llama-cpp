"""下载固定版本的 Vulkan/oneAPI 构建组件。"""
from __future__ import annotations
import os
from pathlib import Path
import subprocess


def download(url: str, path: Path) -> None:
    """使用系统 curl 下载官方 SDK，兼容拒绝默认 Python User-Agent 的 CDN。"""
    subprocess.run(["curl.exe" if os.name == "nt" else "curl", "--fail", "--location", "--retry", "3",
                    "--output", str(path), url], check=True)
