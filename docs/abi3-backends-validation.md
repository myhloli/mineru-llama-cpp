# 六平台 abi3 与可选 GPU 后端验证记录

实现分支：`codex/abi3-gpu-wheels`。源码版本保留 0.1.2，未发布新版本。
llama.cpp 保持 `86a283532072722c5f3363d37d59a874d09fa99b`；原有两个补丁保留，
新增可选后端初始化异常隔离与 SYCL 设备适用性补丁。

## 最新交付范围

根据用户后续调整，所有平台移除 CUDA，取消 ARM64 CUDA / DGX Spark 专用设备代码目标。
不再下载 CUDA 工具链，不打包 CUDA MODULE 或运行库，也不支持 `MINERU_LLAMA_CPP_BACKEND=cuda`。
CMake 强制关闭 CUDA，发布审计拒绝遗留 CUDA MODULE、manifest 和运行库。

| 平台 | 后端 |
|---|---|
| manylinux_2_28 x86_64 | CPU、Vulkan、SYCL |
| manylinux_2_28 aarch64 | CPU、Vulkan |
| Windows AMD64 | CPU、Vulkan、SYCL |
| Windows ARM64 | CPU |
| macOS 14+ arm64 | CPU、Metal |
| macOS 14+ x86_64 | CPU |

- 六个平台各一个 cp310-abi3 wheel，Python 3.10–3.14 复用同一产物安装测试。
- Linux x86_64 保留原有 v3 指令集要求；glibc 下限为 2.28。
- SYCL：oneAPI 2026.1.1，FP32、oneDNN、Graph；固定构建 Level Zero 1.33.1，保证独显/集显分类。
- Windows 内置 SYCL 依赖闭包、Level Zero loader 及许可；Linux 仅包含 SYCL MODULE，运行库由用户安装。
- NVIDIA 和 AMD RDNA3/4 使用 Vulkan；GPU 驱动始终由系统提供。
- macOS 保留现有 llama.cpp Metal/CPU。MLX 是不同推理实现，本轮没有迁移至 MLX。
- 自动模式独显优先，同等级 SYCL/Metal/Vulkan；一个引擎选择一个后端，投影器保持一致。
- 零层卸载强制 CPU；显式请求不可用后端报错；推理开始后的错误不自动重跑。

## 本地验证

本机 Apple M4、macOS 26.6.2。macOS ARM64 wheel 部署下限为 14.0。

| 检查 | 状态 |
|---|---|
| pybind11 基线原有模型回归及当时的策略测试 | 53 passed |
| Limited API 完整模型回归 | Python 3.10、3.11、3.14 各 118 passed；3.12/3.13 完整回归运行中 |
| 同一 wheel 的 Python 3.10–3.14 安装与 ABI | 安装通过；严格审计计算出稳定 ABI 下限 3.10，无非稳定符号 |
| UTF-8、策略及绑定参数边界 | Python 3.10–3.13 各 78 passed；移除 CUDA 后重新运行 |
| 强制 CPU、零层卸载、显式不可用后端 | 真实 Q8_0 模型生成通过；移除 CUDA 后使用 SYCL 配置重新验证 |
| 同机 MinerU 输出与性能 | 三轮交替独立进程，文本及完整页面提取逐轮一致，详见下表 |
| sdist 无子模块 Git 元数据配置 | 通过，提交常量正确，未误读外层仓库 |
| MODULE 合并保护 | 拒绝错误提交、不同补丁、公共核心库冒充后端、非法运行库；额外核心库未合入 |
| 六平台 CI | 旧含 CUDA 构建已取消；重新运行无 CUDA 构建 |

最后一轮绑定迁移对比使用相同 llama.cpp 提交、Q8_0 模型、Q8_0 mmproj 和真实页面图像。
所有生成文本及完整 MinerU 提取结构逐轮一致，三个独立进程取中位数：

| 项目 | pybind11 基线 | abi3 候选 | 候选 / 基线 |
|---|---:|---:|---:|
| 初始化 | 0.853 s | 0.876 s | 1.028 |
| 文本生成 | 0.206 s | 0.206 s | 0.999 |
| 页面布局生成 | 14.643 s | 14.667 s | 1.002 |
| 完整页面提取 | 17.135 s | 17.155 s | 1.001 |
| 峰值 RSS | 1,741,160,448 B | 1,739,456,512 B | 0.999 |

这是单机单页兼容性证据，不扩展为其他平台或 GPU 的性能声明。
初轮还曾发现上游 Metal 注册名为 MTL，已映射并加入回归；错误回退 CPU 的那轮已作废。

产物及日志位于忽略目录 `build/abi3-validation/`：`wheels/`、`candidate-tests-final.log`、
`model-suite-3.*.log`、`contracts-3.*.log`、`abi3audit.json`、`macos-audit.json`、
`benchmark-final/summary.json` 与 `staging-policy/`。
CUDA 移除前的日志仅作为迁移过程证据，最终分发矩阵以无 CUDA 重建结果为准。

## GPU 真机复核

| 设备 / 路径 | 状态 |
|---|---|
| Apple M4 / Metal | 自动选择 MTL0，模型和投影均卸载 25/25 层，完整页面提取通过 |
| Intel GPU / SYCL（Windows/Linux） | 无对应本机硬件，待真机验证 |
| NVIDIA / Vulkan | 无对应本机硬件，待真机验证 |
| AMD RDNA3/4 / Vulkan | 无对应本机硬件，待真机验证 |

无 GPU 的 CI 验证可分发性、ABI、安装与 CPU 后端，不作为 GPU 推理执行证据。
安装对应候选 wheel，准备相同 Q8_0 模型、mmproj 和真实页面图像，按 README 配置运行库。

```bash
MINERU_LLAMA_CPP_BACKEND=sycl python tools/diagnose_metal.py \
  --model /path/to/model-Q8_0.gguf --mmproj /path/to/mmproj-Q8_0.gguf \
  --image /path/to/page.png --extract --output /path/to/gpu-validation
```

Vulkan 将环境变量改为 `vulkan`；Windows 在启动 Python 前设置同名环境变量。
工具名称沿用 Metal 诊断脚本，但调用公开 Engine 和完整 MinerU 提取路径。
检查 `native.log` 的所选设备、模型及投影卸载，保存输出、耗时和 RSS；
额外运行流式、同步/异步、取消等待、关闭重建及内存稳定性回归。

oneAPI 系统要求依据：[Intel oneAPI 2026 编译器发布与系统要求](https://www.intel.com/content/www/us/en/developer/articles/release-notes/oneapi-dpcpp/2026.html)。
RHEL 8.10 在支持范围内，仍以最终包内全部 ELF 的 glibc、GLIBCXX、CXXABI 和动态依赖审计为发布门槛。
