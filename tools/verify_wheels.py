"""审计六平台 abi3 产物及 GPU 分发边界，发布前拒绝缺包和 ABI 混入。"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import zipfile
from packaging.utils import parse_wheel_filename
from windows_pe import pe_machine

PLATFORMS = {
    "manylinux_2_28_x86_64": {"cpu", "vulkan", "sycl"},
    "manylinux_2_28_aarch64": {"cpu", "vulkan"},
    "win_amd64": {"cpu", "vulkan", "sycl"},
    "win_arm64": {"cpu", "vulkan"},
    "macosx_14_0_arm64": {"cpu", "metal"},
    "macosx_14_0_x86_64": {"cpu"},
}
EXTERNAL_LINUX_PREFIXES = ("libcuda", "libcublas", "libsycl", "libmkl", "libdnnl", "libtbb", "libur_", "libiomp", "libumf", "libtcm", "libze_loader",
                           "libhwloc", "libsvml", "libimf", "libintlc", "libirng", "libOpenCL.so", "libvulkan.so")


def verify(wheels: list[Path], require_all: bool = True) -> list[dict]:
    """检查标签、扩展数量、后端及运行库规则；允许手动发布已验证的子集。"""
    seen = set()
    versions = set()
    reports = []
    commits = set()
    for wheel in wheels:
        name, version, _, tags = parse_wheel_filename(wheel.name)
        versions.add(version)
        if name != "mineru-llama-cpp" or any(tag.interpreter != "cp310" or tag.abi != "abi3" for tag in tags):
            raise ValueError(f"Unexpected package or ABI tag: {wheel.name}")
        platforms = {tag.platform for tag in tags}
        matched = platforms.intersection(PLATFORMS)
        if len(matched) != 1 or any("musllinux" in tag for tag in platforms):
            raise ValueError(f"Unsupported platform: {wheel.name}")
        platform = matched.pop()
        if platform in seen:
            raise ValueError(f"Duplicate platform wheel: {platform}")
        seen.add(platform)
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            machines = {}
            if platform.startswith("win_"):
                expected_machine = 0xAA64 if platform == "win_arm64" else 0x8664
                for item in names:
                    if item.endswith((".dll", ".exe", ".pyd")):
                        machine = pe_machine(archive.read(item))
                        if machine != expected_machine:
                            raise ValueError(f"Wrong PE architecture 0x{machine:04x}: {item} in {wheel.name}")
                        machines[item] = f"0x{machine:04x}"
            extensions = [item for item in names if Path(item).name.startswith("_mineru_llama_cpp") and item.endswith((".so", ".pyd"))]
            if len(extensions) != 1 or (platform.startswith("win_") and Path(extensions[0]).name not in {"_mineru_llama_cpp.pyd", "_mineru_llama_cpp.abi3.pyd"}) or (not platform.startswith("win_") and not extensions[0].endswith(".abi3.so")):
                raise ValueError(f"Expected exactly one stable-ABI extension: {extensions}")
            for backend in PLATFORMS[platform]:
                if not any(item.startswith("mineru_llama_cpp/bin/") and Path(item).name.startswith(("ggml-" + backend, "libggml-" + backend)) and item.endswith((".so", ".dll")) for item in names):
                    raise ValueError(f"Missing {backend} MODULE: {wheel.name}")
            for backend in ("sycl",):
                if backend not in PLATFORMS[platform]:
                    continue
                manifest = json.loads(archive.read(f"mineru_llama_cpp/bin/{backend}-build.json"))
                commits.add(manifest["llama_cpp_commit"])
                if manifest["backend"] != backend or f"mineru_llama_cpp/bin/{manifest['module']}" not in names:
                    raise ValueError(f"Invalid {backend} manifest: {wheel.name}")
                if platform == "win_amd64" and backend == "sycl":
                    if not manifest["bundled_runtime"] or not any(item.startswith("mineru_llama_cpp/bin/licenses/oneapi/") for item in names):
                        raise ValueError("Windows SYCL runtime and license notices are required")
                    if "ze_loader.dll" not in manifest["bundled_runtime"] or not any(item.startswith("mineru_llama_cpp/bin/licenses/level-zero/") for item in names):
                        raise ValueError("Windows SYCL requires its Level Zero loader and license notices")
                    for runtime in manifest["bundled_runtime"]:
                        if f"mineru_llama_cpp/bin/{runtime}" not in names:
                            raise ValueError(f"Missing bundled runtime: {runtime}")
            for item in names:
                basename = Path(item).name.lower()
                if "ggml-cuda" in basename or basename == "cuda-build.json" or basename.startswith(("nvcuda", "cudart", "cublas", "nvrtc", "nvjitlink", "libcuda", "libcublas", "libnvrtc", "libnvjitlink")):
                    raise ValueError(f"CUDA backend/runtime is not supported: {item}")
                if platform.startswith("manylinux") and basename.startswith(EXTERNAL_LINUX_PREFIXES):
                    raise ValueError(f"Linux GPU runtime must remain external: {item}")
        reports.append({"wheel": wheel.name, "platform": platform, "required_backends": sorted(PLATFORMS[platform]), "extension": extensions[0], "pe_machines": machines})
    if not wheels or len(versions) != 1 or len(commits) > 1:
        raise ValueError("Wheel set is empty or combines different versions/llama.cpp commits")
    if require_all and seen != set(PLATFORMS):
        raise ValueError(f"Expected six wheels; missing {set(PLATFORMS) - seen}")
    return reports


def main() -> None:
    """校验整个发布目录，或手动发布时明确选择的子集。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--allow-subset", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify(sorted(args.directory.glob("*.whl")), not args.allow_subset), indent=2))


if __name__ == "__main__":
    main()
