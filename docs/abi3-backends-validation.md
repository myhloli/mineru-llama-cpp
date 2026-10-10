# 六平台 abi3 与可选 GPU 后端验证记录

实现分支：`codex/abi3-gpu-wheels`。源码版本保留 0.1.2，未发布新版本。
llama.cpp 保持 `86a283532072722c5f3363d37d59a874d09fa99b`；原有两个补丁保留，
新增可选后端初始化异常隔离、SYCL 设备适用性、SDK 头文件路径及初始化错误传播补丁。

## 最新交付范围

根据用户后续调整，所有平台移除 CUDA，取消 ARM64 CUDA / DGX Spark 专用设备代码目标。
不再下载 CUDA 工具链，不打包 CUDA MODULE 或运行库，也不支持 `MINERU_LLAMA_CPP_BACKEND=cuda`。
CMake 强制关闭 CUDA，发布审计拒绝遗留 CUDA MODULE、manifest 和运行库。

| 平台 | 后端 |
|---|---|
| manylinux_2_28 x86_64 | CPU、Vulkan、SYCL |
| manylinux_2_28 aarch64 | CPU、Vulkan |
| Windows AMD64 | CPU、Vulkan、SYCL |
| Windows ARM64 | CPU、Vulkan |
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

## Windows ARM64 Vulkan 增量

在已通过的六平台候选基础上，仅替换 Windows ARM64 wheel，增加 Vulkan MODULE。
固定使用 LunarG Vulkan SDK 1.4.350.0 的 Windows Arm 安装包，原生 ARM64 shader 工具及链接库；
CPU 保留 clang-cl 和无 OpenMP 配置。所有 Windows DLL、EXE、PYD 都审计 PE Machine，防止混入 x64。
Vulkan loader 与驱动由用户的原生 ARM64 显卡驱动提供，没有新增内置加速运行库。
CI 从官方 ARM64 runtime 组件归档准备固定版本 loader，核对 SHA256 及 PE 架构，
只用于直接 DLL 加载检查，不写入 wheel，也不作为真实 GPU 推理证据。

本轮手动筛选 `windows-arm64`，仅复核新 wheel 在 Python 3.10–3.14 的 MODULE 加载、
设备枚举与缺失 loader 的 CPU 回退，并严格审计 abi3；其他五个平台及已有 API 回归复用前轮结果。
缺失 loader 检查只修改临时 MODULE 副本的 DLL 导入名称，不修改安装包。
实际 Windows ARM64 GPU 推理仍须对应硬件、Q8_0 模型、视觉投影和页面提取验证。
增量构建与候选归档结果待本轮 CI 完成后补入。

## 本地验证

本机 Apple M4、macOS 26.6.2。macOS ARM64 wheel 部署下限为 14.0。

