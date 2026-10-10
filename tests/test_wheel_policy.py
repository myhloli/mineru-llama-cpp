"""验证六平台分发矩阵、稳定 ABI、Windows 架构以及 Linux 系统 ABI 下限。"""
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


def test_six_platform_matrix(wheel_tools, tmp_path):
    """六平台保持完整，Linux/Windows 仅要求 CPU 和 Vulkan。"""
    verifier, _ = wheel_tools
    wheels = [make_wheel(tmp_path, platform, backends) for platform, backends in verifier.PLATFORMS.items()]
    assert len(verifier.verify(wheels)) == 6
    assert verifier.PLATFORMS["manylinux_2_28_x86_64"] == {"cpu", "vulkan"}
    assert verifier.PLATFORMS["win_amd64"] == {"cpu", "vulkan"}


def test_wheel_set_requires_all_platforms(wheel_tools, tmp_path):
    """自动发布仍拒绝缺少平台的集合，手动发布可以校验明确选择的子集。"""
    verifier, _ = wheel_tools
    platform = "macosx_14_0_x86_64"
    wheel = make_wheel(tmp_path, platform, verifier.PLATFORMS[platform])
    with pytest.raises(ValueError, match="Expected six wheels"):
        verifier.verify([wheel])
    assert len(verifier.verify([wheel], require_all=False)) == 1


def test_wheel_requires_stable_abi_tag(wheel_tools, tmp_path):
    """普通 CPython 扩展标签仍不能混入 cp310-abi3 发布集合。"""
    verifier, _ = wheel_tools
    platform = "macosx_14_0_x86_64"
    wheel = make_wheel(tmp_path, platform, verifier.PLATFORMS[platform])
    wheel = wheel.rename(wheel.with_name(wheel.name.replace("cp310-abi3", "cp312-cp312")))
    with pytest.raises(ValueError, match="Unexpected package or ABI tag"):
        verifier.verify([wheel], require_all=False)


def test_wheel_requires_configured_backend_modules(wheel_tools, tmp_path):
    """平台对应的必需模块仍需完整，例如 Linux 不能漏掉配置中的 Vulkan 模块。"""
    verifier, _ = wheel_tools
    wheel = make_wheel(tmp_path, "manylinux_2_28_aarch64", {"cpu"})
    with pytest.raises(ValueError, match="Missing vulkan MODULE"):
        verifier.verify([wheel], require_all=False)


def test_wheel_rejects_wrong_windows_architecture(wheel_tools, tmp_path):
    """ARM64 wheel 中的 AMD64 原生产物仍必须被架构检查拒绝。"""
    verifier, _ = wheel_tools
    platform = "win_arm64"
    wheel = make_wheel(tmp_path, platform, verifier.PLATFORMS[platform])
    with zipfile.ZipFile(wheel) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
    extension = "mineru_llama_cpp/_mineru_llama_cpp.pyd"
    data = bytearray(contents[extension])
    struct.pack_into("<H", data, 68, 0x8664)
    contents[extension] = bytes(data)
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, content in contents.items():
            archive.writestr(name, content)
    with pytest.raises(ValueError, match="Wrong PE architecture"):
        verifier.verify([wheel], require_all=False)


def test_vulkan_dependencies_and_cpu_abi_remain_supported(wheel_tools):
    """Vulkan loader 仍为外部依赖，CPU 的 manylinux ABI 下限保持原规则。"""
    _, repair = wheel_tools
    repair.validate_report({"libggml-vulkan.so": {"needed": ["libvulkan.so.1"],
                                                  "versions": ["GLIBC_2.28", "GLIBCXX_3.4.24", "CXXABI_1.3.11"]}})
    assert repair.GPU_RUNTIME_PREFIXES == ("libvulkan.so",)
    with pytest.raises(RuntimeError, match="ABI floor"):
        repair.validate_report({"libllama.so.0": {"needed": [], "versions": ["GLIBC_2.29"]}})
