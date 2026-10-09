"""在仓库外验证已安装扩展及其打包的 CPU/GPU 后端，无需模型。"""

import ctypes
import json
from pathlib import Path
import sys


def main() -> None:
    """加载真实安装包的后端并要求至少有一个可用 CPU 设备。"""
    import mineru_llama_cpp

    package = Path(mineru_llama_cpp.__file__).resolve().parent
    name = "ggml.dll" if sys.platform == "win32" else "libggml.0.dylib" if sys.platform == "darwin" else "libggml.so.0"
    library_path = package / "lib" / name
    library = ctypes.CDLL(str(library_path))
    library.ggml_backend_load_all_from_path.argtypes = [ctypes.c_char_p]
    library.ggml_backend_load_all_from_path.restype = None
    library.ggml_backend_dev_by_type.argtypes = [ctypes.c_int]
    library.ggml_backend_dev_by_type.restype = ctypes.c_void_p
    library.ggml_backend_load_all_from_path(str(package / "bin").encode())
    # ggml_backend_dev_type 中 CPU、GPU 的值分别为 0、1。
    cpu = bool(library.ggml_backend_dev_by_type(0))
    gpu = bool(library.ggml_backend_dev_by_type(1) or library.ggml_backend_dev_by_type(2))
    assert cpu, "packaged CPU backend is not available"
    print(json.dumps({"package": str(package), "cpu_available": cpu, "gpu_available": gpu,
                      "gpu_model_test": "not run (model-free installation check)"}))
    if not gpu:
        print("SKIP GPU execution: no GPU exposed by this runner; CPU/import checks passed")


if __name__ == "__main__":
    main()
