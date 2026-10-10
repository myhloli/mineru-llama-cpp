"""统一识别已移除 SYCL 后端的模块和运行库，防止旧缓存重新进入 wheel。"""
from pathlib import PurePosixPath
from typing import Iterable


SYCL_RUNTIME_PREFIXES = (
    "libsycl", "sycl", "libmkl", "mkl_", "libdnnl", "dnnl", "libtbb", "tbb",
    "libiomp", "libur_", "ur_", "libumf", "umf", "libtcm", "tcm", "libhwloc", "hwloc",
    "libsvml", "svml", "libimf", "libintlc", "libirng", "libmmd", "libze_loader", "ze_loader",
    "libopencl", "opencl.dll",
)


def is_sycl_runtime(name: str) -> bool:
    """按文件名识别 Windows DLL、Linux SONAME 及 auditwheel 重命名的运行库。"""
    return PurePosixPath(name.replace("\\", "/")).name.lower().startswith(SYCL_RUNTIME_PREFIXES)


def validate_no_sycl_files(names: Iterable[str]) -> None:
    """拒绝 SYCL 模块、构建清单、运行库和仅供旧后端分发的许可目录。"""
    for name in names:
        normalized = name.replace("\\", "/").lower()
        basename = PurePosixPath(normalized).name
        if ("ggml-sycl" in basename or is_sycl_runtime(basename)
                or "/licenses/oneapi/" in normalized or "/licenses/level-zero/" in normalized):
            raise ValueError(f"SYCL backend/runtime is not supported: {name}")
