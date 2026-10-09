"""直接编译并验证绑定层的 UTF-8 解码函数，无需加载 GGUF 模型。"""

import importlib.util
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def utf8_decoder(tmp_path_factory):
    """在临时目录构建测试扩展，复用生产头文件并保留编译失败的诊断。"""
    from setuptools import Distribution, Extension
    from setuptools.command.build_ext import build_ext

    root = Path(__file__).resolve().parents[1]
    output = tmp_path_factory.mktemp("utf8_decoder")
    extension = Extension(
        "_utf8_decode_test",
        [str(root / "tests/native/utf8_decode_binding.cpp")],
        include_dirs=[str(root / "src/cpp")],
        define_macros=[("Py_LIMITED_API", "0x030A0000")],
        py_limited_api=True,
        extra_compile_args=["/utf-8", "/std:c++17"] if sys.platform == "win32" else ["-std=c++17"],
        language="c++",
    )
    command = build_ext(Distribution({"ext_modules": [extension]}))
    command.build_lib = str(output)
    command.build_temp = str(output / "temp")
    command.ensure_finalized()
    command.run()
    spec = importlib.util.spec_from_file_location(
        "_utf8_decode_test", command.get_ext_fullpath("_utf8_decode_test")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.decode


@pytest.mark.parametrize("finish_reason", ["stop", "length"])
@pytest.mark.parametrize(
    "text",
    ["", "ASCII", "中文é😀", "<|box_start|>中文<|box_end|>", "A\0中文B", "中" * 6000],
    ids=["empty", "ascii", "unicode", "structured", "nul", "long"],
)
def test_complete_content_is_preserved(utf8_decoder, text, finish_reason):
    """完整文本及结构标记、零字节和超过 16 KB 的内容必须逐字保留。"""
    assert utf8_decoder(text.encode("utf-8"), finish_reason) == text


_PARTIAL_CHARACTERS = [
    text.encode("utf-8")[:cut]
    for text in ["é", "中", "😀"]
    for cut in range(1, len(text.encode("utf-8")))
]


@pytest.mark.parametrize("partial", _PARTIAL_CHARACTERS)
@pytest.mark.parametrize("prefix", ["", "<|box_start|>中文", "A\0B", "中" * 6000],
                         ids=["empty", "structured", "nul", "long"])
def test_length_limit_discards_only_incomplete_tail(utf8_decoder, partial, prefix):
    """长度停止只舍弃二、三、四字节字符的未完成尾部，不能修改完整前缀。"""
    content = prefix.encode("utf-8") + partial
    assert utf8_decoder(content, "length") == prefix


@pytest.mark.parametrize("partial", _PARTIAL_CHARACTERS)
def test_normal_stop_rejects_incomplete_tail(utf8_decoder, partial):
    """自然停止不掩盖未完成字符，继续抛出原有的严格解码异常。"""
    with pytest.raises(UnicodeDecodeError):
        utf8_decoder(b"prefix:" + partial, "stop")


@pytest.mark.parametrize("finish_reason", ["stop", "length"])
@pytest.mark.parametrize(
    "invalid",
    [b"A\xffB", b"A\xe4XB", b"A\x80", b"A\xc0\xaf", b"A\xed\xa0\x80", b"A\xf4\x90\x80\x80"],
)
def test_invalid_utf8_is_not_silently_discarded(utf8_decoder, invalid, finish_reason):
    """非法起始字节、续字节、过长编码及非法码点都必须报错。"""
    with pytest.raises(UnicodeDecodeError):
        utf8_decoder(invalid, finish_reason)
