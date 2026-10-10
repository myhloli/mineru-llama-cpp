"""读取 Windows 原生产物的 PE 架构，防止跨架构 DLL 混入发布包。"""
import struct


def pe_machine(data: bytes) -> int:
    """检查 DOS/PE 文件头并返回 COFF Machine，不执行待审计二进制。"""
    if len(data) < 64 or data[:2] != b"MZ":
        raise ValueError("Missing DOS header in Windows native binary")
    offset = struct.unpack_from("<I", data, 60)[0]
    if offset + 6 > len(data) or data[offset:offset + 4] != b"PE\0\0":
        raise ValueError("Missing PE header in Windows native binary")
    return struct.unpack_from("<H", data, offset + 4)[0]
