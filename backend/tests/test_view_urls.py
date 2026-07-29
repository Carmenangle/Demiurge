"""view_urls 纯构造测试：本地/在线取图地址格式与编码。零依赖。"""
from urllib.parse import parse_qs, urlparse

from app.config import BACKEND_BASE_URL
from app.services import view_urls


def test_local_view_encodes_path():
    url = view_urls.local_view(r"D:\out\a b.png")
    assert url.startswith(f"{BACKEND_BASE_URL}/api/comfyui/local-view?path=")
    q = parse_qs(urlparse(url).query)
    assert q["path"] == [r"D:\out\a b.png"]         # 编码后能原样解回


def test_remote_view_carries_all_params():
    url = view_urls.remote_view(filename="x.png", type="output",
                                subfolder="sub", comfyui_url="http://c:8188")
    assert url.startswith(f"{BACKEND_BASE_URL}/api/comfyui/view?")
    q = parse_qs(urlparse(url).query)
    assert q["filename"] == ["x.png"]
    assert q["type"] == ["output"]
    assert q["subfolder"] == ["sub"]
    assert q["url"] == ["http://c:8188"]


def test_remote_view_defaults():
    url = view_urls.remote_view(filename="only.png")
    q = parse_qs(urlparse(url).query)
    assert q["filename"] == ["only.png"]
    assert q["type"] == ["output"]                  # 默认 output
