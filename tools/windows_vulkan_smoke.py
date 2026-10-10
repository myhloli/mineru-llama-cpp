"""专门复核 Windows ARM64 Vulkan MODULE、设备枚举与缺少 loader 的 CPU 回退。"""
from __future__ import annotations
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from windows_pe import pe_machine


def load_registry(package: Path):
    """分别加载注册表与设备查询 DLL，只初始化 CPU，供可选 Vulkan 加载检查使用。"""
    library = ctypes.CDLL(str(package / "bin/ggml.dll"))
    base = ctypes.CDLL(str(package / "bin/ggml-base.dll"))
    library.ggml_backend_load.argtypes = [ctypes.c_char_p]
    library.ggml_backend_load.restype = ctypes.c_void_p
    library.ggml_backend_dev_by_type.argtypes = [ctypes.c_int]
    library.ggml_backend_dev_by_type.restype = ctypes.c_void_p
    base.ggml_backend_reg_dev_count.argtypes = [ctypes.c_void_p]
    base.ggml_backend_reg_dev_count.restype = ctypes.c_size_t
    base.ggml_backend_reg_dev_get.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    base.ggml_backend_reg_dev_get.restype = ctypes.c_void_p
    base.ggml_backend_dev_name.argtypes = [ctypes.c_void_p]
    base.ggml_backend_dev_name.restype = ctypes.c_char_p
    base.ggml_backend_dev_description.argtypes = [ctypes.c_void_p]
    base.ggml_backend_dev_description.restype = ctypes.c_char_p
    assert library.ggml_backend_load(str(package / "bin/ggml-cpu.dll").encode())
    assert library.ggml_backend_dev_by_type(0), "CPU unavailable before Vulkan load"
    return library, base


def probe(package: Path, missing_loader: bool) -> None:
    """分别在独立进程中验证原始 MODULE 和仅供故障注入的临时 MODULE 副本。"""
    library, base = load_registry(package)
    module = package / "bin/ggml-vulkan.dll"
    if missing_loader:
        # 仅修改临时副本的导入 DLL 名称，保证即使系统安装了 loader 也确实触发缺失依赖。
        data = module.read_bytes()
        needle = b"vulkan-1.dll\0"
        offset = data.lower().find(needle)
        assert offset >= 0, "Vulkan MODULE must import the external Vulkan loader"
        patched = data[:offset] + b"absent-1.dll\0" + data[offset + len(needle):]
        with tempfile.TemporaryDirectory() as directory:
            copy = Path(directory) / "ggml-vulkan-missing-loader.dll"
            copy.write_bytes(patched)
            assert not library.ggml_backend_load(str(copy).encode()), "Missing loader unexpectedly loaded"
        assert library.ggml_backend_dev_by_type(0), "Missing Vulkan loader broke CPU fallback"
        print(json.dumps({"python": sys.version.split()[0], "missing_vulkan_loader": "isolated",
                          "cpu_fallback": "passed", "original_wheel_modified": False}), flush=True)
        return
    # CI 的 loader 独立于 SDK 工具链且不在 wheel 内；用户运行时由显卡驱动提供。
    sdk = os.environ.get("VULKAN_SDK")
    runtime = Path(sdk) / "TestRuntime" if sdk else None
    directory_handle = os.add_dll_directory(str(runtime)) if runtime and runtime.is_dir() else None
    module_handle = ctypes.WinDLL(str(module), winmode=0x100 | 0x1000)
    registry = library.ggml_backend_load(str(module).encode())
    devices = []
    if registry:
        for index in range(base.ggml_backend_reg_dev_count(registry)):
            device = base.ggml_backend_reg_dev_get(registry, index)
            devices.append({"name": base.ggml_backend_dev_name(device).decode(),
                            "description": base.ggml_backend_dev_description(device).decode()})
    assert library.ggml_backend_dev_by_type(0), "Vulkan initialization broke CPU availability"
    print(json.dumps({"python": sys.version.split()[0], "vulkan_module_load": "passed",
                      "devices": devices, "gpu_inference": "pending hardware/model validation"}), flush=True)


def main() -> None:
    """复用同一 abi3 wheel，在各 Python 版本检查 ARM64 架构和两个独立加载场景。"""
    if sys.platform != "win32":
        raise RuntimeError("This installation check requires native Windows ARM64")
    import mineru_llama_cpp
    package = Path(mineru_llama_cpp.__file__).resolve().parent
    for path in package.rglob("*"):
        if path.suffix.lower() in {".dll", ".exe", ".pyd"}:
            assert pe_machine(path.read_bytes()) == 0xAA64, f"Non-ARM64 native binary: {path}"
    if "--probe" in sys.argv or "--missing-loader" in sys.argv:
        probe(package, "--missing-loader" in sys.argv)
        return
    environment = os.environ.copy()
    environment["PATH"] = os.pathsep.join(item for item in environment.get("PATH", "").split(os.pathsep)
                                          if "vulkansdk" not in item.lower())
    for option in ("--missing-loader", "--probe"):
        subprocess.run([sys.executable, str(Path(__file__).resolve()), option], env=environment, check=True)


if __name__ == "__main__":
    main()
