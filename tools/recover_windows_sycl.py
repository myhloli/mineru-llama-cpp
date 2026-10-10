"""复用已完成 AOT 链接的固定 CI 中间产物，清理 IR 后重新执行完整打包审计。"""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile
from build_gpu_backends import ROOT, llama_revision
from strip_sycl_ir import strip
from audit_sycl_aot import audit
from sycl_profile import WINDOWS_SYCL_PROFILE, fingerprint, validate_module


def recover(module: Path, runtime_wheel: Path, stage: Path, run_id: str) -> dict:
    """核验来源和全部补丁，只复用原生后端及运行库，不复制旧公共核心。"""
    source = json.loads((ROOT / "tools/windows_sycl_recovery.json").read_text())
    patches = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
               for p in (ROOT / "patches/llama.cpp").glob("*.patch") if not p.name.startswith("._")}
    data = module.read_bytes()
    if (run_id != source["run_id"] or patches != source["patches"]
            or llama_revision() != source["llama_cpp_commit"]
            or hashlib.sha256(data).hexdigest() != source["module_original_sha256"]
            or hashlib.sha256(runtime_wheel.read_bytes()).hexdigest() != source["runtime_wheel_sha256"]):
        raise ValueError("Recovery source, patches, module or runtime wheel differs from pinned CI provenance")
    cleaned, records = strip(data)
    stage.mkdir(parents=True, exist_ok=True)
    (stage / "sycl-build.json").unlink(missing_ok=True)
    with zipfile.ZipFile(runtime_wheel) as archive:
        prefix = "mineru_llama_cpp/bin/"
        previous = json.loads(archive.read(prefix + "sycl-build.json"))
        runtime = previous["bundled_runtime"]
        # 清单仅允许扁平 DLL 名称；旧 wheel 的核心、扩展和后端绝不进入暂存区。
        if any(Path(name).name != name or not name.lower().endswith(".dll")
               or name.lower().startswith(("ggml", "llama", "ocloc")) for name in runtime):
            raise ValueError("Unexpected runtime entry in baseline manifest")
        names = [prefix + name for name in runtime]
        names += [name for name in archive.namelist() if name.startswith(prefix + "licenses/") and not name.endswith("/")]
        for name in names:
            relative = Path(name[len(prefix):])
            if ".." in relative.parts:
                raise ValueError("Invalid baseline runtime path")
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))
    (stage / "ggml-sycl.dll").write_bytes(cleaned)
    manifest = {"backend": "sycl", "module": "ggml-sycl.dll", "llama_cpp_commit": llama_revision(),
                "patches": patches, "bundled_runtime": runtime, "configuration": WINDOWS_SYCL_PROFILE,
                "toolchain": source["toolchain"], "ocloc": source["ocloc"], "ir_stripping": records,
                "fingerprint": fingerprint(WINDOWS_SYCL_PROFILE, source["toolchain"], source["ocloc"]),
                "aot_audit": audit(cleaned), "recovery": source}
    validate_module(manifest, cleaned)
    (stage / "sycl-build.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def main():
    """接收已校验的 CI 产物位置，输出可供普通 wheel 构建复核的暂存清单。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", type=Path, required=True)
    parser.add_argument("--runtime-wheel", type=Path, required=True)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    manifest = recover(args.module, args.runtime_wheel, args.stage, args.run_id)
    print(json.dumps({"reused_run": manifest["recovery"]["run_id"], "native_images": manifest["aot_audit"]["image_count"],
                      "cleared_ir_sections": len(manifest["ir_stripping"])}))


if __name__ == "__main__":
    main()
