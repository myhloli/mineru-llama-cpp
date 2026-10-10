"""专项回归 Windows AOT 架构过滤、编号映射、原生映像及缓存契约。"""
import copy
import json
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import pytest
from test_backend_policy import backend_selector

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from audit_sycl_aot import audit
from sycl_profile import WINDOWS_SYCL_PROFILE, WINDOWS_OCLOC_VERSION, WINDOWS_OCLOC_RELATIVE_PATH, fingerprint, validate_module

ARL = (True, True, True, True)
OTHER = (True, True, False, True)
CPU = (False, True, False, False)


@pytest.mark.parametrize("devices,expected", [
    ([ARL], [0]), ([OTHER], []), ([CPU, OTHER, ARL], [2]),
    ([OTHER, ARL, OTHER, ARL], [1, 3]), ([], []),
    ([(True, False, True, True)], []),
    ([(True, True, False, True), ARL], [1]),  # 架构查询失败等价于 arl_h=false。
    ([(True, True, True, False), ARL], [1]),
    ([(True, True, True, False)], [0]),
])
def test_aot_filter_and_original_device_mapping(backend_selector, devices, expected):
    """生产过滤策略稳定保留原编号，注册编号连续且未覆盖设备不参与初始化。"""
    assert backend_selector.__self__.select_aot(devices) == expected


def test_filtered_registry_fallback_strict_and_zero_layers(backend_selector):
    """过滤后无 SYCL 设备时自动回退，显式选择报错，零层卸载总是 CPU。"""
    selected = backend_selector.__self__.select_aot([OTHER])
    devices = [("sycl", True, index) for index in range(len(selected))] + [("vulkan", True, 8)]
    assert backend_selector(devices, "auto", False) == [8]
    assert backend_selector([], "auto", False) == []
    with pytest.raises(RuntimeError, match="sycl.*unavailable"):
        backend_selector(devices, "sycl", False)
    assert backend_selector(devices, "sycl", True) == []
    # 多个匹配设备的默认设备及多设备分配均使用连续编号，不借用原枚举的空洞。
    selected = backend_selector.__self__.select_aot([OTHER, ARL, OTHER, ARL])
    assert backend_selector([("sycl", True, i) for i in range(len(selected))], "sycl", False) == [0, 1]


def native_elf(ip=(12, 74, 4), ir=False, native=True, nested=None):
    """构造符合 Intel Zebin 格式的最小 ELF，覆盖实际节表和兼容 note 解析。"""
    config = (ip[0] << 22) | (ip[1] << 14) | ip[2]
    note = struct.pack("<III", 8, 4, 6) + b"IntelGT\0" + struct.pack("<I", config)
    sections = [(".ze_info", 0xff000011, b"kernels: test"), (".text.test", 1, b"native-code" if native else b""),
                (".note.intelgt.compat", 7, note)] if nested is None else [(".device_binary", 1, nested)]
    if ir:
        sections.append((".spv", 0xff000009, b"fallback-ir"))
    names = b"\0" + b"".join(name.encode() + b"\0" for name, _, _ in sections) + b".shstrtab\0"
    sections.append((".shstrtab", 3, names))
    data = bytearray(64)
    records = [(0,) * 10]
    for name, kind, content in sections:
        offset = len(data)
        data.extend(content)
        records.append((names.index(name.encode() + b"\0"), kind, 0, 0, offset, len(content), 0, 0, 1, 0))
    table = len(data)
    for record in records:
        data.extend(struct.pack("<IIQQQQIIQQ", *record))
    ident = b"\x7fELF\x02\x01\x01" + b"\0" * 9
    data[:64] = struct.pack("<16sHHIQQQIHHHHHH", ident, 0xff12, 205, 1, 0, 0, table, 0, 64, 0, 0, 64, len(records), len(records) - 1)
    return bytes(data)


def manifest_for(data):
    """生成合法构建清单用于缓存与合包破坏性回归。"""
    configuration = copy.deepcopy(WINDOWS_SYCL_PROFILE)
    compiler, ocloc = "Intel oneAPI DPC++ 2026.1.1", {"version": WINDOWS_OCLOC_VERSION,
        "relative_path": WINDOWS_OCLOC_RELATIVE_PATH, "sha256": "a" * 64}
    return {"configuration": configuration, "toolchain": compiler, "ocloc": ocloc,
            "fingerprint": fingerprint(configuration, compiler, ocloc), "aot_audit": audit(data)}


