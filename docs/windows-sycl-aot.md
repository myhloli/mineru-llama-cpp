# Windows Arc 130T AOT/FP16 候选验收

本轮只替换 Windows AMD64 wheel 的 SYCL MODULE。它使用 oneAPI 2026.1.1、
`GGML_SYCL_F16=ON`、`GGML_SYCL_DEVICE_ARCH=arl-h`，编译和链接都传入
`-fsycl-targets=spir64_gen -Xsycl-target-backend=spir64_gen "-device arl-h -exclude_ir"`。
XMX subgroup 为 8，OCLOC 并行链接任务为 1。构建清单记录实际编译器版本、
固定安装目录 OCLOC 的 PE 文件版本及 SHA256，并审计最终 DLL 内嵌的 Intel Zebin ELF。
审计必须找到原生机器码、ARL-H `12.74` 兼容信息，且容器/原生映像不携带备用 SPIR-V。
仅声明 CMake 参数不足以通过检查。

SYCL 架构查询仅允许 `intel_gpu_arl_h`，适用 Arrow Lake-H 的 Arc 130T/140T。
未覆盖或无法识别的设备在创建 queue/context 和 oneDNN 探测前排除，
自动模式回退到 Vulkan，Vulkan 不可用时使用 CPU。显式 `sycl` 会报告 ARL-H 限制；
`n_gpu_layers=0` 仍强制 CPU。注册设备使用连续编号，并记录原枚举编号映射。
Linux SYCL 暂时保持 FP32/JIT；其他平台及 Q8_0 模型、视觉投影文件不变。

FP16 构建使上游 `GGML_SYCL_DYNAMIC_PRECISION` 默认值为 `F16`，
`GGML_SYCL_DYNAMIC_REQUIRED_PRECISION` 仍默认为 `F32`。XMX 使用 float 累加，
oneDNN/oneMKL 半精度 GEMM 使用 float 输出/计算类型。明确要求高精度的算子仍走原路径。
已有环境变量覆盖仍可用，例如 PowerShell 中：

```powershell
$env:MINERU_LLAMA_CPP_BACKEND = 'sycl'
$env:GGML_SYCL_DYNAMIC_PRECISION = 'F32'
python your_script.py
```

驱动由用户安装，Windows 附带的运行库和许可策略保持不变，OCLOC 不进入 wheel。
AOT 覆盖本项目设备内核；oneDNN/oneMKL 仍可能初始化、编译内部内核，
此候选不承诺消除全部启动成本，也没有预设它一定快于 Vulkan。

## 三轮同机对比

保留两份 Python 环境：一份安装旧的 Windows FP32/JIT 候选，另一份安装新的 AOT/FP16 候选。
两份环境使用相同 Python 版本、相同 MinerU SDK 与依赖，并安装 Pillow、pypdfium2、mineru-vl-utils。
完整 PDF 路径要求 SDK 提供 `mineru.parser.parse`、`mineru.config.VlmConfig` 和 `ParseResult.to_dict`。
不要用 PyPI 原始 0.1.2 代替这里的 FP32/JIT 基线；它们的 llama.cpp 提交不同。

旧 wheel 保存在交付目录 `previous-windows-amd64-fp32-jit/`，新 wheel 位于 `final-delivery-wheels/`。
先分别安装到两个已经准备好相同依赖的环境：

```powershell
& C:\venvs\baseline\Scripts\python.exe -m pip install --no-deps --force-reinstall C:\candidates\baseline\mineru_llama_cpp-0.1.2-cp310-abi3-win_amd64.whl
& C:\venvs\candidate\Scripts\python.exe -m pip install --no-deps --force-reinstall C:\candidates\new\mineru_llama_cpp-0.1.2-cp310-abi3-win_amd64.whl

python tools/benchmark_arc_sycl.py `
  --baseline-python C:\venvs\baseline\Scripts\python.exe `
  --candidate-python C:\venvs\candidate\Scripts\python.exe `
  --model C:\models\MinerU2.5-Pro-2605-1.2B-Q8_0.gguf `
  --mmproj C:\models\mmproj-MinerU2.5-Pro-2605-1.2B-Q8_0.gguf `
  --pdf C:\fixtures\representative.pdf `
  --context 8192 --parallel 1 --threads 8 `
  --power-mode '插电；Windows 最佳性能；厂商性能模式固定' `
  --output C:\benchmarks\arc130t-aot-f16
```

阶段计时默认将 PDF 首页按 200 DPI 渲染；可用 `--image` 提供之前评测的同一页面图像。
完整 PDF 默认全部页，可用 `--pages '1-5'` 固定范围。任务的推理采样使用 MinerU SDK 的默认配置；
流式与取消检查固定 temperature=0、top_k=1、seed=42。三条路径使用相同参数，
脚本移除外部动态精度覆盖以测量两包各自的默认值，并记录其他 SYCL/驱动缓存环境变量。
不清除驱动缓存；三轮按 A/B/C、B/C/A、C/A/B 顺序交替，报告环境供复核。

每个组合启动独立进程，分别执行阶段计时和完整 MinerU PDF 解析，保存：

- 导入、Engine 初始化、首次完整页面任务、两次预热后的页面任务；
- 新进程中完整 MinerU PDF 解析、JSON/Markdown 导出耗时；
- 内核峰值工作集，阶段计时与完整 PDF 的内存分开记录；
- 同步/异步流式、取消等待、关闭重建的结果；
- 模型与投影所选设备及原生日志，实际 CPU 回退不能算作 SYCL/Vulkan 对比；
- 模型、投影、PDF、图像、MODULE 的 SHA256，Python/SDK/驱动与电源方案。

脚本通过参数工厂为完整 MinerU 入口固定同一 Engine 参数和实际模型路径，
不替换原生实现。执行失败即停止，不以其他后端重跑。

## 内容与结构验收

`summary.json` 包含三轮原始结果及中位数。每轮每条候选路径输出
`quality-differences.json`，差异路径包含页码/块索引。JSON 字节和逐字文本不要求相同；
数值及措辞变化进入人工复核。自动检查将漏字段、列表缩减、内容消失、乱码、
字段类型变化及非有限值列为拒绝项，但它不能单独判定语义质量。

逐页对照原 PDF、旧/新 Markdown 和 Middle JSON，记录差异是否影响内容、块覆盖、
表格、公式或阅读顺序。新增块、文本变化和坐标变化均需人工裁决；
不允许乱码、漏块或内容退化。没有真机输出及人工裁决时，质量和性能验收保持待验证。

依据：[Intel AOT 目标说明](https://www.intel.com/content/www/us/en/docs/dpcpp-cpp-compiler/developer-guide-reference/2026-0/ahead-of-time-compilation.html)、
[OCLOC AOT 隐式 IR 回退说明](https://github.com/intel/llvm/discussions/15087)、
[SYCL 架构查询扩展](https://github.com/intel/llvm/blob/sycl/sycl/doc/extensions/experimental/sycl_ext_oneapi_device_architecture.asciidoc)、
[Intel Zebin 格式定义](https://github.com/intel/compute-runtime/blob/master/shared/source/device_binary_format/zebin/zebin_elf.h)。
