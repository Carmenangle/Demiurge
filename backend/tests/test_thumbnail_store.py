"""P0-2 缩略图派生层：thumbnail_store 单测 + index_generation/list_generations 集成。

锁四件事：
1. 生成/幂等/尺寸/格式：长边 512 JPEG，二次不重生（mtime 判定）；
2. 兜底语义：非本地图/非静态图/坏图一律静默回落（None/空串），不抛错；
3. 集成：index_generation 对本地图写 thumb_url（.thumbs/ 派生层），list_generations
   透出 thumbUrl；历史无 thumb_url 记录由读路径 thumb_url_if_ready 兜底发现；
4. 清理：删原图顺手删 thumb（remove_thumb），不留孤儿。
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

from PIL import Image

from app.services import rag_backend, rag_store, thumbnail_store, view_urls


def _make_png(path: Path, size: tuple[int, int] = (1024, 768)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (200, 60, 60)).save(path, format="PNG")
    return path


def _make_rgba_png(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", (600, 400), (0, 0, 0, 0))
    img.save(path, format="PNG")
    return path


def test_generates_thumb_within_edge_and_jpeg(tmp_path):
    src = _make_png(tmp_path / "000001.png")
    thumb = thumbnail_store.ensure_thumb(src)
    assert thumb is not None
    assert thumb == thumbnail_store.thumb_path_for(src)
    assert thumb.parent.name == ".thumbs"
    assert thumb.suffix == ".jpg"
    with Image.open(thumb) as img:
        assert max(img.size) <= 512
        assert img.format == "JPEG"


def test_ensure_thumb_is_idempotent(tmp_path):
    src = _make_png(tmp_path / "000001.png")
    first = thumbnail_store.ensure_thumb(src)
    assert first is not None
    before = first.stat().st_mtime_ns
    second = thumbnail_store.ensure_thumb(src)
    assert second == first
    assert second.stat().st_mtime_ns == before  # 未重生（mtime 不变）


def test_rgba_composites_on_white_not_black(tmp_path):
    src = _make_rgba_png(tmp_path / "000002.png")
    thumb = thumbnail_store.ensure_thumb(src)
    assert thumb is not None
    with Image.open(thumb) as img:
        assert img.mode == "RGB"
        # 全透明区域合成白底（中心像素应接近白，而非黑）
        assert img.getpixel((img.width // 2, img.height // 2)) == (255, 255, 255)


def test_thumb_url_for_skips_non_local_and_missing(tmp_path):
    assert thumbnail_store.thumb_url_for("") == ""
    assert thumbnail_store.thumb_url_for("https://cdn.example/x.png") == ""
    # local-view 但文件不存在 → 空串
    assert thumbnail_store.thumb_url_for(view_urls.local_view(str(tmp_path / "nope.png"))) == ""


def _is_thumb_url(url: str) -> bool:
    """URL 是否指向 .thumbs/ 派生缩略图（Windows 下分隔符被 quote，需解码判断）。"""
    decoded = unquote(url)
    return ".thumbs" in decoded and decoded.endswith("_thumb.jpg")


def test_thumb_url_for_local_image(tmp_path):
    src = _make_png(tmp_path / "000003.png")
    url = thumbnail_store.thumb_url_for(view_urls.local_view(str(src)))
    assert _is_thumb_url(url)
    assert thumbnail_store.thumb_path_for(src).is_file()


def test_thumb_url_if_ready_only_reports_existing(tmp_path):
    src = _make_png(tmp_path / "000004.png")
    local = view_urls.local_view(str(src))
    assert thumbnail_store.thumb_url_if_ready(local) == ""     # 未生成
    assert thumbnail_store.ensure_thumb(src) is not None
    assert thumbnail_store.thumb_url_if_ready(local) != ""    # 生成后可发现


def test_非图非视频扩展名跳过(tmp_path):
    src = tmp_path / "notes.txt"
    src.write_bytes(b"hi")
    assert thumbnail_store.ensure_thumb(src) is None
    assert thumbnail_store.thumb_url_for(view_urls.local_view(str(src))) == ""


def test_视频定位不到ffmpeg时回落占位(tmp_path, monkeypatch):
    """手机端 / 未装 ComfyUI：抽不了帧就保持占位卡，绝不抛错、绝不阻断入库。"""
    from app.services import audio_merge

    monkeypatch.setattr(audio_merge, "find_ffmpeg", lambda: None)
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"\x00")
    assert thumbnail_store.ensure_thumb(src) is None
    assert thumbnail_store.thumb_url_for(view_urls.local_view(str(src))) == ""


def test_视频抽帧后走同一条归一链(tmp_path, monkeypatch):
    """真机形状（2026-10-31）：864×1536 竖屏视频 ⇒ 封面 288×512，比例仍是 9:16。

    前端 `<img>` 量到 naturalWidth/naturalHeight 即得真实宽高比，瀑布流不再按写死的
    16:9 估高。
    """
    from PIL import Image

    source_frame = tmp_path / "frame_src.jpg"
    Image.new("RGB", (864, 1536), (200, 60, 60)).save(source_frame)

    def _fake_extract(video_path, dest):
        assert video_path.suffix == ".mp4"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(source_frame.read_bytes())
        return True

    monkeypatch.setattr(thumbnail_store, "extract_video_frame", _fake_extract)
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"\x00")

    thumb = thumbnail_store.ensure_thumb(src)
    assert thumb is not None and thumb.is_file()
    with Image.open(thumb) as opened:
        assert opened.size == (288, 512)
    assert not list(thumb.parent.glob("*_frame.tmp.jpg")), "抽帧临时片必须清掉"
    assert thumbnail_store.thumb_url_if_ready(view_urls.local_view(str(src))) != ""


def test_broken_image_silent_fallback(tmp_path):
    src = tmp_path / "000005.png"
    src.write_bytes(b"not an image at all")
    assert thumbnail_store.ensure_thumb(src) is None  # 不抛错，静默回落


def test_remove_thumb_cleans_derived_file(tmp_path):
    src = _make_png(tmp_path / "000006.png")
    thumb = thumbnail_store.ensure_thumb(src)
    assert thumb is not None and thumb.is_file()
    thumbnail_store.remove_thumb(src)
    assert not thumb.exists()


def test_index_generation_writes_thumb_url_for_local_image(monkeypatch, tmp_path):
    from tests.test_asset_search import MemoryStore

    store = MemoryStore()
    monkeypatch.setattr(rag_store, "_store", lambda *_args: store)
    src = _make_png(tmp_path / "repo" / "000001.png")

    rag_store.index_generation(
        "repo", rag_backend.EmbedConfig(), "anime woman", "red dress",
        view_urls.local_view(str(src)),
    )

    meta = next(iter(store.rows.values())).metadata
    assert meta["thumb_url"]
    assert _is_thumb_url(meta["thumb_url"])
    assert thumbnail_store.thumb_path_for(src).is_file()  # 派生文件确实落盘


def test_index_generation_remote_image_has_empty_thumb_url(monkeypatch):
    from tests.test_asset_search import MemoryStore

    store = MemoryStore()
    monkeypatch.setattr(rag_store, "_store", lambda *_args: store)

    rag_store.index_generation(
        "repo", rag_backend.EmbedConfig(), "p", image_url="https://cdn.example/x.png",
    )

    meta = next(iter(store.rows.values())).metadata
    assert meta["thumb_url"] == ""  # 远程图不生成，前端回落原图


def test_list_generations_falls_back_to_ready_thumb(monkeypatch, tmp_path):
    """历史记录无 thumb_url，但补生成过 thumb 文件 ⇒ 读路径兜底发现。"""
    from tests.test_asset_search import MemoryStore

    store = MemoryStore()
    monkeypatch.setattr(rag_store, "_store", lambda *_args: store)
    src = _make_png(tmp_path / "repo" / "000009.png")
    local = view_urls.local_view(str(src))

    rag_store.index_generation("repo", rag_backend.EmbedConfig(), "p", image_url=local)
    # 模拟历史记录：抹掉 thumb_url，但保留 thumb 文件（= generate-thumbs 补生成的场景）
    doc = next(iter(store.rows.values()))
    doc.metadata["thumb_url"] = ""
    assert thumbnail_store.thumb_path_for(src).is_file()

    item = rag_store.list_generations("repo", rag_backend.EmbedConfig())[0]
    assert item["thumbUrl"]
    assert _is_thumb_url(item["thumbUrl"])
