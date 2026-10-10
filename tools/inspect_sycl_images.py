"""只读解析 Windows SYCL offload 描述符，区分项目内核和编译器设备函数库。"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct


def inspect(data: bytes) -> list[dict]:
    """按 Intel compiler.hpp 的 v3 描述符读取目标、映像范围和注册内核名。"""
    import pefile
    binary = pefile.PE(data=data)
    base = binary.OPTIONAL_HEADER.ImageBase

    def offset(address):
        """将 PE 虚拟地址映射到文件偏移，禁止读取文件外的指针。"""
        value = binary.get_offset_from_rva(address - base)
        if not 0 <= value < len(data):
            raise ValueError("Image pointer outside PE")
        return value

    def string(address):
        """读取描述符中的有限长度字符串，拒绝把随机数据误认为 offload 记录。"""
        start = offset(address)
        end = data.index(b"\0", start, min(start + 8192, len(data)))
        return data[start:end].decode("ascii")

    records = []
    for match in re.finditer(rb"\x03\x00\x04[\x00-\x04]\x00\x00\x00\x00", data):
        position = match.start()
        try:
            pointers = struct.unpack_from("<9Q", data, position + 8)
            target = string(pointers[0])
            if not target.startswith(("spir", "native_cpu")):
                continue
            start = offset(pointers[3])
            end = offset(pointers[4] - 1) + 1
            if end <= start:
                continue
            entry_start, entry_end = pointers[5:7]
            if entry_end < entry_start or (entry_end - entry_start) % 32 or entry_end - entry_start > 32 * 100000:
                continue
            names = []
            for address in range(entry_start, entry_end, 32):
                at = offset(address)
                names.append(string(struct.unpack_from("<Q", data, at + 8)[0]))
            image = data[start:end]
            records.append({"descriptor_offset": position, "target": target, "declared_format": data[position + 3],
                "offset": start, "bytes": end - start, "magic": image[:4].hex(),
                "sha256": hashlib.sha256(image).hexdigest(), "entries": names})
        except (ValueError, UnicodeDecodeError, struct.error, pefile.PEFormatError):
            continue
    binary.close()
    return records


def main():
    """保存全部映像描述符证据；本工具只检查来源，不替代 AOT 合包审计。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("module", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    records = inspect(args.module.read_bytes())
    args.output.write_text(json.dumps(records, indent=2))
    print(json.dumps({"images": len(records), "with_kernels": sum(bool(item["entries"]) for item in records),
                      "without_kernels": [item for item in records if not item["entries"]]}, indent=2))


if __name__ == "__main__":
    main()
