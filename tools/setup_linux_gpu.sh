#!/usr/bin/env bash
# 在 manylinux_2_28 内构建 Vulkan 工具，并独立编译可选 GPU 后端。
set -euo pipefail
project_root="$(cd "$(dirname "$0")/.." && pwd)"
tools_prefix=/opt/mineru-vulkan
stage_dir=/opt/mineru-gpu-backends
work_dir=/opt/mineru-gpu-build
export PATH="/opt/python/cp312-cp312/bin:$PATH"
yum install -y git curl tar xz unzip make gcc gcc-c++
python -m pip install 'cmake==3.31.8' ninja
export CMAKE_BUILD_PARALLEL_LEVEL="${CMAKE_BUILD_PARALLEL_LEVEL:-2}"
mkdir -p "$tools_prefix" "$work_dir" "$stage_dir"

# 固定源码版本，重复运行时复用已下载的工具源码。
clone_tool() {
    local repo_name="$1" repo_url="$2" repo_tag="$3"
    if [ ! -d "$work_dir/$repo_name/.git" ]; then
        git clone --depth 1 --branch "$repo_tag" "$repo_url" "$work_dir/$repo_name"
    fi
}
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
export VULKAN_SDK="$tools_prefix"
export PATH="$tools_prefix/bin:$PATH"
export CUDA_PATH=/opt/mineru-cuda
if [ "$(uname -m)" = aarch64 ]; then
    cuda_version=13.0.0
else
    cuda_version=12.8.1
fi
python "$project_root/tools/install_cuda.py" --version "$cuda_version" --destination "$CUDA_PATH"
python "$project_root/tools/build_gpu_backends.py" --backend cuda --stage "$stage_dir" --work "$work_dir/cuda"
if [ "$(uname -m)" = x86_64 ]; then
    # 官方固定版本离线安装器；Linux 包只暂存 MODULE，不复制 oneAPI 运行库。
    oneapi_installer="$work_dir/oneapi-2026.1.1.sh"
    curl --fail --location --retry 3 \
        https://registrationcenter-download.intel.com/akdlm/IRC_NAS/5996e26b-f48a-42b1-8db0-b002ad0bd8d7/intel-oneapi-toolkit-2026.1.1.33_offline.sh \
        --output "$oneapi_installer"
    bash "$oneapi_installer" -s -a --silent --eula accept
    rm "$oneapi_installer"
    set +u
    source /opt/intel/oneapi/setvars.sh --force
    set -u
    python "$project_root/tools/build_gpu_backends.py" --backend sycl --stage "$stage_dir" --work "$work_dir/sycl"
fi
