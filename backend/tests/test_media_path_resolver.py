"""媒体路径解析回归（2026-10-18 用户实锤）：

「反推打标失败了，仅支持本机留存图片，但是我是在生成内容里面要求的，那本就应该在本机内的」。

根因：媒体产出 2026-10-17 按类型迁入 `<仓库>/pictures|videos|audios/`，老索引仍记
`<仓库>/<name>`；`rag_store.local_path_of` 有双回退，但 `thumbnail_store.local_path_of_url`
（打标 / 自动描述 / 补缩略图三点共用）只认精确路径 ⇒ 200/203 条老记录假报「远程图」。

本文件锁两件事：
1. 解析器必须带 `pictures|videos|audios` 双回退（旧的严格行为只对真正缺失的图返回 None）；
2. 打标端点对「迁移后形态」的记录必须能取到字节（不再 400）。
"""
from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import quote

from app.services import thumbnail_store


def _local_view_url(path) -> str:
    return f"http://127.0.0.1:8010/api/comfyui/local-view?path={quote(str(path))}"


def test_resolver_falls_back_to_kind_dirs(tmp_path):
    """老形态 URL（<仓库>/<name>）⇒ 解析到 <仓库>/pictures/<name>。"""
    repo = tmp_path / "Anima" / "金狮"
    (repo / "pictures").mkdir(parents=True)
    actual = repo / "pictures" / "a.png"
    actual.write_bytes(b"png")

    resolved = thumbnail_store.local_path_of_url(_local_view_url(repo / "a.png"))
    assert resolved == actual

    # 精确命中优先（迁移后写入的新记录）
    nested = tmp_path / "b.png"
    nested.write_bytes(b"png")
    assert thumbnail_store.local_path_of_url(_local_view_url(nested)) == nested


def test_resolver_returns_none_for_missing_and_remote(tmp_path):
    assert thumbnail_store.local_path_of_url(_local_view_url(tmp_path / "nope.png")) is None
    assert thumbnail_store.local_path_of_url("https://example.com/a.png") is None
    assert thumbnail_store.local_path_of_url("") is None


def test_tag_generation_accepts_migrated_media_path(tmp_path, monkeypatch):
    """打标端点：记录是老形态 URL、文件在 pictures/ ⇒ 正常打标（不再 400 假报远程图）。"""
    from app.routers import rag as rag_router
    from app.services import tagger_registry, wd14_tagger

    repo = tmp_path / "repo"
    (repo / "pictures").mkdir(parents=True)
    actual = repo / "pictures" / "x.png"
    actual.write_bytes(b"png")

    item = {"id": "gen-x", "repo_id": "r1", "image_url": _local_view_url(repo / "x.png"), "tags": ["old"]}
    seen: list = []

    monkeypatch.setattr(rag_router.rag_store, "list_generations", lambda *a, **k: [item])
    monkeypatch.setattr(tagger_registry, "resolve",
                        lambda model: SimpleNamespace(id="wd14", name="WD14", dir=tmp_path))
    monkeypatch.setattr(wd14_tagger, "model_available", lambda *a, **k: True)
    monkeypatch.setattr(tagger_registry, "predict",
                        lambda model, path: seen.append(path) or [("1girl", 0.9)])
    monkeypatch.setattr(rag_router.rag_store, "add_tag_snapshot",
                        lambda *a, **k: {"id": "s1", "tags": ["1girl"]})

    out = rag_router.tag_generation(rag_router.TaggerTagRequest(id="gen-x", repo_id="r1", model=""))
    assert out["tags"] == ["1girl"]
    assert seen == [actual]          # 取到的是迁移后真实文件，而非报错
