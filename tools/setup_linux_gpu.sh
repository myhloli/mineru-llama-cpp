#!/usr/bin/env bash
# 在 manylinux_2_28 内构建 Vulkan 工具，并独立编译可选 GPU 后端。
set -euo pipefail
project_root="$(cd "$(dirname "$0")/.." && pwd)"
tools_prefix="$project_root/.gpu-tools"
stage_dir="$project_root/.gpu-stage"
work_dir=/opt/mineru-gpu-build
export PATH="/opt/python/cp312-cp312/bin:$PATH"
yum install -y git curl tar xz unzip make gcc gcc-c++
python -m pip install 'cmake==3.31.10' ninja
export CMAKE_BUILD_PARALLEL_LEVEL="${CMAKE_BUILD_PARALLEL_LEVEL:-2}"
mkdir -p "$tools_prefix" "$work_dir" "$stage_dir"
# 只有固定 SDK 版本才可复用；后端补丁变化时仅重建 SYCL MODULE。
if [ -x "$tools_prefix/bin/glslc" ]; then
    if ! python -c 'from pathlib import Path; import re, subprocess, sys; root=Path(sys.argv[1]); assert re.search(r"#define\s+VK_HEADER_VERSION\s+350\b", (root/"include/vulkan/vulkan_core.h").read_text()); assert "2026.1" in subprocess.check_output([str(root/"bin/glslc"), "--version"], text=True)' "$tools_prefix"; then
        rm -rf "$tools_prefix"
        mkdir -p "$tools_prefix"
    fi
fi
if [ -x "$tools_prefix/bin/glslc" ]; then
    if [ "$(uname -m)" = aarch64 ] || python "$project_root/tools/build_gpu_backends.py" --backend sycl --stage "$stage_dir" --work "$work_dir/sycl" --check-stage; then
        # 缓存键包含后端源码、补丁和工具脚本；CMake install 仍会核验提交一致。
        echo "Using cached Vulkan tools and independently built GPU MODULEs"
        exit 0
    fi
fi

# 固定源码版本，重复运行时复用已下载的工具源码。
clone_tool() {
    local repo_name="$1" repo_url="$2" repo_tag="$3"
    if [ ! -d "$work_dir/$repo_name/.git" ]; then
        git clone --depth 1 --branch "$repo_tag" "$repo_url" "$work_dir/$repo_name"
    fi
}
if [ ! -x "$tools_prefix/bin/glslc" ]; then
    clone_tool Vulkan-Headers https://github.com/KhronosGroup/Vulkan-Headers.git vulkan-sdk-1.4.350.0
    clone_tool Vulkan-Loader https://github.com/KhronosGroup/Vulkan-Loader.git vulkan-sdk-1.4.350.0
    clone_tool shaderc https://github.com/google/shaderc.git v2026.1
    cmake -S "$work_dir/Vulkan-Headers" -B "$work_dir/headers-build" -G Ninja -DCMAKE_INSTALL_PREFIX="$tools_prefix"
    cmake --build "$work_dir/headers-build" --target install
    cmake -S "$work_dir/Vulkan-Loader" -B "$work_dir/loader-build" -G Ninja -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX="$tools_prefix" -DCMAKE_PREFIX_PATH="$tools_prefix" \
        -DBUILD_WSI_XCB_SUPPORT=OFF -DBUILD_WSI_XLIB_SUPPORT=OFF -DBUILD_WSI_WAYLAND_SUPPORT=OFF -DBUILD_TESTS=OFF
    cmake --build "$work_dir/loader-build" --target install --parallel "$CMAKE_BUILD_PARALLEL_LEVEL"
    (cd "$work_dir/shaderc" && python utils/git-sync-deps)
    cmake -S "$work_dir/shaderc" -B "$work_dir/shaderc-build" -G Ninja -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX="$tools_prefix" -DSHADERC_SKIP_TESTS=ON -DSHADERC_SKIP_EXAMPLES=ON \
        -DSHADERC_SKIP_COPYRIGHT_CHECK=ON -DSPIRV_SKIP_TESTS=ON -DGLSLANG_ENABLE_INSTALL=OFF
    cmake --build "$work_dir/shaderc-build" --target install --parallel "$CMAKE_BUILD_PARALLEL_LEVEL"
    cmake -S "$work_dir/shaderc/third_party/spirv-headers" -B "$work_dir/spirv-headers-build" -G Ninja -DCMAKE_INSTALL_PREFIX="$tools_prefix"
    cmake --build "$work_dir/spirv-headers-build" --target install
fi
export VULKAN_SDK="$tools_prefix"
export PATH="$tools_prefix/bin:$PATH"
if [ "$(uname -m)" = x86_64 ]; then
    # 官方固定版本离线安装器；Linux 包只暂存 MODULE，不复制 oneAPI 运行库。
    oneapi_installer="$work_dir/oneapi-2026.1.1.sh"
    curl --fail --location --retry 3 \
        https://registrationcenter-download.intel.com/akdlm/IRC_NAS/5996e26b-f48a-42b1-8db0-b002ad0bd8d7/intel-oneapi-toolkit-2026.1.1.33_offline.sh \
        --output "$oneapi_installer"
    bash "$oneapi_installer" -s -a --silent --eula accept
    rm "$oneapi_installer"
    python "$project_root/tools/build_level_zero.py" --prefix "$tools_prefix" --work "$work_dir/level-zero"
    export LEVEL_ZERO_V1_SDK_PATH="$tools_prefix"
    export CMAKE_PREFIX_PATH="$tools_prefix${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"
    export LD_LIBRARY_PATH="$tools_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    set +u
    source /opt/intel/oneapi/setvars.sh --force
    set -u
    python "$project_root/tools/build_gpu_backends.py" --backend sycl --stage "$stage_dir" --work "$work_dir/sycl"
fi

# 容器 /project 是源码副本；把完成的 SDK/MODULE 缓存保存回宿主工作区。
if [ -n "${GITHUB_WORKSPACE:-}" ] && [ -d /host ]; then
    durable_root="/host$GITHUB_WORKSPACE"
    mkdir -p "$durable_root/.gpu-tools" "$durable_root/.gpu-stage"
    cp -a "$tools_prefix/." "$durable_root/.gpu-tools/"
    cp -a "$stage_dir/." "$durable_root/.gpu-stage/"
fi
