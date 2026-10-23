"""液态玻璃壁纸库：扫描/分页/过滤 + 缩略图缓存 + 端点边界。"""
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from PIL import Image

from app.routers import user_state
from app.services import wallpaper_library


def _png(path: Path, color=(120, 60, 200)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (800, 600), color).save(path)


def _tree(root: Path) -> None:
    _png(root / "zeta.png")
    _png(root / "sub" / "alpha.jpg")
    (root / "sub2").mkdir()
    (root / "sub2" / "clip.mp4").write_bytes(b"not a real video")
    (root / "notes.txt").write_text("ignore me", encoding="utf-8")
    (root / ".hidden").mkdir()
    _png(root / ".hidden" / "skip.png")
    (root / "loop.gif").write_bytes((root / "zeta.png").read_bytes())


def test_scan_递归过滤排序(tmp_path):
    _tree(tmp_path)
    result = wallpaper_library.scan(str(tmp_path))
    assert result["folder"] == str(tmp_path.resolve())
    assert result["total"] == 4
    assert result["pages"] == 1
    assert result["truncated"] is False
    assert [item["name"] for item in result["items"]] == [
        "loop", "sub/alpha", "sub2/clip", "zeta",
    ]
    kinds = {item["name"]: item["kind"] for item in result["items"]}
    assert kinds["sub2/clip"] == "video"
    assert kinds["zeta"] == "image"
    animated = {item["name"]: item["animated"] for item in result["items"]}
    assert animated["loop"] is True
    assert animated["zeta"] is False


def test_scan_类型过滤与分页(tmp_path):
    _tree(tmp_path)
    videos = wallpaper_library.scan(str(tmp_path), kind="video")
    assert videos["total"] == 1
    assert videos["items"][0]["name"] == "sub2/clip"

    first = wallpaper_library.scan(str(tmp_path), kind="image", page=1, page_size=2)
    assert first["total"] == 3
    assert first["pages"] == 2
    assert len(first["items"]) == 2
    second = wallpaper_library.scan(str(tmp_path), kind="image", page=2, page_size=2)
    assert second["page"] == 2
    assert len(second["items"]) == 1
    # 越界页收敛到最后一页
    assert wallpaper_library.scan(str(tmp_path), page=99, page_size=2)["page"] == 2


def test_scan_空地址与不存在目录报错(tmp_path):
    with pytest.raises(ValueError, match="请先填写"):
        wallpaper_library.scan("  ")
    with pytest.raises(ValueError, match="文件夹不存在"):
        wallpaper_library.scan(str(tmp_path / "missing"))


def test_scan_按名称搜索_不区分大小写且参与分页(tmp_path):
    _tree(tmp_path)
    alpha = wallpaper_library.scan(str(tmp_path), query="ALPHA")
    assert alpha["query"] == "ALPHA"
    assert alpha["total"] == 1
    assert [item["name"] for item in alpha["items"]] == ["sub/alpha"]

    sub = wallpaper_library.scan(str(tmp_path), query="sub", page_size=1)
    assert sub["total"] == 2
    assert sub["pages"] == 2
    assert [item["name"] for item in sub["items"]] == ["sub/alpha"]
    assert wallpaper_library.scan(str(tmp_path), query="sub", page=2, page_size=1)["items"][0]["name"] == "sub2/clip"

    # 搜索与类型/隐藏状态叠加：隐藏项只在已隐藏页里被搜到
    hidden = [str(tmp_path / "zeta.png")]
    assert wallpaper_library.scan(str(tmp_path), query="zeta", hidden=hidden)["total"] == 0
    assert wallpaper_library.scan(
        str(tmp_path), query="zeta", hidden=hidden, hidden_only=True,
    )["items"][0]["name"] == "zeta"
    assert wallpaper_library.scan(str(tmp_path), query="zzz")["total"] == 0


