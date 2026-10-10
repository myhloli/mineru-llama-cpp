"""无 oneAPI SDK 的安装检查：ARL-H 原生映像、DLL 闭包、无设备回退与诊断。"""
from __future__ import annotations
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
from sycl_profile import validate_module


def main() -> None:
    """在新进程移除 SDK 搜索路径，验证真实安装的 wheel，而非源码替代品。"""
    if "--child" not in sys.argv:
        environment = os.environ.copy()
        environment.pop("ONEAPI_ROOT", None)
        environment["PATH"] = os.pathsep.join(p for p in environment.get("PATH", "").split(os.pathsep)
                                               if "oneapi" not in p.lower())
        subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child"], env=environment, check=True)
        return
    import mineru_llama_cpp
    package = Path(mineru_llama_cpp.__file__).resolve().parent
    manifest = json.loads((package / "bin/sycl-build.json").read_text())
    report = validate_module(manifest, (package / "bin/ggml-sycl.dll").read_bytes())
    handles = []
    for path in sorted((package / "bin").glob("*.dll")):
        if path.name == "ggml-vulkan.dll":
            continue  # 无驱动 runner 可能没有系统 Vulkan loader，沿用已有独立回退检查。
        handles.append(ctypes.WinDLL(str(path), winmode=0x100 | 0x1000))
    subprocess.run([sys.executable, str(Path(__file__).with_name("wheel_smoke.py"))], check=True)
    registry = ctypes.CDLL(str(package / "bin/ggml.dll"))
    base = ctypes.CDLL(str(package / "bin/ggml-base.dll"))
    registry.ggml_backend_reg_by_name.argtypes = [ctypes.c_char_p]
    registry.ggml_backend_reg_by_name.restype = ctypes.c_void_p
    registry.ggml_backend_load_all_from_path.argtypes = [ctypes.c_char_p]
    registry.ggml_backend_load_all_from_path(str(package / "bin").encode())
    base.ggml_backend_reg_dev_count.argtypes = [ctypes.c_void_p]
    base.ggml_backend_reg_dev_count.restype = ctypes.c_size_t
    reg = registry.ggml_backend_reg_by_name(b"SYCL")
    count = base.ggml_backend_reg_dev_count(reg) if reg else 0
    if count == 0:
        os.environ["MINERU_LLAMA_CPP_BACKEND"] = "sycl"
        try:
            mineru_llama_cpp.Engine("missing-for-sycl-smoke.gguf", "missing-mmproj.gguf", n_ctx_seq=8192, n_gpu_layers=99)
        except RuntimeError as error:
            assert "arl-h" in str(error), str(error)
        else:
            raise AssertionError("Explicit unavailable SYCL must fail")
        try:
            mineru_llama_cpp.Engine("missing-for-sycl-smoke.gguf", "missing-mmproj.gguf", n_ctx_seq=8192, n_gpu_layers=0)
        except RuntimeError as error:
            assert "arl-h" not in str(error), "Zero layers did not force CPU"
        else:
            raise AssertionError("Missing test model unexpectedly loaded")
    print(json.dumps({"python": sys.version, "configuration": manifest["configuration"],
                      "native_images": report["image_count"], "sycl_devices": count,
                      "sdk_free_runtime_closure": "passed", "gpu_execution": "not tested"}))


if __name__ == "__main__":
    main()
