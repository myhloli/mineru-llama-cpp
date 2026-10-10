# 六平台 abi3 与可选 GPU 后端验证记录

实现分支：`codex/abi3-gpu-wheels`。源码版本保留 0.1.2，未发布新版本。
llama.cpp 保持 `86a283532072722c5f3363d37d59a874d09fa99b`；保留分词、Windows DLL 搜索和可选后端初始化异常隔离三个通用补丁；
SYCL 设备适用性、SDK 路径、初始化错误传播及 Windows AOT 四个专用补丁已移除。

## 最新交付范围

根据用户后续调整，所有平台移除 CUDA，取消 ARM64 CUDA / DGX Spark 专用设备代码目标。
不再下载 CUDA 工具链，不打包 CUDA MODULE 或运行库，也不支持 `MINERU_LLAMA_CPP_BACKEND=cuda`。
随后移除 SYCL：源码构建及运行时选择均不再支持，不再下载 oneAPI 或 Level Zero SDK，
也不再暂存、合包或预加载 SYCL MODULE / 运行库。CMake 强制关闭 CUDA/SYCL，
发布审计拒绝两者的遗留 MODULE、manifest 和运行库，Linux ELF 审计拒绝 oneAPI 动态依赖。
本地移除阶段完成源码、CI 配置、文档及本机验证；随后提交 `eff00ce` 的六平台完整 CI 全部成功。
未发布新版本，旧 SYCL 候选继续保留为历史证据。

| 平台 | 后端 |
|---|---|
| manylinux_2_28 x86_64 | CPU、Vulkan |
| manylinux_2_28 aarch64 | CPU、Vulkan |
| Windows AMD64 | CPU、Vulkan |
| Windows ARM64 | CPU、Vulkan |
| macOS 14+ arm64 | CPU、Metal |
| macOS 14+ x86_64 | CPU |

- 六个平台各一个 cp310-abi3 wheel，Python 3.10–3.14 复用同一产物安装测试。
- Linux x86_64 保留原有 v3 指令集要求；glibc 下限为 2.28。
- NVIDIA 和 AMD RDNA3/4 使用 Vulkan；GPU 驱动始终由系统提供。
- macOS 保留现有 llama.cpp Metal/CPU。MLX 是不同推理实现，本轮没有迁移至 MLX。
- 自动模式独显优先，同等级 Metal/Vulkan；一个引擎选择一个后端，投影器保持一致。
- 零层卸载强制 CPU；显式请求不可用后端报错；推理开始后的错误不自动重跑。
- 支持配置仅为 auto/cpu/vulkan/metal；sycl/cuda 配置始终报错，包括零层卸载。

## 本轮本机验证（移除 SYCL）

本机 Apple M4，Python 3.14.4。使用指定 `.venv4` 构建，在独立目录安装新 wheel，
原先环境中的 `mineru-llama-cpp` 安装未被替换。最终候选为
`build/no-sycl-validation/wheels/mineru_llama_cpp-0.1.2-cp310-abi3-macosx_14_0_arm64.whl`。

| 检查 | 本轮结果 |
|---|---|
| 完整公开 API、模型、UTF-8、流式、并发、生命周期及分发规则回归 | 153 passed，305.94 s；`model-suite-final.log` / `model-suite-final.xml` |
| 严格 abi3 审计 | 稳定 ABI 下限及实际符号均为 3.10，无不稳定或未来 ABI 符号；`abi3-audit.json` |
| macOS 原生产物审计 | 11 个 Mach-O 均为 arm64，部署下限不超过 14.0，动态依赖和 RPATH 可迁移；`macos-audit.json` |
| 源码构建禁用 SYCL | 显式传入 `GGML_SYCL=ON` 后，最终缓存仍为 OFF，构建图无 SYCL target |
| 安装及 CPU 后端 | 仓库外新进程移除 GPU SDK 搜索路径后导入成功，打包 CPU 可用；`wheel-smoke-final.log` |
| 旧真实产物拒绝 | 原 Linux x86_64、Windows AMD64 的 SYCL wheel 均被发布审计拒绝；`legacy-rejection.json` |
| 同机真实页面兼容性 | 相同 Q8_0 模型、投影器及 demo1 第一页，一轮独立进程对比：文本、布局、完整 MinerU 提取结构完全一致；`compatibility/summary.json` |
| Metal 和 CPU 生成 | 新 wheel 选择 MTL0，模型和投影均卸载 25/25 层；强制 CPU 时均为 0/25 层并完成文本生成；`compatibility/round-1/candidate/native.log` / `cpu/result.json` |