def test_scan_隐藏列表从正常列表剔除并可单独查看(tmp_path):
    _tree(tmp_path)
    hidden = [str(tmp_path / "zeta.png"), str(tmp_path / "sub2" / "clip.mp4")]
    normal = wallpaper_library.scan(str(tmp_path), hidden=hidden)
    assert normal["total"] == 2
    assert normal["visible_total"] == 2
    assert normal["hidden_total"] == 2
    assert [item["name"] for item in normal["items"]] == ["loop", "sub/alpha"]

    hidden_page = wallpaper_library.scan(str(tmp_path), hidden=hidden, hidden_only=True)
    assert hidden_page["total"] == 2
    assert hidden_page["visible_total"] == 2
    assert [item["name"] for item in hidden_page["items"]] == ["sub2/clip", "zeta"]

    # 隐藏路径大小写/斜杠差异不影响命中（Windows 路径不区分大小写）
    upper = [str(tmp_path).upper() + "/ZETA.PNG"]
    assert wallpaper_library.scan(str(tmp_path), hidden=upper)["hidden_total"] == 1

    # 隐藏项也跟随类型过滤
    only_video = wallpaper_library.scan(str(tmp_path), kind="video", hidden=hidden, hidden_only=True)
    assert [item["name"] for item in only_video["items"]] == ["sub2/clip"]
    assert only_video["hidden_total"] == 1


def test_thumbnail_图片生成并缓存(tmp_path, monkeypatch):
    monkeypatch.setattr(wallpaper_library, "CACHE_DIR", tmp_path / "cache")
    source = tmp_path / "wall" / "one.png"
    _png(source, color=(10, 200, 40))

    first = wallpaper_library.thumbnail(source)
    assert first is not None and first.is_file()
    with Image.open(first) as image:
        assert image.format == "JPEG"
        assert max(image.size) <= wallpaper_library._THUMB_EDGE
    stamp = first.stat().st_mtime_ns

    second = wallpaper_library.thumbnail(source)
    assert second == first
    assert second.stat().st_mtime_ns == stamp  # 命中缓存，不重复生成


