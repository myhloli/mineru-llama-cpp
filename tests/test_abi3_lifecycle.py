"""用真实模型验证 Limited API 的 reader 所有权和兼容参数转换。"""
import gc
import sys

import pytest

from mineru_llama_cpp import SamplingParams
from conftest import MODEL, MMPROJ


def test_core_reader_retains_owner_until_destroyed(engine):
    """reader 消耗完成后仍持有引擎，只有销毁包装时才释放该引用。"""
    core = engine._core
    references = sys.getrefcount(core)
    body = engine._build_body([{"role": "user", "content": "hi"}], SamplingParams(n_predict=4), True)
    reader = core.generate_stream(body=body)
    assert sys.getrefcount(core) == references + 1
    chunks = list(reader)
    assert chunks[-1]["finish_reason"] in ("stop", "length")
    with pytest.raises(StopIteration):
        next(reader)
    del reader
    gc.collect()
    assert sys.getrefcount(core) == references


def test_failed_reinitialization_preserves_live_engine(engine):
    """重复原生初始化必须明确拒绝，原引擎仍能继续生成。"""
    with pytest.raises(RuntimeError, match="already initialized"):
        engine._core.__init__(MODEL, MMPROJ, 4096, 99, 1, 0, 1)
    assert engine.generate([{"role": "user", "content": "hi"}], SamplingParams(n_predict=4)).content


def test_low_level_body_accepts_mutable_utf8_bytes(engine):
    """保持旧 std::string 绑定接受 bytearray 的能力，并按快照复制请求。"""
    body = engine._build_body([{"role": "user", "content": "hi"}], SamplingParams(n_predict=4), False)
    output = engine._core.generate(body=bytearray(body.encode()))
    assert isinstance(output["content"], str)
    assert output["tokens_predicted"] > 0