def test_actual_native_images_and_nested_container():
    """宿主 PE 内嵌的外层容器及多个原生映像都必须被检查，不能只读 CMake 参数。"""
    data = b"host-PE" + native_elf(nested=native_elf()) + native_elf()
    report = audit(data)
    assert report["image_count"] == 2 and report["ir_excluded"]
    assert report["images"][0]["ip_versions"] == ["12.74.4"]
    assert validate_module(manifest_for(data), data) == report


@pytest.mark.parametrize("data,diagnostic", [
    (native_elf(ir=True), "SPIR-V"),
    (native_elf(nested=native_elf(), ir=True), "SPIR-V"),
    (native_elf() + struct.pack("<7I", 0x07230203, 0x00010600, 0, 32, 0, (2 << 16) | 17, 6), "SPIR-V"),
    (native_elf(ir=True).replace(b".ze_info", b".xx_info") + native_elf(), "SPIR-V"),
    (native_elf(ip=(20, 1, 0)), "ARL-H"),
    (native_elf(native=False), "machine code"),
    (native_elf()[:-20], "No embedded"), (b"CMake: arl-h -exclude_ir", "No embedded"),
])
def test_reject_ir_wrong_gpu_empty_or_truncated_native_code(data, diagnostic):
    """备用 IR、错误架构、无机器码及无效节表均不能通过原生代码审计。"""
    with pytest.raises(ValueError, match=diagnostic):
        audit(data)


@pytest.mark.parametrize("key,value", [("precision", "f32"), ("aot_target", ""), ("exclude_ir", False),
                                      ("xmx_subgroup", 16), ("parallel_aot_jobs", 8), ("jit_fallback", True)])
def test_reject_old_or_incompatible_profile(key, value):
    """旧 FP32/JIT 清单和配置不匹配的缓存必须重建，不能借用新版本标签。"""
    data = native_elf()
    manifest = manifest_for(data)
    manifest["configuration"][key] = value
    with pytest.raises(ValueError, match="configuration"):
        validate_module(manifest, data)


def test_toolchain_and_binary_fingerprints():
    """实际编译器、OCLOC 版本及 MODULE 字节变化都会使缓存失效。"""
    data = native_elf()
    manifest = manifest_for(data)
    manifest["ocloc"]["version"] = "different"
    with pytest.raises(ValueError, match="OCLOC"):
        validate_module(manifest, data)
    with pytest.raises(ValueError, match="differs"):
        validate_module(manifest_for(data), b"changed-host" + data)
    with pytest.raises(KeyError):
        validate_module({"toolchain": "old FP32/JIT"}, data)


def test_stage_cache_rebuilds_old_profile_and_changed_module(tmp_path, monkeypatch):
    """真实缓存入口拒绝旧清单、配置变化及二进制变化，新 runner 无 SDK 时仍可复用。"""
    import hashlib
    import build_gpu_backends as builder
    patches = tmp_path / "patches/llama.cpp"
    patches.mkdir(parents=True)
    patch = patches / "test.patch"
    patch.write_bytes(b"fixed-patch")
    monkeypatch.setattr(builder, "ROOT", tmp_path)
    monkeypatch.setattr(builder, "llama_revision", lambda: "fixed-commit")  # 固定源码身份，隔离缓存规则。
    monkeypatch.setattr(builder, "os", SimpleNamespace(name="nt", environ={"ONEAPI_ROOT": str(tmp_path / "missing-sdk")}))
    stage = tmp_path / "stage"
    stage.mkdir()
    data = native_elf()
    module = stage / "ggml-sycl.dll"
    module.write_bytes(data)
    manifest = manifest_for(data)
    manifest.update(backend="sycl", llama_cpp_commit="fixed-commit", module=module.name,
                    patches={patch.name: hashlib.sha256(patch.read_bytes()).hexdigest()})
    source = stage / "sycl-build.json"
    source.write_text(json.dumps(manifest))
    assert builder.stage_matches(stage, "sycl")
    module.write_bytes(b"different-host" + data)
    assert not builder.stage_matches(stage, "sycl")
    module.write_bytes(data)
    manifest["configuration"]["precision"] = "f32"
    source.write_text(json.dumps(manifest))
    assert not builder.stage_matches(stage, "sycl")
    del manifest["configuration"]
    source.write_text(json.dumps(manifest))
    assert not builder.stage_matches(stage, "sycl")
