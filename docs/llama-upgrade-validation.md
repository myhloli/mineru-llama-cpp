# llama.cpp 升级候选验证记录

日期：2026-10-09。当前源码候选，尚未正式发布。

## 构建与行为基线

- 项目基线：`07442b0`，llama.cpp `9a3bf2b84923a85583b4ee8177b0cca13824bb03`，包含既有并发分词、Windows DLL 补丁和 UTF-8 修复。
- 候选上游：`86a283532072722c5f3363d37d59a874d09fa99b`，仍应用重新生成的两个既有补丁。
- 保留 Python API、返回结构、异常类别、独立上下文、无 prompt 复用和流式 reader 生命周期。
- 新上游创建批处理线程池，绑定层补齐 `cpuparams_batch` 默认值解析，避免负线程数造成崩溃。
- 新 JSON 接受原始字节，绑定层继续将非法生成内容映射为原有 `InvalidRequestError`；长度截断只舍弃未完成字符，严格 UTF-8 解码辅助函数保持原语义。
- 常规 wheel 扩展在构建目录生成，源码包复制排除 editable 残留的 `.so`/`.pyd`，避免串入其他 Python ABI。

## 本地验证

机器：Apple M4，macOS 26.6.2（25G83）。本地使用 Command Line Tools、macOS SDK 27.0；CI 已配置 Xcode 26.6 发布构建及 Xcode 16.4 兼容检查。本地结果不代替这些 SDK 的 CI 结果。

| 检查 | 结果 |
|---|---|
| 升级前完整测试 | 83 passed，1 skipped（原开发者图像路径不存在） |
| 候选完整测试 | 最终运行中，日志见下方 |
| UTF-8、JSON、流式定向回归 | 17 passed；覆盖非法字节后的请求恢复 |
| Python 3.10、3.11、3.12、3.13、3.14 | 五个 arm64 wheel 构建、安装、后端加载及文本生成通过 |
| 五个 wheel 的 Mach-O 审计 | 每包 11 个原生产物，arm64、最低系统版本均为 14.0，依赖及 RPATH 可迁移 |
| wheel ABI 混入检查 | 每包恰好一个对应 CPython ABI 的扩展 |
| CMake 补丁重入及低目标检查 | 两个补丁已应用时重复配置通过；13.0 部署目标明确拒绝 |
| 打包的 llama-server | 仓库外 `--list-devices` 可加载 Apple M4 后端 |
| 默认 / 禁用 Tensor | 普通 Metal，25/25 层卸载 |
| M4 显式开启 Tensor | 探测通过、完整 Shader 编译及生成通过，25/25 层卸载 |
| 显式 CPU | 0/25 层卸载，生成通过 |
| Q8_0/F16 MUL_MAT | 上游 test-backend-ops 与 CPU 比较，Metal 37/37 用例通过 |

Tensor 能力为真只证明该路径可用，不能单凭该字段判断某个请求实际调用了 Tensor 内核。M4 显式开启测试不等同于 M5 加速验证。

## 同机三轮交替测量与输出裁决

使用相同 Q8_0 主模型与 mmproj，独立进程，顺序为 AB、BA、AB。每轮先预热文本生成和两阶段提取。没有同时运行其他构建或模型测试。输入为 Magic-PDF 示例 `demo1.pdf` 第一页，渲染为最长边 1400 的 PNG。记录完整 DEBUG 原生日志；这些数字只适用于该输入和配置。

| 指标（中位数） | 基线 | 候选 | 候选变化 |
|---|---:|---:|---:|
| 初始化 | 0.8467 s | 0.8564 s | +1.16% |
| 文本生成 | 0.2066 s | 0.2044 s | -1.05% |
| 单次布局生成 | 14.6606 s | 15.1578 s | +3.39% |
| MinerU 两阶段提取 | 17.1629 s | 17.1804 s | +0.10% |
| 进程峰值 RSS | 1600.9 MiB | 1659.4 MiB | +3.66% |

三轮文本生成和布局输出逐字一致，布局计数均为 1875 tokens。两阶段结果均为 19 块；类别、区域、顺序及前 18 块内容一致。仅零基编号 18 的页脚内容变化：DOI 从误识别的 `jlydrol` 改为原文的 `jhydrol`，同时调整美元符号转义和换行。已与源页、候选标框图核对，未发现内容或区域回归；未更新测试预期来掩盖差异。

现有 README 历史性能数字保持原记录。本次测量没有证明全面提速，也不能推广到其他芯片或文档。

## 待验证项与证据位置

Linux、Windows、Intel Mac 的 CI 构建和安装检查，以及 macOS 15/Xcode 16.4、macOS 26/Xcode 26.6 构建尚待本轮 CI 结果。macOS 14/15 的 GPU 真机和 M5/macOS 26/27 的完整提取及性能验证仍待外部反馈。

本地产物位于 `build/upgrade-validation/`（忽略的构建目录）：

- `baseline/wheels/`、`baseline/build.log`、`baseline/tests.log`：升级前产物。
- `candidate/wheels/`：Python 3.10–3.14 的 `macosx_14_0_arm64` 候选包；版本仍为 0.1.2，以编译 SHA 区分源码候选与已发布包。
- `candidate/audits/`、`candidate/tests-final.log`、`candidate/backend-ops.log`、`candidate/server-devices.log`：最终检查。
- `candidate/python-*/`、`candidate/mode-*/`：各 Python 和运行模式的结构化结果及原生日志。
- `benchmark/summary.json`、`benchmark/round-*/{baseline,candidate}/`：完整三轮数据和提取结果。
- `images/demo1-page1.png`、`candidate/layout-overlay.png`：源页和已查看的区域标框。

`tools/diagnose_metal.py` 可复用安装包诊断；`tools/benchmark_upgrade.py` 可复用交替测量。正式发布、BF16 专项修复和性能宣传调整不属于此次候选验证。
