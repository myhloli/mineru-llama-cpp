"""清理 OCLOC ESIMD 遗留的备用 IR，保留 PE 地址、原生 ELF 代码及兼容 note。"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
from audit_sycl_aot import elf_sections, intel_product_configs, audit


def strip(data: bytes) -> tuple[bytes, list[dict]]:
    """仅删除已带 ARL-H 原生代码的 ELF 的 SPIR-V 内容，不改变映像长度或其他节。"""
    result = bytearray(data)
    records = []
    cursor = 0
    while (offset := data.find(b"\x7fELF", cursor)) >= 0:
        cursor = offset + 4
        try:
            sections, _ = elf_sections(data, offset)
        except (ValueError, struct.error, UnicodeDecodeError):
            continue
        ir = [(index, content) for index, (name, kind, content) in enumerate(sections)
              if content and (name.startswith((".spv", ".spirv")) or kind == 0xff000009)]
        if not ir:
            continue
        text = b"".join(content for name, _, content in sections if name.startswith(".text"))
        configs = [value for name, _, content in sections if name == ".note.intelgt.compat"
                   for value in intel_product_configs(content)]
        if not text or not any(name == ".ze_info" for name, _, _ in sections) or not configs or any(
                (value >> 22, (value >> 14) & 255) != (12, 74) for value in configs):
            raise ValueError("Refusing to strip IR from an image without native ARL-H code")
        table = struct.unpack_from("<Q", data, offset + 40)[0]
        native_hash = hashlib.sha256(text).hexdigest()
        for index, content in ir:
            header = offset + table + index * 64
            section_offset, size = struct.unpack_from("<QQ", data, header + 24)
            start = offset + section_offset
            # 清空 payload 并把节长度设为零；所有 BinaryStart/End 和其他节偏移保持不变。
            result[start:start + size] = b"\0" * size
            struct.pack_into("<Q", result, header + 32, 0)
            records.append({"elf_offset": offset, "section": sections[index][0], "removed_bytes": size,
                            "ir_sha256": hashlib.sha256(content).hexdigest(), "native_text_sha256": native_hash})
        after = elf_sections(bytes(result), offset)[0]
        if hashlib.sha256(b"".join(content for name, _, content in after if name.startswith(".text"))).hexdigest() != native_hash:
            raise ValueError("Native code changed while stripping IR")
    final = bytes(result)
    audit(final)  # 不允许把只有 IR 的包或未清理的备用映像转换成可分发 AOT 包。
    return final, records


def main():
    """写入单独的清理后 MODULE 并保存前后证据，不覆盖原始编译产物。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("module", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    original = args.module.read_bytes()
    cleaned, records = strip(original)
    args.output.write_bytes(cleaned)
    report = {"original_sha256": hashlib.sha256(original).hexdigest(), "module_sha256": hashlib.sha256(cleaned).hexdigest(),
              "bytes_unchanged": len(original) == len(cleaned), "sections": records, "aot_audit": audit(cleaned)}
    args.report.write_text(json.dumps(report, indent=2))
    print(json.dumps({"cleared_ir_sections": len(records), "native_images": report["aot_audit"]["image_count"],
                      "module_sha256": report["module_sha256"]}))


if __name__ == "__main__":
    main()
