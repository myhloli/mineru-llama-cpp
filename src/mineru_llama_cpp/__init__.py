import os
from pathlib import Path

# Windows has no RPATH ($ORIGIN / @loader_path). The .pyd's DLL dependencies
# (llama.dll, ggml*.dll, mtmd.dll, ...) live in sibling bin/ and lib/
# directories -- in the wheel (mineru_llama_cpp/{bin,lib}) or, for editable
# inplace builds, in the build tree (<repo>/bin). Those directories must be on
# the process DLL search path BEFORE importing _mineru_llama_cpp, which
# triggers the .pyd load and its dependency resolution.
#
# The return value of os.add_dll_directory() MUST be kept alive: the returned
# object's deallocation calls RemoveDllDirectory(), so discarding it (as this
# module once did) removes the directory immediately -- a silent no-op. The
# module-level list below pins the handles for the interpreter's lifetime.
#
# Note this does NOT make the directories visible to plain LoadLibraryW()
# calls from native code (ggml's backend loader included) -- those only consult
# the legacy search order unless the caller passes LOAD_LIBRARY_SEARCH_* flags.
# Backend loading is fixed separately in C++ (see
# patches/llama.cpp/0002-fix-windows-backend-dll-search.patch); the directories
# registered here cover the .pyd/ctypes load paths.
_dll_directory_handles: list = []
_sycl_runtime_handles: list = []


def _preload_windows_sycl_runtime(package: Path) -> None:
    """预加载内置运行库，兼容 Intel DLL 内部使用传统搜索路径的加载行为。"""
    import ctypes
    import json
    import logging
    manifest = package / "bin/sycl-build.json"
    if not manifest.is_file():
        return
    try:
        pending = json.loads(manifest.read_text())["bundled_runtime"]
        # 先加载能够独立解析的库，再复核其余库；不修改进程 PATH。
        for _ in range(2):
            unavailable = []
            for name in pending:
                try:
                    _sycl_runtime_handles.append(ctypes.WinDLL(str(package / "bin" / name), winmode=0x100 | 0x1000))
                except OSError:
                    unavailable.append(name)
            pending = unavailable
            if not pending:
                break
        if pending:
            logging.getLogger(__name__).debug("Bundled SYCL runtime DLLs could not load: %s", pending)
    except (OSError, ValueError, KeyError):
        # 可选运行库损坏不能阻止导入 CPU 接口；原生后端加载仍会提供诊断。
        logging.getLogger(__name__).debug("Could not read bundled SYCL runtime manifest", exc_info=True)

if os.name == "nt":
    _pkg = Path(__file__).resolve().parent
    for _d in [
        _pkg / "bin",
        _pkg / "lib",
        _pkg.parent / "bin",
        _pkg.parent.parent / "bin",
    ]:
        if _d.is_dir():
            _dll_directory_handles.append(os.add_dll_directory(str(_d)))
    _preload_windows_sycl_runtime(_pkg)

from .engine import Engine
from .exceptions import (
    ContextExceededError,
    EngineError,
    InvalidRequestError,
    MineruLlamaCppError,
)
from .sampling import SamplingParams
from .types import (
    ContentPart,
    GenerateChunk,
    GenerateResult,
    GenerationTimings,
    ImagePart,
    ImageURL,
    Message,
    Messages,
    TextPart,
)
from .verbosity import (
    LOG_LEVEL_DEBUG,
    LOG_LEVEL_ERROR,
    LOG_LEVEL_INFO,
    LOG_LEVEL_OUTPUT,
    LOG_LEVEL_TRACE,
    LOG_LEVEL_WARN,
)

__all__ = [
    "Engine",
    "SamplingParams",
    "MineruLlamaCppError",
    "InvalidRequestError",
    "ContextExceededError",
    "EngineError",
    "Message",
    "Messages",
    "ContentPart",
    "TextPart",
    "ImagePart",
    "ImageURL",
    "GenerateResult",
    "GenerateChunk",
    "GenerationTimings",
    "LOG_LEVEL_OUTPUT",
    "LOG_LEVEL_ERROR",
    "LOG_LEVEL_WARN",
    "LOG_LEVEL_INFO",
    "LOG_LEVEL_TRACE",
    "LOG_LEVEL_DEBUG",
]
