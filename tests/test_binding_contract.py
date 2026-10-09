"""验证已安装 Limited API 扩展的失败路径与对象边界，无需模型。"""
import gc
import pytest
from mineru_llama_cpp._mineru_llama_cpp import _EngineCore, _StreamIterator


@pytest.mark.parametrize("method", ["generate", "generate_stream"])
def test_uninitialized_core_rejects_calls(method):
    """未初始化实例应返回 Python 异常，不能访问空原生指针。"""
    core = _EngineCore.__new__(_EngineCore)
    with pytest.raises(RuntimeError, match="not initialized"):
        getattr(core, method)(body="{}")
    with pytest.raises(RuntimeError, match="not initialized"):
        _ = core.eos_token_str
    del core
    gc.collect()


def test_failed_constructor_is_safe_to_collect():
    """参数转换失败的部分构造对象能够反复回收。"""
    for _ in range(50):
        with pytest.raises(TypeError):
            _EngineCore(object(), "projector", 1, 0, 1, 0, 1)
    gc.collect()


def test_stream_cannot_be_created_without_reader():
    """只有生成接口可以创建带 reader 的流式实例。"""
    with pytest.raises(TypeError, match="cannot be constructed"):
        _StreamIterator()


def test_supports_int_conversion_and_float_rejection():
    """保留旧绑定的 __int__ 转换，同时拒绝隐式浮点截断和整数溢出。"""
    class IntLike:
        # 通过 __int__ 提供整数，故意不实现 __index__。
        def __int__(self):
            return 256

    with pytest.raises(RuntimeError, match="load_model failed"):
        _EngineCore("/nonexistent/mineru-model.gguf", "missing-projector", IntLike(), 0, 1, 0, 1)
    for value in (1.5, 2**80, "256"):
        with pytest.raises(TypeError):
            _EngineCore("missing", "missing", value, 0, 1, 0, 1)
