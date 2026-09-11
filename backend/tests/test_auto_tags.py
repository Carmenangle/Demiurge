"""纯标签提取测试：D站标签保留 + 元信息垃圾过滤 + 去重保序。

_auto_tags 是私有纯函数，接口即测试面——无需 Chroma/嵌入接口即可验证分类逻辑。
"""
from app.services.rag_store import _auto_tags


def test_D站标签按分隔符拆分保留():
    assert _auto_tags("", "1girl, solo, blue_sky") == ["1girl", "solo", "blue_sky"]


def test_标签在前提示词在后():
    # tags 先入，prompt 后入
    assert _auto_tags("masterpiece", "red dress") == ["red dress", "masterpiece"]


def test_过滤元信息键值():
    # "Model: xxx" 是元信息键 → 丢弃；真正的画面短语保留
    out = _auto_tags("Model: gpt-image-2, masterpiece", "red dress")
    assert "red dress" in out
    assert "masterpiece" in out
    assert not any("gpt-image-2" in t for t in out)


def test_过滤URL和HTTP动词():
    out = _auto_tags("GET /v1/foo, best quality", "")
    assert out == ["best quality"]


def test_去重保序():
    assert _auto_tags("a, a, b", "") == ["a", "b"]


def test_过滤纯数字尺寸():
    # "123x456" 纯数字尺寸被滤，"8k" 含字母保留
    out = _auto_tags("123x456, 8k", "")
    assert "123x456" not in out
    assert "8k" in out


def test_超长短语丢弃():
    long = "x" * 41
    assert long not in _auto_tags(long, "")
