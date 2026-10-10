"""统一 Windows ARL-H AOT/FP16 构建契约，供缓存、合包及发布审计使用。"""
import hashlib
import json
from pathlib import Path

WINDOWS_SYCL_PROFILE = {
    "profile": "windows-arl-h-aot-f16-v1", "toolkit": "2026.1.1",
    "precision": "f16", "aot_target": "arl-h", "exclude_ir": True,
    "xmx_subgroup": 8, "jit_fallback": False, "parallel_aot_jobs": 1,
}


def fingerprint(configuration: dict, compiler: str, ocloc: dict) -> str:
    """将计算配置与实际工具链身份纳入稳定摘要，拒绝旧 FP32/JIT 缓存。"""
    data = {"configuration": configuration, "compiler": compiler, "ocloc": ocloc}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def validate_manifest(manifest: dict) -> None:
    """校验 Windows 构建契约及原生映像报告，不接受仅声明 AOT 的清单。"""
    if manifest["configuration"] != WINDOWS_SYCL_PROFILE:
        raise ValueError("Windows SYCL requires ARL-H AOT-only FP16 configuration")
    compiler, ocloc = manifest["toolchain"], manifest["ocloc"]
    if "2026.1.1" not in compiler or not ocloc["version"] or len(ocloc["sha256"]) != 64:
        raise ValueError("SYCL compiler/OCLOC identity is missing or incompatible")
    if manifest["fingerprint"] != fingerprint(manifest["configuration"], compiler, ocloc):
        raise ValueError("SYCL configuration/toolchain fingerprint differs")
    audit = manifest["aot_audit"]
    if audit["aot_target"] != "arl-h" or audit["image_count"] < 1 or not audit["ir_excluded"]:
        raise ValueError("Native ARL-H code audit is missing")


def validate_module(manifest: dict, data: bytes) -> dict:
    """重新审计实际 MODULE，避免篡改或旧二进制借用新清单通过合包。"""
    from audit_sycl_aot import audit
    validate_manifest(manifest)
    actual = audit(data)
    if actual != manifest["aot_audit"]:
        raise ValueError("SYCL MODULE differs from its native-image audit")
    return actual


if __name__ == "__main__":
    import sys
    source = Path(sys.argv[1])
    manifest = json.loads(source.read_text())
    validate_module(manifest, (source.parent / manifest["module"]).read_bytes())
