"""构建固定 Level Zero SDK，使 SYCL 能按上游逻辑区分独显与集显。"""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import shutil
import subprocess


def main() -> None:
    """只安装开发头文件、loader 和许可；Linux loader 始终作为外部依赖。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", required=True, type=Path)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--stage", type=Path)
    args = parser.parse_args()
    source = args.work.resolve() / "source"
    if not source.exists():
        subprocess.run(["git", "clone", "--depth", "1", "--branch", "v1.33.1",
                        "https://github.com/oneapi-src/level-zero.git", str(source)], check=True)
    command = ["cmake", "-S", str(source), "-B", str(args.work.resolve() / "build"), "-G", "Ninja",
               "-DCMAKE_BUILD_TYPE=Release", "-DBUILD_L0_LOADER_TESTS=OFF", "-DCMAKE_INSTALL_LIBDIR=lib",
               f"-DCMAKE_INSTALL_PREFIX={args.prefix.resolve()}"]
    if os.name == "nt":
        command += ["-DCMAKE_C_COMPILER=cl", "-DCMAKE_CXX_COMPILER=cl"]
    subprocess.run(command, check=True)
    subprocess.run(["cmake", "--build", str(args.work.resolve() / "build"), "--target", "install",
                    "--parallel", os.environ.get("CMAKE_BUILD_PARALLEL_LEVEL", "2")], check=True)
    if args.stage:
        notices = args.stage / "licenses/level-zero"
        notices.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / "LICENSE", notices / "LICENSE")
        shutil.copytree(source / "LICENSES", notices / "LICENSES", dirs_exist_ok=True)


if __name__ == "__main__":
    main()
