"""审计 Linux ELF 下限，仅将非 GPU 运行库依赖修复进 wheel。"""
from __future__ import annotations
import argparse
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

GPU_RUNTIME_PREFIXES = ("libcuda.so", "libcudart.so", "libcublas", "libsycl", "libmkl", "libdnnl", "libtbb",
                        "libiomp", "libur_", "libumf", "libtcm", "libhwloc", "libsvml", "libimf", "libintlc",
                        "libOpenCL.so", "libvulkan.so")


def inspect_elf(path: Path) -> tuple[set[str], set[str]]:
    """读取 DT_NEEDED 和实际所需符号版本，忽略共享库提供的版本定义。"""
    dynamic = subprocess.check_output(["readelf", "-d", str(path)], text=True)
    needed = set(re.findall(r"Shared library: \[([^]]+)\]", dynamic))
    symbols = subprocess.check_output(["readelf", "--dyn-syms", "--wide", str(path)], text=True)
    versions = set()
    for line in symbols.splitlines():
        if " UND " in line:
            versions.update(re.findall(r"@((?:GLIBC|GLIBCXX|CXXABI)_[0-9.]+)", line))
    return needed, versions


def repair(wheel: Path, destination: Path) -> None:
    """检查包内每个 ELF 的 glibc 下限，并显式排除外部 GPU 运行库。"""
    external = set()
    report = {}
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(root)
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            with path.open("rb") as source:
                if source.read(4) != b"\x7fELF":
                    continue
            needed, versions = inspect_elf(path)
            glibc = [tuple(map(int, value.removeprefix("GLIBC_").split("."))) for value in versions if value.startswith("GLIBC_")]
            if any(version > (2, 28) for version in glibc):
                raise RuntimeError(f"{path.name} needs glibc newer than 2.28: {versions}")
            external.update(name for name in needed if name.startswith(GPU_RUNTIME_PREFIXES))
            report[str(path.relative_to(root))] = {"needed": sorted(needed), "versions": sorted(versions)}
    command = ["auditwheel", "repair", "--plat", "manylinux_2_28_" + ("aarch64" if "aarch64" in wheel.name else "x86_64"),
               "--disable-isa-ext-check", "-w", str(destination)]
    for name in sorted(external):
        command.extend(["--exclude", name])
    command.append(str(wheel))
    subprocess.run(command, check=True)
    # 报告写在 wheelhouse 之外，发布目录只收集真正的 wheel。
    import json
    audit_dir = destination.parent / "wheel-audits"
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / (wheel.stem + ".json")).write_text(json.dumps({"external_gpu_runtime": sorted(external), "elf": report}, indent=2))


def main() -> None:
    """处理 cibuildwheel 的 repair 阶段参数。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    repair(args.wheel, args.destination)


if __name__ == "__main__":
    main()