候选大小为 6,340,456 B，SHA256 为
`65d6633e5b4e11bfbe1fae76a4fb98f73733efc7670c119fc902a0af9362dfdf`。
包内无 SYCL / oneAPI / Level Zero 文件及 SYCL 预加载代码。
新策略与分发回归已加入 cibuildwheel 安装测试；六平台矩阵测试使用临时最小文件，
用于检查标签、模块和拒绝规则，不作为跨平台构建或 GPU 执行证据。

本轮日志与审计保存于 `build/no-sycl-validation/`，汇总为 `validation-summary.json`，旧候选及历史日志保留。
真实页面对比仅用于本轮输出兼容性验收，不据此更新性能声明。
首次回归中的两个新增构造用例误将异常类型预期为 RuntimeError；
按既有绑定的 ValueError 映射修正后，以上最终完整回归全部通过。

## 六平台 CI（移除 SYCL 后）

[完整 CI 38059937369](https://github.com/myhloli/mineru-llama-cpp/actions/runs/38059937369)
在提交 `eff00ce08f5a22795b840020a2e2c569f0784b35` 全部成功。
使用 `platform=all` 全量重建；六个平台各一个 `cp310-abi3` wheel，
每个平台在 Python 3.10–3.14 各通过 113 项安装、UTF-8、后端选择、绑定、
可选后端异常隔离和分发规则回归。后四个版本均明确复用第一个 wheel，未重复编译。
Xcode 16.4 兼容检查在 Python 3.12 额外通过 113 项回归及部署审计。

六包汇总、严格 abi3 及 Windows PE 架构审计均通过；每包只有一个 Python 扩展，
不含 CUDA/SYCL MODULE、manifest、oneAPI/Level Zero 运行库或许可。
Windows AMD64 和 ARM64 各有 10 个原生产物，PE Machine 全部匹配目标架构。
Windows ARM64 在五个 Python 版本检查原始 Vulkan MODULE 加载，
并用临时 MODULE 副本注入缺失 loader 验证 CPU 回退，没有修改安装包。
Linux 两架构各审计 11 个最终 ELF，最高依赖为 GLIBC 2.28、GLIBCXX 3.4.21、
CXXABI 1.3.9；外部 GPU 运行库仅为 `libvulkan.so.1`。

| 平台 | 压缩 wheel 大小 |
|---|---:|
| macosx_14_0_arm64 | 6.03 MiB |
| macosx_14_0_x86_64 | 5.54 MiB |
| manylinux_2_28_aarch64 | 19.78 MiB |
| manylinux_2_28_x86_64 | 20.62 MiB |
| win_amd64 | 18.56 MiB |
| win_arm64 | 18.56 MiB |

本轮 CI、产物和报告位于 `build/no-sycl-validation/ci-38059937369/`：
`status.json`、`full.log`、`test-matrix.json`、`wheel-manifest.json`、
`linux-elf-summary.json`、`abi3-audit.json` 和 `validation-summary.json`。
完整六包及审计报告归档为 `mineru-llama-cpp-no-sycl-ci-candidates.zip`，
每包 SHA256 和完整字节数见 `wheel-manifest.json`。
发布任务按分支条件跳过，没有创建标签或上传 PyPI。
无模型 CI 不作为 Windows/Linux GPU 实机推理或完整页面提取证据；
本机真实页面兼容性结果仍以上述本地检查为准。

## 历史验证（移除 SYCL 前）

以下记录描述此前含 SYCL 的候选及增量验证，不作为本次修改后的跨平台验证结论。
旧产物与日志保留，不覆盖。

### Windows AMD64 Arc 130T AOT/FP16 增量

只替换 Windows AMD64 wheel；其他五包复用上一批原始文件。
构建和缓存契约明确固定 `arl-h`、F16、备用 IR 排除、XMX subgroup=8、
并行 AOT 链接任务=1、oneAPI 编译器及 OCLOC 版本。最终 MODULE 的审计直接解析
Intel GPU Zebin ELF，核对 `12.74` 兼容 note、原生机器码及无备用 SPIR-V；
还检查 ELF 外的独立 SPIR-V 映像，不能凭 CMake 参数认定 AOT 成功。
旧 FP32/JIT 清单、配置或工具链不一致的缓存和 MODULE 均拒绝合包。

设备先经 `intel_gpu_arl_h` 架构查询过滤，再创建 queue/context 和 oneDNN 探测。
未知/不匹配设备不注册为 SYCL；自动模式继续 Vulkan/CPU，显式 SYCL 报告 ARL-H 限制。
连续内部编号对应原枚举编号，默认设备、注册索引和多设备分配使用同一列表。
模型及投影保持相同后端，零层卸载保留 CPU，推理错误不重跑。
FP16 默认沿用上游精度策略，保留 FP32 累加、高精度要求及环境变量覆盖。

本地专项回归 41 passed，覆盖过滤、混合架构、多个匹配设备、无法识别架构、
原编号映射、严格选择和零层 CPU，以及 ELF、备用 IR、缓存指纹和质量比较工具。
原始 Windows FP32/JIT wheel 及六包归档已保存在
`previous-windows-amd64-fp32-jit/`，不会被新的候选覆盖。

oneAPI 2026.1.1 编译/链接均指定 `-exclude_ir`，实测一个 ESIMD 原生 ELF 仍含
90,876 字节 `.spv`。打包前清空该节并设置零长度，保持 PE 长度、映像地址和其他节不变。
清理后的 DLL 包含 209 个带机器码的 ARL-H `12.74.4` 原生映像，严格审计未发现备用 IR。
逐节比较证明全部原生指令和兼容信息保持不变；IR-only 或非 ARL-H 映像不能通过清理流程。
Windows 附带的 20 个运行库 DLL 和 53 个许可文件与旧包完全一致，OCLOC 仅供构建使用。

Windows 专项 CI 只运行本平台新 MODULE、运行库闭包、Python 3.10–3.14 安装、
无设备 CPU 回退、设备策略及严格 abi3 / AMD64 PE 审计，其他平台与原有 CPU/Vulkan
模型回归均跳过。[Windows 专项 CI 38051516723](https://github.com/myhloli/mineru-llama-cpp/actions/runs/38051516723)
通过，同一 wheel 在五个 Python 版本分别通过 41 项专项回归及实际安装检查，
SYCL 设备数均为零，CPU 回退和显式 ARL-H 诊断通过；31 个原生产物均为 AMD64。
[无 oneAPI SDK 的独立 Windows 复核 38052347518](https://github.com/myhloli/mineru-llama-cpp/actions/runs/38052347518)
通过，核验原始 wheel 的 DLL 闭包、导入、无设备 CPU 回退及 AOT 契约。
六包最终汇总和严格 abi3 审计通过，每包仅一个 Python 扩展；其他五包 SHA256 完全不变。
链接中间产物来自固定源码/补丁的 CI 38045181417，原 DLL 摘要为
`b488b8362fee5dca4df5c7270b2353324708733f63a2c9fa8f29c3ea278a0841`，
清理后 MODULE 摘要为 `317066a899f9db0ea963d2157c8b2b987a270f64b341abd58018ef7cf2db0030`。
该中间 CI 因备用 IR 审计失败；恢复流程先验证来源及摘要，清理后重新构建主体并执行以上检查。
失败构建本身不作为安装或 GPU 执行验收成功证据。

Arc 130T 实机性能和输出质量待验证；本机是 Apple M4，没有目标 Windows GPU。
[同机对比说明](windows-sycl-aot.md) 提供三轮交替独立进程工具
`tools/benchmark_arc_sycl.py`，比较旧 SYCL、新 SYCL 和 Vulkan，记录初始化、首次/预热任务、
完整 MinerU PDF 解析导出、峰值工作集、流式、取消、关闭重建及模型/投影设备一致性。
输出差异按页/块记录，不要求逐字或 JSON 字节一致；有结构缺失、漏块、内容消失、
乱码或非有限值时拒绝，其他差异仍需对照原 PDF 人工确认内容与结构不退化。
AOT 不覆盖 oneDNN/oneMKL 的全部内部初始化，也不预设新 SYCL 必须快于 Vulkan。
精度复核发现上游 F16 开关会让 oneDNN 允许缩窄 F32 输入；Windows 专用补丁
显式使用 `fpmath_mode::strict`，F16 输入仍执行 F16 GEMM，同时保留 F32 覆盖和高精度路径。
构建契约升级为 v2，拒绝没有这项保护的过渡模块。

### Windows ARM64 Vulkan 增量

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
[增量 CI 38025408463](https://github.com/myhloli/mineru-llama-cpp/actions/runs/38025408463)
在提交 `11ed1a7` 全部成功。同一 wheel 在 Python 3.10.11 / 3.11.9 / 3.12.10 / 3.13.16 / 3.14.8
均完成原始 Vulkan MODULE 加载，以及临时副本注入缺失 loader 后的 CPU 存活检查；
后四个 Python 版本明确复用已构建的 cp310-abi3 wheel，未重复编译。
CI 未枚举出 Vulkan GPU，不代表真实模型执行。其他平台任务及已有 API 回归均跳过。

新 wheel 大小为 19,467,251 B（18.57 MiB），原 Windows ARM64 CPU 包为 5.47 MiB。
包内十个原生产物均为 ARM64；仅 `ggml-vulkan.dll` 导入 `vulkan-1.dll`，
Python 扩展和公共核心库没有新增 GPU 运行库硬依赖，wheel 不包含 Vulkan loader 或 SYCL。
最终六包中其余五包与原始最终 CI 产物 SHA256 完全一致；原 CPU-only ARM64 包及六包归档
保存在 `previous-windows-arm64-cpu-only/`。增量证据为 `windows-arm64-vulkan.log`、
`windows-arm64-vulkan-status.json`、`windows-arm64-vulkan-pe.json` 和 `delivery-provenance.json`。

### 原本地验证

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
| 原六平台 CI（Windows ARM64 为 CPU） | [运行 38021630646](https://github.com/myhloli/mineru-llama-cpp/actions/runs/38021630646) 全部成功，恰好六个 cp310-abi3 wheel，Python 3.10–3.14 每平台各 79 passed；严格 ABI 汇总及 Xcode 16.4 检查通过。Windows ARM64 Vulkan 由上述增量 CI 替换 |

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

## 当前 GPU 真机复核

| 设备 / 路径 | 状态 |
|---|---|
| Apple M4 / Metal | 自动选择 MTL0，模型和投影均卸载 25/25 层，完整页面提取通过 |
| Intel GPU / Vulkan（Windows/Linux） | 无对应本机硬件，待真机验证 |
| NVIDIA / Vulkan | 无对应本机硬件，待真机验证 |
| AMD RDNA3/4 / Vulkan | 无对应本机硬件，待真机验证 |
| Windows ARM64 / Vulkan | 本轮 CI 的 MODULE 加载与缺失 loader 的 CPU 回退通过；CI 无 GPU，实际模型与页面提取待真机验证 |

无 GPU 的 CI 验证可分发性、ABI、安装与 CPU 后端，不作为 GPU 推理执行证据。
安装对应候选 wheel，准备相同 Q8_0 模型、mmproj 和真实页面图像，按 README 配置运行库。

```bash
MINERU_LLAMA_CPP_BACKEND=vulkan python tools/diagnose_metal.py \
  --model /path/to/model-Q8_0.gguf --mmproj /path/to/mmproj-Q8_0.gguf \
  --image /path/to/page.png --extract --output /path/to/gpu-validation
```

Metal 将环境变量改为 `metal`；Windows 在启动 Python 前设置同名环境变量。
工具名称沿用 Metal 诊断脚本，但调用公开 Engine 和完整 MinerU 提取路径。
检查 `native.log` 的所选设备、模型及投影卸载，保存输出、耗时和 RSS；
额外运行流式、同步/异步、取消等待、关闭重建及内存稳定性回归。

## 历史候选包（移除 SYCL 前）

本地归档：`build/abi3-validation/mineru-llama-cpp-abi3-candidates.zip`。每包仅含一个 Python 扩展。

| 平台 | 压缩 wheel 大小 |
|---|---:|
| macosx_14_0_arm64 | 6.03 MiB |
| macosx_14_0_x86_64 | 5.54 MiB |
| manylinux_2_28_aarch64 | 19.78 MiB |
| manylinux_2_28_x86_64 | 36.65 MiB |
| win_amd64 | 174.95 MiB |
| win_arm64 | 18.57 MiB |

SHA256 与完整字节数：`delivery-sizes.json`。Windows AMD64 包含 SYCL 运行库，Linux 保持外部依赖策略。
Windows AOT/FP16 候选比保留的 FP32/JIT 基线 146.18 MiB 增加 28.77 MiB。
新增工具及同机验收说明随六包归档一起交付，真机结果尚未产生。
