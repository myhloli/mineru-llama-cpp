"""独立编译可选 GPU MODULE，只暂存后端及 Windows SYCL 运行库。"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def llama_revision() -> str:
    """核验子模块提交；sdist 使用随源码保存的提交，避免误读外层 Git 仓库。"""
    expected = (ROOT / "llama-cpp-revision.txt").read_text().strip()
    source = ROOT / "third_party/llama.cpp"
    top = subprocess.run(["git", "-C", str(source), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if top.returncode == 0 and Path(top.stdout.strip()).resolve() == source.resolve():
        actual = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        if actual != expected:
            raise RuntimeError("llama.cpp checkout differs from llama-cpp-revision.txt; update both together")
    return expected


def bundle_sycl_runtime(stage: Path, root: Path) -> list[str]:
    """按上游 2026.1.1 清单复制运行库，并验证 PE 的递归 DLL 依赖。"""
    import pefile
    names = ["mkl_sycl_blas.6.dll", "mkl_core.3.dll", "mkl_def.3.dll", "mkl_avx2.3.dll", "mkl_tbb_thread.3.dll",
             "sycl9.dll", "svml_dispmd.dll", "libmmd.dll", "libiomp5md.dll", "dnnl.dll", "tbb12.dll",
             "ur_adapter_level_zero.dll", "ur_adapter_level_zero_v2.dll", "ur_adapter_opencl.dll", "ur_loader.dll",
             "ur_win_proxy_loader.dll", "tcm.dll", "libhwloc-15.dll", "umf.dll"]
    available = {path.name.lower(): path for path in root.rglob("*.dll") if "latest" not in path.parts}
    # 同一版本根目录缺少文件时，从其 latest 链接路径补齐。
    for path in root.rglob("*.dll"):
        available.setdefault(path.name.lower(), path)
    # 使用固定版本 SDK 的 loader，使 Windows 无需另外安装用户态 Level Zero 运行库。
    sdk = Path(os.environ["LEVEL_ZERO_V1_SDK_PATH"])
    for path in sdk.rglob("*.dll"):
        available[path.name.lower()] = path
    names += ["ze_loader.dll"]
    copied = []
    for name in names:
        if name.lower() not in available:
            raise RuntimeError(f"Missing oneAPI runtime {name} under {root}")
        shutil.copy2(available[name.lower()], stage / name)
        copied.append(name)
    system = {"kernel32.dll", "ntdll.dll", "advapi32.dll", "user32.dll", "gdi32.dll", "shell32.dll", "ole32.dll",
              "oleaut32.dll", "ws2_32.dll", "shlwapi.dll", "bcrypt.dll", "version.dll", "setupapi.dll",
              "cfgmgr32.dll", "psapi.dll", "dbghelp.dll", "dbgcore.dll", "ucrtbase.dll", "msvcrt.dll",
              "msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"}
    # 未在静态清单里的 Intel 依赖也需复制，不能依赖构建机 PATH。
    queue = [stage / name for name in copied] + [stage / "ggml-sycl.dll"]
    visited = set()
    unresolved = set()
    while queue:
        path = queue.pop()
        if path.name.lower() in visited:
            continue
        visited.add(path.name.lower())
        with pefile.PE(str(path)) as binary:
            imports = getattr(binary, "DIRECTORY_ENTRY_IMPORT", []) + getattr(binary, "DIRECTORY_ENTRY_DELAY_IMPORT", [])
            for item in imports:
                name = item.dll.decode().lower()
                if name in visited or (stage / name).exists() or name.startswith(("api-ms-", "ext-ms-", "ggml")) or name in system:
                    continue
                if name in available:
                    target = stage / available[name].name
                    shutil.copy2(available[name], target)
                    queue.append(target)
                    copied.append(target.name)
                else:
                    unresolved.add((path.name, name))
    if unresolved:
        # 一次返回全部缺失依赖，避免每次完整编译后只能发现一个问题。
        raise RuntimeError(f"Unresolved SYCL runtime dependencies: {sorted(unresolved)}")
    license_files = [path for path in root.rglob("*.txt")
                     if any(word in str(path).lower() for word in ("license", "licensing", "eula", "third-party-programs"))]
    if not license_files:
        raise RuntimeError(f"No oneAPI redistribution license found under {root}")
    licenses = stage / "licenses" / "oneapi"
    licenses.mkdir(parents=True, exist_ok=True)
    for index, path in enumerate(sorted(set(license_files))):
        shutil.copy2(path, licenses / f"{index}-{path.name}")
    return copied


def build(backend: str, stage: Path, work: Path) -> None:
    """使用独立编译器构建 MODULE，不暂存 ggml 公共库或开发产物。"""
    stage.mkdir(parents=True, exist_ok=True)
    # 与主体配置保持相同补丁集合；重复构建时不重放已应用的补丁。
    patch_hashes = {}
    for patch in sorted((ROOT / "patches/llama.cpp").glob("*.patch")):
        if patch.name.startswith("._"):
            continue
        patch_hashes[patch.name] = hashlib.sha256(patch.read_bytes()).hexdigest()
        prefix = ["git", "-C", str(ROOT / "third_party/llama.cpp"), "apply"]
        if subprocess.run(prefix + ["--reverse", "--check", str(patch)], capture_output=True).returncode:
            subprocess.run(prefix + ["--check", str(patch)], check=True)
            subprocess.run(prefix + [str(patch)], check=True)
    args = ["cmake", "-S", str(ROOT / "third_party/llama.cpp"), "-B", str(work), "-G", "Ninja",
            "-DCMAKE_BUILD_TYPE=Release", "-DBUILD_SHARED_LIBS=ON", "-DGGML_BACKEND_DL=ON", "-DGGML_NATIVE=OFF",
            "-DGGML_CPU=OFF", "-DLLAMA_BUILD_TESTS=OFF", "-DLLAMA_BUILD_TOOLS=OFF", "-DLLAMA_BUILD_COMMON=OFF",
            "-DLLAMA_OPENSSL=OFF", f"-DGGML_{backend.upper()}=ON"]
    if os.name != "nt":
        args += ["-DCMAKE_BUILD_WITH_INSTALL_RPATH=ON", "-DCMAKE_INSTALL_RPATH=$ORIGIN;$ORIGIN/../lib"]
    args += ["-DCMAKE_C_COMPILER=" + ("cl" if os.name == "nt" else "icx"),
             "-DCMAKE_CXX_COMPILER=" + ("icx" if os.name == "nt" else "icpx"), "-DGGML_SYCL_F16=OFF"]
    subprocess.run(args, check=True)
    if backend == "sycl":
        configuration = (work / "build.ninja").read_text()
        # 必须实际启用 oneDNN、Graph 和 Level Zero API，不能只接受 option=ON 后的降级配置。
        for required in ("GGML_SYCL_DNNL=1", "GGML_SYCL_GRAPH", "GGML_SYCL_SUPPORT_LEVEL_ZERO_API"):
            if required not in configuration:
                raise RuntimeError(f"SYCL build is missing required feature: {required}")
    target = "ggml-" + backend
    subprocess.run(["cmake", "--build", str(work), "--target", target, "--parallel", os.environ.get("CMAKE_BUILD_PARALLEL_LEVEL", "2")], check=True)
    files = list((work / "bin").glob(("" if os.name == "nt" else "lib") + target + (".dll" if os.name == "nt" else ".so")))
    if len(files) != 1:
        raise RuntimeError(f"Expected one {target} MODULE, got {files}")
    shutil.copy2(files[0], stage / files[0].name)
    if os.name != "nt":
        # 擦除工具链写入的绝对目录，保证缺少外部运行库时能真实回退。
        subprocess.run(["patchelf", "--set-rpath", "$ORIGIN:$ORIGIN/../lib", str(stage / files[0].name)], check=True)
    runtime = bundle_sycl_runtime(stage, Path(os.environ["ONEAPI_ROOT"])) if backend == "sycl" and os.name == "nt" else []
    compiler = "icx" if os.name == "nt" else "icpx"
    # 同时保存编译器版本和补丁摘要，供合包时核验及设备复核时追溯。
    toolchain = subprocess.check_output([compiler, "--version"], text=True, stderr=subprocess.STDOUT)
    manifest = {"backend": backend, "llama_cpp_commit": llama_revision(), "patches": patch_hashes,
                "module": files[0].name, "bundled_runtime": runtime, "toolchain": toolchain}
    (stage / f"{backend}-build.json").write_text(json.dumps(manifest, indent=2))


def main() -> None:
    """接收后端与暂存目录，构建结果供普通 wheel 的 CMake install 使用。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["sycl"], required=True)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    build(args.backend, args.stage.resolve(), args.work.resolve())


if __name__ == "__main__":
    main()
