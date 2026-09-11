"""M1.3 受控下载安全链测试：候选校验、域名策略、provenance、data URI 豁免。"""
import base64

import pytest

from app.services import image_store, web_material_candidates

# 1x1 PNG（合法魔数）
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _fake_urlopen(data: bytes, history: list | None = None):
    class _Resp:
        def read(self, max_bytes: int = -1):  # noqa: N802
            if history is not None:
                history.append(max_bytes)
            return data

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _open(url, timeout=30):
        return _Resp()

    return _open


def test_候选注册表_登记查询淘汰(monkeypatch):
    web_material_candidates._CANDIDATES.clear()
    web_material_candidates.register_candidates(
        [{"full_url": "https://a.example/1.png", "source_url": "https://a.example/page"}],
        query="女仆装", provider="bing-images",
    )
    assert web_material_candidates.is_candidate("https://a.example/1.png")
    meta = web_material_candidates.candidate_meta("https://a.example/1.png")
    assert meta["query"] == "女仆装"
    assert meta["provider"] == "bing-images"
    assert meta["source_url"] == "https://a.example/page"
    # 未登记 / 空 URL
    assert not web_material_candidates.is_candidate("https://a.example/2.png")
    assert not web_material_candidates.is_candidate("")
    # TTL 过期后不再是候选
    web_material_candidates._CANDIDATES["https://a.example/1.png"]["registered_at"] -= (
        web_material_candidates._CANDIDATE_TTL_SECONDS + 1
    )
    assert not web_material_candidates.is_candidate("https://a.example/1.png")
    web_material_candidates._CANDIDATES.clear()


def test_save_web_material_未登记url被拒(monkeypatch, tmp_path):
    """受控核心：不是搜索候选的 http(s) URL 一律拒绝，不接受任意 URL 落盘。"""
    monkeypatch.setattr(image_store, "_from_src", lambda *a, **k: (_PNG, "png"))
    with pytest.raises(Exception) as ei:
        image_store.save_web_material(str(tmp_path), "https://evil.example/x.png")
    assert "候选列表" in str(ei.value)
    assert not list(tmp_path.glob("*.png"))


def test_save_web_material_候选url可保存且带provenance(monkeypatch, tmp_path):
    web_material_candidates._CANDIDATES.clear()
    web_material_candidates.register_candidates(
        [{"full_url": "https://img.example/1.png", "source_url": "https://img.example/page"}],
        query="女仆装", provider="bing-images",
    )
    monkeypatch.setattr(
        image_store, "_from_src",
        lambda *a, **k: (_PNG, "png"),
    )
    res = image_store.save_web_material(str(tmp_path), "https://img.example/1.png")
    assert res["filename"].endswith(".png")
    assert res["query"] == "女仆装"
    # provenance 落盘
    prov = image_store._load_provenance(image_store.web_materials_dir(str(tmp_path)))
    assert res["filename"] in prov
    assert prov[res["filename"]]["source_url"] == "https://img.example/page"
    assert prov[res["filename"]]["detected_ext"] == "png"
    assert prov[res["filename"]]["size"] == len(_PNG)
    # list 带 provenance
    listed = image_store.list_web_materials(str(tmp_path))
    assert listed[0]["query"] == "女仆装"
    assert listed[0]["source_url"] == "https://img.example/page"
    web_material_candidates._CANDIDATES.clear()


def test_save_web_material_http域名白名单(monkeypatch, tmp_path):
    web_material_candidates._CANDIDATES.clear()
    web_material_candidates.register_candidates([{"full_url": "http://img.example/1.png"}])
    monkeypatch.setattr(image_store, "_from_src", lambda *a, **k: (_PNG, "png"))
    # 默认：http 明文拒绝（未配置白名单）
    with pytest.raises(Exception) as ei:
        image_store.save_web_material(str(tmp_path), "http://img.example/1.png")
    assert "http" in str(ei.value).lower()
    # 配置白名单后放行
    monkeypatch.setenv("WEB_MATERIAL_ALLOWED_DOMAINS", "img.example")
    res = image_store.save_web_material(str(tmp_path), "http://img.example/1.png")
    assert res["filename"].endswith(".png")
    # 白名单外域名仍拒绝（先登记候选，确保是域名策略拦截而非候选校验）
    web_material_candidates.register_candidates([{"full_url": "http://img.example/2.png"}])
    monkeypatch.setenv("WEB_MATERIAL_ALLOWED_DOMAINS", "other.example")
    with pytest.raises(Exception) as ei2:
        image_store.save_web_material(str(tmp_path), "http://img.example/2.png")
    assert "白名单" in str(ei2.value)
    web_material_candidates._CANDIDATES.clear()


def test_save_web_material_dataURI豁免候选校验(monkeypatch, tmp_path):
    """data URI（画布拖放本地文件）不要求搜索候选，但仍走魔数校验。"""
    monkeypatch.setattr(image_store, "_from_src", lambda *a, **k: (_PNG, "png"))
    uri = "data:image/png;base64," + base64.b64encode(_PNG).decode()
    res = image_store.save_web_material(str(tmp_path), uri, "", "本地图.png")
    assert res["filename"].endswith(".png")
    assert res["title"] == "本地图.png"
    # 非图片字节仍被魔数拒绝
    monkeypatch.setattr(image_store, "_from_src", lambda *a, **k: (b"not-an-image", "png"))
    with pytest.raises(Exception) as ei:
        image_store.save_web_material(str(tmp_path), uri, "", "坏.png")
    assert "魔数" in str(ei.value) or "格式" in str(ei.value)


def test_save_web_material_私网url被拒(monkeypatch, tmp_path):
    """SSRF：私网 URL 即使登记候选也被 validate_media_url 拦截（在下载前）。"""
    web_material_candidates._CANDIDATES.clear()
    web_material_candidates.register_candidates([{"full_url": "http://169.254.169.254/latest/meta-data"}])
    monkeypatch.setattr(image_store, "_from_src", lambda *a, **k: (_PNG, "png"))
    with pytest.raises(Exception) as ei:
        image_store.save_web_material(str(tmp_path), "http://169.254.169.254/latest/meta-data")
    assert "私网" in str(ei.value) or "拒绝" in str(ei.value)
    web_material_candidates._CANDIDATES.clear()


def test_灵感卡图片_thread快照可保存(monkeypatch, tmp_path):
    """重启后候选列表丢失：src 若在本会话快照灵感卡 images 里，仍可保存。"""
    web_material_candidates._CANDIDATES.clear()
    # 快照：灵感卡含该图
    from app.services import chat_snapshot
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    chat_snapshot.save("thread-x", [{
        "id": "card-1", "role": "assistant", "text": "", "inspiration": {
            "title": "t", "content": "c", "sources": [], "selected": [],
            "images": [{"full_url": "https://img.example/old.png", "source_url": "https://img.example/page"}],
        },
    }])
    monkeypatch.setattr(image_store, "_from_src", lambda *a, **k: (_PNG, "png"))
    monkeypatch.setenv("WEB_MATERIAL_ALLOWED_DOMAINS", "img.example")
    res = image_store.save_web_material(
        str(tmp_path), "https://img.example/old.png", thread_id="thread-x",
    )
    assert res["filename"].endswith(".png")
    assert res["query"] == ""
    web_material_candidates._CANDIDATES.clear()
