"""view_urls 纯构造测试：wire 形态＝根相对，origin 只在 fetchable 里出现。零依赖。"""
from urllib.parse import parse_qs, urlparse

from app.config import BACKEND_BASE_URL
from app.services import view_urls


def test_local_view_is_root_relative_and_encodes_path():
    url = view_urls.local_view(r"D:\out\a b.png")
    assert url.startswith("/api/comfyui/local-view?path=")
    assert "://" not in url                       # origin 进 wire 就会被回写进存档
    q = parse_qs(urlparse(url).query)
    assert q["path"] == [r"D:\out\a b.png"]        # 编码后能原样解回


def test_remote_view_is_root_relative_and_carries_all_params():
    url = view_urls.remote_view(filename="x.png", type="output",
                                subfolder="sub", comfyui_url="http://c:8188")
    assert url.startswith("/api/comfyui/view?")
    assert urlparse(url).netloc == ""
    q = parse_qs(urlparse(url).query)
    assert q["filename"] == ["x.png"]
    assert q["type"] == ["output"]
    assert q["subfolder"] == ["sub"]
    assert q["url"] == ["http://c:8188"]


def test_remote_view_defaults():
    url = view_urls.remote_view(filename="only.png")
    q = parse_qs(urlparse(url).query)
    assert q["filename"] == ["only.png"]
    assert q["type"] == ["output"]                 # 默认 output


def test_fetchable_only_prefixes_root_relative():
    assert view_urls.fetchable("/api/comfyui/local-view?path=x") == \
        f"{BACKEND_BASE_URL}/api/comfyui/local-view?path=x"
    assert view_urls.fetchable("//evil/api") == "//evil/api"        # 协议相对不拼：主机不由我们给
    assert view_urls.fetchable("https://pub/a.png") == "https://pub/a.png"
    assert view_urls.fetchable(r"C:\out\a.png") == r"C:\out\a.png"  # 本地路径原样


def test_fetchable_按调用期origin解析(monkeypatch):
    """origin 必须调用期读，不是 import 期快照。

    端口来自 LAF_RUNTIME_PORT（壳协商/隔离实例各不相同）；import 期绑定会让换端口的进程
    去敲一个没人听的端口，且测试无法在不改导入顺序的前提下注入 origin。
    """
    from app import config

    monkeypatch.setattr(config, "BACKEND_BASE_URL", "http://127.0.0.1:9999")
    assert view_urls.fetchable("/api/comfyui/local-view?path=x") == \
        "http://127.0.0.1:9999/api/comfyui/local-view?path=x"


def test_normalize_wire_collapses_legacy_loopback_origin():
    legacy = "http://127.0.0.1:8010/api/comfyui/local-view?path=D%3A%5Ca.png"
    assert view_urls.normalize_wire(legacy) == "/api/comfyui/local-view?path=D%3A%5Ca.png"
    # 端口由壳协商，归一不能只认 8010；localhost / IPv6 回环 / view 端点同口径
    for text, want in [
        ("http://127.0.0.1:8013/api/comfyui/view?filename=a.png", "/api/comfyui/view?filename=a.png"),
        ("http://localhost:8010/api/comfyui/local-view?path=x", "/api/comfyui/local-view?path=x"),
        ("http://[::1]:8010/api/comfyui/local-view?path=x", "/api/comfyui/local-view?path=x"),
        ("https://127.0.0.1:8010/api/comfyui/view?filename=a.png", "/api/comfyui/view?filename=a.png"),
    ]:
        assert view_urls.normalize_wire(text) == want


def test_normalize_wire_keeps_non_view_text():
    # 外部主机即使带同样的端点路径也必须原样留着，交给 url_guard 判假
    outside = "http://evil.com/api/comfyui/local-view?path=x"
    assert view_urls.normalize_wire(outside) == outside
    # 不是取图端点的本机地址不动（ComfyUI 自身地址、健康检查等）
    assert view_urls.normalize_wire("http://127.0.0.1:8188/view?filename=a.png") == \
        "http://127.0.0.1:8188/view?filename=a.png"
    # 正文里的多处出现逐条收敛，前后文字不受影响
    md = ("看图 http://127.0.0.1:8010/api/comfyui/view?filename=a.png 与 "
          "![b](http://localhost:8010/api/comfyui/local-view?path=b) 结束")
    assert view_urls.normalize_wire(md) == (
        "看图 /api/comfyui/view?filename=a.png 与 "
        "![b](/api/comfyui/local-view?path=b) 结束")


def test_normalize_wire_bytes_matches_text_variant():
    text = "前 http://127.0.0.1:8010/api/comfyui/view?filename=a.png 后"
    assert view_urls.normalize_wire_bytes(
        text.encode("utf-8")).decode("utf-8") == view_urls.normalize_wire(text)


def test_backend_base_url_follows_runtime_port(monkeypatch):
    """自取图的 origin 必须用实际监听端口；钉死 8010 会让壳换端口后取不到图。"""
    from app import config
    for raw, want in [("8013", 8013), ("", 8010), (" 8010 ", 8010),
                      ("not-a-port", 8010), ("0", 8010), ("70000", 8010)]:
        monkeypatch.setenv("LAF_RUNTIME_PORT", raw)
        assert config._backend_port() == want
