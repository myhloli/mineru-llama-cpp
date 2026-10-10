"""验证无 SYCL 分发矩阵以及旧模块、运行库和 ELF 依赖的拒绝行为。"""
import importlib
from pathlib import Path
import struct
import zipfile

import pytest


@pytest.fixture
def wheel_tools(monkeypatch):
    """导入生产审计工具，使用临时 wheel 而不运行跨平台构建。"""
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "tools"))
    return importlib.import_module("verify_wheels"), importlib.import_module("repair_linux_wheel")


def make_wheel(directory, platform, backends, extra=None):
    """构造最小标签及 PE 头，专门验证分发规则而非原生代码执行。"""
    data = bytearray(128)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 64)
    data[64:68] = b"PE\0\0"
    struct.pack_into("<H", data, 68, 0xAA64 if platform == "win_arm64" else 0x8664)
    windows = platform.startswith("win_")
    module_suffix = ".dll" if windows else ".so"
    module_prefix = "" if windows else "lib"
    extension_suffix = ".pyd" if windows else ".abi3.so"
    contents = {f"mineru_llama_cpp/_mineru_llama_cpp{extension_suffix}": bytes(data)}
    contents.update({f"mineru_llama_cpp/bin/{module_prefix}ggml-{backend}{module_suffix}": bytes(data)
                     for backend in backends})
    contents.update(extra or {})
    wheel = directory / f"mineru_llama_cpp-0.1.2-cp310-abi3-{platform}.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, content in contents.items():
            archive.writestr(name, content)
    return wheel


def test_six_platform_matrix_without_sycl(wheel_tools, tmp_path):
    """六平台保持完整，Linux/Windows 仅要求 CPU 和 Vulkan。"""
    verifier, _ = wheel_tools
    wheels = [make_wheel(tmp_path, platform, backends) for platform, backends in verifier.PLATFORMS.items()]
    assert len(verifier.verify(wheels)) == 6
    assert verifier.PLATFORMS["manylinux_2_28_x86_64"] == {"cpu", "vulkan"}
    assert verifier.PLATFORMS["win_amd64"] == {"cpu", "vulkan"}


@pytest.mark.parametrize("legacy_file", [
    "bin/libggml-sycl.so", "bin/ggml-sycl.dll", "bin/SYCL-BUILD.JSON", "bin/sycl9.dll",
    "bin/mkl_sycl_blas.6.dll", "bin/dnnl.dll", "bin/tbb12.dll", "bin/ur_adapter_level_zero.dll",
    "bin/ze_loader.dll", "bin/libmmd.dll", "bin/libiomp5md.dll", "bin/umf.dll", "bin/tcm.dll",
    "bin/svml_dispmd.dll", "bin/libhwloc-15.dll", "lib/libsycl-abcdef.so.9",
    "bin/licenses/oneapi/LICENSE.txt", "bin/licenses/level-zero/LICENSE",
])
def test_wheel_rejects_legacy_sycl_files(wheel_tools, tmp_path, legacy_file):
    """分发入口拒绝旧缓存中的 MODULE、manifest、依赖闭包和许可。"""
    verifier, _ = wheel_tools
    platform = "manylinux_2_28_x86_64"
    wheel = make_wheel(tmp_path, platform, verifier.PLATFORMS[platform],
                       {f"mineru_llama_cpp/{legacy_file}": b"legacy"})
    with pytest.raises(ValueError, match="SYCL backend/runtime is not supported"):
        verifier.verify([wheel], require_all=False)


def test_linux_repair_rejects_sycl_before_extracting(wheel_tools, tmp_path):
    """旧 SYCL 模块必须在 auditwheel 解析或复制运行库之前被拒绝。"""
    verifier, repair = wheel_tools
    platform = "manylinux_2_28_x86_64"
    wheel = make_wheel(tmp_path, platform, verifier.PLATFORMS[platform],
                       {"mineru_llama_cpp/bin/libggml-sycl.so": b"legacy"})
    with pytest.raises(ValueError, match="SYCL"):
        repair.inspect_wheel(wheel)


@pytest.mark.parametrize("dependency", [
    "libsycl.so.9", "libmkl_sycl_blas.so.6", "libdnnl.so.3", "libtbb.so.12",
    "libze_loader.so.1", "libur_loader.so.0", "libirng.so", "libimf.so", "libintlc.so.5",
])
def test_linux_elf_rejects_sycl_dependencies(wheel_tools, dependency):
    """原包及修复后包共用的 ELF 检查拒绝 oneAPI 动态依赖。"""
    _, repair = wheel_tools
    with pytest.raises(RuntimeError, match="SYCL dependencies"):
        repair.validate_report({"mineru_llama_cpp/lib/libllama.so.0": {"needed": [dependency], "versions": []}})


def test_vulkan_dependencies_and_cpu_abi_remain_supported(wheel_tools):
    """Vulkan loader 仍为外部依赖，CPU 的 manylinux ABI 下限保持原规则。"""
    _, repair = wheel_tools
    repair.validate_report({"libggml-vulkan.so": {"needed": ["libvulkan.so.1"],
                                                  "versions": ["GLIBC_2.28", "GLIBCXX_3.4.24", "CXXABI_1.3.11"]}})
    assert repair.GPU_RUNTIME_PREFIXES == ("libvulkan.so",)
    with pytest.raises(RuntimeError, match="ABI floor"):
        repair.validate_report({"libllama.so.0": {"needed": [], "versions": ["GLIBC_2.29"]}})