| 检查 | 状态 |
|---|---|
| pybind11 基线原有模型回归及当时的策略测试 | 53 passed |
| Limited API 完整模型回归 | 迁移版 Python 3.10–3.14 各 118 passed；无 CUDA CI 候选本地完整 119 passed（282.48 s）；设备数组终止项修正后本地 Python 3.14 完整 119 passed（314.88 s） |
| 同一 wheel 的 Python 3.10–3.14 安装与 ABI | 安装通过；严格审计计算出稳定 ABI 下限 3.10，无非稳定符号 |
| UTF-8、策略及绑定参数边界 | 无 CUDA 同一 wheel，Python 3.10–3.14 各 79 passed，含驱动初始化失败后的 CPU 存活检查 |
| 强制 CPU、零层卸载、显式不可用后端 | 真实 Q8_0 模型生成通过；SYCL 不可用时显式报错，零层卸载及 CPU 覆盖有效，CUDA 参数明确拒绝 |
| 同机 MinerU 输出与性能 | 三轮交替独立进程，文本及完整页面提取逐轮一致，详见下表 |
| macOS Intel CPU 产物 | CI wheel 在 M4 Rosetta / x86_64 Python 3.14 下加载 Q8_0 模型和投影，并完成 32 token 生成；不代表物理 Intel GPU 或性能证据 |
| sdist 无子模块 Git 元数据配置 | 通过，提交常量正确，未误读外层仓库 |
| MODULE 合并保护 | 拒绝错误提交、不同补丁、公共核心库冒充后端、非法运行库；额外核心库未合入 |
| 六平台 CI | [最终运行 38021630646](https://github.com/myhloli/mineru-llama-cpp/actions/runs/38021630646) 全部成功，恰好六个 cp310-abi3 wheel，Python 3.10–3.14 每平台各 79 passed；严格 ABI 汇总及 Xcode 16.4 检查通过 |

最后一轮绑定迁移对比使用相同 llama.cpp 提交、Q8_0 模型、Q8_0 mmproj 和真实页面图像。
所有生成文本及完整 MinerU 提取结构逐轮一致，三个独立进程取中位数：

| 项目 | pybind11 基线 | abi3 候选 | 候选 / 基线 |
|---|---:|---:|---:|
| 初始化 | 0.854 s | 0.849 s | 0.994 |
| 文本生成 | 0.206 s | 0.206 s | 1.002 |
| 页面布局生成 | 14.609 s | 14.673 s | 1.004 |
| 完整页面提取 | 17.091 s | 17.089 s | 1.000 |
| 峰值 RSS | 1,740,881,920 B | 1,740,800,000 B | 1.000 |

这是单机单页兼容性证据，不扩展为其他平台或 GPU 的性能声明。
另将 CI run `38016309954` 的实际 macOS ARM64 wheel 安装到本地，完整模型回归 119 passed，
并运行完整 MinerU 页面提取：文本和提取结构与上述 pybind11 基线一致，
日志确认模型及视觉投影均由 Metal 卸载 25/25 层。
记录为 `ci-ebc-model-suite.log` 和 `ci-ebc-extraction/result.json`。
随后复核上游 `llama_model_params.devices` 的空指针终止契约，为 GPU 设备数组补充明确终止项，
修正后本地同一 wheel 在 Python 3.10–3.14 各 79 passed、Python 3.14 完整 119 passed。
Windows 无 SDK 复核进一步发现 Intel DLL 内部加载顺序要求。
预加载内置运行库后，独立 Windows 复核成功；无 GPU 安装检查随后发现上游 SYCL 初始化通过 `exit(1)` 处理缺少设备；
补丁将该初始化错误向可选后端加载器传播，允许回退至 Vulkan/CPU。
最终完整重建为 CI run `38021630646`，全部构建与汇总审计通过。
Windows 五个 Python 版本均记录附带 SYCL MODULE / DLL 加载成功，
随后无设备初始化错误被隔离，CPU 保持可用；额外在 [未安装 oneAPI SDK 的独立 Windows 任务](https://github.com/myhloli/mineru-llama-cpp/actions/runs/38024157828)
使用未经修改的最终 wheel 复核，新进程直接加载 SYCL 及 CPU 回退均通过。
预加载发生在模型创建前，失败仍允许 CPU 导入；不修改 PATH，不重跑模型请求。
初轮还曾发现上游 Metal 注册名为 MTL，已映射并加入回归；错误回退 CPU 的那轮已作废。

产物及日志位于忽略目录 `build/abi3-validation/`：`wheels/`、`candidate-tests-final.log`、
`model-suite-3.*.log`、`contracts-3.*.log`、`abi3audit.json`、`macos-audit.json`、
`benchmark-no-cuda/summary.json` 与 `staging-policy/`。
最终交付目录为 `final-delivery-wheels/`，最终 CI 记录为 `delivery-ci-status.json`、
`delivery-windows-amd64.log` 和 `delivery-aggregate.log`；Linux ELF 报告为
`delivery-linux-*-audits/*.post.json`，ABI / 分发汇总为 `delivery-abi3.json` / `delivery-manifest.json`。
CUDA 移除前的日志仅作为迁移过程证据，最终分发矩阵以无 CUDA 重建结果为准。

Linux 审计采用 manylinux_2_28 策略的 GLIBC 2.28、GLIBCXX 3.4.24、CXXABI 1.3.11 下限。
`libllama` 的随机数实现原本引用 GLIBCXX 3.4.25，因此只对该库静态链接 libstdc++，
并附带 GCC Runtime Library Exception 与 GPL 许可文件；Linux 两架构重建已通过。
Linux SYCL 的 `libirng.so` 等编译器运行库与 oneMKL/oneDNN 一并保持外部依赖。
Windows DLL 闭包检查排除系统 [ImageHlp](https://learn.microsoft.com/windows/win32/api/imagehlp/nf-imagehlp-imagegetdigeststream)
和 [WinTrust](https://learn.microsoft.com/windows/win32/api/wintrust/nf-wintrust-winverifytrust)，
额外在移除 oneAPI 搜索路径后直接加载附带 SYCL MODULE，检查 DLL 能否解析。
Linux 两架构修复后的 ELF 分别为 11 / 12 个，实际最高依赖为 GLIBC 2.28、
GLIBCXX 3.4.21、CXXABI 1.3.9；完整报告保存在 `delivery-linux-*-audits/*.post.json`。

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

## 最终候选包

本地归档：`build/abi3-validation/mineru-llama-cpp-abi3-candidates.zip`。每包仅含一个 Python 扩展。

| 平台 | 压缩 wheel 大小 |
|---|---:|
| macosx_14_0_arm64 | 6.03 MiB |
| macosx_14_0_x86_64 | 5.54 MiB |
| manylinux_2_28_aarch64 | 19.78 MiB |
| manylinux_2_28_x86_64 | 36.65 MiB |
| win_amd64 | 146.18 MiB |
| win_arm64 | 5.47 MiB |

SHA256 与完整字节数：`delivery-sizes.json`。Windows AMD64 包含 SYCL 运行库，Linux 保持外部依赖策略。
