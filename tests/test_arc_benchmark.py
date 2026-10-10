"""验收工具必须允许细微差异，同时拒绝漏块、结构退化、乱码和无效数值。"""
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from benchmark_arc_sycl import output_differences, peak_rss


def test_small_numeric_and_wording_differences_require_review():
    """小幅精度和措辞差异不按 JSON 字节相等处理，也不能冒充人工质量验收。"""
    before = [{"type": "text", "bbox": [0, 0.1, 1, 0.9], "content": "This is a test."}]
    after = [{"type": "text", "bbox": [0.0, 0.101, 1.0, 0.9], "content": "This is one test."}]
    result = output_differences(before, after)
    assert result["reject"] == [] and len(result["review"]) == 2


@pytest.mark.parametrize("after", [
    [], [{"content": "text"}], [{"type": "text", "content": ""}],
    [{"type": "text", "content": "broken\ufffd"}],
    [{"type": "text", "content": "text", "value": float("inf")}],
    [{"type": "text", "content": ["wrong type"]}],
])
def test_degraded_outputs_are_rejected(after):
    """丢块、缺字段、内容消失、乱码、非有限值和字段类型变化都必须明确拒绝。"""
    assert output_differences([{"type": "text", "content": "text"}], after)["reject"]


def test_peak_memory_readout_is_available():
    """验证使用本机内核峰值内存接口，Windows CI 同样调用 PSAPI 路径。"""
    assert peak_rss() > 0
