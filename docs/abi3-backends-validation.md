# abi3 与可选 GPU 后端验证记录

实现分支：`codex/abi3-gpu-wheels`。源码版本保留 0.1.2，未发布新版本。
llama.cpp 保持 `86a283532072722c5f3363d37d59a874d09fa99b`，已有两个补丁继续应用。

## 交付边界

- 六个平台各一个 cp310-abi3 wheel，Python 3.10–3.14 复用同一产物测试。
- Linux 下限为 manylinux_2_28；x86_64 的原有 v3 指令集要求保留。
- CUDA：Windows/Linux x86_64 使用 12.8，Linux ARM64-SBSA 使用 13.0 并要求包含 GB10 sm_121 原生设备代码；运行库全部由用户按需安装。
- SYCL：oneAPI 2026.1.1。Windows 内置依赖闭包及许可；Linux 仅包含 MODULE。
- AMD RDNA3/4 使用 Vulkan。ARM64 CUDA 不自动承诺 Jetson/JetPack 兼容。
- 自动模式独显优先，同等级 CUDA/SYCL/Metal/Vulkan；每个引擎使用一个后端，投影器保持一致。零层卸载强制 CPU。

## 本地证据

本机 Apple M4、macOS 26.6.2。候选 wheel 的部署下限是 macOS 14.0。

| 检查 | 状态 |
|---|---|
| pybind11 基线原有模型回归及选择策略测试 | 53 passed；UTF-8 临时扩展另行测试 |
| macOS ARM64 abi3 wheel 的编译、严格 ABI 审计 | 通过，计算出的稳定 ABI 下限为 Python 3.10，无非稳定符号 |
| 同一 wheel 在 CPython 3.10–3.14 安装及后端加载 | 通过 |
| CPython 3.10 无模型 UTF-8、选择策略及绑定边界测试 | 初轮 74 passed；新增 MTL 别名后重新运行 |
| 原生完整模型测试 | 进行中 |
| 同机 MinerU 输出、耗时及 RSS 对比 | 待执行 |
| 六平台构建与安装 CI | 待执行 |

初轮运行发现上游 Metal 的注册名为 MTL，原选择器未映射该别名而回退 CPU。
该轮不作为 GPU 行为验收结果，修正别名并补充测试后重新验证。

本地产物和完整日志位于忽略目录 `build/abi3-validation/`。

## GPU 真机状态

| 设备 / 路径 | 状态 |
|---|---|
| Apple M4 / Metal | 本地完整回归进行中 |
| NVIDIA x86_64 / CUDA | 无对应本机硬件，待真机验证 |
| Intel GPU / SYCL（Windows/Linux） | 无对应本机硬件，待真机验证 |
| AMD RDNA3/4 / Vulkan | 无对应本机硬件，待真机验证 |
| DGX Spark / ARM64 CUDA | 无对应本机硬件，待真机验证 |

无 GPU 的 CI 验证可分发性、ABI、安装和 CPU 回退；不会写成 GPU 推理已通过。
