"""纯工具函数测试：文件名清洗 + LLM 内容展平 + base_url 归一。"""
from app.services.pathnames import safe_seg
from app.services import llm


def test_safe_seg_非法字符换下划线():
    assert safe_seg("a b/..c.png") == "a_b_..c.png".strip("._")


def test_safe_seg_空值回退():
    assert safe_seg("") == "x"
    assert safe_seg("", "home") == "home"


def test_safe_seg_不剥离两端():
    # chat_snapshot 用法：strip=False，保留原始形态
    assert safe_seg("_x_", strip=False) == "_x_"
    assert safe_seg("", "home", strip=False) == "home"


def test_flatten_content_字符串原样():
    assert llm.flatten_content("hello") == "hello"
    assert llm.flatten_content("") == ""


def test_flatten_content_分段拼接():
    assert llm.flatten_content(["a", {"text": "b"}, {"nokey": 1}]) == "ab"


def test_normalize_base_url_补v1():
    assert llm.normalize_base_url("http://h:1") == "http://h:1/v1"
    assert llm.normalize_base_url("http://h:1/") == "http://h:1/v1"


def test_normalize_base_url_已含不重复():
    assert llm.normalize_base_url("http://h:1/v1") == "http://h:1/v1"
    assert llm.normalize_base_url("http://h/chat/completions") == "http://h/chat/completions"
