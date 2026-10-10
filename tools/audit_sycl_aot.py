"""直接审计 SYCL MODULE 内嵌的 Intel Zebin ELF，确认 ARL-H 代码及备用 IR 排除。"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import struct


def elf_sections(data: bytes, offset: int) -> tuple[list[tuple[str, int, bytes]], int]:
    """解析嵌入的 ELF64 节表，严格限制范围，兼容外层 Windows PE 容器。"""
    header = struct.unpack_from("<16sHHIQQQIHHHHHH", data, offset)
    if header[0][:6] != b"\x7fELF\x02\x01":
        raise ValueError("Expected little-endian ELF64")
    table, stride, count, strings = header[6], header[11], header[12], header[13]
    if stride != 64 or not 0 < count < 16384 or strings >= count or offset + table + stride * count > len(data):
        raise ValueError("Invalid embedded ELF section table")
    raw = [struct.unpack_from("<IIQQQQIIQQ", data, offset + table + index * stride) for index in range(count)]
    extent = max(64, table + stride * count, *(section[4] + section[5] for section in raw if section[1] != 8))
    if offset + extent > len(data):
        raise ValueError("Truncated embedded ELF")
    names_section = raw[strings]
    names = data[offset + names_section[4]:offset + names_section[4] + names_section[5]]
    sections = []
    for section in raw:
        name_offset = section[0]
        if name_offset >= len(names):
            raise ValueError("Invalid ELF section name")
        end = names.find(b"\0", name_offset)
        if end < 0:
            raise ValueError("Unterminated ELF section name")
        name = names[name_offset:end].decode("ascii")
        content = b"" if section[1] == 8 else data[offset + section[4]:offset + section[4] + section[5]]
        sections.append((name, section[1], content))
    return sections, extent


def intel_product_configs(data: bytes) -> list[int]:
    """读取 IntelGT 兼容 note 的 productConfig；位域定义来自 Intel compute-runtime。"""
    values = []
    offset = 0
    while offset + 12 <= len(data):
        name_size, value_size, kind = struct.unpack_from("<III", data, offset)
        offset += 12
        name = data[offset:offset + name_size].rstrip(b"\0")
        offset += (name_size + 3) & ~3
        if offset + value_size > len(data):
            raise ValueError("Truncated IntelGT compatibility note")
        value = data[offset:offset + value_size]
        offset += (value_size + 3) & ~3
        if name == b"IntelGT" and kind == 6 and value_size == 4:
            values.append(struct.unpack("<I", value)[0])
    return values


def audit(data: bytes) -> dict:
    """检查每个原生映像的 ARL-H IP、机器码和 IR 节，不依赖 GPU 或驱动。"""
    cursor = 0
    while True:
        offset = data.find(b"\x03\x02\x23\x07", cursor)
        if offset < 0:
            break
        cursor = offset + 4
        if offset + 24 > len(data):
            continue
        _, version, _, bound, schema, first = struct.unpack_from("<6I", data, offset)
        # 同时识别独立 SPIR-V 映像，不能让 ELF 之外的通用 JIT 备用映像漏过检查。
        if (version & 0xff0000ff == 0 and 0x00010000 <= version <= 0x00010600
                and 0 < bound < (1 << 30) and schema == 0
                and first & 65535 in (10, 17) and 2 <= first >> 16 <= 64):
            raise ValueError(f"MODULE retains standalone fallback SPIR-V at {offset}")
    records = []
    cursor = 0
    while True:
        offset = data.find(b"\x7fELF", cursor)
        if offset < 0:
            break
        cursor = offset + 4
        try:
            sections, extent = elf_sections(data, offset)
        except (ValueError, struct.error, UnicodeDecodeError):
            continue
        names = {name for name, _, _ in sections}
        if any(content and (name.startswith((".spv", ".spirv")) or kind == 0xff000009)
               for name, kind, content in sections):
            raise ValueError(f"Embedded GPU image at {offset} retains fallback SPIR-V")
        if ".ze_info" not in names:
            continue
        if not any(name.startswith(".text") and content for name, _, content in sections):
            raise ValueError("Zebin image contains no native machine code")
        configs = []
        for name, _, content in sections:
            if name == ".note.intelgt.compat":
                configs += intel_product_configs(content)
        if not configs or any((value >> 22, (value >> 14) & 0xff) != (12, 74) for value in configs):
            raise ValueError(f"GPU image does not identify ARL-H (12.74): {configs}")
        records.append({"offset": offset, "bytes": extent,
                        "sha256": hashlib.sha256(data[offset:offset + extent]).hexdigest(),
                        "ip_versions": [f"{value >> 22}.{(value >> 14) & 0xff}.{value & 0x3f}" for value in configs]})
    if not records:
        raise ValueError("No embedded native ARL-H GPU images found")
    return {"aot_target": "arl-h", "image_count": len(records), "ir_excluded": True,
            "module_sha256": hashlib.sha256(data).hexdigest(), "images": records}


def main() -> None:
    """将 MODULE 原生代码证据保存为 JSON，供 CI 和合包校验引用。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("module", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit(args.module.read_bytes())
    if args.output:
        args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps({key: value for key, value in report.items() if key != "images"}, indent=2))


if __name__ == "__main__":
    main()
