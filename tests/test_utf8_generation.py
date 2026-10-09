"""使用 MinerU Q8_0 词表的字节 token，确定性复现长度停止时的字符截断。"""

import pytest

from mineru_llama_cpp import InvalidRequestError, SamplingParams


class _ByteTokenSampling(SamplingParams):
    def __init__(self, token_id: int, n_predict: int = 1, grammar: str | None = None):
        """仅在回归测试中强制采样指定字节，保留默认 Unicode grammar。"""
        super().__init__(n_predict=n_predict, temperature=0.0, top_k=1, seed=42)
        self.token_id = token_id
        self.grammar = grammar

    def to_json_fields(self) -> dict:
        """通过现有请求组装路径注入测试采样配置，不扩展正式采样 API。"""
        fields = super().to_json_fields()
        fields["logit_bias"] = {str(self.token_id): 1000.0}
        if self.grammar is not None:
            fields["grammar"] = self.grammar
        return fields


_MESSAGES = [{"role": "user", "content": "输出中文。"}]


@pytest.mark.parametrize("token_id", [160, 3490])
def test_generate_matches_stream_at_partial_utf8_limit(engine, token_id):
    """中文字符只生成前一或两个字节时，同步接口返回合法空前缀及原始元数据。"""
    sampling = _ByteTokenSampling(token_id)
    result = engine.generate(_MESSAGES, sampling)
    chunks = list(engine.stream(_MESSAGES, sampling))
    final = chunks[-1]
    assert result.content == "".join(chunk.delta for chunk in chunks) == ""
    assert result.finish_reason == final.finish_reason == "length"
    assert result.tokens_predicted == final.tokens_predicted == 1
    assert result.tokens_evaluated == final.tokens_evaluated > 0
    assert result.timings.prompt_n == result.tokens_evaluated
    assert result.timings.predicted_n == result.tokens_predicted


@pytest.mark.parametrize("token_id", [160, 3490])
async def test_agenerate_matches_astream_at_partial_utf8_limit(engine, token_id):
    """异步生成与异步流式必须采用相同的字符边界和结束原因。"""
    sampling = _ByteTokenSampling(token_id)
    result = await engine.agenerate(_MESSAGES, sampling)
    chunks = [chunk async for chunk in engine.astream(_MESSAGES, sampling)]
    assert result.content == "".join(chunk.delta for chunk in chunks) == ""
    assert result.finish_reason == chunks[-1].finish_reason == "length"
    assert result.tokens_predicted == chunks[-1].tokens_predicted == 1
    assert result.tokens_evaluated == chunks[-1].tokens_evaluated > 0


def test_generate_preserves_complete_prefix_before_partial_character(engine):
    """强制先生成 A 再生成部分中文字符，确认完整前缀不会一并丢失。"""
    sampling = _ByteTokenSampling(160, n_predict=2, grammar='root ::= "A中"')
    result = engine.generate(_MESSAGES, sampling)
    chunks = list(engine.stream(_MESSAGES, sampling))
    assert result.content == "".join(chunk.delta for chunk in chunks) == "A"
    assert result.finish_reason == chunks[-1].finish_reason == "length"
    assert result.tokens_predicted == chunks[-1].tokens_predicted == 2


def test_engine_recovers_after_invalid_output_error(engine):
    """禁用 grammar 后的非法 FF 输出被上游解析器拒绝，引擎仍能服务后续请求。"""
    with pytest.raises(InvalidRequestError):
        engine.generate(_MESSAGES, _ByteTokenSampling(187, grammar=""))
    result = engine.generate(_MESSAGES, SamplingParams(temperature=0.0, top_k=1, n_predict=4))
    assert result.content
    assert result.content.encode("utf-8")


async def test_async_engine_recovers_after_invalid_output_error(engine):
    """异步非法输出异常应传回调用方，不能破坏后台生成线程或后续请求。"""
    with pytest.raises(InvalidRequestError):
        await engine.agenerate(_MESSAGES, _ByteTokenSampling(187, grammar=""))
    result = await engine.agenerate(
        _MESSAGES, SamplingParams(temperature=0.0, top_k=1, n_predict=4)
    )
    assert result.content
    assert result.content.encode("utf-8")
