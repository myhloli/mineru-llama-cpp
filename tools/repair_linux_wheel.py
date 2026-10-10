"""审计 Linux ELF 下限，修复 wheel 时保留 Vulkan loader 作为外部依赖。"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import shutil
import tempfile
import zipfile

GPU_RUNTIME_PREFIXES = ("libvulkan.so",)


def inspect_elf(path: Path) -> tuple[set[str], set[str]]:
    """读取 DT_NEEDED 和实际所需符号版本，忽略共享库提供的版本定义。"""
    dynamic = subprocess.check_output(["readelf", "-d", str(path)], text=True)
    needed = set(re.findall(r"Shared library: \[([^]]+)\]", dynamic))
    symbols = subprocess.check_output(["readelf", "--dyn-syms", "--wide", str(path)], text=True)
    versions = set()
    for line in symbols.splitlines():
        if " UND " in line:
            versions.update(re.findall(r"@([A-Za-z][A-Za-z0-9_]*_[0-9.]+)", line))
    # 无对应 UND 符号的 ABI 标记也属于依赖，例如 GLIBC_ABI_DT_RELR。
    information = subprocess.check_output(["readelf", "--version-info", "--wide", str(path)], text=True)
    if "Version needs section" in information:
        versions.update(re.findall(r"Name: ([A-Za-z][A-Za-z0-9_.]+)", information.split("Version needs section", 1)[1]))
    return needed, versions


def inspect_wheel(wheel: Path) -> tuple[dict, set[str]]:
    """审计每个 ELF 的动态依赖和系统 C/C++ ABI 下限，包括修复时加入的库。"""
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
            external.update(name for name in needed if name.startswith(GPU_RUNTIME_PREFIXES))
            report[str(path.relative_to(root))] = {"needed": sorted(needed), "versions": sorted(versions)}
    return report, external


def validate_report(report: dict) -> None:
    """对全部 ELF 执行 manylinux_2_28 的系统 C/C++ 符号版本下限检查。"""
    for path, info in report.items():
        for prefix, maximum in (("GLIBC_", (2, 28)), ("GLIBCXX_", (3, 4, 24)), ("CXXABI_", (1, 3, 11))):
            required = [tuple(map(int, value.removeprefix(prefix).split("."))) for value in info["versions"] if value.startswith(prefix) and value.removeprefix(prefix)[0].isdigit()]
            if any(version > maximum for version in required):
                raise RuntimeError(f"{path} exceeds manylinux_2_28 {prefix} ABI floor: {info['versions']}")


def repair(wheel: Path, destination: Path) -> None:
    """先审计原包，排除外部 GPU 库后修复，再审计最终发布包的全部 ELF。"""
    before, external = inspect_wheel(wheel)
    # cibuildwheel 会复制 /project；CI 证据须写回 /host 挂载的真实工作区。
    workspace = os.environ.get("GITHUB_WORKSPACE")
    root = Path("/host") / workspace.lstrip("/") if workspace and Path("/host").is_dir() else Path(__file__).resolve().parents[1]
    diagnostics = root / "build/ci-wheel-audits"
    diagnostics.mkdir(parents=True, exist_ok=True)
    (diagnostics / (wheel.stem + ".json")).write_text(json.dumps({"external_gpu_runtime": sorted(external), "elf": before}, indent=2))
    command = ["auditwheel", "repair", "--plat", "manylinux_2_28_" + ("aarch64" if "aarch64" in wheel.name else "x86_64"),
               "--disable-isa-ext-check", "-w", str(destination)]
    for name in sorted(external):
        command.extend(["--exclude", name])
    command.append(str(wheel))
    try:
        validate_report(before)
        subprocess.run(command, check=True)
    except (subprocess.CalledProcessError, RuntimeError):
        # 失败候选单独保存，不参与六平台发布集合；保留证据供完整 ABI 诊断。
        shutil.copy2(wheel, diagnostics / wheel.name)
        subprocess.run(["auditwheel", "show", str(wheel)], check=False)
        print(json.dumps(before, indent=2), flush=True)
        raise
    prefix = wheel.name.rsplit("-", 1)[0] + "-"
    repaired = [path for path in destination.glob("*.whl") if path.name.startswith(prefix)]
    if len(repaired) != 1:
        raise RuntimeError(f"Expected one repaired wheel, got {repaired}")
    report, _ = inspect_wheel(repaired[0])
    validate_report(report)
    # 报告写在 wheelhouse 之外，发布目录只收集真正的 wheel。
    audit_dir = destination.parent / "wheel-audits"
    audit_dir.mkdir(parents=True, exist_ok=True)
    final_report = json.dumps({"external_gpu_runtime": sorted(external), "elf": report}, indent=2)
    (audit_dir / (wheel.stem + ".json")).write_text(final_report)
    # 保留修复后全部 ELF 的报告，包括 auditwheel 新加入的运行库，便于交付复核。
    (diagnostics / (repaired[0].stem + ".post.json")).write_text(final_report)


def main() -> None:
    """处理 cibuildwheel 的 repair 阶段参数。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    repair(args.wheel, args.destination)


if __name__ == "__main__":
    main()
