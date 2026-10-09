"""用真实生产策略验证混合显卡、可选运行库缺失及集显选择。"""
import importlib.util
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def backend_selector(tmp_path_factory):
    """在临时目录编译 Limited API 测试扩展，复用生产策略头文件。"""
    from setuptools import Distribution, Extension
    from setuptools.command.build_ext import build_ext

    root = Path(__file__).resolve().parents[1]
    output = tmp_path_factory.mktemp("backend_policy")
    extension = Extension("_backend_policy_test", [str(root / "tests/native/backend_policy_binding.cpp")],
                          include_dirs=[str(root / "src/cpp")], language="c++", py_limited_api=True,
                          define_macros=[("Py_LIMITED_API", "0x030A0000")],
                          extra_compile_args=["/std:c++17", "/utf-8"] if sys.platform == "win32" else ["-std=c++17"])
    command = build_ext(Distribution({"ext_modules": [extension]}))
    command.build_lib, command.build_temp = str(output), str(output / "temp")
    command.ensure_finalized()
    command.run()
    spec = importlib.util.spec_from_file_location("_backend_policy_test", command.get_ext_fullpath("_backend_policy_test"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.select


@pytest.mark.parametrize("devices,expected", [
    ([("sycl", False, 0), ("metal", True, 1)], [0]),
    ([("vulkan", False, 0), ("sycl", False, 1)], [1]),
    ([("vulkan", False, 0), ("sycl", True, 1)], [0]),
    ([("vulkan", True, 0), ("sycl", True, 1)], [1]),
    ([("sycl", False, 0), ("sycl", False, 1), ("vulkan", False, 2)], [0, 1]),
    ([("sycl", False, 0), ("sycl", True, 1)], [0]),
    ([("metal", True, 0)], [0]),
    ([("MTL", True, 0)], [0]),
    ([("Vulkan", False, 0), ("SYCL", False, 1)], [1]),
    ([("vulkan", False, 0)], [0]),
    ([], []),
])
def test_auto_selects_one_backend_and_falls_back(backend_selector, devices, expected):
    """独显优先；同类优先 SYCL；未注册的后端不妨碍 Vulkan/CPU。"""
    assert backend_selector(devices, "auto", False) == expected


@pytest.mark.parametrize("requested", ["auto", "cpu", "sycl", "metal", "vulkan"])
def test_zero_layers_forces_cpu(backend_selector, requested):
    """显式零层卸载在任何有效后端配置下均保留 CPU 行为。"""
    assert backend_selector([("sycl", False, 0)], requested, True) == []


def test_explicit_backend_is_strict(backend_selector):
    """强制 Vulkan 不受 SYCL 优先级影响；不可用及错误配置必须报错。"""
    assert backend_selector([("sycl", False, 0), ("vulkan", True, 1)], "vulkan", False) == [1]
    with pytest.raises(RuntimeError, match="unavailable"):
        backend_selector([("vulkan", False, 0)], "sycl", False)
    with pytest.raises(ValueError, match="MINERU_LLAMA_CPP_BACKEND"):
        backend_selector([], "typo", True)


def test_cuda_is_not_supported(backend_selector):
    """拒绝已移除的 CUDA 配置；旧外部设备注册也不能进入自动选择。"""
    with pytest.raises(ValueError, match="MINERU_LLAMA_CPP_BACKEND"):
        backend_selector([], "cuda", False)
    assert backend_selector([("cuda", False, 0), ("vulkan", True, 1)], "auto", False) == [1]
