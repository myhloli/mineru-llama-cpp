# Known Issues

## Incomplete UTF-8 at a generation length limit

[Issue #1](https://github.com/opendatalab/mineru-llama-cpp/issues/1) affects
0.1.2: `generate()` / `agenerate()` can raise `UnicodeDecodeError` when a
length limit stops generation between the bytes of a Unicode character.
There is no fixed 16 KB threshold; a one-token completion can trigger it.
The default Unicode grammar allows a character to span multiple tokens,
so it cannot prevent an incomplete character at a forced stopping point.

The current source strictly decodes the complete prefix when
`finish_reason == "length"` and drops only an unfinished trailing character.
Complete content, structure markers, embedded zero bytes, token counts and
timings are preserved. Invalid UTF-8 still raises an error; replacement
characters are not inserted. Natural stops retain strict decoding. The
streaming path already holds back incomplete characters, so concatenated
streaming content follows the same boundary.

The fix needs a rebuilt extension; updating Python source alone does not
change an already installed 0.1.2 binary.

## Metal tensor API warnings do not imply CPU fallback

[Issue #2](https://github.com/opendatalab/mineru-llama-cpp/issues/2) reports:

```text
ggml_metal_library_init_from_source: error compiling source
ggml_metal_device_init: - the tensor API is not supported in this environment - disabling
```

These messages come from an optional tensor API capability probe. Failure
disables that feature while allowing the regular Metal kernels to run.
On the previous `9a3bf2b` build, Apple M4 / macOS 26.6.2 enabling the probe with
`GGML_METAL_TENSOR_ENABLE=1` reproduces both warnings, followed by successful
Metal initialization, `offloaded 25/25 layers to GPU`, and generation.
This does not establish what happens on the reporter's M5 Pro / macOS 27.

Current source pins `86a283532072722c5f3363d37d59a874d09fa99b`, including
upstream's Metal 4.0 language selection for tensor probes and actual shader
compilation. Rebuild/install a candidate wheel to use it. This is independent
of the macOS 14 deployment floor: tensor support still requires a compatible
runtime, device and successful probes. M5 validation remains pending.

For temporary troubleshooting, set `GGML_METAL_TENSOR_DISABLE=1` before
starting Python. It disables tensor acceleration while retaining ordinary
Metal; use `n_gpu_layers=0` for an explicit CPU run. The diagnostic tool in
README captures package provenance, full logs, generation and optional
MinerU two-step extraction. See [the validation report](llama-upgrade-validation.md).

The embedded shader includes `ggml-common.h` only in the non-embedded
`#else` branch. Extracting it and calling `newLibraryWithSource` without
the runtime `GGML_METAL_EMBED_LIBRARY=1` preprocessor macro produces the
reported missing-header error; it does not reproduce the actual library
initialization. The CPython 3.12 arm64 0.1.2 wheel was checked for this
conditional, and the local wheel's shader compiled successfully with the
runtime macro enabled. Do not infer a missing wheel resource merely from
the presence of that include.

To investigate an actual CPU fallback, retain the full initialization log,
package/Python versions, chip name, macOS version and build. For a minimal
engine check with local Q8_0 models:

```bash
python - <<'PY' 2>&1 | tee metal-init.log
from mineru_llama_cpp import Engine, SamplingParams
from mineru_llama_cpp.verbosity import LOG_LEVEL_DEBUG

with Engine(
    "/path/to/MinerU2.5-Pro-2605-1.2B-Q8_0.gguf",
    "/path/to/mmproj-MinerU2.5-Pro-2605-1.2B-Q8_0.gguf",
    n_ctx_seq=2048,
    n_parallel=1,
    n_gpu_layers=99,
    verbosity=LOG_LEVEL_DEBUG,
) as engine:
    print(engine.generate(
        [{"role": "user", "content": "hello"}],
        SamplingParams(n_predict=8),
    ))
PY
```

Check the Metal backend loading result, layer offloading count, and actual
generation. A failure in the main Metal library or model allocation needs
its own complete error log; the two probe messages alone are insufficient.

## BF16 models SIGSEGV under Metal when loaded from a Python extension (.so)

**Symptom:** Loading a BF16 GGUF model (main model or mmproj) with the Metal
backend enabled, from inside a pybind11-built `.so` (i.e. via
`import mineru_llama_cpp`), crashes with SIGSEGV. The same model loads fine
from a standalone C++ executable on the same machine.

**Root cause (confirmed via direct Metal API probing):** Metal's shader
compiler does not instantiate certain MSL `[[host_name(...)]]` template
specializations (the BF16 matmul kernels, e.g.
`kernel_mul_mv_ext_bf16_f32_r1_2`) when the compiling process is a `dlopen`ed
extension module rather than the main executable. This is a Metal framework
bug, not a bug in llama.cpp or in this library. It matches upstream report
[ggml-org/llama.cpp#21381](https://github.com/ggml-org/llama.cpp/issues/21381)
(closed as not planned).

**Workaround:** Use Q8_0 (or any non-BF16 quantization) for both the main
model and the mmproj. Q8_0 kernels do not use MSL template specialization and
load correctly in every context tested. Q8_0 is also the quantization
recommended on quality/performance grounds independent of this bug (see the
technical-validation phase's `QUANT-BENCHMARK.md`).

**Status:** Not planned to be worked around in this library — see
design spec §1 "非目标". Not applicable on non-Metal backends (CPU/CUDA/Vulkan
are untested but the failure mode is Metal-specific by construction).
