# mineru-llama-cpp

In-process llama.cpp VLM inference engine for MinerU, exposing a single
`Engine` class with synchronous and asynchronous generate/stream methods.
Wraps a pinned build of [llama.cpp](https://github.com/ggml-org/llama.cpp)
(no HTTP layer, no subprocess) via the CPython Limited API.

Current source pins llama.cpp to `86a283532072722c5f3363d37d59a874d09fa99b`.
macOS source builds and wheels require macOS 14 or newer. This source update
requires rebuilding the native extension; released 0.1.2 wheels retain their
original upstream version and deployment targets.

## Status

The performance figures below describe earlier validated builds. The current
upstream upgrade has its own [validation report](docs/llama-upgrade-validation.md);
pending platforms are not covered by these historical measurements.

**Verified on 4 platforms** (build + import + text generate + two-step
document extraction):

| Platform | Backend | Two-step extract | Notes |
|---|---|---|---|
| macOS arm64 (Apple Silicon) | Metal | 7.3s / page | 6.4x faster than CPU |
| macOS arm64 | CPU | 47.1s / page | Fallback |
| Linux x86_64 (NVIDIA GPU) | Vulkan | ~16s / page | OpenMP ON + libgomp bundled |
| Windows x86_64 | CPU | 241.5s / page | OpenMP OFF |
| Windows x86_64 | Vulkan (Intel UHD) | 378.3s / page | Weak iGPU — CPU faster |
| Windows arm64 | CPU (clang-cl) | 208.8s / page | MSVC rejects ARM; clang-cl required |

Key build decisions (all in top-level `CMakeLists.txt`):
- `GGML_BACKEND_DL=ON` — backends are dlopen'd MODULEs, not hard-linked;
  missing GPU loaders (e.g. no `libvulkan.so.1`) gracefully fall back to CPU
- `LLAMA_OPENSSL=OFF` — drops OpenSSL/libssl/libcrypto dependency
- `GGML_NATIVE=OFF` — portable binaries (no `-march=native`); required for
  `GGML_BACKEND_DL` on x86 anyway
- `GGML_OPENMP` — ON on Linux (14% faster, libgomp bundled in wheel),
  OFF on macOS/Windows (zero benefit, removes libomp/libgomp dependency)
- `BUILD_SHARED_LIBS=ON` — prerequisite for `GGML_BACKEND_DL`

## CI wheels

Current source targets six `cp310-abi3` wheels. A wheel is reused across
standard, GIL-enabled CPython 3.10–3.14, with installation tests on each
version. The released 0.1.2 wheels predate this migration; rebuild from
source or use a validated candidate to try the new backends.

| Platform | Wheel platform tag | Packaged backends | External GPU requirements |
|---|---|---|---|
| Linux x86_64 | `manylinux_2_28_x86_64` | CPU, Vulkan | Driver; Vulkan loader |
| Linux aarch64 | `manylinux_2_28_aarch64` | CPU, Vulkan | Driver; Vulkan loader |
| Windows AMD64 | `win_amd64` | CPU, Vulkan | Driver; Vulkan loader |
| Windows ARM64 | `win_arm64` | CPU, Vulkan | Native ARM64 GPU driver and Vulkan loader |
| macOS arm64 | `macosx_14_0_arm64` | CPU, Metal | macOS 14+ |
| macOS x86_64 | `macosx_14_0_x86_64` | CPU | macOS 14+ |

Alpine/musllinux wheels are no longer built. Linux CPU wheels bundle
`libgomp.so.1`; Linux x86_64 retains the existing x86_64-v3 CPU requirement
(AVX2/FMA/BMI2/F16C). The glibc tag does not describe CPU instruction support.
AMD RDNA3/4 GPUs use Vulkan, without a ROCm installation.

Vulkan is an optional dynamically loaded module. Missing runtime libraries
or devices leave CPU available and do not prevent importing the Python
package. CUDA and SYCL are disabled in all builds and cannot be selected.
GPU drivers and the Vulkan loader are provided by the system; GPU runtimes
are not bundled. macOS keeps the existing llama.cpp Metal/CPU implementation.

Every wheel is checked outside the checkout, including a child process with
external GPU SDK search paths removed, and must load its packaged CPU
backend. Strict ABI auditing verifies the Python 3.10 floor. macOS also audits
Mach-O deployment targets and dependencies. CPU-only CI does not establish
GPU execution or full MinerU extraction correctness; device-specific evidence
is recorded separately in [the validation record](docs/abi3-backends-validation.md).

### Backend selection

Set `MINERU_LLAMA_CPP_BACKEND` before constructing an engine:

```bash
MINERU_LLAMA_CPP_BACKEND=auto python your_script.py
```

Values: `auto` (default), `cpu`, `vulkan`, `metal`.
Automatic selection prefers discrete GPUs over integrated GPUs, then Metal
and Vulkan within the same device class. Each engine selects one
backend and may use multiple devices from it; its model and multimodal
projector use that backend.
`n_gpu_layers=0` forces CPU. Explicitly requesting an unavailable backend
raises an error. Removed `sycl` and `cuda` values are invalid even with
`n_gpu_layers=0`. Errors after inference starts are surfaced without retrying
the request on another backend. Use `verbosity=LOG_LEVEL_INFO` to see the
chosen backend/device, and `LOG_LEVEL_DEBUG` for native loading details.

### External runtimes

Vulkan requires a GPU driver and Vulkan loader matching the system
architecture. Linux needs `libvulkan.so.1`; Windows needs `vulkan-1.dll` from
the GPU driver installation. The wheel does not include a Vulkan loader.
Without a usable Vulkan device or loader, automatic selection falls back to
CPU. Explicit `vulkan` selection raises an error when unavailable.

Release CI prepares a fixed Vulkan SDK and builds its backend together with
the ggml core. No oneAPI toolkit, SYCL runtime or Level Zero SDK is used.
The aggregate release check requires exactly six wheels and rejects legacy
CUDA/SYCL modules and runtimes. The macOS build still includes the Xcode 16.4
compatibility check.

## Install (development)

```bash
git clone --recurse-submodules https://github.com/johnking0099/mineru-llama-cpp.git
cd mineru-llama-cpp
pip install --no-build-isolation -e .
pip install -e ".[test]"
```

The first build compiles llama.cpp from source (a few minutes); subsequent
builds only recompile this library's own C++ files.

Tests accept `MINERU_LLAMA_CPP_TEST_MODEL` and
`MINERU_LLAMA_CPP_TEST_MMPROJ` to select local MinerU Q8_0 model files.
Set `MINERU_LLAMA_CPP_TEST_IMAGE` to a real page image to run the image test
without the original developer's local fixture.
`tests/test_output_utf8.py` tests the production UTF-8 decoder without
loading models; it builds a small test extension using the `[test]`
dependencies and a C++ compiler. The deterministic generation regressions
use byte token IDs from the MinerU Q8_0 vocabulary.

## Preparing GGUF models

`Engine` takes two local `.gguf` files: the main model and its multimodal
projector (mmproj). MinerU2.5 ships as HuggingFace safetensors, so you
convert it once with llama.cpp's own tooling. Both tools come with the
llama.cpp submodule; `llama-quantize` is also shipped in this package's
`bin/` directory after a build.

Pre-built Q8_0 GGUF files are available on
[ModelScope](https://www.modelscope.cn/models/jinzhenj/MinerU2.5-Pro-2605-1.2B-GGUF):
`MinerU2.5-Pro-2605-1.2B-Q8_0.gguf` (main model, 506MB) and
`mmproj-MinerU2.5-Pro-2605-1.2B-Q8_0.gguf` (mmproj, 677MB).

To convert from safetensors yourself:

**Main model** — convert to a BF16 GGUF, then quantize from that BF16 file
with `llama-quantize`:

```bash
# safetensors dir -> BF16 GGUF
python third_party/llama.cpp/convert_hf_to_gguf.py \
    /path/to/MinerU2.5-Pro-2605-1.2B \
    --outfile MinerU2.5-Pro-2605-1.2B.gguf \
    --outtype bf16

# BF16 GGUF -> quantized variants (pick what you need)
bin/llama-quantize MinerU2.5-Pro-2605-1.2B.gguf MinerU2.5-Pro-2605-1.2B-Q8_0.gguf   Q8_0
bin/llama-quantize MinerU2.5-Pro-2605-1.2B.gguf MinerU2.5-Pro-2605-1.2B-Q5_K_M.gguf Q5_K_M
bin/llama-quantize MinerU2.5-Pro-2605-1.2B.gguf MinerU2.5-Pro-2605-1.2B-Q4_K_M.gguf Q4_K_M
```

**mmproj (vision projector)** — convert directly from safetensors with
`--mmproj` at the desired precision (no separate `llama-quantize` step;
`--outtype` does the quantization here). The tool auto-prepends the
`mmproj-` prefix to the output filename:

```bash
# BF16 mmproj
python third_party/llama.cpp/convert_hf_to_gguf.py \
    /path/to/MinerU2.5-Pro-2605-1.2B \
    --mmproj \
    --outfile mmproj-MinerU2.5-Pro-2605-1.2B.gguf \
    --outtype bf16

# Q8_0 mmproj
python third_party/llama.cpp/convert_hf_to_gguf.py \
    /path/to/MinerU2.5-Pro-2605-1.2B \
    --mmproj \
    --outfile mmproj-MinerU2.5-Pro-2605-1.2B-Q8_0.gguf \
    --outtype q8_0
```

On macOS/Metal, use the **Q8_0** main model — BF16 crashes on Metal (see
Known issues). The mmproj can stay BF16 or Q8_0.

## Usage

```python
from mineru_llama_cpp import Engine, SamplingParams

with Engine("/path/to/model.gguf", "/path/to/mmproj.gguf") as engine:
    result = engine.generate([{"role": "user", "content": "hello"}])
    print(result.content)

    for chunk in engine.stream([{"role": "user", "content": "hello"}]):
        print(chunk.delta, end="", flush=True)
```

Async:

```python
async with Engine("/path/to/model.gguf", "/path/to/mmproj.gguf") as engine:
    result = await engine.agenerate([{"role": "user", "content": "hello"}])
    async for chunk in engine.astream([{"role": "user", "content": "hello"}]):
        print(chunk.delta, end="", flush=True)
```

Images go in `content` as `{"type": "image_url", "image_url": {"url": "data:image/png;base64,...."}}`
— **must** be a pre-encoded base64 data URI; this library does not accept
`PIL.Image` objects, file paths, or HTTP URLs (see
`docs/superpowers/specs/2026-08-03-mineru-llama-cpp-engine-design.md` §6 for
why).

### Engine parameters

| Parameter | Default | Description |
|---|---|---|
| `n_ctx_seq` | 0 (= model training context) | Per-slot context length. Total KV = `n_ctx_seq × n_parallel`. |
| `n_gpu_layers` | 99 | Layers to offload to GPU. Set 0 to force CPU. |
| `n_parallel` | 4 | Concurrent slots. Tune to available GPU memory / CPU cores. |
| `verbosity` | LOG_LEVEL_WARN | Log threshold (LOG_LEVEL_INFO for backend info). |
| `n_threads` | -1 (= auto) | CPU threads. |

### Batch inference

```python
from mineru_llama_cpp import Engine
from mineru_vl_utils import MinerUClient

with Engine(model_path, mmproj_path, n_parallel=8) as engine:
    client = MinerUClient(backend="llama-cpp-engine", llama_cpp_engine=engine)
    results = client.batch_two_step_extract(images)
```

`batch_two_step_extract` dispatches requests concurrently across the
engine's slots (`batching_mode="concurrent"`).

## Cross-platform deployment

### Linux

```bash
# Standard build (CPU + Vulkan via GGML_BACKEND_DL, OpenMP ON)
uv pip install --no-build-isolation -e .

# Or with Vulkan explicitly:
SKBUILD_CMAKE_ARGS="-DGGML_VULKAN=ON" uv pip install --no-build-isolation -e .

```

With `GGML_BACKEND_DL=ON`, the Vulkan backend is a dlopen'd MODULE — if
`libvulkan.so.1` is missing, the engine gracefully falls back to CPU
without crashing. The `libgomp.so.1` OpenMP runtime is bundled into the
wheel (under `mineru_llama_cpp/lib/`), so users don't need it
preinstalled.

### Windows

```bash
# Requires MSVC Build Tools + vcvars64 in PATH
set CMAKE_GENERATOR=Ninja
uv pip install --no-build-isolation -e .
```

On Windows x86_64, MSVC compiles ggml-cpu directly. On Windows ARM64,
`ggml-cpu/CMakeLists.txt` rejects MSVC — use `clang-cl` instead:

```bash
set SKBUILD_CMAKE_ARGS=-DCMAKE_C_COMPILER=clang-cl;-DCMAKE_CXX_COMPILER=clang-cl
```

Windows ARM64 wheels also include a native Vulkan MODULE. Install an ARM64
GPU driver that provides Vulkan; missing loader or devices allow CPU fallback.
The Vulkan SDK is only required when building from source. Use the native
[Windows Arm SDK](https://vulkan.lunarg.com/doc/view/1.4.350.0/windows/getting_started.html)
and add `-DGGML_VULKAN=ON` to the ARM64 build arguments above.
Its native `Lib/vulkan-1.lib` and `Bin/glslc.exe` must target ARM64.
The release build pins SDK 1.4.350.0 and audits every DLL, executable and Python
extension for ARM64 machine code. It does not bundle a GPU driver or SYCL.

The `__init__.py` adds `bin/` to the DLL search path via
`os.add_dll_directory()` — Windows has no RPATH (`$ORIGIN`), so this is
needed for the `.pyd` to find its sibling DLLs.

On older Intel UHD iGPUs (pre-2020, 24-32 EU), Vulkan may be **slower**
than CPU for small models. Set `n_gpu_layers=0` to force CPU.

### macOS

Requires macOS 14+. Apple Silicon wheels embed Metal shader source and
compile it on the user's machine. M1-M4 default to ordinary Metal kernels;
eligible M5 devices enable the tensor API only after runtime capability
and compilation checks. Tensor compilation uses Metal 4.0 on supported
systems; the package's minimum deployment target remains macOS 14.
Intel wheels use CPU. Use Q8_0 models for both model and mmproj (see the
existing BF16 limitation). OpenMP is OFF on macOS.
Intel source builds default to a CPU instruction baseline compatible with
Rosetta; explicit CMake feature flags can opt into AVX/FMA on capable hardware.

To disable only the optional tensor API, set this before starting Python:

```bash
export GGML_METAL_TENSOR_DISABLE=1
```

Ordinary Metal GPU acceleration remains available. To force CPU, use
`Engine(..., n_gpu_layers=0)`.

Diagnose an installed wheel from outside the checkout, preserving complete
initialization logs and structured results:

```bash
python /path/to/mineru-llama-cpp/tools/diagnose_metal.py \
  --model /path/to/model-Q8_0.gguf --mmproj /path/to/mmproj-Q8_0.gguf \
  --image /path/to/page.png --extract --mode default --output /tmp/metal-check
```

Modes are `default`, `disable`, `enable` and `cpu`; each starts a separate
process. `--extract` additionally requires Pillow and mineru-vl-utils.
`result.json` records the compiled upstream SHA, timings, peak RSS, tensor
capability and actual layer offloading; `native.log` retains the raw logs.
Tensor capability alone does not prove tensor kernels were executed.
See [upgrade validation](docs/llama-upgrade-validation.md) for measured and
pending combinations.

## Build configuration

All build decisions are in the top-level `CMakeLists.txt` as forced cache
variables. The `patches/llama.cpp/*.patch` files are auto-applied during
CMake configure via `git apply` (requires the submodule to be a real git
checkout).

### OpenMP (libgomp) — platform-differential

- **Linux**: ON (default). ~14% faster than pthread fallback. `libgomp.so.1`
  bundled in the wheel as a real file (symlink resolved via `file(REAL_PATH)`).
  Verified in a `python:3.12-slim` container with no system libgomp.
- **macOS**: OFF. Metal is primary path; no libgomp on macOS.
- **Windows**: OFF. Zero perf delta on ARM64; removes `libomp140` dependency.

## Verifying the build

```bash
python -c "
from mineru_llama_cpp import Engine
with Engine('/path/to/model.gguf', '/path/to/mmproj.gguf') as engine:
    print(engine.generate([{'role': 'user', 'content': 'hello'}]).content)
"
```

## Known issues

See `docs/known-issues.md` — notably: **use Q8_0 models, not BF16**, on
Metal.