def test_thumbnail_视频无ffmpeg返回None(tmp_path, monkeypatch):
    monkeypatch.setattr(wallpaper_library, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(wallpaper_library, "find_ffmpeg", lambda: None)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not a real video")
    assert wallpaper_library.thumbnail(video) is None


def test_resolve_wallpaper_拒绝越界路径(tmp_path):
    root = tmp_path / "walls"
    root.mkdir()
    inside = root / "a.png"
    _png(inside)
    (root / "notes.txt").write_text("x", encoding="utf-8")
    outside = tmp_path / "secret.png"
    _png(outside)

    assert wallpaper_library.resolve_wallpaper(str(root), str(inside)) == inside.resolve()
    with pytest.raises(ValueError, match="不在指定文件夹内"):
        wallpaper_library.resolve_wallpaper(str(root), str(outside))
    with pytest.raises(ValueError, match="不支持的壁纸格式"):
        wallpaper_library.resolve_wallpaper(str(root), str(root / "notes.txt"))


def test_router_scan_非法目录返回400(tmp_path):
    with pytest.raises(HTTPException) as exc:
        user_state.scan_wallpapers(user_state.WallpaperScanRequest(folder=str(tmp_path / "nope")))
    assert exc.value.status_code == 400


def test_router_thumb_越界返回404(tmp_path, monkeypatch):
    monkeypatch.setattr(wallpaper_library, "CACHE_DIR", tmp_path / "cache")
    root = tmp_path / "walls"
    root.mkdir()
    outside = tmp_path / "outside.png"
    _png(outside)
    with pytest.raises(HTTPException) as exc:
        user_state.wallpaper_thumb(str(root), str(outside))
    assert exc.value.status_code == 404


def _workshop_item(root: Path, item_id: str, project: dict, files: dict[str, bytes]) -> Path:
    item = root / item_id
    item.mkdir(parents=True)
    (item / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    for name, content in files.items():
        (item / name).write_bytes(content)
    return item


def _workshop_tree(tmp_path: Path) -> Path:
    root = tmp_path / "steamapps" / "workshop" / "content" / "431960"
    _workshop_item(root, "100", {
        "type": "Video", "file": "clip.mp4", "preview": "preview.gif", "title": "B Video",
    }, {"clip.mp4": b"video", "preview.gif": b"gif"})
    _workshop_item(root, "200", {
        "type": "scene", "file": "scene.json", "preview": "preview.jpg", "title": "A Scene",
    }, {"scene.pkg": b"pkg", "preview.jpg": b"jpg"})
    _workshop_item(root, "300", {
        "type": "web", "file": "index.html", "preview": "preview.png", "title": "C Web",
    }, {"index.html": b"<html></html>", "preview.png": b"png"})
    # 坏 project.json / 缺预览的条目不应中断扫描
    bad = root / "400"
    bad.mkdir(parents=True)
    (bad / "project.json").write_text("{not json", encoding="utf-8")
    _workshop_item(root, "500", {"type": "scene", "title": "No Preview"}, {})
    return root


def test_vdf_library_paths_解析转义反斜杠(tmp_path):
    steam = tmp_path / "Steam"
    (steam / "steamapps").mkdir(parents=True)
    (steam / "steamapps" / "libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"C:\\\\Program Files (x86)\\\\Steam"\n\t}\n'
        '\t"1"\n\t{\n\t\t"path"\t\t"D:\\\\SteamLibrary"\n\t}\n}\n',
        encoding="utf-8",
    )
    paths = wallpaper_library._vdf_library_paths(steam)
    assert [str(p) for p in paths] == [r"C:\Program Files (x86)\Steam", r"D:\SteamLibrary"]


def test_scan_workshop_识别视频场景网页与静态预览(tmp_path, monkeypatch):
    root = _workshop_tree(tmp_path)
    monkeypatch.setattr(wallpaper_library, "workshop_roots", lambda: [root])

    result = wallpaper_library.scan_workshop()
    assert result["found"] is True
    assert result["roots"] == [str(root)]
    assert result["total"] == 3
    assert [item["title"] for item in result["items"]] == ["A Scene", "B Video", "C Web"]

    by_title = {item["title"]: item for item in result["items"]}
    video = by_title["B Video"]
    assert video["kind"] == "video"
    assert video["preview_only"] is False
    assert video["we_type"] == "video"
    assert video["folder"] == str(root / "100")
    assert video["path"].endswith("clip.mp4")
    # 视频作品也带工坊封面，列表用 GIF 动图而不是暗首帧
    assert video["preview"].endswith("preview.gif")
    assert video["preview_width"] == 0  # 占位字节不是真图，尺寸读不出不影响扫描

    scene = by_title["A Scene"]
    assert scene["kind"] == "image"
    assert scene["preview_only"] is True
    assert scene["we_type"] == "scene"
    assert scene["path"].endswith("preview.jpg")
    assert scene["preview"] == scene["path"]

    web = by_title["C Web"]
    assert web["preview_only"] is True
    assert web["path"].endswith("preview.png")

    only_video = wallpaper_library.scan_workshop(kind="video")
    assert only_video["total"] == 1
    assert only_video["items"][0]["title"] == "B Video"

    hidden = [scene["path"]]
    normal = wallpaper_library.scan_workshop(hidden=hidden)
    assert normal["total"] == 2
    assert normal["visible_total"] == 2
    assert normal["hidden_total"] == 1
    hidden_page = wallpaper_library.scan_workshop(hidden=hidden, hidden_only=True)
    assert [item["title"] for item in hidden_page["items"]] == ["A Scene"]


def test_scan_workshop_按标题与工坊id搜索(tmp_path, monkeypatch):
    root = _workshop_tree(tmp_path)
    monkeypatch.setattr(wallpaper_library, "workshop_roots", lambda: [root])

    assert [item["title"] for item in wallpaper_library.scan_workshop(query="SCENE")["items"]] == ["A Scene"]
    assert [item["title"] for item in wallpaper_library.scan_workshop(query="200")["items"]] == ["A Scene"]
    assert [item["title"] for item in wallpaper_library.scan_workshop(query="b video")["items"]] == ["B Video"]
    assert wallpaper_library.scan_workshop(query="scene", kind="video")["total"] == 0
    assert wallpaper_library.scan_workshop(query="not-there")["total"] == 0


def test_scan_workshop_返回真实预览尺寸(tmp_path, monkeypatch):
    root = tmp_path / "steamapps" / "workshop" / "content" / "431960"
    item = _workshop_item(root, "600", {
        "type": "Video", "file": "clip.mp4", "preview": "preview.png", "title": "HD Cover",
    }, {"clip.mp4": b"video"})
    _png(item / "preview.png", color=(30, 200, 120))
    monkeypatch.setattr(wallpaper_library, "workshop_roots", lambda: [root])

    result = wallpaper_library.scan_workshop()
    assert result["total"] == 1
    entry = result["items"][0]
    assert entry["preview"].endswith("preview.png")
    assert (entry["preview_width"], entry["preview_height"]) == (800, 600)
    assert entry["preview_only"] is False


def test_scan_workshop_分页(tmp_path, monkeypatch):
    root = _workshop_tree(tmp_path)
    monkeypatch.setattr(wallpaper_library, "workshop_roots", lambda: [root])
    first = wallpaper_library.scan_workshop(page=1, page_size=2)
    assert first["pages"] == 2
    assert len(first["items"]) == 2
    second = wallpaper_library.scan_workshop(page=2, page_size=2)
    assert len(second["items"]) == 1
    assert second["items"][0]["title"] == "C Web"


def test_scan_workshop_无Steam返回空(monkeypatch):
    monkeypatch.setattr(wallpaper_library, "workshop_roots", lambda: [])
    result = wallpaper_library.scan_workshop()
    assert result["found"] is False
    assert result["roots"] == []
    assert result["total"] == 0
    assert result["items"] == []


def test_router_workshop_返回识别结果(tmp_path, monkeypatch):
    root = _workshop_tree(tmp_path)
    monkeypatch.setattr(wallpaper_library, "workshop_roots", lambda: [root])
    result = user_state.scan_workshop_wallpapers(user_state.WallpaperWorkshopRequest())
    assert result["found"] is True
    assert result["total"] == 3
    searched = user_state.scan_workshop_wallpapers(
        user_state.WallpaperWorkshopRequest(query="web"),
    )
    assert [item["title"] for item in searched["items"]] == ["C Web"]


def test_scan_workshop_scene_pkg_只标给能实时渲染的作品(tmp_path, monkeypatch):
    root = _workshop_tree(tmp_path)
    monkeypatch.setattr(wallpaper_library, "workshop_roots", lambda: [root])

    by_title = {item["title"]: item for item in wallpaper_library.scan_workshop()["items"]}
    assert by_title["A Scene"]["scene_pkg"] is True
    assert by_title["B Video"]["scene_pkg"] is False
    assert by_title["C Web"]["scene_pkg"] is False
    # scene 类型但没有包 / 有包但类型不是 scene，都不能被标成可实时渲染
    assert wallpaper_library.has_scene_package(root / "500", "scene") is False
    assert wallpaper_library.has_scene_package(root / "200", "video") is False


def test_scene_package_只按数字id在工坊根下找(tmp_path, monkeypatch):
    root = _workshop_tree(tmp_path)
    monkeypatch.setattr(wallpaper_library, "workshop_roots", lambda: [root])

    assert wallpaper_library.scene_package("200") == (root / "200" / "scene.pkg").resolve()
    for bad in ("", "abc", "../100", "200/../100", "200/scene.pkg", " 200 x", "200\\100"):
        with pytest.raises(ValueError):
            wallpaper_library.scene_package(bad)
    for absent in ("100", "999999"):  # 有 id 但不是 scene 包 / 作品不存在
        with pytest.raises(ValueError):
            wallpaper_library.scene_package(absent)


def test_router_scene_pkg_交出包字节否则404(tmp_path, monkeypatch):
    root = _workshop_tree(tmp_path)
    monkeypatch.setattr(wallpaper_library, "workshop_roots", lambda: [root])

    response = user_state.workshop_scene_pkg("200")
    assert Path(response.path).resolve() == (root / "200" / "scene.pkg").resolve()
    assert response.media_type == "application/octet-stream"
    assert response.headers["Cache-Control"] == "private, max-age=86400"

    with pytest.raises(HTTPException) as exc:
        user_state.workshop_scene_pkg("999999")
    assert exc.value.status_code == 404


def test_router_scan_透传搜索词(tmp_path):
    _tree(tmp_path)
    result = user_state.scan_wallpapers(
        user_state.WallpaperScanRequest(folder=str(tmp_path), query="zeta"),
    )
    assert result["total"] == 1
    assert result["items"][0]["name"] == "zeta"


def test_store_info_默认目录与用户主目录展开(tmp_path, monkeypatch):
    monkeypatch.setattr(wallpaper_library, "DEFAULT_STORE_DIR", tmp_path / "default-store")
    info = wallpaper_library.store_info("")
    assert info["is_default"] is True
    assert info["dir"] == str((tmp_path / "default-store").resolve())
    assert info["count"] == 0
    assert info["formats"] == ["jpg", "jpeg", "png", "mp4"]

    home = tmp_path / "home"
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    expanded = wallpaper_library.store_info("~/walls")
    assert expanded["dir"] == str((home / "walls").resolve())
    assert expanded["is_default"] is False

    with pytest.raises(ValueError, match="绝对路径"):
        wallpaper_library.store_info("relative/walls")
    with pytest.raises(ValueError, match="绝对路径"):
        wallpaper_library.migrate_store("old", str(tmp_path / "new"))


def test_store_上传_格式限制_重名去重与计数(tmp_path, monkeypatch):
    import io

    monkeypatch.setattr(wallpaper_library, "DEFAULT_STORE_DIR", tmp_path / "store")
    first = wallpaper_library.save_upload(io.BytesIO(b"one"), "Snow Day.PNG", "")
    second = wallpaper_library.save_upload(io.BytesIO(b"two"), "Snow Day.PNG", "")
    assert first["path"] != second["path"]
    assert Path(first["path"]).name == "Snow Day.png"
    assert Path(second["path"]).name == "Snow Day (2).png"
    assert first["name"] == "Snow Day.PNG"
    assert wallpaper_library.store_info("")["count"] == 2

    with pytest.raises(ValueError, match="格式仅限"):
        wallpaper_library.save_upload(io.BytesIO(b"x"), "clip.webm", "")
    with pytest.raises(ValueError, match="为空"):
        wallpaper_library.save_upload(io.BytesIO(b""), "empty.jpg", "")
    assert wallpaper_library.store_info("")["count"] == 2


def test_store_迁移_只搬直接媒体文件并返回映射(tmp_path):
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    (old / "a.png").write_bytes(b"a")
    (old / "b.mp4").write_bytes(b"b")
    (old / "notes.txt").write_text("x", encoding="utf-8")
    nested = old / "sub"
    nested.mkdir()
    (nested / "c.png").write_bytes(b"c")

    result = wallpaper_library.migrate_store(str(old), str(new))
    assert result["moved"] == 2
    assert set(result["mapping"]) == {str(old / "a.png"), str(old / "b.mp4")}
    assert (new / "a.png").read_bytes() == b"a"
    assert (new / "b.mp4").read_bytes() == b"b"
    assert (old / "notes.txt").is_file()
    assert (nested / "c.png").is_file()
    assert result["count"] == 2

    # 同名文件先占位 ⇒ 迁移自动加序号，映射指向新名字
    (old / "a.png").write_bytes(b"again")
    again = wallpaper_library.migrate_store(str(new), str(old))
    assert (old / "a.png").read_bytes() == b"again"
    assert (old / "a (2).png").read_bytes() == b"a"
    assert again["mapping"][str(new / "a.png")] == str(old / "a (2).png")

    same = wallpaper_library.migrate_store(str(old), str(old))
    assert same["moved"] == 0 and same["mapping"] == {}
    with pytest.raises(ValueError, match="父目录或子目录"):
        wallpaper_library.migrate_store(str(old), str(old / "nested-target"))


def test_router_store_非法路径与上传格式返回400(tmp_path, monkeypatch):
    import io
    from fastapi import UploadFile

    monkeypatch.setattr(wallpaper_library, "DEFAULT_STORE_DIR", tmp_path / "store")
    with pytest.raises(HTTPException) as exc:
        user_state.wallpaper_store("relative")
    assert exc.value.status_code == 400

    bad = UploadFile(filename="clip.webm", file=io.BytesIO(b"x"))
    with pytest.raises(HTTPException) as exc:
        user_state.wallpaper_upload(file=bad, dir="")
    assert exc.value.status_code == 400

    good = UploadFile(filename="wall.jpg", file=io.BytesIO(b"jpeg"))
    saved = user_state.wallpaper_upload(file=good, dir="")
    assert saved["count"] == 1
    assert Path(saved["path"]).is_file()


# ── 本地文件夹里的 Wallpaper Engine 工程（A 刀①：来源不再只认纯数字工坊 id）──────────


def _local_project(root: Path, title: str = "My Scene") -> Path:
    """造一个 WE 工程目录：project.json + scene.pkg + preview.gif。"""
    project = root / "we-project"
    project.mkdir(parents=True, exist_ok=True)
    (project / "project.json").write_text(
        json.dumps({"title": title, "type": "scene", "file": "scene.json", "preview": "preview.gif"}),
        encoding="utf-8",
    )
    (project / "scene.pkg").write_bytes(b"PKGV0001 fake package")
    _png(project / "preview.gif")
    return project


def test_scene_package_本地工程来源(tmp_path):
    project = _local_project(tmp_path)
    assert wallpaper_library.scene_package("local:" + str(project)) == (project / "scene.pkg").resolve()
    for bad in ("local:", "local:" + str(tmp_path / "nope"), "local:" + str(tmp_path)):
        with pytest.raises(ValueError):
            wallpaper_library.scene_package(bad)


def test_scene_package_本地目录必须同时有工程清单和包(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "scene.pkg").write_bytes(b"x")
    with pytest.raises(ValueError):  # 只有同名包，不能证明是 WE 工程
        wallpaper_library.scene_package("local:" + str(plain))

    project = _local_project(tmp_path / "p2")
    (project / "scene.pkg").unlink()
    with pytest.raises(ValueError):
        wallpaper_library.scene_package("local:" + str(project))


def test_scene_package_workshop前缀等价于纯数字(tmp_path, monkeypatch):
    root = _workshop_tree(tmp_path)
    monkeypatch.setattr(wallpaper_library, "workshop_roots", lambda: [root])
    assert wallpaper_library.scene_package("workshop:200") == (root / "200" / "scene.pkg").resolve()
    for bad in ("workshop:", "workshop:abc"):
        with pytest.raises(ValueError):
            wallpaper_library.scene_package(bad)


def test_scan_本地工程作为一条场景条目(tmp_path):
    project = _local_project(tmp_path)
    _png(tmp_path / "plain.png")

    result = wallpaper_library.scan(str(tmp_path))
    assert result["total"] == 2  # 工程只出 1 条（不是 preview 图 + 场景两条）
    by_name = {item["name"]: item for item in result["items"]}
    scene = by_name["My Scene"]
    assert scene["scene_pkg"] is True
    assert scene["scene_source"] == "local:" + project.as_posix()
    assert scene["path"].endswith("preview.gif")
    assert "plain" in by_name


def test_router_scene_pkg_接受通用来源(tmp_path):
    project = _local_project(tmp_path)
    response = user_state.wallpaper_scene_pkg(source="local:" + str(project))
    assert Path(response.path).resolve() == (project / "scene.pkg").resolve()

    with pytest.raises(HTTPException) as exc:
        user_state.wallpaper_scene_pkg(source="local:" + str(tmp_path / "nope"))
    assert exc.value.status_code == 404
